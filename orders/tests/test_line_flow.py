import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory
from core.models import SiteSettings
from orders.cart import Cart
from orders.line_login import LineLoginError, generate_oauth_values, verify_id_token
from orders.line_messaging import LineMessagingError, schedule_order_received, send_order_notification
from orders.line_webhooks import process_event
from orders.models import LineCustomer, LineNotification, LineWebhookEvent, NotificationOutbox, Order, OrderItem, Payment, PaymentMethod, PolicyAcceptance
from orders.notifications import process_next_outbox
from orders.operations import confirm_shipping_and_request_payment, mark_shipped
from orders.payment_links import PaymentLinkError, make_payment_token, resolve_payment_token
from orders.services import create_order_from_cart, send_order_emails


LINE_SETTINGS = {
    "LINE_LOGIN_CHANNEL_ID": "1234567890",
    "LINE_LOGIN_CHANNEL_SECRET": "login-secret",
    "LINE_LOGIN_CALLBACK_URL": "https://restfull-xhex.onrender.com/auth/line/callback/",
    "LINE_MESSAGING_CHANNEL_ACCESS_TOKEN": "message-token",
    "LINE_MESSAGING_CHANNEL_SECRET": "message-secret",
    "LINE_OFFICIAL_ACCOUNT_BASIC_ID": "@rfull",
    "LINE_FRIENDSHIP_MAX_AGE": 900,
    "CANONICAL_ORIGIN": "https://restfull-xhex.onrender.com",
}


@override_settings(**LINE_SETTINGS)
class LineLoginCheckoutTests(TestCase):
    def setUp(self):
        self.site = SiteSettings.objects.create(checkout_enabled=True, line_add_friend_url="https://line.me/R/ti/p/@rfull")
        category = ProductCategory.objects.create(name="器物", slug="goods")
        self.product = Product.objects.create(category=category, name="杯", slug="line-cup", sku="L1", description="d", price=1000, stock=5, is_published=True)

    def test_login_redirect_has_state_nonce_pkce_and_aggressive_friend_option(self):
        response = self.client.get(reverse("line_login_start"))
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["openid profile"])
        self.assertEqual(query["bot_prompt"], ["aggressive"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        oauth = self.client.session["line_oauth"]
        expected = base64.urlsafe_b64encode(hashlib.sha256(oauth["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["state"], [oauth["state"]])
        self.assertEqual(query["nonce"], [oauth["nonce"]])
        self.assertNotIn("email", query["scope"][0])

    def test_state_mismatch_and_direct_callback_are_rejected(self):
        self.client.get(reverse("line_login_start"))
        response = self.client.get(reverse("line_login_callback"), {"state": "wrong", "code": "code"})
        self.assertRedirects(response, reverse("orders:checkout"))
        self.assertEqual(LineCustomer.objects.count(), 0)
        response = self.client.get(reverse("line_login_callback"), {"state": "x", "code": "code"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LineCustomer.objects.count(), 0)

    def _complete_callback(self, friend=True, sub="U123"):
        self.client.get(reverse("line_login_start"))
        oauth = self.client.session["line_oauth"]
        with patch("orders.views.exchange_code", return_value={"access_token": "temporary", "id_token": "id-token"}) as exchange, \
             patch("orders.views.verify_id_token", return_value={"iss": "https://access.line.me", "aud": "1234567890", "exp": int(time.time()) + 60, "nonce": oauth["nonce"], "sub": sub, "name": "林小姐"}), \
             patch("orders.views.get_friendship_status", return_value=friend):
            response = self.client.get(reverse("line_login_callback"), {"state": oauth["state"], "code": "authorization-code"})
        self.assertEqual(exchange.call_args.kwargs["code_verifier"], oauth["code_verifier"])
        return response

    def test_callback_success_creates_one_customer_and_preserves_cart(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 2})
        self._complete_callback(friend=True)
        self.assertEqual(self.client.session["cart"][str(self.product.pk)], 2)
        customer = LineCustomer.objects.get()
        self.assertTrue(customer.is_friend)
        self.assertEqual(customer.display_name, "林小姐")
        self._complete_callback(friend=True)
        self.assertEqual(LineCustomer.objects.count(), 1)

    def test_cancel_keeps_cart_and_creates_no_customer(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self.client.get(reverse("line_login_start"))
        response = self.client.get(reverse("line_login_callback"), {"error": "access_denied"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.product.pk), self.client.session["cart"])
        self.assertFalse(LineCustomer.objects.exists())

    def test_friend_true_can_checkout_friend_false_cannot(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self._complete_callback(friend=False)
        get_response = self.client.get(reverse("orders:checkout"))
        self.assertContains(get_response, "加入 LINE 好友")
        self.assertContains(get_response, "購買商品必須加入 Rfull 官方 LINE 好友")
        self.assertContains(get_response, "data-line-friend-watch")
        self.assertNotContains(get_response, "<form class=\"contact-form checkout-form\"", html=False)
        self.assertFalse(Order.objects.exists())
        self._complete_callback(friend=True)
        self.assertContains(self.client.get(reverse("orders:checkout")), "送出訂單")

    def test_basic_id_fallback_displays_add_friend_button_and_enables_checkout(self):
        self.site.line_add_friend_url = ""
        self.site.line_official_url = ""
        self.site.save()
        self._complete_callback(friend=False, sub="U-basic-id")

        response = self.client.get(reverse("orders:checkout"))

        self.assertContains(response, 'href="https://line.me/R/ti/p/@rfull"', html=False)
        self.assertContains(response, "加入 LINE 好友")
        self.assertNotContains(response, "訂單服務目前準備中")

    def test_returning_customer_prefills_latest_delivery_information(self):
        customer = LineCustomer.objects.create(
            line_user_id="U-returning", display_name="林小姐", is_friend=True,
        )
        Order.objects.create(
            idempotency_key="previous-order", line_customer=customer,
            customer_name="林美玲", phone="0912-345-678", email="lin@example.com",
            shipping_information="台北市中正區示範路 1 號", customer_note="舊訂單備註",
            subtotal=1000,
        )
        self._complete_callback(friend=True, sub="U-returning")

        response = self.client.get(reverse("orders:checkout"))
        form = response.context["form"]

        self.assertEqual(form["customer_name"].value(), "林美玲")
        self.assertEqual(form["phone"].value(), "0912-345-678")
        self.assertEqual(form["email"].value(), "lin@example.com")
        self.assertEqual(form["shipping_information"].value(), "台北市中正區示範路 1 號")
        self.assertFalse(form["customer_note"].value())
        self.assertContains(response, "已帶入上次購買資料")

    def test_callback_friendship_change_confirms_friend_when_status_api_fails(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self.client.get(reverse("line_login_start"))
        oauth = self.client.session["line_oauth"]
        with patch("orders.views.exchange_code", return_value={"access_token": "temporary", "id_token": "id-token"}), \
             patch("orders.views.verify_id_token", return_value={"sub": "U-added-during-login", "name": "林小姐"}), \
             patch("orders.views.get_friendship_status", side_effect=LineLoginError("LINE API rejected the request (403).")):
            response = self.client.get(reverse("line_login_callback"), {
                "state": oauth["state"], "code": "authorization-code", "friendship_status_changed": "true",
            })

        self.assertRedirects(response, reverse("orders:checkout"))
        self.assertTrue(self.client.session["line_friend_verified"])
        self.assertNotIn("line_friend_check_failed", self.client.session)
        self.assertTrue(LineCustomer.objects.get(line_user_id="U-added-during-login").is_friend)
        self.assertContains(self.client.get(reverse("orders:checkout")), "送出訂單")

    def test_follow_webhook_unlocks_waiting_checkout_status(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self._complete_callback(friend=False, sub="U-follow-after-login")

        pending = self.client.get(reverse("orders:line_friend_status"))
        self.assertEqual(pending.json(), {"authenticated": True, "friend": False})
        process_event({
            "type": "follow", "webhookEventId": "follow-after-login",
            "source": {"type": "user", "userId": "U-follow-after-login"},
        })
        confirmed = self.client.get(reverse("orders:line_friend_status"))

        self.assertTrue(confirmed.json()["friend"])
        self.assertEqual(confirmed.json()["redirect"], reverse("orders:checkout"))
        self.assertTrue(self.client.session["line_friend_verified"])
        self.assertNotIn("line_friend_wait_started_at", self.client.session)
        self.assertContains(self.client.get(reverse("orders:checkout")), "送出訂單")

    def test_friendship_api_failure_keeps_verified_line_identity(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self.client.get(reverse("line_login_start"))
        oauth = self.client.session["line_oauth"]
        with patch("orders.views.exchange_code", return_value={"access_token": "temporary", "id_token": "id-token"}), \
             patch("orders.views.verify_id_token", return_value={"sub": "U-friend-api", "name": "林小姐"}), \
             patch("orders.views.get_friendship_status", side_effect=LineLoginError("LINE API rejected the request (403).")):
            response = self.client.get(reverse("line_login_callback"), {"state": oauth["state"], "code": "authorization-code"})

        self.assertRedirects(response, reverse("orders:checkout"))
        customer = LineCustomer.objects.get(line_user_id="U-friend-api")
        self.assertEqual(self.client.session["line_customer_id"], customer.pk)
        self.assertFalse(self.client.session["line_friend_verified"])
        self.assertTrue(self.client.session["line_friend_check_failed"])
        self.assertIsNone(customer.friend_checked_at)
        self.assertIn(str(self.product.pk), self.client.session["cart"])
        checkout = self.client.get(reverse("orders:checkout"))
        self.assertContains(checkout, "LINE 登入已完成")
        self.assertContains(checkout, "加入 LINE 好友")
        self.assertContains(checkout, "已完成加入，繼續購買")
        self.assertNotContains(checkout, "<form class=\"contact-form checkout-form\"", html=False)

    def test_friendship_api_failure_reuses_existing_webhook_confirmation(self):
        customer = LineCustomer.objects.create(
            line_user_id="U-existing-friend",
            display_name="林小姐",
            is_friend=True,
            followed_at=timezone.now() - timedelta(days=3),
        )
        self.client.get(reverse("line_login_start"))
        oauth = self.client.session["line_oauth"]
        with patch("orders.views.exchange_code", return_value={"access_token": "temporary", "id_token": "id-token"}), \
             patch("orders.views.verify_id_token", return_value={"sub": customer.line_user_id, "name": "林小姐"}), \
             patch("orders.views.get_friendship_status", side_effect=LineLoginError("LINE API rejected the request (403).")):
            response = self.client.get(reverse("line_login_callback"), {
                "state": oauth["state"], "code": "authorization-code",
            })

        self.assertRedirects(response, reverse("orders:checkout"))
        self.assertTrue(self.client.session["line_friend_verified"])
        self.assertNotIn("line_friend_check_failed", self.client.session)
        self.assertContains(self.client.get(reverse("orders:checkout")), "送出訂單")

    def test_status_poll_accepts_existing_webhook_confirmation(self):
        customer = LineCustomer.objects.create(
            line_user_id="U-existing-poll",
            display_name="林小姐",
            is_friend=True,
            followed_at=timezone.now() - timedelta(days=3),
        )
        session = self.client.session
        session["line_customer_id"] = customer.pk
        session["line_friend_verified"] = False
        session["line_friend_wait_started_at"] = time.time()
        session.save()

        response = self.client.get(reverse("orders:line_friend_status"))

        self.assertTrue(response.json()["friend"])
        self.assertTrue(self.client.session["line_friend_verified"])
        self.assertNotIn("line_friend_wait_started_at", self.client.session)

    def test_friendship_retry_clears_previous_failure(self):
        self._complete_callback(friend=True, sub="U-retry")
        session = self.client.session
        session["line_friend_check_failed"] = True
        session.save()

        self._complete_callback(friend=True, sub="U-retry")

        self.assertNotIn("line_friend_check_failed", self.client.session)
        self.assertTrue(self.client.session["line_friend_verified"])

    def test_invalid_id_token_claims_are_rejected(self):
        now = int(time.time())
        valid = {"iss": "https://access.line.me", "aud": "1234567890", "exp": now + 60, "nonce": "n", "sub": "U1", "name": "Name"}
        mutations = [
            {"iss": "https://evil.example"}, {"aud": "wrong"}, {"exp": now - 1}, {"nonce": "wrong"}, {"sub": ""},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), patch("orders.line_login._post_form", return_value={**valid, **mutation}):
                with self.assertRaises(LineLoginError):
                    verify_id_token(id_token="opaque", nonce="n")


@override_settings(**LINE_SETTINGS)
class NotificationPaymentShippingTests(TestCase):
    def setUp(self):
        self.customer = LineCustomer.objects.create(line_user_id="U-notify", display_name="顧客", is_friend=True)
        self.order = Order.objects.create(
            idempotency_key="line-order", line_customer=self.customer, customer_name="顧客", phone="1",
            email="a@example.com", shipping_information="address", subtotal=Decimal("3000"), status=Order.Status.SHIPPING_REVIEW,
        )
        OrderItem.objects.create(order=self.order, product_name_snapshot="器", unit_price_snapshot=3000, quantity=1, line_total=3000)

    @patch("orders.line_messaging.push_message")
    def test_order_received_is_sent_once(self, push):
        schedule_order_received(self.order.pk)
        schedule_order_received(self.order.pk)
        self.assertEqual(NotificationOutbox.objects.count(), 1)
        process_next_outbox()
        self.assertEqual(push.call_count, 1)
        self.assertEqual(LineNotification.objects.filter(notification_type=LineNotification.Type.ORDER_RECEIVED).count(), 1)

    def test_api_timeout_4xx_5xx_do_not_remove_order_and_failures_are_logged(self):
        for index, error in enumerate((
            LineMessagingError("timeout"), LineMessagingError("client", http_status=400), LineMessagingError("server", http_status=500)
        )):
            with self.subTest(index=index), patch("orders.line_messaging.push_message", side_effect=error):
                notification = send_order_notification(self.order.pk, LineNotification.Type.ORDER_RECEIVED, force=True)
                self.assertEqual(notification.status, LineNotification.Status.FAILED)
                self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_shipping_confirmation_uses_snapshot_total_and_notifies_once(self):
        self.order.shipping_fee = 200
        self.order.save()
        with self.captureOnCommitCallbacks(execute=True):
            confirm_shipping_and_request_payment(self.order.pk)
        with self.captureOnCommitCallbacks(execute=True):
            confirm_shipping_and_request_payment(self.order.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.final_total, 3200)
        self.assertEqual(self.order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.order.payment_link_version, 1)
        self.assertEqual(NotificationOutbox.objects.filter(event_type="payment_request").count(), 2)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ORDER_NOTIFICATION_EMAILS=["staff-one@example.com", "staff-two@example.com"],
    )
    def test_order_email_contains_staff_admin_url(self):
        send_order_emails(self.order)

        self.assertEqual(len(mail.outbox), 2)
        staff_email = mail.outbox[1]
        expected_path = reverse("admin:orders_order_change", args=[self.order.pk])
        self.assertIn(f"https://restfull-xhex.onrender.com{expected_path}", staff_email.body)
        self.assertIn("確定運費並發送付款通知", staff_email.body)
        self.assertIn("staff-one@example.com", staff_email.to)
        self.assertIn("staff-two@example.com", staff_email.to)

    def test_admin_confirmation_saves_posted_shipping_fee_and_schedules_line_payment(self):
        admin_user = get_user_model().objects.create_superuser(
            username="shipping-admin", email="admin@example.com", password="password",
        )
        self.client.force_login(admin_user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:orders_order_confirm_shipping", args=[self.order.pk]),
                {"shipping_fee": "240"},
            )

        self.assertRedirects(response, reverse("admin:orders_order_change", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_fee, 240)
        self.assertEqual(self.order.final_total, 3240)
        self.assertEqual(self.order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.order.payment_link_version, 1)
        self.assertEqual(NotificationOutbox.objects.filter(event_type="payment_request").count(), 2)

    def _awaiting_payment(self):
        self.order.shipping_fee = 200
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.payment_link_version = 1
        self.order.payment_request_total = 3200
        self.order.save()
        return self.order

    def test_payment_link_normal_tampered_expired_old_cancelled_and_paid(self):
        order = self._awaiting_payment()
        token = make_payment_token(order)
        self.assertEqual(resolve_payment_token(token).pk, order.pk)
        with self.assertRaises(PaymentLinkError):
            resolve_payment_token(token + "x")
        with override_settings(PAYMENT_LINK_MAX_AGE=-1):
            with self.assertRaises(PaymentLinkError) as expired:
                resolve_payment_token(token)
            self.assertEqual(str(expired.exception), "expired")
        order.payment_link_version = 2
        order.save()
        with self.assertRaises(PaymentLinkError):
            resolve_payment_token(token)
        fresh = make_payment_token(order)
        order.status = Order.Status.CANCELLED
        order.save()
        with self.assertRaises(PaymentLinkError) as cancelled:
            resolve_payment_token(fresh)
        self.assertEqual(str(cancelled.exception), "cancelled")
        order.status = Order.Status.AWAITING_PAYMENT
        order.inventory_reserved = False
        order.save()
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, enabled=True, display_name="轉帳")
        with patch("orders.line_messaging.schedule_payment_confirmed"):
            Payment.objects.create(order=order, method=method, amount=order.final_total, status=Payment.Status.CONFIRMED)
        with self.assertRaises(PaymentLinkError) as paid:
            resolve_payment_token(fresh)
        self.assertEqual(str(paid.exception), "paid")

    def test_opening_payment_page_does_not_confirm_payment(self):
        order = self._awaiting_payment()
        Payment.objects.create(order=order, amount=order.final_total, status=Payment.Status.PENDING)
        response = self.client.get(reverse("payment", args=[make_payment_token(order)]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(status=Payment.Status.CONFIRMED).exists())

    def test_payment_page_has_one_method_selector_and_shows_qr_after_acceptance(self):
        order = self._awaiting_payment()
        site = SiteSettings.load()
        site.bank_name = "測試銀行"
        site.bank_code = "001"
        site.bank_account_number = "123456"
        site.bank_account_name = "Rfull"
        site.save()
        PaymentMethod.objects.create(
            code=PaymentMethod.Method.TAIWAN_PAY,
            enabled=True,
            display_name="Taiwan Pay",
            instructions="請掃描 QR Code",
            qr_image="payments/methods/taiwan-pay.png",
            sort_order=1,
        )
        PaymentMethod.objects.create(
            code=PaymentMethod.Method.BANK_TRANSFER,
            enabled=True,
            display_name="銀行轉帳",
            sort_order=2,
        )

        url = reverse("payment", args=[make_payment_token(order)])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="payment_method"', count=2, html=False)
        self.assertNotContains(response, '<select name="payment_method"', html=False)
        self.assertNotContains(response, '<details class="payment-method"', html=False)
        self.assertNotContains(response, 'src="/media/payments/methods/taiwan-pay.png"', html=False)
        self.assertContains(response, "請選擇付款方式")

        taiwan_pay = PaymentMethod.objects.get(code=PaymentMethod.Method.TAIWAN_PAY)
        response = self.client.post(url, {
            "payment_method": taiwan_pay.pk,
            "final_terms_accepted": "on",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/media/payments/methods/taiwan-pay.png"', html=False)
        self.assertEqual(Payment.objects.filter(order=order, method=taiwan_pay, status=Payment.Status.AWAITING_CONFIRMATION).count(), 1)
        self.assertEqual(PolicyAcceptance.objects.filter(order=order, document_type="final-payment-terms").count(), 1)

    def test_payment_post_passes_csrf_in_webview_without_origin(self):
        order = self._awaiting_payment()
        site = SiteSettings.load()
        site.bank_name = "測試銀行"
        site.bank_code = "001"
        site.bank_account_number = "123456"
        site.bank_account_name = "Rfull"
        site.save()
        method = PaymentMethod.objects.create(
            code=PaymentMethod.Method.BANK_TRANSFER,
            enabled=True,
            display_name="銀行轉帳",
        )
        url = reverse("payment", args=[make_payment_token(order)])
        csrf_client = Client(enforce_csrf_checks=True)

        get_response = csrf_client.get(url, secure=True)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response["Referrer-Policy"], "same-origin")
        csrf_token = csrf_client.cookies["csrftoken"].value
        post_response = csrf_client.post(
            url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "payment_method": method.pk,
                "final_terms_accepted": "on",
            },
            secure=True,
            HTTP_REFERER=f"https://testserver{url}",
        )

        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(Payment.objects.filter(order=order, method=method).exists())

    @patch("orders.line_messaging.push_message")
    def test_payment_confirmed_notification_is_deduped(self, push):
        order = self._awaiting_payment()
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, enabled=True, display_name="轉帳")
        payment = Payment(order=order, method=method, amount=order.final_total, status=Payment.Status.CONFIRMED)
        with self.captureOnCommitCallbacks(execute=True):
            payment.full_clean()
            payment.save()
        with self.captureOnCommitCallbacks(execute=True):
            payment.save()
        process_next_outbox()
        self.assertEqual(LineNotification.objects.filter(notification_type=LineNotification.Type.PAYMENT_CONFIRMED).count(), 1)
        self.assertEqual(push.call_count, 1)

    @patch("orders.line_messaging.push_message")
    def test_unpaid_cannot_ship_and_paid_ships_once(self, push):
        self.order.carrier = "黑貓宅急便"
        self.order.save()
        with self.assertRaises(ValidationError):
            mark_shipped(self.order.pk)
        order = self._awaiting_payment()
        order.carrier = "黑貓宅急便"
        order.tracking_number = "TRACK1"
        order.save()
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, enabled=True, display_name="轉帳")
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(order=order, method=method, amount=order.final_total, status=Payment.Status.CONFIRMED)
        with self.captureOnCommitCallbacks(execute=True):
            mark_shipped(order.pk)
        mark_shipped(order.pk)
        while NotificationOutbox.objects.filter(event_type="order_shipped").exclude(status=NotificationOutbox.Status.SENT).exists():
            process_next_outbox()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SHIPPED)
        self.assertEqual(LineNotification.objects.filter(notification_type=LineNotification.Type.ORDER_SHIPPED).count(), 1)


@override_settings(LINE_MESSAGING_CHANNEL_SECRET="webhook-secret")
class LineWebhookTests(TestCase):
    def _post(self, payload, signature=True):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        value = base64.b64encode(hmac.new(b"webhook-secret", raw, hashlib.sha256).digest()).decode() if signature else "invalid"
        return self.client.post(reverse("line_messaging_webhook"), data=raw, content_type="application/json", HTTP_X_LINE_SIGNATURE=value)

    def test_valid_signature_follow_unfollow_and_duplicate_event(self):
        follow = {"events": [{"type": "follow", "webhookEventId": "evt-1", "source": {"type": "user", "userId": "U-webhook"}}]}
        self.assertEqual(self._post(follow).status_code, 200)
        customer = LineCustomer.objects.get(line_user_id="U-webhook")
        self.assertTrue(customer.is_friend)
        self.assertFalse(customer.is_blocked)
        self._post(follow)
        self.assertEqual(LineWebhookEvent.objects.count(), 1)
        unfollow = {"events": [{"type": "unfollow", "webhookEventId": "evt-2", "source": {"type": "user", "userId": "U-webhook"}}]}
        self.assertEqual(self._post(unfollow).status_code, 200)
        customer.refresh_from_db()
        self.assertFalse(customer.is_friend)
        self.assertTrue(customer.is_blocked)

    def test_invalid_or_missing_signature_processes_nothing(self):
        payload = {"events": [{"type": "follow", "webhookEventId": "evt-bad", "source": {"userId": "U-bad"}}]}
        self.assertEqual(self._post(payload, signature=False).status_code, 403)
        raw = json.dumps(payload).encode()
        self.assertEqual(self.client.post(reverse("line_messaging_webhook"), data=raw, content_type="application/json").status_code, 403)
        self.assertFalse(LineWebhookEvent.objects.exists())
        self.assertFalse(LineCustomer.objects.exists())
