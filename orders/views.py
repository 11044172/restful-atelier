import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from catalog.models import Product
from core.models import SiteSettings
from core.line_config import line_settings_configured
from inquiries.antispam import rate_limit_exceeded, verify_turnstile
from .cart import Cart
from .forms import CheckoutForm
from .line_login import LineLoginError, authorization_url, exchange_code, generate_oauth_values, get_friendship_status, verify_id_token
from .line_messaging import schedule_order_received
from .line_webhooks import parse_payload, process_event, valid_signature
from .models import LineCustomer, Order, PaymentMethod
from .payment_links import PaymentLinkError, resolve_payment_token
from .payment_providers import get_provider
from .services import CartValidationError, create_order_from_cart, send_order_emails


def cart_detail(request):
    cart = Cart(request)
    return render(request, "orders/cart.html", {"cart": cart, "cart_items": cart.items(), "shop_page": True, "noindex": True})


@require_POST
def cart_add(request, slug):
    product = get_object_or_404(Product.objects.published(), slug=slug)
    try:
        quantity = max(1, min(99, int(request.POST.get("quantity", "1"))))
    except ValueError:
        return HttpResponseBadRequest("Invalid quantity")
    if not product.is_preorder and product.stock < quantity:
        messages.error(request, f"目前庫存為 {product.stock} 件。")
    else:
        Cart(request).add(product, quantity)
        messages.success(request, "商品已加入購物車。")
    return redirect(request.POST.get("next") or "orders:cart")


@require_POST
def cart_update(request, product_id):
    product = get_object_or_404(Product.objects.published(), pk=product_id)
    action = request.POST.get("action")
    cart = Cart(request)
    if action == "remove":
        cart.remove(product.pk)
        messages.success(request, "商品已從購物車移除。")
    else:
        try:
            quantity = max(1, min(99, int(request.POST.get("quantity", "1"))))
        except ValueError:
            return HttpResponseBadRequest("Invalid quantity")
        if not product.is_preorder and quantity > product.stock:
            messages.error(request, f"「{product.name}」目前庫存為 {product.stock} 件。")
        else:
            cart.add(product, quantity, replace=True)
    return redirect("orders:cart")


def checkout(request):
    cart = Cart(request)
    site = SiteSettings.load()
    enabled = site.checkout_available(debug=settings.DEBUG)
    line_customer = _session_line_customer(request)
    line_identity = LineCustomer.objects.filter(pk=request.session.get("line_customer_id")).first() if request.session.get("line_customer_id") else None
    if request.method == "GET":
        token = secrets.token_urlsafe(32)
        request.session["checkout_token"] = token
        form = CheckoutForm(initial={"idempotency_key": token})
    else:
        if not enabled:
            messages.error(request, "訂單服務目前準備中。")
            return redirect("orders:cart")
        if rate_limit_exceeded(request, "checkout", settings.CHECKOUT_RATE_LIMIT):
            return render(request, "orders/checkout.html", {"form": CheckoutForm(request.POST), "cart": cart, "cart_items": cart.items(), "checkout_enabled": enabled, "line_customer": line_customer, "line_identity": line_identity, "turnstile_site_key": settings.TURNSTILE_SITE_KEY, "shop_page": True, "noindex": True, "rate_limited": True}, status=429)
        form = CheckoutForm(request.POST)
        expected = request.session.get("checkout_token")
        if form.is_valid() and expected and secrets.compare_digest(form.cleaned_data["idempotency_key"], expected):
            if not verify_turnstile(request):
                form.add_error(None, "安全驗證失敗，請再試一次。")
            else:
                try:
                    if not line_customer:
                        raise CartValidationError("請先使用 LINE 登入並加入 Rfull 官方帳號。")
                    order, created = create_order_from_cart(cart=cart, cleaned_data=form.cleaned_data, line_customer=line_customer)
                except CartValidationError as exc:
                    form.add_error(None, str(exc))
                else:
                    if created:
                        cart.clear()
                        transaction.on_commit(lambda: send_order_emails(order))
                        transaction.on_commit(lambda order_id=order.pk: schedule_order_received(order_id))
                    request.session.pop("checkout_token", None)
                    request.session["completed_order"] = order.public_number
                    return redirect("orders:complete", order_number=order.public_number)
        elif form.is_valid():
            form.add_error(None, "此訂單表單已失效，請重新載入頁面。")
    return render(request, "orders/checkout.html", {"form": form, "cart": cart, "cart_items": cart.items(), "checkout_enabled": enabled, "line_customer": line_customer, "line_identity": line_identity, "turnstile_site_key": settings.TURNSTILE_SITE_KEY, "shop_page": True, "noindex": True})


def order_complete(request, order_number):
    if request.session.get("completed_order") != order_number:
        return redirect("catalog:shop")
    order = get_object_or_404(Order.objects.prefetch_related("items"), public_number=order_number)
    return render(request, "orders/complete.html", {"order": order, "shop_page": True, "noindex": True})


def _session_line_customer(request):
    customer_id = request.session.get("line_customer_id")
    checked_at = request.session.get("line_friend_checked_at", 0)
    if not customer_id or request.session.get("line_friend_verified") is not True or time.time() - checked_at > settings.LINE_FRIENDSHIP_MAX_AGE:
        return None
    return LineCustomer.objects.filter(pk=customer_id, is_friend=True, is_blocked=False).first()


def line_login_start(request):
    if not line_settings_configured():
        messages.error(request, "LINE 登入目前尚未完成設定，請稍後再試。")
        return redirect("orders:checkout")
    values = generate_oauth_values()
    next_url = request.GET.get("next") or reverse("orders:checkout")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=not settings.DEBUG):
        next_url = reverse("orders:checkout")
    request.session["line_oauth"] = {
        "state": values["state"], "nonce": values["nonce"],
        "code_verifier": values["code_verifier"], "next": next_url,
    }
    return redirect(authorization_url(state=values["state"], nonce=values["nonce"], code_challenge=values["code_challenge"]))


def line_login_callback(request):
    oauth = request.session.pop("line_oauth", None)
    if request.GET.get("error"):
        messages.info(request, "LINE 登入已取消。您的購物車內容仍保留著。")
        return redirect((oauth or {}).get("next") or "orders:checkout")
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if not oauth or not state or not secrets.compare_digest(state, oauth.get("state", "")) or not code:
        messages.error(request, "LINE 登入驗證失敗，請重新操作。")
        return redirect("orders:checkout")
    try:
        tokens = exchange_code(code=code, code_verifier=oauth["code_verifier"])
        claims = verify_id_token(id_token=tokens["id_token"], nonce=oauth["nonce"])
        is_friend = get_friendship_status(tokens["access_token"])
    except (LineLoginError, KeyError):
        messages.error(request, "LINE 登入驗證失敗，請稍後再試。")
        return redirect("orders:checkout")
    now = timezone.now()
    customer, created = LineCustomer.objects.get_or_create(
        line_user_id=claims["sub"], defaults={"display_name": claims["name"]}
    )
    was_friend = customer.is_friend
    customer.display_name = claims["name"]
    customer.picture_url = claims.get("picture", "")
    customer.is_friend = is_friend
    if is_friend:
        customer.is_blocked = False
        if created or not was_friend:
            customer.followed_at = now
    customer.last_login_at = now
    customer.friend_checked_at = now
    customer.save()
    request.session.cycle_key()
    request.session["line_customer_id"] = customer.pk
    request.session["line_friend_checked_at"] = time.time()
    request.session["line_friend_verified"] = is_friend
    if is_friend:
        messages.success(request, "LINE 登入及好友狀態已確認。")
    else:
        messages.warning(request, "請先加入 Rfull 官方 LINE，才能送出訂單。")
    return redirect(oauth.get("next") or "orders:checkout")


def payment(request, token):
    try:
        order = resolve_payment_token(token)
    except PaymentLinkError as exc:
        status = 410 if str(exc) in {"expired", "cancelled", "paid"} else 404
        return render(request, "orders/payment_link_error.html", {"reason": str(exc), "noindex": True}, status=status)
    site = SiteSettings.load()
    methods = []
    for method in PaymentMethod.objects.filter(enabled=True):
        if method.code == PaymentMethod.Method.BANK_TRANSFER:
            if not all((site.bank_name, site.bank_code, site.bank_account_number, site.bank_account_name)):
                continue
        elif method.code == PaymentMethod.Method.TAIWAN_PAY:
            if not (method.qr_image or site.taiwan_pay_qr):
                continue
        elif not method.provider or get_provider(method.provider) is None:
            continue
        methods.append(method)
    return render(request, "orders/payment_instructions.html", {"order": order, "methods": methods, "shop_page": True, "noindex": True})


@csrf_exempt
@require_POST
def line_messaging_webhook(request):
    raw_body = request.body
    if not valid_signature(raw_body, request.headers.get("x-line-signature")):
        return HttpResponseForbidden("Invalid signature")
    try:
        payload = parse_payload(raw_body)
    except ValueError:
        return HttpResponseBadRequest("Invalid payload")
    for event in payload.get("events", []):
        process_event(event)
    return HttpResponse(status=200)
