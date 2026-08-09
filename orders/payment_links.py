from django.conf import settings
from django.core import signing
from django.urls import reverse


SALT = "orders.payment-link.v1"


class PaymentLinkError(Exception):
    pass


def make_payment_token(order):
    return signing.dumps({"order": order.pk, "version": order.payment_link_version}, salt=SALT, compress=True)


def make_payment_url(order):
    path = reverse("payment", kwargs={"token": make_payment_token(order)})
    return f"{settings.CANONICAL_ORIGIN}{path}"


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
