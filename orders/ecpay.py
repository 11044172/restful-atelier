import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, quote_plus

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Order, Payment
from .operations import record_audit
from .payment_links import make_payment_result_url
from .payment_providers import PaymentProvider

logger = logging.getLogger(__name__)

STAGE_CHECKOUT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
PRODUCTION_CHECKOUT_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"
PROVIDER_NAME = "ecpay"


class ECPayError(Exception):
    pass


class ECPayConfigurationError(ECPayError):
    pass


class ECPayCallbackError(ECPayError):
    pass


@dataclass(frozen=True)
class ECPayConfig:
    environment: str
    merchant_id: str
    hash_key: str
    hash_iv: str
    checkout_url: str

    @property
    def production(self):
        return self.environment == "production"


def get_config():
    environment = settings.ECPAY_ENV
    if environment not in {"stage", "production"}:
        raise ECPayConfigurationError("ECPAY_ENV must be 'stage' or 'production'.")
    values = {
        "ECPAY_MERCHANT_ID": settings.ECPAY_MERCHANT_ID,
        "ECPAY_HASH_KEY": settings.ECPAY_HASH_KEY,
        "ECPAY_HASH_IV": settings.ECPAY_HASH_IV,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ECPayConfigurationError(f"Missing ECPay environment variables: {', '.join(missing)}")
    return ECPayConfig(
        environment=environment,
        merchant_id=values["ECPAY_MERCHANT_ID"],
        hash_key=values["ECPAY_HASH_KEY"],
        hash_iv=values["ECPAY_HASH_IV"],
        checkout_url=PRODUCTION_CHECKOUT_URL if environment == "production" else STAGE_CHECKOUT_URL,
    )


def configuration_status():
    try:
        get_config()
    except ECPayConfigurationError as exc:
        return False, str(exc)
    return True, ""


def ecpay_urlencode(value):
    # ECPay AIO requires application/x-www-form-urlencoded / .NET-style
    # encoding. Python always leaves '~' unescaped, so normalize it explicitly.
    return quote_plus(str(value), safe="-_.!*()").replace("~", "%7E")


def generate_check_mac_value(parameters, *, hash_key, hash_iv):
    items = sorted(
        (str(key), str(value))
        for key, value in parameters.items()
        if str(key).lower() != "checkmacvalue"
    )
    plaintext = f"HashKey={hash_key}&" + "&".join(f"{key}={value}" for key, value in items) + f"&HashIV={hash_iv}"
    encoded = ecpay_urlencode(plaintext).lower()
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


def verify_check_mac_value(parameters, *, hash_key, hash_iv):
    received = str(parameters.get("CheckMacValue", "")).upper()
    if not received:
        return False
    expected = generate_check_mac_value(parameters, hash_key=hash_key, hash_iv=hash_iv)
    return secrets.compare_digest(received, expected)


def _base36(value):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result or "0"


def _assign_merchant_trade_no(payment):
    if payment.merchant_trade_no:
        return payment.merchant_trade_no
    for _ in range(8):
        candidate = f"RF{_base36(payment.pk)[-6:]}{secrets.token_hex(6).upper()}"
        try:
            updated = Payment.objects.filter(pk=payment.pk, merchant_trade_no__isnull=True).update(
                merchant_trade_no=candidate
            )
        except IntegrityError:
            continue
        if updated:
            payment.merchant_trade_no = candidate
            return candidate
        payment.refresh_from_db(fields=("merchant_trade_no",))
        if payment.merchant_trade_no:
            return payment.merchant_trade_no
    raise ECPayError("Unable to allocate a unique ECPay MerchantTradeNo.")


@transaction.atomic
def get_or_create_payment_attempt(*, order_id, method):
    order = Order.objects.select_for_update().prefetch_related("items").get(pk=order_id)
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("此訂單已取消，無法付款。")
    if order.is_paid:
        raise ValidationError("此訂單已完成付款。")
    if (
        order.status != Order.Status.AWAITING_PAYMENT
        or order.shipping_fee is None
        or order.final_total is None
        or order.payment_request_total != order.final_total
    ):
        raise ValidationError("運費或最終付款金額尚未確認，暫時無法付款。")
    if method.provider != PROVIDER_NAME or method.code != method.Method.CREDIT_CARD or not method.enabled:
        raise ValidationError("此信用卡付款方式目前無法使用。")

    payment = (
        Payment.objects.select_for_update()
        .filter(
            order=order,
            method=method,
            provider=PROVIDER_NAME,
            amount=order.final_total,
            currency="TWD",
            status=Payment.Status.AWAITING_CONFIRMATION,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if payment is None:
        payment = Payment.objects.create(
            order=order,
            method=method,
            provider=PROVIDER_NAME,
            amount=order.final_total,
            currency="TWD",
            status=Payment.Status.AWAITING_CONFIRMATION,
            idempotency_key=f"ecpay:{uuid.uuid4()}",
        )
        record_audit(
            order,
            "payment_created",
            actor_label="customer",
            changes={"payment_id": payment.pk, "method": method.code, "amount": str(order.final_total)},
        )
    _assign_merchant_trade_no(payment)
    return payment


def _safe_item_name(payment):
    names = []
    for item in payment.order.items.all():
        cleaned = " ".join(item.product_name_snapshot.replace("#", " ").split())
        names.append(f"{cleaned} x {item.quantity}")
    value = "#".join(names) or f"Order {payment.order.public_number}"
    return value[:400]


def build_checkout_data(payment):
    config = get_config()
    if payment.amount is None or payment.amount != payment.order.final_total:
        raise ECPayError("Payment amount no longer matches the order total.")
    try:
        total_amount = int(payment.amount)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ECPayError("ECPay requires an integer TWD amount.") from exc
    if Decimal(total_amount) != payment.amount or total_amount <= 0:
        raise ECPayError("ECPay requires a positive integer TWD amount.")
    if not payment.merchant_trade_no or len(payment.merchant_trade_no) > 20:
        raise ECPayError("Invalid ECPay MerchantTradeNo.")

    callback_url = f"{settings.CANONICAL_ORIGIN}{reverse('ecpay_callback')}"
    fields = {
        "MerchantID": config.merchant_id,
        "MerchantTradeNo": payment.merchant_trade_no,
        "MerchantTradeDate": timezone.localtime(payment.created_at).strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "TotalAmount": str(total_amount),
        "TradeDesc": "RESTFULL ATELIER order",
        "ItemName": _safe_item_name(payment),
        "ReturnURL": callback_url,
        "ChoosePayment": "Credit",
        "EncryptType": "1",
        "NeedExtraPaidInfo": "N",
        "ClientBackURL": make_payment_result_url(payment),
    }
    fields["CheckMacValue"] = generate_check_mac_value(
        fields, hash_key=config.hash_key, hash_iv=config.hash_iv
    )
    return {"action": config.checkout_url, "fields": fields}


AUDIT_CALLBACK_FIELDS = {
    "MerchantID",
    "MerchantTradeNo",
    "TradeNo",
    "TradeAmt",
    "RtnCode",
    "RtnMsg",
    "PaymentDate",
    "PaymentType",
    "PaymentTypeChargeFee",
    "TradeDate",
    "SimulatePaid",
}


def _sanitized_callback(parameters):
    return {
        key: str(parameters[key])[:255]
        for key in AUDIT_CALLBACK_FIELDS
        if key in parameters
    }


def parse_callback_body(raw_body):
    try:
        pairs = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ECPayCallbackError("Malformed ECPay callback form data.") from exc
    parameters = {}
    for key, value in pairs:
        if key in parameters:
            raise ECPayCallbackError("Duplicate ECPay callback parameter.")
        parameters[key] = value
    return parameters


def _parse_payment_date(value):
    if not value:
        raise ECPayCallbackError("Missing ECPay PaymentDate for successful payment.")
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=timezone.get_current_timezone()
        )
    except ValueError as exc:
        raise ECPayCallbackError("Invalid ECPay PaymentDate.") from exc
    return parsed


@transaction.atomic
def process_callback(parameters):
    config = get_config()
    required = {"MerchantID", "MerchantTradeNo", "TradeAmt", "RtnCode", "CheckMacValue"}
    if not required.issubset(parameters):
        raise ECPayCallbackError("Missing required ECPay callback parameters.")
    if not verify_check_mac_value(parameters, hash_key=config.hash_key, hash_iv=config.hash_iv):
        raise ECPayCallbackError("Invalid ECPay CheckMacValue.")
    if not secrets.compare_digest(parameters["MerchantID"], config.merchant_id):
        raise ECPayCallbackError("Unexpected ECPay MerchantID.")

    payment = (
        Payment.objects.select_for_update()
        .select_related("order", "method")
        .filter(provider=PROVIDER_NAME, merchant_trade_no=parameters["MerchantTradeNo"])
        .first()
    )
    if payment is None:
        raise ECPayCallbackError("Unknown ECPay MerchantTradeNo.")
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if not parameters["TradeAmt"].isdigit():
        raise ECPayCallbackError("Invalid ECPay TradeAmt.")
    callback_amount = Decimal(parameters["TradeAmt"])
    if (
        payment.amount is None
        or callback_amount != payment.amount
        or order.final_total is None
        or callback_amount != order.final_total
        or payment.currency != "TWD"
    ):
        raise ECPayCallbackError("ECPay callback amount mismatch.")

    metadata = dict(payment.provider_metadata or {})
    metadata["callback"] = _sanitized_callback(parameters)
    metadata["callback_received_at"] = timezone.now().isoformat()
    payment.provider_metadata = metadata

    if payment.status == Payment.Status.CONFIRMED:
        payment.save(update_fields=("provider_metadata", "updated_at"))
        return payment, False

    rtn_code = parameters["RtnCode"]
    simulated_in_production = config.production and parameters.get("SimulatePaid") == "1"
    if rtn_code != "1" or simulated_in_production:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=("status", "provider_metadata", "updated_at"))
        record_audit(
            order,
            "payment_failed",
            actor_label="ecpay-callback",
            changes={"payment_id": payment.pk, "rtn_code": rtn_code, "simulated": simulated_in_production},
        )
        return payment, False

    trade_no = parameters.get("TradeNo", "").strip()
    if not trade_no:
        raise ECPayCallbackError("Missing ECPay TradeNo for successful payment.")
    if Payment.objects.filter(provider=PROVIDER_NAME, provider_reference=trade_no).exclude(pk=payment.pk).exists():
        raise ECPayCallbackError("Duplicate ECPay TradeNo.")
    if Payment.objects.filter(order=order, status=Payment.Status.CONFIRMED).exclude(pk=payment.pk).exists():
        raise ECPayCallbackError("Order already has a different confirmed payment.")

    payment.provider_reference = trade_no
    payment.provider_event_id = f"ecpay:{payment.merchant_trade_no}:{trade_no}:paid"
    payment.paid_at = _parse_payment_date(parameters.get("PaymentDate"))
    payment.confirmed_at = timezone.now()
    payment.status = Payment.Status.CONFIRMED
    payment.full_clean()
    if order.status == Order.Status.CANCELLED:
        try:
            Payment.objects.filter(pk=payment.pk).update(
                provider_reference=payment.provider_reference,
                provider_event_id=payment.provider_event_id,
                provider_metadata=payment.provider_metadata,
                paid_at=payment.paid_at,
                confirmed_at=payment.confirmed_at,
                status=Payment.Status.CONFIRMED,
                updated_at=timezone.now(),
            )
        except IntegrityError as exc:
            raise ECPayCallbackError("ECPay payment confirmation conflict.") from exc
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.REFUND_PENDING,
            paid_at=payment.paid_at,
            updated_at=timezone.now(),
        )
        order.status = Order.Status.REFUND_PENDING
        record_audit(
            order,
            "payment_received_after_cancellation",
            actor_label="ecpay-callback",
            from_status=Order.Status.CANCELLED,
            changes={"payment_id": payment.pk, "amount": str(payment.amount)},
        )
        payment.refresh_from_db()
        return payment, True
    try:
        payment.save()
    except IntegrityError as exc:
        raise ECPayCallbackError("ECPay payment confirmation conflict.") from exc
    return payment, True


class ECPayProvider(PaymentProvider):
    def is_configured(self):
        return configuration_status()[0]

    def configuration_error(self):
        return configuration_status()[1]

    def create_payment(self, *, payment):
        return build_checkout_data(payment)

    def get_payment_url(self, *, payment):
        return get_config().checkout_url

    def verify_webhook(self, *, raw_body, headers):
        parameters = parse_callback_body(raw_body)
        config = get_config()
        return verify_check_mac_value(parameters, hash_key=config.hash_key, hash_iv=config.hash_iv)

    def handle_webhook(self, *, raw_body, headers):
        return process_callback(parse_callback_body(raw_body))
