import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductCategory
from core.models import SiteSettings
from orders.cart import Cart
from orders.line_login import LineLoginError, generate_oauth_values, verify_id_token
from orders.line_messaging import LineMessagingError, schedule_order_received, send_order_notification
from orders.models import LineCustomer, LineNotification, LineWebhookEvent, Order, OrderItem, Payment, PaymentMethod
from orders.operations import confirm_shipping_and_request_payment, mark_shipped
from orders.payment_links import PaymentLinkError, make_payment_token, resolve_payment_token
from orders.services import create_order_from_cart


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
        self.assertContains(get_response, "加入好友並繼續")
        self.assertNotContains(get_response, "<form class=\"contact-form checkout-form\"", html=False)
        self.assertFalse(Order.objects.exists())
        self._complete_callback(friend=True)
        self.assertContains(self.client.get(reverse("orders:checkout")), "送出訂單")

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

    @patch("orders.operations.schedule_payment_request")
    def test_shipping_confirmation_uses_snapshot_total_and_notifies_once(self, notify):
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
        notify.assert_called_once_with(self.order.pk)

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
