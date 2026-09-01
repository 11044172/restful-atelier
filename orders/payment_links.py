from django.conf import settings
from django.core import signing
from django.urls import reverse

SALT = "orders.payment-link.v1"
CANCEL_SALT = "orders.cancel-link.v1"
PAYMENT_RESULT_SALT = "orders.payment-result.v1"


class PaymentLinkError(Exception):
    pass


def make_payment_token(order):
    return signing.dumps({"order": order.pk, "version": order.payment_link_version}, salt=SALT, compress=True)


def make_payment_url(order):
    path = reverse("payment", kwargs={"token": make_payment_token(order)})
    return f"{settings.CANONICAL_ORIGIN}{path}"


def make_cancel_token(order):
    return signing.dumps({"order": order.pk, "version": order.cancel_link_version, "access": order.access_token}, salt=CANCEL_SALT, compress=True)


def make_cancel_url(order):
    path = reverse("orders:cancel", kwargs={"token": make_cancel_token(order)})
    return f"{settings.CANONICAL_ORIGIN}{path}"


def make_payment_result_token(payment):
    return signing.dumps(
        {"payment": payment.pk, "trade": payment.merchant_trade_no},
        salt=PAYMENT_RESULT_SALT,
        compress=True,
    )


def make_payment_result_url(payment):
    path = reverse("ecpay_return", kwargs={"token": make_payment_result_token(payment)})
    return f"{settings.CANONICAL_ORIGIN}{path}"


def resolve_payment_result_token(token):
    from .models import Payment

    try:
        payload = signing.loads(token, salt=PAYMENT_RESULT_SALT, max_age=settings.PAYMENT_LINK_MAX_AGE)
        payment_id = int(payload["payment"])
        trade_no = str(payload["trade"])
    except signing.SignatureExpired as exc:
        raise PaymentLinkError("expired") from exc
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise PaymentLinkError("invalid") from exc
    payment = Payment.objects.select_related("order").filter(
        pk=payment_id,
        provider="ecpay",
        merchant_trade_no=trade_no,
    ).first()
    if payment is None:
        raise PaymentLinkError("invalid")
    return payment


def resolve_cancel_token(token):
    from .models import Order
    try:
        payload = signing.loads(token, salt=CANCEL_SALT, max_age=settings.PAYMENT_LINK_MAX_AGE)
        order_id = int(payload["order"])
        version = int(payload["version"])
        access = str(payload["access"])
    except signing.SignatureExpired as exc:
        raise PaymentLinkError("expired") from exc
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise PaymentLinkError("invalid") from exc
    order = Order.objects.filter(pk=order_id, access_token=access).first()
    if not order or version != order.cancel_link_version or version < 1:
        raise PaymentLinkError("invalid")
    return order


def resolve_payment_token(token):
    from .models import Order

    try:
        payload = signing.loads(token, salt=SALT, max_age=settings.PAYMENT_LINK_MAX_AGE)
        order_id = int(payload["order"])
        version = int(payload["version"])
    except signing.SignatureExpired as exc:
        raise PaymentLinkError("expired") from exc
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise PaymentLinkError("invalid") from exc
    order = Order.objects.filter(pk=order_id).first()
    if not order or version != order.payment_link_version or version < 1:
        raise PaymentLinkError("invalid")
    if order.status == Order.Status.CANCELLED:
        raise PaymentLinkError("cancelled")
    if order.is_paid:
        raise PaymentLinkError("paid")
    if (
        order.final_total is None
        or order.payment_request_total != order.final_total
        or order.status != Order.Status.AWAITING_PAYMENT
    ):
        raise PaymentLinkError("unavailable")
    return order
