import base64
import json
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import quote_plus

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.ecpay_invoice import (
    STAGE_BASE_URL,
    ECPayInvoiceAPIError,
    ECPayInvoiceConfigurationError,
    _api_call,
    _parse_invoice_date,
    build_issue_data,
    decrypt_data,
    encrypt_data,
    get_config,
    issue_invoice_safe,
    prepare_invoice_after_payment,
)
from orders.forms import CheckoutForm
from orders.models import Invoice, Order, OrderInvoiceProfile, OrderItem, Payment


def _official_sample_text(character_codes):
    """Keep public documentation vectors distinct from deployable credentials."""
    return "".join(chr(code) for code in character_codes)


def _encrypt_url_encoded(encoded, *, hash_key, hash_iv):
    """Build an AES response from the already URL-encoded bytes ECPay returns."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(encoded.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(hash_key.encode("utf-8")),
        modes.CBC(hash_iv.encode("utf-8")),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


INVOICE_SETTINGS = {
    "ECPAY_INVOICE_ENABLED": True,
    "ECPAY_INVOICE_ENV": "stage",
    "ECPAY_INVOICE_MERCHANT_ID": "invoice-test-merchant",
    "ECPAY_INVOICE_HASH_KEY": _official_sample_text(
        (53, 50, 57, 52, 121, 48, 54, 74, 98, 73, 83, 112, 77, 53, 120, 57)
    ),
    "ECPAY_INVOICE_HASH_IV": _official_sample_text(
        (118, 55, 55, 104, 111, 75, 71, 113, 52, 107, 87, 120, 78, 78, 73, 83)
    ),
    "ECPAY_INVOICE_TAX_TYPE": "1",
    "ECPAY_INVOICE_INV_TYPE": "07",
    "ECPAY_INVOICE_VAT": "1",
    "ECPAY_INVOICE_TIMEOUT": 3,
}


@override_settings(**INVOICE_SETTINGS)
class ECPayInvoiceTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            idempotency_key="invoice-order",
            customer_name="王小明",
            phone="0912-345-678",
            email="buyer@example.test",
            shipping_information="Taipei",
            subtotal=Decimal("1000"),
            shipping_fee=Decimal("100"),
            payment_request_total=Decimal("1100"),
            status=Order.Status.AWAITING_PAYMENT,
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            product_name_snapshot="訂單時木椅",
            unit_price_snapshot=Decimal("500"),
            quantity=2,
            line_total=Decimal("1000"),
        )
        self.profile = OrderInvoiceProfile.objects.create(
            order=self.order,
            invoice_type=OrderInvoiceProfile.InvoiceType.PERSONAL,
            carrier_type="1",
            email=self.order.email,
            phone="0912345678",
            configuration_snapshot={
                "environment": "stage",
                "tax_type": "1",
                "inv_type": "07",
                "vat": "1",
            },
        )
        self.invoice = Invoice.objects.create(
            order=self.order,
            profile=self.profile,
            relate_number=f"RF{self.order.public_id.hex[:28].upper()}",
            sales_amount=self.order.final_total,
            carrier_type="1",
        )

    def test_aes_matches_official_ecpay_sample_and_round_trips(self):
        data = {"Name": "Test", "ID": "A123456789"}
        expected = "0FKSa0j4InjlU0ewoWpzd9FmU9LVR/8z9Zmh8d+shjJ8fuvlmNxsxyOQfC2BB4VVPEA/MyAHNjzV6HcAGYXgCw=="
        encrypted = encrypt_data(
            data,
            hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
            hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
        )
        self.assertEqual(encrypted, expected)
        self.assertEqual(
            decrypt_data(
                encrypted,
                hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
                hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
            ),
            data,
        )

    def test_aes_round_trip_preserves_unicode_spaces_and_literal_plus(self):
        data = {"Message": "靜院 日本語 A+B", "InvoiceDate": "2026-10-03 18:06:49"}

        encrypted = encrypt_data(
            data,
            hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
            hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
        )

        self.assertEqual(
            decrypt_data(
                encrypted,
                hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
                hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
            ),
            data,
        )

    def test_decrypt_restores_form_spaces_and_preserves_encoded_plus(self):
        data = {"InvoiceDate": "2026-10-03 18:06:49", "Marker": "A+B"}
        plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        form_encoded = quote_plus(plaintext, safe="")
        self.assertIn("2026-10-03+18%3A06%3A49", form_encoded)
        self.assertIn("A%2BB", form_encoded)
        encrypted = _encrypt_url_encoded(
            form_encoded,
            hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
            hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
        )

        decrypted = decrypt_data(
            encrypted,
            hash_key=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"],
            hash_iv=INVOICE_SETTINGS["ECPAY_INVOICE_HASH_IV"],
        )

        self.assertEqual(decrypted["InvoiceDate"], "2026-10-03 18:06:49")
        self.assertEqual(decrypted["Marker"], "A+B")
        self.assertEqual(
            _parse_invoice_date(decrypted["InvoiceDate"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "2026-10-03 18:06:49",
        )

    def test_invoice_config_validation_and_stage_url(self):
        self.assertEqual(get_config().base_url, STAGE_BASE_URL)
        with override_settings(ECPAY_INVOICE_MERCHANT_ID=""):
            with self.assertRaises(ECPayInvoiceConfigurationError):
                get_config()
        with override_settings(ECPAY_INVOICE_ENABLED=False):
            with self.assertRaises(ECPayInvoiceConfigurationError):
                get_config()
        with override_settings(ECPAY_INVOICE_TIMEOUT="not-a-number"):
            with self.assertRaises(ECPayInvoiceConfigurationError):
                get_config()

    def test_invoice_date_parser_accepts_official_and_defensive_formats(self):
        cases = {
            "2026-10-03 17:30:00": (17, 30, 0, 0),
            "2026/10/03 17:30:00": (17, 30, 0, 0),
            " 2026-10-03 17:30:00 ": (17, 30, 0, 0),
            "2026-10-03 17:30:00.123": (17, 30, 0, 123000),
            "2026-10-03T17:30:00": (17, 30, 0, 0),
            "2026-10-03T17:30:00+08:00": (17, 30, 0, 0),
            "2026-10-03T09:30:00+00:00": (17, 30, 0, 0),
        }

        for value, expected_time in cases.items():
            with self.subTest(value=value):
                parsed = _parse_invoice_date(value)
                self.assertTrue(timezone.is_aware(parsed))
                self.assertEqual(
                    (parsed.hour, parsed.minute, parsed.second, parsed.microsecond),
                    expected_time,
                )
                self.assertEqual(str(parsed.tzinfo), "Asia/Taipei")

    def test_invoice_date_parser_rejects_invalid_values_without_echoing_them(self):
        invalid_values = (None, "", "2026-10-03", 20261003, "not-a-date")
        with self.assertLogs("orders.ecpay_invoice", level="WARNING") as logs:
            for value in invalid_values:
                with self.subTest(value=value):
                    with self.assertRaises(ECPayInvoiceAPIError) as caught:
                        _parse_invoice_date(value)
                    self.assertEqual(caught.exception.code, "invalid_invoice_date")
                    self.assertEqual(
                        str(caught.exception), "電子發票開立日期格式無效。"
                    )
        self.assertTrue(
            all("Unexpected ECPay invoice date format:" in entry for entry in logs.output)
        )

    def test_invoice_date_parser_truncates_warning_value(self):
        invalid_value = "x" * 100
        with self.assertLogs("orders.ecpay_invoice", level="WARNING") as logs:
            with self.assertRaises(ECPayInvoiceAPIError):
                _parse_invoice_date(invalid_value)
        self.assertNotIn(invalid_value, logs.output[0])
        self.assertIn("x" * 70, logs.output[0])

    def test_individual_invoice_uses_ecpay_carrier_and_snapshot_items(self):
        data = build_issue_data(self.invoice)
        self.assertEqual(data["CarrierType"], "1")
        self.assertEqual(data["CarrierNum"], "")
        self.assertEqual(data["Print"], "0")
        self.assertEqual(data["CustomerIdentifier"], "")
        self.assertEqual(data["Items"][0]["ItemName"], "訂單時木椅")
        self.assertEqual(data["Items"][0]["ItemPrice"], 500)

    def test_sales_amount_and_shipping_line_match_final_total(self):
        data = build_issue_data(self.invoice)
        self.assertEqual(data["SalesAmount"], 1100)
        self.assertEqual(data["Items"][-1]["ItemName"], "運費")
        self.assertEqual(data["Items"][-1]["ItemAmount"], 100)
        self.assertEqual(sum(item["ItemAmount"] for item in data["Items"]), 1100)

    def test_mobile_barcode_and_company_payloads(self):
        self.profile.invoice_type = OrderInvoiceProfile.InvoiceType.MOBILE_BARCODE
        self.profile.carrier_type = "3"
        self.profile.carrier_number = "/AB12+-."
        self.profile.save()
        data = build_issue_data(self.invoice)
        self.assertEqual((data["CarrierType"], data["CarrierNum"]), ("3", "/AB12+-."))

        self.profile.invoice_type = OrderInvoiceProfile.InvoiceType.COMPANY
        self.profile.customer_identifier = "12345678"
        self.profile.customer_name = "靜院設計有限公司"
        self.profile.carrier_type = "1"
        self.profile.carrier_number = ""
        self.profile.save()
        data = build_issue_data(self.invoice)
        self.assertEqual(data["CustomerIdentifier"], "12345678")
        self.assertEqual(data["CustomerName"], "靜院設計有限公司")
        self.assertEqual(data["Print"], "0")

    def test_checkout_rejects_invalid_vat_number_and_mobile_barcode(self):
        common = {
            "customer_name": "王小明",
            "phone": "0912345678",
            "email": "buyer@example.test",
            "shipping_information": "Taipei",
            "recipient_name": "王小明",
            "postal_code": "100",
            "city": "台北市",
            "district": "中正區",
            "street_address": "測試路1號",
            "policies_accepted": True,
            "idempotency_key": "token",
            "website": "",
        }
        company = CheckoutForm(
            {
                **common,
                "invoice_type": "company",
                "invoice_customer_identifier": "123",
                "invoice_customer_name": "",
            }
        )
        self.assertFalse(company.is_valid())
        self.assertIn("invoice_customer_identifier", company.errors)
        mobile = CheckoutForm(
            {
                **common,
                "invoice_type": "mobile_barcode",
                "invoice_carrier_number": "ABC",
            }
        )
        self.assertFalse(mobile.is_valid())
        self.assertIn("invoice_carrier_number", mobile.errors)

    @patch("orders.ecpay_invoice._api_call")
    def test_invoice_api_success_and_duplicate_issue_prevention(self, api_call):
        api_call.return_value = {
            "RtnCode": 1,
            "RtnMsg": "開立發票成功",
            "InvoiceNo": "UV11100012",
            "InvoiceDate": "2026-10-03 12:30:00",
            "RandomNumber": "6866",
        }
        result = issue_invoice_safe(self.invoice.pk)
        self.assertEqual(result.status, Invoice.Status.ISSUED)
        self.assertEqual(result.invoice_no, "UV11100012")
        issue_invoice_safe(self.invoice.pk)
        self.assertEqual(api_call.call_count, 1)

    @patch("orders.ecpay_invoice._api_call")
    def test_api_failure_timeout_and_retry_are_safe(self, api_call):
        api_call.side_effect = ECPayInvoiceAPIError(
            "服務暫時無法連線", code="connection_error"
        )
        failed = issue_invoice_safe(self.invoice.pk)
        self.assertEqual(failed.status, Invoice.Status.FAILED)
        self.assertEqual(failed.error_code, "connection_error")

        api_call.side_effect = [
            {"RtnCode": 0, "RtnMsg": "查無資料"},
            {
                "RtnCode": 1,
                "RtnMsg": "成功",
                "InvoiceNo": "UV11100013",
                "InvoiceDate": "2026/10/03 12:40:00",
                "RandomNumber": "1234",
            },
        ]
        issued = issue_invoice_safe(self.invoice.pk, allow_retry=True)
        self.assertEqual(issued.status, Invoice.Status.ISSUED)
        self.assertEqual(issued.attempt_count, 2)

    @patch("orders.ecpay_invoice._api_call")
    def test_retry_recovers_existing_invoice_before_issue(self, api_call):
        self.invoice.status = Invoice.Status.FAILED
        self.invoice.attempt_count = 1
        self.invoice.save(update_fields=("status", "attempt_count", "updated_at"))
        api_call.return_value = {
            "RtnCode": 1,
            "RtnMsg": "查詢成功",
            "IIS_Number": "UV11100014",
            "IIS_Create_Date": "2026-10-03 12:50:00",
            "IIS_Random_Number": "2468",
        }

        issued = issue_invoice_safe(self.invoice.pk, allow_retry=True)

        self.assertEqual(issued.status, Invoice.Status.ISSUED)
        self.assertEqual(issued.invoice_no, "UV11100014")
        self.assertTrue(issued.provider_metadata["recovered_by_query"])
        api_call.assert_called_once()
        self.assertEqual(api_call.call_args.args[0], "GetIssue")
        self.assertEqual(
            api_call.call_args.args[1],
            {
                "MerchantID": INVOICE_SETTINGS["ECPAY_INVOICE_MERCHANT_ID"],
                "RelateNumber": self.invoice.relate_number,
            },
        )

    @patch("orders.ecpay_invoice._api_call")
    def test_mobile_barcode_validation_outage_does_not_block_issue(self, api_call):
        self.profile.invoice_type = OrderInvoiceProfile.InvoiceType.MOBILE_BARCODE
        self.profile.carrier_type = "3"
        self.profile.carrier_number = "/AB12+-."
        self.profile.save()
        api_call.side_effect = [
            ECPayInvoiceAPIError("載具驗證服務中斷", code="connection_error"),
            {
                "RtnCode": 1,
                "RtnMsg": "開立成功",
                "InvoiceNo": "UV11100015",
                "InvoiceDate": "2026-10-03 13:00:00",
                "RandomNumber": "1357",
            },
        ]

        issued = issue_invoice_safe(self.invoice.pk)

        self.assertEqual(issued.status, Invoice.Status.ISSUED)
        self.assertEqual(api_call.call_count, 2)

    @patch("orders.ecpay_invoice.urlopen", side_effect=TimeoutError)
    def test_http_timeout_is_normalized_without_exposing_request(self, _urlopen):
        with self.assertRaises(ECPayInvoiceAPIError) as caught:
            _api_call(
                "Issue",
                {"MerchantID": INVOICE_SETTINGS["ECPAY_INVOICE_MERCHANT_ID"]},
            )
        self.assertEqual(caught.exception.code, "connection_error")
        self.assertNotIn(
            INVOICE_SETTINGS["ECPAY_INVOICE_HASH_KEY"], str(caught.exception)
        )

    @patch("orders.ecpay_invoice.urlopen")
    def test_api_response_rejects_wrong_merchant_and_stale_timestamp(self, urlopen):
        config = get_config()

        def response_for(envelope):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                envelope
            ).encode("utf-8")
            return response

        encrypted = encrypt_data(
            {"RtnCode": 1}, hash_key=config.hash_key, hash_iv=config.hash_iv
        )
        urlopen.return_value = response_for(
            {
                "MerchantID": "unexpected",
                "RpHeader": {"Timestamp": int(time.time())},
                "TransCode": 1,
                "Data": encrypted,
            }
        )
        with self.assertRaises(ECPayInvoiceAPIError) as merchant_error:
            _api_call("Issue", {"MerchantID": config.merchant_id}, config=config)
        self.assertEqual(merchant_error.exception.code, "merchant_mismatch")

        urlopen.return_value = response_for(
            {
                "MerchantID": config.merchant_id,
                "RpHeader": {"Timestamp": int(time.time()) - 601},
                "TransCode": 1,
                "Data": encrypted,
            }
        )
        with self.assertRaises(ECPayInvoiceAPIError) as timestamp_error:
            _api_call("Issue", {"MerchantID": config.merchant_id}, config=config)
        self.assertEqual(timestamp_error.exception.code, "stale_response")

    @patch("orders.ecpay_invoice._api_call")
    def test_concurrent_issuing_state_does_not_call_provider_again(self, api_call):
        self.invoice.status = Invoice.Status.ISSUING
        self.invoice.save(update_fields=("status", "updated_at"))
        result = issue_invoice_safe(self.invoice.pk)
        self.assertEqual(result.status, Invoice.Status.ISSUING)
        api_call.assert_not_called()

    @patch(
        "orders.ecpay_invoice._api_call",
        side_effect=ECPayInvoiceAPIError("timeout", code="connection_error"),
    )
    def test_payment_remains_confirmed_when_invoice_api_fails(self, _api_call):
        self.invoice.delete()
        payment = Payment.objects.create(
            order=self.order,
            amount=self.order.final_total,
            currency="TWD",
            status=Payment.Status.AWAITING_CONFIRMATION,
        )
        with self.captureOnCommitCallbacks(execute=True):
            payment.status = Payment.Status.CONFIRMED
            payment.save()
        payment.refresh_from_db()
        self.order.refresh_from_db()
        invoice = Invoice.objects.get(order=self.order)
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(invoice.status, Invoice.Status.FAILED)

    def test_existing_order_without_invoice_profile_remains_compatible(self):
        legacy = Order.objects.create(
            idempotency_key="legacy-order",
            customer_name="舊顧客",
            phone="0900",
            email="legacy@example.test",
            shipping_information="Taipei",
            subtotal=100,
            shipping_fee=10,
            payment_request_total=110,
            status=Order.Status.PAID,
        )
        invoice = prepare_invoice_after_payment(legacy.pk)
        self.assertEqual(
            invoice.profile.invoice_type, OrderInvoiceProfile.InvoiceType.PERSONAL
        )

    def test_admin_retry_requires_order_change_permission(self):
        user = get_user_model().objects.create_user(
            "staff", password="pw", is_staff=True
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse("admin:orders_order_retry_invoice", args=[self.order.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_opens_for_existing_order_without_invoice_profile(self):
        legacy = Order.objects.create(
            idempotency_key="legacy-admin",
            customer_name="舊顧客",
            phone="0900",
            email="legacy-admin@example.test",
            shipping_information="Taipei",
            subtotal=100,
            shipping_fee=None,
            status=Order.Status.SHIPPING_REVIEW,
        )
        admin_user = get_user_model().objects.create_superuser(
            "admin", "admin@example.test", "pw"
        )
        client = Client()
        client.force_login(admin_user)
        response = client.get(reverse("admin:orders_order_change", args=[legacy.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "此既有訂單沒有發票資訊快照")


class InvoiceProductionTaxSafetyTests(TestCase):
    @override_settings(
        **{
            **INVOICE_SETTINGS,
            "ECPAY_INVOICE_ENV": "production",
            "ECPAY_INVOICE_TAX_TYPE": "",
            "ECPAY_INVOICE_INV_TYPE": "",
            "ECPAY_INVOICE_VAT": "",
        }
    )
    def test_production_missing_tax_settings_never_issues(self):
        order = Order.objects.create(
            idempotency_key="prod-tax",
            customer_name="顧客",
            phone="0900",
            email="buyer@example.test",
            shipping_information="Taipei",
            subtotal=100,
            shipping_fee=10,
            payment_request_total=110,
            status=Order.Status.PAID,
        )
        OrderItem.objects.create(
            order=order,
            product_name_snapshot="商品",
            unit_price_snapshot=100,
            quantity=1,
            line_total=100,
        )
        profile = OrderInvoiceProfile.objects.create(
            order=order,
            invoice_type="personal",
            carrier_type="1",
            email=order.email,
            configuration_snapshot={
                "environment": "production",
                "tax_type": "",
                "inv_type": "",
                "vat": "",
            },
        )
        invoice = Invoice.objects.create(
            order=order,
            profile=profile,
            relate_number=f"RF{order.public_id.hex[:28].upper()}",
            sales_amount=110,
            carrier_type="1",
        )
        result = issue_invoice_safe(invoice.pk)
        self.assertEqual(result.status, Invoice.Status.PENDING)
        self.assertEqual(result.error_code, "configuration")
