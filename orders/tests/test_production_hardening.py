from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductCategory
from orders.line_messaging import LineMessagingError, PUSH_URL, push_message
from orders.models import LineCustomer, NotificationOutbox, Order, OrderAuditLog, OrderItem, Payment, PaymentMethod
from orders.notifications import enqueue_order_notifications, process_next_outbox
from orders.operations import cancel_order, mark_shipped, record_refund, transition
from orders.payment_links import make_cancel_token, make_payment_token


class DeploymentConfigurationTests(SimpleTestCase):
    def test_render_web_service_runs_the_outbox_worker(self):
        project_root = Path(__file__).resolve().parents[2]
        render_config = (project_root / "render.yaml").read_text()
        start_script = (project_root / "start.sh").read_text()

        self.assertIn("startCommand: ./start.sh", render_config)
        self.assertIn('- key: OUTBOX_WORKER_CONFIGURED\n        value: "True"', render_config)
        self.assertIn("process_notification_outbox --limit 0", start_script)
        self.assertIn("gunicorn config.wsgi:application", start_script)


@override_settings(LINE_MESSAGING_CHANNEL_ACCESS_TOKEN="test-token", LINE_API_TIMEOUT=1, CANONICAL_ORIGIN="https://restfull-xhex.onrender.com")
class SignedActionsAndOutboxTests(TestCase):
    def setUp(self):
        self.customer = LineCustomer.objects.create(line_user_id="U-owner", display_name="Owner", is_friend=True)
        self.other = LineCustomer.objects.create(line_user_id="U-other", display_name="Other", is_friend=True)
        self.order = Order.objects.create(
            idempotency_key="hardening-order", line_customer=self.customer, customer_name="Owner", phone="0900",
            email="owner@example.com", shipping_information="Taipei", subtotal=Decimal("1000"), shipping_fee=100,
            status=Order.Status.AWAITING_PAYMENT, payment_link_version=1, cancel_link_version=1, payment_request_total=1100,
        )
        OrderItem.objects.create(order=self.order, product_name_snapshot="器", unit_price_snapshot=1000, quantity=1, line_total=1000)

    def test_cancel_link_is_owned_and_idempotent(self):
        token = make_cancel_token(self.order)
        session = self.client.session; session["line_customer_id"] = self.other.pk; session.save()
        self.assertEqual(self.client.get(reverse("orders:cancel", args=[token])).status_code, 403)
        session = self.client.session; session["line_customer_id"] = self.customer.pk; session.save()
        self.assertEqual(self.client.post(reverse("orders:cancel", args=[token])).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(OrderAuditLog.objects.filter(order=self.order, event="cancelled").count(), 1)
        self.assertContains(self.client.post(reverse("orders:cancel", args=[token])), "已取消")
        self.assertEqual(OrderAuditLog.objects.filter(order=self.order, event="cancelled").count(), 1)

    def test_cancel_after_payment_or_shipping_is_refused(self):
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, display_name="Bank", enabled=True)
        Payment.objects.create(order=self.order, method=method, amount=1100, status=Payment.Status.CONFIRMED)
        self.order.refresh_from_db()
        token = make_cancel_token(self.order)
        self.assertContains(self.client.post(reverse("orders:cancel", args=[token])), "オンラインではキャンセルできません")
        self.order.carrier = "Carrier"; self.order.save(); mark_shipped(self.order.pk)
        with self.assertRaisesMessage(Exception, "無法在線取消"):
            cancel_order(self.order.pk)

    def test_expired_forged_and_wrong_customer_payment_links(self):
        token = make_payment_token(self.order)
        with override_settings(PAYMENT_LINK_MAX_AGE=-1):
            self.assertEqual(self.client.get(reverse("payment", args=[token])).status_code, 410)
        self.assertEqual(self.client.get(reverse("payment", args=[token + "x"])).status_code, 404)
        session = self.client.session; session["line_customer_id"] = self.other.pk; session.save()
        self.assertEqual(self.client.get(reverse("payment", args=[token])).status_code, 403)

    def test_signed_action_pages_are_not_cached_or_leaked_as_referrers(self):
        response = self.client.get(reverse("payment", args=[make_payment_token(self.order)]))
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("no-cache", response["Cache-Control"])

    @patch("orders.line_messaging.push_message", side_effect=LineMessagingError("limited", http_status=429, retryable=True))
    def test_line_429_moves_outbox_to_exponential_retry(self, push):
        enqueue_order_notifications(self.order.pk, "payment_request", channels=("line",))
        job = process_next_outbox(); job.refresh_from_db()
        self.assertEqual(job.status, NotificationOutbox.Status.RETRY)
        self.assertEqual(job.attempt_count, 1)
        self.assertGreater(job.next_attempt_at, job.updated_at)

    def test_line_retry_reuses_retry_key_and_eventually_succeeds(self):
        with patch(
            "orders.line_messaging.push_message",
            side_effect=[LineMessagingError("limited", http_status=429, retryable=True), {"request_id": "accepted"}],
        ) as push:
            enqueue_order_notifications(self.order.pk, "payment_request", channels=("line",))
            job = process_next_outbox()
            job.next_attempt_at = timezone.now()
            job.save(update_fields=("next_attempt_at", "updated_at"))
            process_next_outbox()
        job.refresh_from_db()
        self.assertEqual(job.status, NotificationOutbox.Status.SENT)
        self.assertEqual(push.call_count, 2)
        self.assertEqual(push.call_args_list[0].kwargs["retry_key"], push.call_args_list[1].kwargs["retry_key"])

    @patch("orders.notifications.send_order_email_event", side_effect=RuntimeError("smtp unavailable"))
    def test_smtp_failure_is_durable_and_retryable(self, sender):
        enqueue_order_notifications(self.order.pk, "payment_request", channels=("email",))
        job = process_next_outbox(); job.refresh_from_db()
        self.assertEqual(job.status, NotificationOutbox.Status.RETRY)
        self.assertIn("smtp unavailable", job.last_error)

    def test_line_http_409_means_retry_key_was_already_accepted(self):
        error = HTTPError(PUSH_URL, 409, "Conflict", {"x-line-accepted-request-id": "accepted-1"}, BytesIO(b"{}"))
        with patch("orders.line_messaging.urlopen", side_effect=error):
            metadata = push_message(line_user_id=self.customer.line_user_id, message={"type": "text", "text": "ok"}, retry_key="123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(metadata["accepted_request_id"], "accepted-1")

    def test_duplicate_confirmed_payment_is_blocked_by_database(self):
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, display_name="Bank")
        Payment.objects.create(order=self.order, method=method, amount=1100, status=Payment.Status.CONFIRMED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=self.order, method=method, amount=1100, status=Payment.Status.CONFIRMED)

    def test_partial_and_full_refund_are_auditable(self):
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, display_name="Bank")
        payment = Payment.objects.create(order=self.order, method=method, amount=1100, status=Payment.Status.CONFIRMED)
        record_refund(payment.pk, amount=100, reason="partial")
        payment.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(payment.refunded_amount, 100)
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        record_refund(payment.pk, amount=1000, reason="remaining")
        payment.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.assertTrue(OrderAuditLog.objects.filter(order=self.order, event="refund_completed").exists())

    def test_illegal_state_transition_is_blocked(self):
        self.order.status = Order.Status.SHIPPED; self.order.save()
        with self.assertRaisesMessage(Exception, "不允許"):
            transition(self.order, Order.Status.AWAITING_PAYMENT, event="forged")


class RedirectSecurityTests(TestCase):
    def setUp(self):
        category = ProductCategory.objects.create(name="Cat", slug="cat")
        self.product = Product.objects.create(category=category, name="Item", slug="item", sku="ITEM-1", description="desc", price=10, stock=4, is_published=True)

    def test_cart_add_rejects_external_and_javascript_redirects(self):
        for target in ("https://evil.example/phish", "//evil.example/phish", "javascript:alert(1)"):
            with self.subTest(target=target):
                response = self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1, "next": target})
                self.assertEqual(response["Location"], reverse("orders:cart"))
