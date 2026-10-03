import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote_plus
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Invoice, Order, OrderInvoiceProfile
from .operations import record_audit

logger = logging.getLogger(__name__)

STAGE_BASE_URL = "https://einvoice-stage.ecpay.com.tw/B2CInvoice"
PRODUCTION_BASE_URL = "https://einvoice.ecpay.com.tw/B2CInvoice"
MOBILE_BARCODE_PATTERN = r"/[0-9A-Z+\-.]{7}"


class ECPayInvoiceError(Exception):
    pass


class ECPayInvoiceConfigurationError(ECPayInvoiceError):
    pass


class ECPayInvoiceValidationError(ECPayInvoiceError):
    pass


class ECPayInvoiceAPIError(ECPayInvoiceError):
    def __init__(self, message, *, code="api_error"):
        super().__init__(message)
        self.code = str(code)[:64]


@dataclass(frozen=True)
class ECPayInvoiceConfig:
    environment: str
    merchant_id: str
    hash_key: str
    hash_iv: str
    base_url: str
    timeout: float

    @property
    def production(self):
        return self.environment == "production"


def get_config(*, require_enabled=True):
    if require_enabled and not settings.ECPAY_INVOICE_ENABLED:
        raise ECPayInvoiceConfigurationError("電子發票未啟用。")
    environment = settings.ECPAY_INVOICE_ENV
    if environment not in {"stage", "production"}:
        raise ECPayInvoiceConfigurationError(
            "ECPAY_INVOICE_ENV 必須為 stage 或 production。"
        )
    values = {
        "ECPAY_INVOICE_MERCHANT_ID": settings.ECPAY_INVOICE_MERCHANT_ID,
        "ECPAY_INVOICE_HASH_KEY": settings.ECPAY_INVOICE_HASH_KEY,
        "ECPAY_INVOICE_HASH_IV": settings.ECPAY_INVOICE_HASH_IV,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ECPayInvoiceConfigurationError("電子發票未設定：" + "、".join(missing))
    key_length = len(values["ECPAY_INVOICE_HASH_KEY"].encode("utf-8"))
    iv_length = len(values["ECPAY_INVOICE_HASH_IV"].encode("utf-8"))
    if key_length not in {16, 24, 32} or iv_length != 16:
        raise ECPayInvoiceConfigurationError(
            "電子發票 HashKey／HashIV 長度不符合 AES 規格。"
        )
    try:
        timeout = float(settings.ECPAY_INVOICE_TIMEOUT)
    except (TypeError, ValueError) as exc:
        raise ECPayInvoiceConfigurationError(
            "ECPAY_INVOICE_TIMEOUT 必須為數字。"
        ) from exc
    if not (0 < timeout <= 30):
        raise ECPayInvoiceConfigurationError(
            "ECPAY_INVOICE_TIMEOUT 必須介於 0 與 30 秒。"
        )
    if environment == "production" and not all(
        (
            settings.ECPAY_INVOICE_TAX_TYPE,
            settings.ECPAY_INVOICE_INV_TYPE,
            settings.ECPAY_INVOICE_VAT,
        )
    ):
        raise ECPayInvoiceConfigurationError(
            "正式環境的課稅別、字軌類別與含稅設定尚未完整設定。"
        )
    return ECPayInvoiceConfig(
        environment=environment,
        merchant_id=values["ECPAY_INVOICE_MERCHANT_ID"],
        hash_key=values["ECPAY_INVOICE_HASH_KEY"],
        hash_iv=values["ECPAY_INVOICE_HASH_IV"],
        base_url=PRODUCTION_BASE_URL if environment == "production" else STAGE_BASE_URL,
        timeout=timeout,
    )


def configuration_status():
    try:
        get_config()
    except ECPayInvoiceConfigurationError as exc:
        return False, str(exc)
    return True, ""


def encrypt_data(data, *, hash_key, hash_iv):
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    encoded = quote(plaintext, safe="").encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(encoded) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(hash_key.encode("utf-8")),
        modes.CBC(hash_iv.encode("utf-8")),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_data(value, *, hash_key, hash_iv):
    try:
        encrypted = base64.b64decode(value, validate=True)
        decryptor = Cipher(
            algorithms.AES(hash_key.encode("utf-8")),
            modes.CBC(hash_iv.encode("utf-8")),
        ).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        encoded = unpadder.update(padded) + unpadder.finalize()
        return json.loads(unquote_plus(encoded.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ECPayInvoiceAPIError(
            "電子發票回應解密失敗。", code="decrypt_failed"
        ) from exc


def _api_call(path, data, *, config=None):
    config = config or get_config()
    body = {
        "MerchantID": config.merchant_id,
        "RqHeader": {"Timestamp": int(time.time())},
        "Data": encrypt_data(data, hash_key=config.hash_key, hash_iv=config.hash_iv),
    }
    request = Request(
        f"{config.base_url}/{path}",
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout) as response:
            raw = response.read(1024 * 1024)
    except HTTPError as exc:
        raise ECPayInvoiceAPIError(
            "電子發票服務回傳 HTTP 錯誤。", code=f"http_{exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ECPayInvoiceAPIError(
            "電子發票服務暫時無法連線。", code="connection_error"
        ) from exc
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ECPayInvoiceAPIError(
            "電子發票服務回應格式無效。", code="invalid_response"
        ) from exc
    if not secrets.compare_digest(
        str(envelope.get("MerchantID", "")), config.merchant_id
    ):
        raise ECPayInvoiceAPIError(
            "電子發票回應 MerchantID 不符。", code="merchant_mismatch"
        )
    response_timestamp = (envelope.get("RpHeader") or {}).get("Timestamp")
    try:
        timestamp_valid = abs(int(response_timestamp) - int(time.time())) <= 600
        trans_code = int(envelope.get("TransCode", 0))
    except (TypeError, ValueError) as exc:
        raise ECPayInvoiceAPIError(
            "電子發票回應標頭無效。", code="invalid_header"
        ) from exc
    if not timestamp_valid:
        raise ECPayInvoiceAPIError("電子發票回應時間戳已失效。", code="stale_response")
    if trans_code != 1:
        raise ECPayInvoiceAPIError(
            str(envelope.get("TransMsg") or "電子發票傳輸失敗。")[:300],
            code=f"trans_{envelope.get('TransCode', 'unknown')}",
        )
    if not envelope.get("Data"):
        raise ECPayInvoiceAPIError("電子發票回應缺少加密資料。", code="missing_data")
    return decrypt_data(
        envelope["Data"], hash_key=config.hash_key, hash_iv=config.hash_iv
    )


def _configuration_snapshot():
    environment = settings.ECPAY_INVOICE_ENV
    stage = environment == "stage"
    return {
        "environment": environment,
        "tax_type": settings.ECPAY_INVOICE_TAX_TYPE or ("1" if stage else ""),
        "inv_type": settings.ECPAY_INVOICE_INV_TYPE or ("07" if stage else ""),
        "vat": settings.ECPAY_INVOICE_VAT or ("1" if stage else ""),
    }


def _ensure_profile(order):
    try:
        return order.invoice_profile
    except OrderInvoiceProfile.DoesNotExist:
        profile = OrderInvoiceProfile(
            order=order,
            invoice_type=OrderInvoiceProfile.InvoiceType.PERSONAL,
            carrier_type="1",
            email=order.email,
            phone="".join(
                character for character in order.phone if character.isdigit()
            )[:20],
            configuration_snapshot=_configuration_snapshot(),
        )
        profile.full_clean()
        profile.save()
        return profile


def _relate_number(order):
    return f"RF{order.public_id.hex[:28].upper()}"


def prepare_invoice_after_payment(order_id):
    order = Order.objects.select_related("invoice_profile").get(pk=order_id)
    profile = _ensure_profile(order)
    try:
        invoice, created = Invoice.objects.get_or_create(
            order=order,
            defaults={
                "profile": profile,
                "relate_number": _relate_number(order),
                "sales_amount": order.final_total,
                "customer_identifier": profile.customer_identifier,
                "carrier_type": profile.carrier_type,
            },
        )
    except IntegrityError:
        invoice = Invoice.objects.get(order=order)
        created = False
    if created:
        record_audit(
            order,
            "invoice_pending",
            actor_label="payment-confirmed",
            changes={
                "invoice_id": invoice.pk,
                "sales_amount": str(invoice.sales_amount),
            },
        )
    transaction.on_commit(lambda invoice_id=invoice.pk: issue_invoice_safe(invoice_id))
    return invoice


def _validated_tax_settings(profile, config):
    snapshot = profile.configuration_snapshot or {}
    if snapshot.get("environment") != config.environment:
        raise ECPayInvoiceValidationError(
            "訂單建立時與目前的電子發票環境不同，請人工確認。"
        )
    tax_type = str(snapshot.get("tax_type", ""))
    inv_type = str(snapshot.get("inv_type", ""))
    vat = str(snapshot.get("vat", ""))
    if config.production and not all((tax_type, inv_type, vat)):
        raise ECPayInvoiceConfigurationError(
            "正式環境的課稅別、字軌類別與含稅設定尚未完整設定。"
        )
    if (
        inv_type not in {"07", "08"}
        or tax_type not in {"1", "2", "3", "4"}
        or vat not in {"0", "1"}
    ):
        raise ECPayInvoiceValidationError("電子發票稅務設定不受目前訂單明細模型支援。")
    if (inv_type == "07" and tax_type not in {"1", "2", "3"}) or (
        inv_type == "08" and tax_type not in {"3", "4"}
    ):
        raise ECPayInvoiceValidationError("字軌類別與課稅類別組合不符合 ECPay 規格。")
    if vat != "1":
        raise ECPayInvoiceValidationError(
            "訂單價格快照為含稅總價；vat 未明確設為 1 時不自動開立。"
        )
    if tax_type == "2":
        raise ECPayInvoiceValidationError(
            "零稅率需要另行確認通關方式與零稅率原因，暫不自動開立。"
        )
    if tax_type in {"3", "4"}:
        raise ECPayInvoiceValidationError(
            "免稅／特種稅額需要另行確認稅務設定，暫不自動開立。"
        )
    return tax_type, inv_type, vat


def build_issue_data(invoice, *, config=None):
    config = config or get_config()
    order = invoice.order
    profile = invoice.profile
    tax_type, inv_type, vat = _validated_tax_settings(profile, config)
    if order.final_total is None or invoice.sales_amount != order.final_total:
        raise ECPayInvoiceValidationError("發票金額與訂單總額不一致。")
    try:
        sales_amount = int(order.final_total)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ECPayInvoiceValidationError("發票金額必須為新台幣整數。") from exc
    if Decimal(sales_amount) != order.final_total or sales_amount <= 0:
        raise ECPayInvoiceValidationError("發票金額必須為正的新台幣整數。")
    items = []
    item_total = Decimal("0")
    for sequence, item in enumerate(order.items.all(), start=1):
        if sequence > 998:
            raise ECPayInvoiceValidationError("發票商品明細超過 ECPay 上限。")
        expected = item.unit_price_snapshot * item.quantity
        if item.line_total != expected:
            raise ECPayInvoiceValidationError("訂單商品快照金額不一致。")
        item_total += item.line_total
        items.append(
            {
                "ItemSeq": sequence,
                "ItemName": " ".join(item.product_name_snapshot.split())[:100],
                "ItemCount": item.quantity,
                "ItemWord": "件",
                "ItemPrice": int(item.unit_price_snapshot),
                "ItemTaxType": tax_type,
                "ItemAmount": int(item.line_total),
                "ItemRemark": "",
            }
        )
    if order.shipping_fee is None:
        raise ECPayInvoiceValidationError("運費尚未確定。")
    if order.shipping_fee > 0:
        items.append(
            {
                "ItemSeq": len(items) + 1,
                "ItemName": "運費",
                "ItemCount": 1,
                "ItemWord": "式",
                "ItemPrice": int(order.shipping_fee),
                "ItemTaxType": tax_type,
                "ItemAmount": int(order.shipping_fee),
                "ItemRemark": "",
            }
        )
        item_total += order.shipping_fee
    if (
        item_total != order.final_total
        or sum(Decimal(item["ItemAmount"]) for item in items) != order.final_total
    ):
        raise ECPayInvoiceValidationError("商品明細加運費與訂單總額不一致。")
    if profile.invoice_type == OrderInvoiceProfile.InvoiceType.MOBILE_BARCODE:
        import re

        if not re.fullmatch(MOBILE_BARCODE_PATTERN, profile.carrier_number):
            raise ECPayInvoiceValidationError("手機條碼格式無效。")
    elif profile.invoice_type == OrderInvoiceProfile.InvoiceType.COMPANY:
        if (
            not profile.customer_identifier.isdigit()
            or len(profile.customer_identifier) != 8
        ):
            raise ECPayInvoiceValidationError("統一編號必須為 8 碼數字。")
        if not profile.customer_name.strip():
            raise ECPayInvoiceValidationError("公司用電子發票缺少發票抬頭。")
    return {
        "MerchantID": config.merchant_id,
        "RelateNumber": invoice.relate_number,
        "CustomerID": "",
        "CustomerIdentifier": profile.customer_identifier,
        "CustomerName": profile.customer_name[:60],
        "CustomerAddr": "",
        "CustomerPhone": profile.phone if not profile.email else "",
        "CustomerEmail": profile.email,
        "ClearanceMark": "",
        "Print": "0",
        "Donation": "0",
        "LoveCode": "",
        "CarrierType": profile.carrier_type,
        "CarrierNum": profile.carrier_number,
        "TaxType": tax_type,
        "SalesAmount": sales_amount,
        "InvoiceRemark": f"訂單 {order.public_number}"[:200],
        "InvType": inv_type,
        "vat": vat,
        "Items": items,
    }


def _parse_invoice_date(value):
    if not isinstance(value, str) or not value.strip():
        _raise_invalid_invoice_date(value)

    value = value.strip()
    parsed = None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, pattern)
            break
        except ValueError:
            continue

    if parsed is None:
        iso_value = value.replace("/", "-")
        if len(iso_value) > 10 and iso_value[10] in {" ", "T"}:
            try:
                parsed = datetime.fromisoformat(iso_value)
            except ValueError:
                pass

    if parsed is None:
        _raise_invalid_invoice_date(value)

    current_timezone = timezone.get_current_timezone()
    try:
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, current_timezone)
        return parsed.astimezone(current_timezone)
    except (OverflowError, ValueError):
        _raise_invalid_invoice_date(value)


def _raise_invalid_invoice_date(value):
    if isinstance(value, str):
        preview = repr(value)
    elif value is None:
        preview = "None"
    else:
        preview = f"<{type(value).__name__}>"
    if len(preview) > 80:
        preview = f"{preview[:77]}..."
    logger.warning("Unexpected ECPay invoice date format: %s", preview)
    raise ECPayInvoiceAPIError(
        "電子發票開立日期格式無效。", code="invalid_invoice_date"
    )


def _apply_success(invoice_id, response, *, recovered=False):
    invoice_no = str(
        response.get("InvoiceNo") or response.get("IIS_Number") or ""
    ).strip()
    invoice_date_value = response.get("InvoiceDate") or response.get("IIS_Create_Date")
    random_number = str(
        response.get("RandomNumber") or response.get("IIS_Random_Number") or ""
    ).strip()
    if not invoice_no or not invoice_date_value or len(random_number) != 4:
        raise ECPayInvoiceAPIError(
            "電子發票成功回應缺少必要欄位。", code="incomplete_success"
        )
    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update()
            .select_related("order")
            .get(pk=invoice_id)
        )
        if invoice.status == Invoice.Status.ISSUED:
            return invoice
        invoice.status = Invoice.Status.ISSUED
        invoice.invoice_no = invoice_no
        invoice.invoice_date = _parse_invoice_date(invoice_date_value)
        invoice.random_number = random_number
        invoice.error_code = ""
        invoice.error_message = ""
        invoice.provider_metadata = {
            "rtn_code": str(response.get("RtnCode", "1"))[:32],
            "rtn_msg": str(response.get("RtnMsg", ""))[:200],
            "recovered_by_query": recovered,
        }
        invoice.issued_at = timezone.now()
        invoice.save()
        record_audit(
            invoice.order,
            "invoice_issued",
            actor_label="ecpay-invoice",
            changes={
                "invoice_id": invoice.pk,
                "invoice_no": invoice.invoice_no,
                "recovered": recovered,
            },
        )
        return invoice


def _query_existing(invoice, config):
    response = _api_call(
        "GetIssue",
        {"MerchantID": config.merchant_id, "RelateNumber": invoice.relate_number},
        config=config,
    )
    if int(response.get("RtnCode", 0)) == 1 and response.get("IIS_Number"):
        return response
    return None


def issue_invoice(invoice_id, *, allow_retry=False):
    config = get_config()
    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update()
            .select_related("order", "profile")
            .prefetch_related("order__items")
            .get(pk=invoice_id)
        )
        if invoice.status in {Invoice.Status.ISSUED, Invoice.Status.VOIDED}:
            return invoice
        if invoice.status == Invoice.Status.ISSUING and not allow_retry:
            return invoice
        previous_attempts = invoice.attempt_count
        invoice.status = Invoice.Status.ISSUING
        invoice.attempt_count += 1
        invoice.last_attempt_at = timezone.now()
        invoice.error_code = ""
        invoice.error_message = ""
        invoice.save(
            update_fields=(
                "status",
                "attempt_count",
                "last_attempt_at",
                "error_code",
                "error_message",
                "updated_at",
            )
        )
        invoice_snapshot = invoice
    if previous_attempts:
        existing = _query_existing(invoice_snapshot, config)
        if existing:
            return _apply_success(invoice_id, existing, recovered=True)
    data = build_issue_data(invoice_snapshot, config=config)
    if (
        invoice_snapshot.profile.invoice_type
        == OrderInvoiceProfile.InvoiceType.MOBILE_BARCODE
    ):
        try:
            barcode_response = _api_call(
                "CheckBarcode",
                {
                    "MerchantID": config.merchant_id,
                    "BarCode": invoice_snapshot.profile.carrier_number,
                },
                config=config,
            )
        except ECPayInvoiceAPIError as exc:
            if exc.code in {
                "merchant_mismatch",
                "decrypt_failed",
                "stale_response",
                "invalid_header",
            }:
                raise
            barcode_response = {}
        barcode_code = str(barcode_response.get("RtnCode", ""))
        if barcode_code == "1" and barcode_response.get("IsExist") == "N":
            raise ECPayInvoiceValidationError("手機條碼不存在，請人工確認。")
        # ECPay explicitly advises that Ministry maintenance (9000001) must not
        # be the sole reason to block an otherwise well-formed carrier.
    response = _api_call("Issue", data, config=config)
    if int(response.get("RtnCode", 0)) != 1:
        raise ECPayInvoiceAPIError(
            str(response.get("RtnMsg") or "電子發票開立失敗。")[:300],
            code=f"rtn_{response.get('RtnCode', 'unknown')}",
        )
    return _apply_success(invoice_id, response)


def _mark_failure(invoice_id, exc):
    if isinstance(exc, ECPayInvoiceConfigurationError):
        status = Invoice.Status.PENDING
        code = "configuration"
    elif isinstance(exc, (ECPayInvoiceValidationError, ValidationError)):
        status = Invoice.Status.REVIEW_REQUIRED
        code = "validation"
    else:
        status = Invoice.Status.FAILED
        code = getattr(exc, "code", "unexpected")
    message = str(exc)[:300] or "電子發票處理失敗。"
    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update()
            .select_related("order")
            .get(pk=invoice_id)
        )
        if invoice.status in {Invoice.Status.ISSUED, Invoice.Status.VOIDED}:
            return invoice
        invoice.status = status
        invoice.error_code = str(code)[:64]
        invoice.error_message = message
        invoice.save(
            update_fields=("status", "error_code", "error_message", "updated_at")
        )
        record_audit(
            invoice.order,
            "invoice_issue_failed"
            if status != Invoice.Status.PENDING
            else "invoice_configuration_pending",
            actor_label="ecpay-invoice",
            changes={
                "invoice_id": invoice.pk,
                "status": status,
                "error_code": invoice.error_code,
            },
        )
        return invoice


def issue_invoice_safe(invoice_id, *, allow_retry=False):
    try:
        return issue_invoice(invoice_id, allow_retry=allow_retry)
    except (ECPayInvoiceError, ValidationError) as exc:
        return _mark_failure(invoice_id, exc)
    except Exception:
        logger.exception(
            "Unexpected ECPay invoice processing failure for invoice_id=%s", invoice_id
        )
        return _mark_failure(
            invoice_id, ECPayInvoiceAPIError("電子發票處理發生非預期錯誤。")
        )
