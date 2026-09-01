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
PAYMENT_VARIANT_STANDARD = "standard"
PAYMENT_VARIANT_INSTALLMENT = "installment"
PAYMENT_VARIANTS = {PAYMENT_VARIANT_STANDARD, PAYMENT_VARIANT_INSTALLMENT}
ALLOWED_CREDIT_INSTALLMENTS = ("3", "6", "12", "18", "24")
ALLOWED_IGNORE_PAYMENTS = ("WebATM", "ATM", "CVS", "BARCODE", "BNPL", "WeiXin")
FORBIDDEN_IGNORE_PAYMENTS = {"Credit", "ApplePay", "TWQR", "DigitalPayment"}
TWQR_MIN_AMOUNT = Decimal("6")
TWQR_MAX_AMOUNT = Decimal("49999")

# ECPay AIO V5 official "reply payment method" values. Apple Pay is returned
# as Credit_CreditCard by AIO (the official table describes it as credit card
# or Apple Mobile Pay), so no invented ApplePay callback value is accepted.
PAYMENT_TYPE_LABELS = {
    "Credit_CreditCard": "信用卡／Apple Pay",
    "TWQR_OPAY": "TWQR",
    "DigitalPayment_Jkopay": "街口支付",
    "DigitalPayment_IPASS": "iPASS MONEY",
}
ALLOWED_CALLBACK_PAYMENT_TYPES = frozenset(PAYMENT_TYPE_LABELS)


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


def _configured_installments():
    raw = str(settings.ECPAY_CREDIT_INSTALLMENTS).strip()
    if not raw:
        return ()
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    invalid = [value for value in values if value not in ALLOWED_CREDIT_INSTALLMENTS]
    if invalid:
        raise ECPayConfigurationError(
            "ECPAY_CREDIT_INSTALLMENTS contains unsupported values: " + ", ".join(invalid)
        )
    return tuple(dict.fromkeys(values))


def _configured_ignore_payment():
    raw = str(settings.ECPAY_IGNORE_PAYMENT).strip()
    values = tuple(value.strip() for value in raw.split("#") if value.strip())
    forbidden = [value for value in values if value in FORBIDDEN_IGNORE_PAYMENTS]
    invalid = [value for value in values if value not in ALLOWED_IGNORE_PAYMENTS]
    if forbidden:
        raise ECPayConfigurationError(
            "ECPAY_IGNORE_PAYMENT must not hide: " + ", ".join(forbidden)
        )
    if invalid:
        raise ECPayConfigurationError(
            "ECPAY_IGNORE_PAYMENT contains unsupported values: " + ", ".join(invalid)
        )
    return "#".join(dict.fromkeys(values))


def payment_variant_status(variant):
    if variant not in PAYMENT_VARIANTS:
        return False, "Unsupported ECPay payment variant."
    configured, error = configuration_status()
    if not configured:
        return False, error
    if variant == PAYMENT_VARIANT_STANDARD:
        if not settings.ECPAY_STANDARD_ENABLED:
            return False, "ECPay standard payment is disabled."
        try:
            _configured_ignore_payment()
        except ECPayConfigurationError as exc:
            return False, str(exc)
        return True, ""
    if not settings.ECPAY_INSTALLMENT_ENABLED:
        return False, "ECPay installment payment is disabled."
    try:
        installments = _configured_installments()
    except ECPayConfigurationError as exc:
        return False, str(exc)
    if not installments:
        return False, "ECPAY_CREDIT_INSTALLMENTS is empty."
    return True, ""


def enabled_payment_variants():
    return tuple(
        variant
        for variant in (PAYMENT_VARIANT_STANDARD, PAYMENT_VARIANT_INSTALLMENT)
        if payment_variant_status(variant)[0]
    )


def configured_installments():
    return _configured_installments()


def twqr_amount_is_eligible(amount):
    return amount is not None and TWQR_MIN_AMOUNT <= amount <= TWQR_MAX_AMOUNT


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
def get_or_create_payment_attempt(*, order_id, method, payment_variant=PAYMENT_VARIANT_STANDARD):
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
    available, error = payment_variant_status(payment_variant)
    if not available:
        raise ValidationError(error)
    if Payment.objects.filter(
        order=order,
        provider=PROVIDER_NAME,
        status=Payment.Status.AWAITING_CONFIRMATION,
    ).exclude(provider_reference="").exists():
        raise ValidationError("此訂單有一筆 ECPay 付款需要人工確認，暫時無法再次付款。")

    cancelled_at = timezone.now()
    replaced = list(
        Payment.objects.select_for_update()
        .filter(
            order=order,
            provider=PROVIDER_NAME,
            status__in=(Payment.Status.PENDING, Payment.Status.AWAITING_CONFIRMATION),
        )
        .values_list("pk", flat=True)
    )
    if replaced:
        Payment.objects.filter(pk__in=replaced).update(
            status=Payment.Status.CANCELLED,
            cancelled_at=cancelled_at,
        )

    metadata = {"payment_variant": payment_variant}
    if payment_variant == PAYMENT_VARIANT_INSTALLMENT:
        metadata["requested_installments"] = list(_configured_installments())
    payment = Payment.objects.create(
        order=order,
        method=method,
        provider=PROVIDER_NAME,
        amount=order.final_total,
        currency="TWD",
        status=Payment.Status.AWAITING_CONFIRMATION,
        idempotency_key=f"ecpay:{uuid.uuid4()}",
        provider_metadata=metadata,
    )
    record_audit(
        order,
        "payment_created",
        actor_label="customer",
        changes={
            "payment_id": payment.pk,
            "method": method.code,
            "payment_variant": payment_variant,
            "amount": str(order.final_total),
            "replaced_payment_ids": replaced,
        },
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

    metadata = dict(payment.provider_metadata or {})
    variant = metadata.get("payment_variant", PAYMENT_VARIANT_STANDARD)
    available, error = payment_variant_status(variant)
    if not available:
        raise ECPayConfigurationError(error)

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
        "EncryptType": "1",
        "ClientBackURL": make_payment_result_url(payment),
    }
    if variant == PAYMENT_VARIANT_STANDARD:
        fields.update({
            "ChoosePayment": "ALL",
            "IgnorePayment": _configured_ignore_payment(),
            "NeedExtraPaidInfo": "N",
        })
    else:
        installments = tuple(metadata.get("requested_installments") or _configured_installments())
        if not installments or any(value not in ALLOWED_CREDIT_INSTALLMENTS for value in installments):
            raise ECPayConfigurationError("Invalid stored ECPay installment configuration.")
        fields.update({
            "ChoosePayment": "Credit",
            "CreditInstallment": ",".join(installments),
            "NeedExtraPaidInfo": "Y",
        })
    metadata["payment_variant"] = variant
    metadata["checkout"] = {
        "choose_payment": fields["ChoosePayment"],
        "ignore_payment": fields.get("IgnorePayment", ""),
        "credit_installment": fields.get("CreditInstallment", ""),
    }
    payment.provider_metadata = metadata
    payment.save(update_fields=("provider_metadata", "updated_at"))
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
    "stage",
    "TWQRTradeNo",
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


def _actual_installments(parameters):
    value = str(parameters.get("stage", "")).strip()
    if not value:
        return None
    if not value.isdigit():
        raise ECPayCallbackError("Invalid ECPay installment stage.")
    return int(value)


def _payment_display_name(variant, payment_type, actual_installments):
    if variant == PAYMENT_VARIANT_INSTALLMENT and actual_installments:
        return f"信用卡分期付款（{actual_installments}期）"
    return PAYMENT_TYPE_LABELS.get(payment_type, "待確認")


def _mark_callback_for_review(payment, order, *, metadata, trade_no, paid_at, reason):
    payment.provider_reference = trade_no
    payment.provider_event_id = f"ecpay:{payment.merchant_trade_no}:{trade_no}:review"
    payment.provider_metadata = metadata
    payment.paid_at = paid_at
    payment.status = Payment.Status.AWAITING_CONFIRMATION
    try:
        payment.save()
    except IntegrityError as exc:
        raise ECPayCallbackError("ECPay payment review conflict.") from exc
    record_audit(
        order,
        "payment_review_required",
        actor_label="ecpay-callback",
        changes={"payment_id": payment.pk, "reason": reason, "trade_no": trade_no},
    )
    return payment, False


@transaction.atomic
def process_callback(parameters):
    config = get_config()
    required = {
        "MerchantID", "MerchantTradeNo", "TradeAmt", "RtnCode", "PaymentType", "CheckMacValue"
    }
    if not required.issubset(parameters):
        raise ECPayCallbackError("Missing required ECPay callback parameters.")
    if not verify_check_mac_value(parameters, hash_key=config.hash_key, hash_iv=config.hash_iv):
        raise ECPayCallbackError("Invalid ECPay CheckMacValue.")
    if not secrets.compare_digest(parameters["MerchantID"], config.merchant_id):
        raise ECPayCallbackError("Unexpected ECPay MerchantID.")

    payment = (
        Payment.objects.select_for_update()
        .select_related("order")
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
    variant = metadata.get("payment_variant", PAYMENT_VARIANT_STANDARD)
    if variant not in PAYMENT_VARIANTS:
        variant = PAYMENT_VARIANT_STANDARD
    metadata["payment_variant"] = variant
    metadata["callback"] = _sanitized_callback(parameters)
    metadata["callback_received_at"] = timezone.now().isoformat()
    payment_type = parameters["PaymentType"].strip()
    actual_installments = _actual_installments(parameters)
    metadata["ecpay_payment_type"] = payment_type
    metadata["actual_installments"] = actual_installments
    metadata["normalized_payment_method"] = _payment_display_name(
        variant, payment_type, actual_installments
    )
    payment.provider_metadata = metadata

    if payment.status == Payment.Status.CONFIRMED:
        if parameters.get("TradeNo", "").strip() != payment.provider_reference:
            raise ECPayCallbackError("Confirmed ECPay payment TradeNo mismatch.")
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

    paid_at = _parse_payment_date(parameters.get("PaymentDate"))
    review_reason = ""
    if payment_type not in ALLOWED_CALLBACK_PAYMENT_TYPES:
        review_reason = "unknown_payment_type"
    elif payment_type == "TWQR_OPAY" and not twqr_amount_is_eligible(callback_amount):
        review_reason = "twqr_amount_out_of_range"
    elif variant == PAYMENT_VARIANT_INSTALLMENT:
        requested = {int(value) for value in metadata.get("requested_installments", [])}
        if payment_type != "Credit_CreditCard":
            review_reason = "unexpected_installment_payment_type"
        elif actual_installments is None or actual_installments not in requested:
            review_reason = "installment_count_mismatch"
    elif actual_installments not in (None, 0):
        review_reason = "unexpected_standard_installment"

    if review_reason:
        metadata["review_required"] = review_reason
        return _mark_callback_for_review(
            payment,
            order,
            metadata=metadata,
            trade_no=trade_no,
            paid_at=paid_at,
            reason=review_reason,
        )

    payment.provider_reference = trade_no
    payment.provider_event_id = f"ecpay:{payment.merchant_trade_no}:{trade_no}:paid"
    payment.paid_at = paid_at
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
    Payment.objects.filter(
        order=order,
        status__in=(Payment.Status.PENDING, Payment.Status.AWAITING_CONFIRMATION),
    ).exclude(pk=payment.pk).update(status=Payment.Status.CANCELLED, cancelled_at=timezone.now())
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
