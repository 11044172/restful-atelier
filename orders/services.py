import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse

from catalog.models import Product
from core.models import SiteSettings
from .models import Order, OrderItem

logger = logging.getLogger(__name__)


class CartValidationError(Exception):
    pass


@transaction.atomic
def create_order_from_cart(*, cart, cleaned_data, line_customer=None):
    token = cleaned_data["idempotency_key"]
    existing = Order.objects.filter(idempotency_key=token).first()
    if existing:
        return existing, False

    raw_items = cart.items()
    if not raw_items:
        raise CartValidationError("購物車目前是空的。")
    ids = [item["product"].pk for item in raw_items]
    locked_products = {product.pk: product for product in Product.objects.select_for_update().filter(pk__in=ids, is_published=True)}
    prepared = []
    subtotal = Decimal("0")
    for cart_item in raw_items:
        product = locked_products.get(cart_item["product"].pk)
        quantity = cart_item["quantity"]
        if not product:
            raise CartValidationError("購物車の商品が現在購入できません。")
        if not product.is_preorder and product.stock < quantity:
            raise CartValidationError(f"「{product.name}」の在庫は {product.stock} 件です。数量を調整してください。")
        line_total = product.price * Decimal(quantity)
        subtotal += line_total
        prepared.append((product, quantity, line_total))

    try:
        with transaction.atomic():
            order = Order.objects.create(
                idempotency_key=token,
                line_customer=line_customer,
                customer_name=cleaned_data["customer_name"],
                phone=cleaned_data["phone"],
                email=cleaned_data["email"],
                shipping_information=cleaned_data["shipping_information"],
                customer_note=cleaned_data.get("customer_note", ""),
                subtotal=subtotal,
                shipping_fee=None,
                final_total=None,
                status=Order.Status.SHIPPING_REVIEW,
                inventory_reserved=True,
            )
    except IntegrityError:
        return Order.objects.get(idempotency_key=token), False

    for product, quantity, line_total in prepared:
        reserved = not product.is_preorder
        if reserved:
            product.stock -= quantity
            product.save(update_fields=["stock", "updated_at"])
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name_snapshot=product.name,
            sku_snapshot=product.sku,
            unit_price_snapshot=product.price,
            quantity=quantity,
            line_total=line_total,
            stock_was_reserved=reserved,
        )
    return order, True


def send_order_emails(order):
    site = SiteSettings.load()
    order_admin_url = f"{settings.CANONICAL_ORIGIN}{reverse('admin:orders_order_change', args=[order.pk])}"
    context = {"order": order, "site_settings": site, "order_admin_url": order_admin_url}
    subject = f"[Rfull] 注文受付 {order.public_number}"
    customer_body = render_to_string("orders/email/customer_received.txt", context)
    staff_body = render_to_string("orders/email/staff_received.txt", context)
    try:
        send_mail(subject, customer_body, settings.DEFAULT_FROM_EMAIL, [order.email], fail_silently=False)
    except Exception:
        logger.exception("Customer order email failed for %s", order.public_number)
    recipients = []
    primary_recipient = site.order_notification_email or settings.ORDER_NOTIFICATION_EMAIL
    for recipient in [primary_recipient, *settings.ORDER_NOTIFICATION_EMAILS]:
        if recipient and recipient not in recipients:
            recipients.append(recipient)
    if recipients:
        try:
            send_mail(subject, staff_body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
        except Exception:
            logger.exception("Staff order email failed for %s", order.public_number)
