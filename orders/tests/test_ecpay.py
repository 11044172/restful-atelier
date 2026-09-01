from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.ecpay import (
    ALLOWED_CALLBACK_PAYMENT_TYPES,
    PRODUCTION_CHECKOUT_URL,
    STAGE_CHECKOUT_URL,
    ecpay_urlencode,
    generate_check_mac_value,
    verify_check_mac_value,
)
from orders.line_messaging import build_payment_confirmed
from orders.models import (
    LineCustomer,
    LineNotification,
    NotificationOutbox,
    Order,
    OrderItem,
    Payment,
    PaymentMethod,
)
from orders.notifications import process_next_outbox
from orders.operations import cancel_order, confirm_shipping_and_request_payment
from orders.payment_links import make_payment_token

ECPAY_SETTINGS = {
    "ECPAY_ENV": "stage",
    "ECPAY_MERCHANT_ID": "FAKE123456",
    "ECPAY_HASH_KEY": "FakeHashKey123456",
    "ECPAY_HASH_IV": "FakeHashIV123456",
    "ECPAY_STANDARD_ENABLED": True,
    "ECPAY_INSTALLMENT_ENABLED": False,
    "ECPAY_CREDIT_INSTALLMENTS": "3,6,12,18,24",
    "ECPAY_IGNORE_PAYMENT": "WebATM#ATM#CVS#BARCODE#BNPL#WeiXin",
    "CANONICAL_ORIGIN": "https://shop.example.test",
    "LINE_MESSAGING_CHANNEL_ACCESS_TOKEN": "line-test-token",
}


@override_settings(**ECPAY_SETTINGS)
class ECPayTests(TestCase):
    def setUp(self):
        self.customer = LineCustomer.objects.create(
            line_user_id="U-ecpay-customer",
            display_name="測試顧客",
            is_friend=True,
        )
        self.method = PaymentMethod.objects.create(
            code=PaymentMethod.Method.CREDIT_CARD,
            enabled=True,
            display_name="信用卡",
            provider="ecpay",
        )
        self.order = Order.objects.create(
            idempotency_key="ecpay-order",
            line_customer=self.customer,
            customer_name="測試顧客",
            phone="0912345678",
            email="customer@example.test",
            shipping_information="Taipei",
            subtotal=Decimal(1000),
            shipping_fee=Decimal(100),
            payment_request_total=Decimal(1100),
            payment_link_version=1,
            cancel_link_version=1,
            status=Order.Status.AWAITING_PAYMENT,
        )
        OrderItem.objects.create(
            order=self.order,
            product_name_snapshot="木椅",
            unit_price_snapshot=1000,
            quantity=1,
            line_total=1000,
        )

    def _payment_url(self, order=None):
        order = order or self.order
        return reverse("payment", args=[make_payment_token(order)])

    def _start_payment(self, variant="standard", order=None):
        order = order or self.order
        response = self.client.post(
            self._payment_url(order),
            {
                "payment_variant": variant,
                "final_terms_accepted": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response, Payment.objects.filter(order=order, provider="ecpay").latest("pk")

    def _new_order(self, suffix, amount=1100):
        order = Order.objects.create(
            idempotency_key=f"ecpay-order-{suffix}",
            line_customer=self.customer,
            customer_name="測試顧客",
            phone="0912345678",
            email="customer@example.test",
            shipping_information="Taipei",
            subtotal=Decimal(amount - 100),
            shipping_fee=Decimal(100),
            payment_request_total=Decimal(amount),
            payment_link_version=1,
            cancel_link_version=1,
            status=Order.Status.AWAITING_PAYMENT,
        )
        OrderItem.objects.create(
            order=order,
            product_name_snapshot="測試商品",
            unit_price_snapshot=amount - 100,
            quantity=1,
            line_total=amount - 100,
        )
        return order

    def _callback_fields(self, payment, **overrides):
        fields = {
            "MerchantID": ECPAY_SETTINGS["ECPAY_MERCHANT_ID"],
            "MerchantTradeNo": payment.merchant_trade_no,
            "TradeAmt": str(int(payment.amount)),
            "RtnCode": "1",
            "RtnMsg": "交易成功",
            "PaymentDate": "2026/09/01 12:05:00",
            "PaymentType": "Credit_CreditCard",
            "PaymentTypeChargeFee": "10",
            "TradeDate": "2026/09/01 12:00:00",
            "TradeNo": f"260901{payment.pk:014d}"[-20:],
            "SimulatePaid": "0",
        }
        fields.update({key: str(value) for key, value in overrides.items()})
        fields["CheckMacValue"] = generate_check_mac_value(
            fields,
            hash_key=ECPAY_SETTINGS["ECPAY_HASH_KEY"],
            hash_iv=ECPAY_SETTINGS["ECPAY_HASH_IV"],
        )
        return fields

    def _post_callback(self, fields):
        return self.client.post(
            reverse("ecpay_callback"),
            urlencode(fields),
            content_type="application/x-www-form-urlencoded",
        )

    def test_check_mac_value_known_fabricated_vector_and_encoding(self):
        parameters = {
            "MerchantID": "FAKE123456",
            "MerchantTradeNo": "RFTEST123",
            "MerchantTradeDate": "2026/09/01 12:00:00",
            "PaymentType": "aio",
            "TotalAmount": "1100",
            "TradeDesc": "RESTFULL ATELIER order",
            "ItemName": "Chair x 1",
            "ReturnURL": "https://example.test/payments/ecpay/callback/",
            "ChoosePayment": "Credit",
            "EncryptType": "1",
        }
        expected = "D6FAD983B8EBD2E069A9C045F261B978985B955AEA0219E95DAD3A48FCBDE28A"
        self.assertEqual(
            generate_check_mac_value(
                parameters,
                hash_key=ECPAY_SETTINGS["ECPAY_HASH_KEY"],
                hash_iv=ECPAY_SETTINGS["ECPAY_HASH_IV"],
            ),
            expected,
        )
        self.assertEqual(ecpay_urlencode("-_.!*() ~"), "-_.!*()+%7E")
        signed = {**parameters, "CheckMacValue": expected}
        self.assertTrue(
            verify_check_mac_value(
                signed,
                hash_key=ECPAY_SETTINGS["ECPAY_HASH_KEY"],
                hash_iv=ECPAY_SETTINGS["ECPAY_HASH_IV"],
            )
        )
        signed["TotalAmount"] = "1101"
        self.assertFalse(
            verify_check_mac_value(
                signed,
                hash_key=ECPAY_SETTINGS["ECPAY_HASH_KEY"],
                hash_iv=ECPAY_SETTINGS["ECPAY_HASH_IV"],
            )
        )

    def test_standard_checkout_uses_all_expected_exclusions_and_server_signature(self):
        response, payment = self._start_payment()
        fields = response.context["fields"]
        self.assertEqual(response.context["action"], STAGE_CHECKOUT_URL)
        self.assertEqual(fields["TotalAmount"], "1100")
        self.assertEqual(fields["ChoosePayment"], "ALL")
        ignored = set(fields["IgnorePayment"].split("#"))
        self.assertTrue({"WebATM", "ATM", "CVS", "BARCODE", "BNPL", "WeiXin"}.issubset(ignored))
        self.assertTrue({"Credit", "ApplePay", "TWQR", "DigitalPayment"}.isdisjoint(ignored))
        self.assertNotIn("CreditInstallment", fields)
        self.assertEqual(fields["PaymentType"], "aio")
        self.assertEqual(fields["EncryptType"], "1")
        self.assertEqual(fields["MerchantTradeNo"], payment.merchant_trade_no)
        self.assertLessEqual(len(payment.merchant_trade_no), 20)
        self.assertIn(reverse("ecpay_callback"), fields["ReturnURL"])
        self.assertIn("payments/ecpay/return/", fields["ClientBackURL"])
        self.assertNotContains(response, ECPAY_SETTINGS["ECPAY_HASH_KEY"])
        self.assertNotContains(response, ECPAY_SETTINGS["ECPAY_HASH_IV"])
        self.assertContains(response, f'action="{STAGE_CHECKOUT_URL}"', html=False)
        unsigned = {key: value for key, value in fields.items() if key != "CheckMacValue"}
        self.assertEqual(
            fields["CheckMacValue"],
            generate_check_mac_value(
                unsigned,
                hash_key=ECPAY_SETTINGS["ECPAY_HASH_KEY"],
                hash_iv=ECPAY_SETTINGS["ECPAY_HASH_IV"],
            ),
        )

    @override_settings(ECPAY_ENV="production")
    def test_production_switch_changes_endpoint_without_code_change(self):
        response, _payment = self._start_payment()
        self.assertEqual(response.context["action"], PRODUCTION_CHECKOUT_URL)

    def test_payment_page_only_shows_site_level_ecpay_variants(self):
        response = self.client.get(self._payment_url())
        self.assertContains(response, "使用 ECPay 付款")
        self.assertNotContains(response, "信用卡分期付款")
        self.assertNotContains(response, 'name="payment_method"', html=False)
        self.assertNotContains(response, "台灣 Pay")
        self.assertNotContains(response, "銀行轉帳")
        self.assertNotContains(response, "PayPal")
        self.assertFalse(Payment.objects.exists())

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="3,6,12")
    def test_installment_checkout_uses_credit_and_configured_installments(self):
        page = self.client.get(self._payment_url())
        self.assertContains(page, "信用卡分期付款")
        response, payment = self._start_payment("installment")
        fields = response.context["fields"]
        self.assertEqual(fields["ChoosePayment"], "Credit")
        self.assertEqual(fields["CreditInstallment"], "3,6,12")
        self.assertEqual(fields["NeedExtraPaidInfo"], "Y")
        self.assertNotIn("IgnorePayment", fields)
        self.assertEqual(payment.provider_metadata["payment_variant"], "installment")
        self.assertEqual(payment.provider_metadata["requested_installments"], ["3", "6", "12"])

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="")
    def test_installment_button_is_hidden_when_installments_are_not_configured(self):
        response = self.client.get(self._payment_url())
        self.assertContains(response, "使用 ECPay 付款")
        self.assertNotContains(response, "信用卡分期付款")

    @override_settings(ECPAY_STANDARD_ENABLED=False)
    def test_standard_button_is_hidden_when_disabled(self):
        response = self.client.get(self._payment_url())
        self.assertNotContains(response, "使用 ECPay 付款")

    @override_settings(ECPAY_IGNORE_PAYMENT="WebATM#Credit")
    def test_forbidden_ignore_payment_configuration_hides_standard_entry(self):
        response = self.client.get(self._payment_url())
        self.assertNotContains(response, "使用 ECPay 付款")
        self.assertContains(response, "目前沒有已完成設定的付款方式")

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="3,7")
    def test_invalid_installment_configuration_is_not_customer_selectable(self):
        response = self.client.get(self._payment_url())
        self.assertNotContains(response, "信用卡分期付款")
        invalid = self.client.post(
            self._payment_url(),
            {"payment_variant": "installment", "final_terms_accepted": "on"},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_customer_ecpay_parameter_injection_is_ignored(self):
        response = self.client.post(
            self._payment_url(),
            {
                "payment_variant": "standard",
                "final_terms_accepted": "on",
                "ChoosePayment": "ATM",
                "IgnorePayment": "Credit",
                "CreditInstallment": "30N",
                "ChooseSubPayment": "anything",
                "TotalAmount": "1",
            },
        )
        fields = response.context["fields"]
        self.assertEqual(fields["ChoosePayment"], "ALL")
        self.assertEqual(fields["TotalAmount"], "1100")
        self.assertNotIn("CreditInstallment", fields)
        self.assertNotIn("ChooseSubPayment", fields)

    def test_unknown_payment_variant_cannot_create_payment(self):
        response = self.client.post(
            self._payment_url(),
            {"payment_variant": "credit", "final_terms_accepted": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_unconfirmed_shipping_paid_cancelled_and_invalid_token_cannot_start(self):
        order = self.order
        order.shipping_fee = None
        order.status = Order.Status.SHIPPING_REVIEW
        order.payment_request_total = None
        order.save()
        self.assertEqual(self.client.get(self._payment_url(order)).status_code, 404)

        order.shipping_fee = 100
        order.payment_request_total = 1100
        order.status = Order.Status.CANCELLED
        order.save()
        self.assertEqual(self.client.get(self._payment_url(order)).status_code, 410)

        order.status = Order.Status.AWAITING_PAYMENT
        order.save()
        Payment.objects.create(order=order, method=self.method, amount=1100, status=Payment.Status.CONFIRMED)
        self.assertEqual(self.client.get(self._payment_url(order)).status_code, 410)
        self.assertEqual(self.client.get(reverse("payment", args=["not-a-token"])).status_code, 404)

    def test_failed_attempt_gets_new_merchant_trade_no_on_retry(self):
        _response, first = self._start_payment()
        fields = self._callback_fields(first, RtnCode="10100251", RtnMsg="卡片過期")
        callback = self._post_callback(fields)
        self.assertEqual(callback.content, b"1|OK")
        first.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.FAILED)

        _response, second = self._start_payment()
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.merchant_trade_no, second.merchant_trade_no)

    def test_retry_after_ecpay_order_creation_uses_new_merchant_trade_no(self):
        _response, first = self._start_payment()
        _response, second = self._start_payment()
        first.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.CANCELLED)
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.merchant_trade_no, second.merchant_trade_no)

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="3,6")
    def test_switching_standard_and_installment_never_reuses_merchant_trade_no(self):
        _response, standard = self._start_payment("standard")
        _response, installment = self._start_payment("installment")
        standard.refresh_from_db()
        self.assertEqual(standard.status, Payment.Status.CANCELLED)
        self.assertNotEqual(standard.merchant_trade_no, installment.merchant_trade_no)

        _response, standard_again = self._start_payment("standard")
        installment.refresh_from_db()
        self.assertEqual(installment.status, Payment.Status.CANCELLED)
        self.assertNotEqual(installment.merchant_trade_no, standard_again.merchant_trade_no)

    def test_shipping_change_cancels_old_unpaid_attempt_and_uses_new_db_total(self):
        _response, first = self._start_payment()
        self.order.shipping_fee = 200
        self.order.save()
        with self.captureOnCommitCallbacks(execute=True):
            confirm_shipping_and_request_payment(self.order.pk)
        first.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.CANCELLED)
        self.assertEqual(self.order.payment_request_total, 1200)

        response, second = self._start_payment()
        self.assertEqual(response.context["fields"]["TotalAmount"], "1200")
        self.assertNotEqual(first.merchant_trade_no, second.merchant_trade_no)

    def test_payment_arriving_after_cancellation_requires_admin_refund_without_success_line(self):
        _response, payment = self._start_payment()
        with self.captureOnCommitCallbacks(execute=True):
            cancel_order(self.order.pk, actor_label="customer")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)

        with self.captureOnCommitCallbacks(execute=True):
            callback = self._post_callback(self._callback_fields(payment))
        self.assertEqual(callback.content, b"1|OK")
        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(self.order.status, Order.Status.REFUND_PENDING)
        self.assertFalse(NotificationOutbox.objects.filter(event_type="payment_confirmed").exists())

    def test_success_callback_confirms_payment_order_and_audited_fields(self):
        _response, payment = self._start_payment()
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post_callback(self._callback_fields(payment))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"1|OK")
        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertTrue(payment.provider_reference)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(payment.provider_metadata["callback"]["TradeAmt"], "1100")
        self.assertNotIn("CheckMacValue", payment.provider_metadata["callback"])
        self.assertEqual(NotificationOutbox.objects.filter(event_type="payment_confirmed").count(), 2)

    def test_all_official_standard_callback_payment_types_are_confirmed_and_normalized(self):
        expected = {
            "Credit_CreditCard": "信用卡／Apple Pay",
            "TWQR_OPAY": "TWQR",
            "DigitalPayment_Jkopay": "街口支付",
            "DigitalPayment_IPASS": "iPASS MONEY",
        }
        self.assertEqual(ALLOWED_CALLBACK_PAYMENT_TYPES, frozenset(expected))
        for index, (payment_type, label) in enumerate(expected.items(), start=1):
            with self.subTest(payment_type=payment_type):
                order = self._new_order(f"payment-type-{index}")
                _response, payment = self._start_payment(order=order)
                callback = self._post_callback(
                    self._callback_fields(payment, PaymentType=payment_type)
                )
                self.assertEqual(callback.content, b"1|OK")
                payment.refresh_from_db()
                order.refresh_from_db()
                self.assertEqual(payment.status, Payment.Status.CONFIRMED)
                self.assertEqual(payment.ecpay_payment_type, payment_type)
                self.assertEqual(payment.normalized_payment_method, label)
                self.assertEqual(order.status, Order.Status.PAID)

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="3,6,12")
    def test_installment_callback_saves_actual_installments(self):
        _response, payment = self._start_payment("installment")
        response = self._post_callback(self._callback_fields(payment, stage="6"))
        self.assertEqual(response.content, b"1|OK")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.actual_installments, 6)
        self.assertEqual(payment.normalized_payment_method, "信用卡分期付款（6期）")

    @override_settings(ECPAY_INSTALLMENT_ENABLED=True, ECPAY_CREDIT_INSTALLMENTS="3,6,12")
    def test_installment_fallback_or_unconfigured_count_requires_manual_review(self):
        _response, payment = self._start_payment("installment")
        response = self._post_callback(self._callback_fields(payment, stage="0"))
        self.assertEqual(response.content, b"1|OK")
        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.AWAITING_CONFIRMATION)
        self.assertEqual(payment.actual_installments, 0)
        self.assertEqual(payment.provider_metadata["review_required"], "installment_count_mismatch")
        self.assertNotEqual(self.order.status, Order.Status.PAID)

        retry = self.client.post(
            self._payment_url(),
            {"payment_variant": "standard", "final_terms_accepted": "on"},
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(Payment.objects.filter(order=self.order).count(), 1)

    def test_unknown_payment_type_is_audited_without_confirming_order(self):
        _response, payment = self._start_payment()
        response = self._post_callback(
            self._callback_fields(payment, PaymentType="FutureWallet_Guessed")
        )
        self.assertEqual(response.content, b"1|OK")
        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.AWAITING_CONFIRMATION)
        self.assertEqual(payment.provider_metadata["review_required"], "unknown_payment_type")
        self.assertEqual(payment.ecpay_payment_type, "FutureWallet_Guessed")
        self.assertNotEqual(self.order.status, Order.Status.PAID)
        self.assertFalse(NotificationOutbox.objects.filter(event_type="payment_confirmed").exists())

    def test_twqr_out_of_range_callback_requires_review_but_standard_entry_remains_available(self):
        order = self._new_order("twqr-out-of-range", amount=50000)
        page = self.client.get(self._payment_url(order))
        self.assertContains(page, "使用 ECPay 付款")
        self.assertContains(page, "本訂單金額不在 TWQR")
        _response, payment = self._start_payment(order=order)
        callback = self._post_callback(
            self._callback_fields(payment, PaymentType="TWQR_OPAY")
        )
        self.assertEqual(callback.content, b"1|OK")
        payment.refresh_from_db()
        self.assertEqual(payment.provider_metadata["review_required"], "twqr_amount_out_of_range")

    def test_callback_rejects_bad_mac_merchant_trade_number_and_amount(self):
        _response, payment = self._start_payment()
        bad_mac = self._callback_fields(payment)
        bad_mac["CheckMacValue"] = "0" * 64
        self.assertEqual(self._post_callback(bad_mac).status_code, 400)

        wrong_merchant = self._callback_fields(payment, MerchantID="OTHER12345")
        self.assertEqual(self._post_callback(wrong_merchant).status_code, 400)

        wrong_trade = self._callback_fields(payment, MerchantTradeNo="RFUNKNOWN123")
        self.assertEqual(self._post_callback(wrong_trade).status_code, 400)

        wrong_amount = self._callback_fields(payment, TradeAmt="1101")
        self.assertEqual(self._post_callback(wrong_amount).status_code, 400)
        payment.refresh_from_db()
        self.assertNotEqual(payment.status, Payment.Status.CONFIRMED)

    def test_duplicate_callback_is_idempotent_and_line_is_sent_once(self):
        _response, payment = self._start_payment()
        callback = self._callback_fields(payment)
        with self.captureOnCommitCallbacks(execute=True):
            first = self._post_callback(callback)
        with self.captureOnCommitCallbacks(execute=True):
            second = self._post_callback(callback)
        self.assertEqual(first.content, b"1|OK")
        self.assertEqual(second.content, b"1|OK")
        self.assertEqual(NotificationOutbox.objects.filter(event_type="payment_confirmed").count(), 2)

        with patch("orders.line_messaging.push_message") as push:
            process_next_outbox()
        self.assertEqual(push.call_count, 1)
        self.assertEqual(
            LineNotification.objects.filter(notification_type=LineNotification.Type.PAYMENT_CONFIRMED).count(),
            1,
        )

    @override_settings(ECPAY_ENV="production")
    def test_production_simulated_payment_is_not_confirmed(self):
        _response, payment = self._start_payment()
        response = self._post_callback(self._callback_fields(payment, SimulatePaid="1"))
        self.assertEqual(response.content, b"1|OK")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)

    def test_return_page_only_displays_database_status(self):
        response, payment = self._start_payment()
        return_url = response.context["fields"]["ClientBackURL"]
        path = return_url.removeprefix(ECPAY_SETTINGS["CANONICAL_ORIGIN"])
        pending = self.client.get(path)
        self.assertContains(pending, "正在確認付款結果")
        self.assertEqual(payment.status, Payment.Status.AWAITING_CONFIRMATION)

    def test_paid_order_amount_is_immutable(self):
        Payment.objects.create(
            order=self.order,
            method=self.method,
            provider="ecpay",
            amount=1100,
            status=Payment.Status.CONFIRMED,
        )
        self.order.refresh_from_db()
        self.order.shipping_fee = 200
        with self.assertRaises(ValidationError):
            self.order.full_clean()
        with self.assertRaises(ValidationError):
            self.order.save()

    def test_payment_confirmation_line_copy_matches_brand_language(self):
        message = build_payment_confirmed(self.order)
        serialized = str(message)
        self.assertIn("已確認收到您的付款", serialized)
        self.assertIn("再透過 LINE 通知您", serialized)


class ECPayMissingConfigurationTests(TestCase):
    @override_settings(
        ECPAY_ENV="stage",
        ECPAY_MERCHANT_ID="",
        ECPAY_HASH_KEY="",
        ECPAY_HASH_IV="",
    )
    def test_incomplete_configuration_hides_ecpay_instead_of_500(self):
        method = PaymentMethod.objects.create(
            code=PaymentMethod.Method.CREDIT_CARD,
            enabled=True,
            display_name="信用卡",
            provider="ecpay",
        )
        order = Order.objects.create(
            idempotency_key="missing-config-order",
            customer_name="顧客",
            phone="1",
            email="customer@example.test",
            shipping_information="Taipei",
            subtotal=1000,
            shipping_fee=100,
            payment_request_total=1100,
            payment_link_version=1,
            status=Order.Status.AWAITING_PAYMENT,
        )
        response = self.client.get(reverse("payment", args=[make_payment_token(order)]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "目前沒有已完成設定的付款方式")
        self.assertNotContains(response, f'value="{method.pk}"', html=False)
