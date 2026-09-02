from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.admin_site import backoffice_site
from orders.admin import NotificationOutboxAdmin, OrderAuditLogAdmin, PaymentAdmin
from orders.models import NotificationOutbox, Order, OrderAuditLog, OrderItem, Payment, PaymentMethod


class OrderAdminOperationsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("ops-admin", "ops@example.com", "password")
        self.client.force_login(self.user)
        self.order = self.make_order(Order.Status.SHIPPING_REVIEW, "primary")
        OrderItem.objects.create(
            order=self.order,
            product_name_snapshot="靜謐器皿",
            sku_snapshot="RF-001",
            unit_price_snapshot=Decimal("3000"),
            quantity=1,
            line_total=Decimal("3000"),
        )

    @staticmethod
    def make_order(status, key, **extra):
        defaults = {
            "idempotency_key": f"admin-{key}",
            "customer_name": "營運顧客",
            "phone": "0900",
            "email": "customer@example.com",
            "shipping_information": "台北市",
            "subtotal": Decimal("3000"),
            "status": status,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def change_url(self, order=None):
        return reverse("admin:orders_order_change", args=[(order or self.order).pk])

    def test_next_action_precedes_fields_and_inlines(self):
        response = self.client.get(self.change_url())
        body = response.content.decode()
        self.assertLess(body.index("NEXT ACTION"), body.index("購買者與配送資訊"))
        self.assertLess(body.index("NEXT ACTION"), body.index('id="payments-group"'))
        self.assertLess(body.index("NEXT ACTION"), body.index('id="audit_logs-group"'))

    def test_each_state_only_shows_its_valid_operation(self):
        cases = (
            (Order.Status.RECEIVED, "orders_order_start_shipping_review", "orders_order_confirm_shipping"),
            (Order.Status.SHIPPING_REVIEW, "orders_order_confirm_shipping", "orders_order_mark_preparing"),
            (Order.Status.AWAITING_PAYMENT, "orders_order_resend_payment", "orders_order_mark_shipped"),
            (Order.Status.PAID, "orders_order_mark_preparing", "orders_order_cancel"),
            (Order.Status.PREPARING, "orders_order_mark_shipped", "orders_order_cancel"),
            (Order.Status.SHIPPED, "orders_order_complete", "orders_order_cancel"),
            (Order.Status.COMPLETED, None, "orders_order_cancel"),
        )
        for index, (status, shown, hidden) in enumerate(cases):
            order = self.make_order(
                status,
                f"state-{index}",
                shipping_fee=100 if status not in (Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW) else None,
            )
            response = self.client.get(self.change_url(order))
            if shown:
                self.assertContains(response, reverse(f"admin:{shown}", args=[order.pk]))
            self.assertNotContains(response, reverse(f"admin:{hidden}", args=[order.pk]))

    def test_shipping_action_is_atomic_and_complete_without_normal_save(self):
        response = self.client.post(
            reverse("admin:orders_order_confirm_shipping", args=[self.order.pk]),
            {"shipping_fee": "240"},
        )
        self.assertRedirects(response, self.change_url())
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_fee, 240)
        self.assertEqual(self.order.final_total, 3240)
        self.assertEqual(self.order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.order.payment_link_version, 1)
        self.assertEqual(NotificationOutbox.objects.filter(order=self.order, event_type="payment_request").count(), 2)
        self.assertTrue(OrderAuditLog.objects.filter(order=self.order, event="shipping_confirmed").exists())

    def test_shipping_dispatch_action_saves_fields_status_time_notifications_and_audit(self):
        self.order.shipping_fee = 100
        self.order.status = Order.Status.PREPARING
        self.order.save()
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, display_name="銀行轉帳")
        Payment.objects.create(order=self.order, method=method, amount=3100, status=Payment.Status.CONFIRMED)
        self.order.status = Order.Status.PREPARING
        self.order.save(update_fields=("status", "updated_at"))
        response = self.client.post(
            reverse("admin:orders_order_mark_shipped", args=[self.order.pk]),
            {"carrier": "黑貓宅急便", "tracking_number": "TRACK-1", "tracking_url": "https://example.com/track/1"},
        )
        self.assertRedirects(response, self.change_url())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)
        self.assertEqual(self.order.carrier, "黑貓宅急便")
        self.assertIsNotNone(self.order.shipped_at)
        self.assertEqual(NotificationOutbox.objects.filter(order=self.order, event_type="order_shipped").count(), 2)
        self.assertTrue(OrderAuditLog.objects.filter(order=self.order, event="order_shipped").exists())

    def test_manual_confirmation_only_shows_matching_manual_records_not_ecpay(self):
        self.order.shipping_fee = 100
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.payment_request_total = 3100
        self.order.payment_link_version = 1
        self.order.save()
        card = PaymentMethod.objects.create(code=PaymentMethod.Method.CREDIT_CARD, display_name="信用卡", provider="ecpay")
        ecpay = Payment.objects.create(order=self.order, method=card, provider="ecpay", amount=3100)
        response = self.client.get(self.change_url())
        self.assertNotContains(response, "manual-payment-form")
        bank = PaymentMethod.objects.create(code=PaymentMethod.Method.BANK_TRANSFER, display_name="銀行轉帳")
        manual = Payment.objects.create(order=self.order, method=bank, amount=3100, status=Payment.Status.AWAITING_CONFIRMATION)
        response = self.client.get(self.change_url())
        self.assertContains(response, "manual-payment-form")
        self.assertContains(response, str(manual))

    def test_shipping_revision_invalidates_old_payment_and_link_and_notifies_new_total(self):
        self.order.shipping_fee = 100
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.payment_link_version = 2
        self.order.cancel_link_version = 2
        self.order.payment_request_total = 3100
        self.order.save()
        method = PaymentMethod.objects.create(code=PaymentMethod.Method.CREDIT_CARD, display_name="信用卡")
        payment = Payment.objects.create(order=self.order, method=method, amount=3100, status=Payment.Status.PENDING)
        response = self.client.post(
            reverse("admin:orders_order_revise_shipping", args=[self.order.pk]),
            {"shipping_fee": "350", "acknowledge_reissue": "on"},
        )
        self.assertRedirects(response, self.change_url())
        self.order.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(self.order.final_total, 3350)
        self.assertEqual(self.order.payment_link_version, 3)
        self.assertEqual(self.order.cancel_link_version, 3)
        self.assertEqual(NotificationOutbox.objects.filter(order=self.order, event_type="payment_request").count(), 2)

    def test_resend_click_is_deduped_while_jobs_are_pending(self):
        self.order.shipping_fee = 100
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.payment_link_version = 1
        self.order.payment_request_total = 3100
        self.order.save()
        url = reverse("admin:orders_order_resend_payment", args=[self.order.pk])
        self.client.post(url); self.client.post(url)
        self.assertEqual(NotificationOutbox.objects.filter(order=self.order, event_type="payment_request").count(), 2)

    def test_operations_are_post_only_permission_checked_and_csrf_protected(self):
        url = reverse("admin:orders_order_confirm_shipping", args=[self.order.pk])
        self.client.get(url)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPING_REVIEW)
        limited = get_user_model().objects.create_user("viewer", password="password", is_staff=True)
        self.client.force_login(limited)
        self.assertEqual(self.client.post(url, {"shipping_fee": "100"}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url, {"shipping_fee": "100"}).status_code, 403)

    def test_dashboard_counts_and_old_store_work_is_prioritized(self):
        self.order.created_at = timezone.now() - timedelta(days=3)
        self.order.save(update_fields=("created_at",))
        waiting = self.make_order(Order.Status.AWAITING_PAYMENT, "waiting", shipping_fee=100, payment_request_total=3100, payment_link_version=1)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["orders_need_action"], 1)
        self.assertEqual(response.context["customer_waiting"], 1)
        self.assertEqual(response.context["recent_orders"][0].pk, self.order.pk)
        self.assertContains(response, waiting.public_number)
        changelist = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(changelist.context["cl"].result_list[0].pk, self.order.pk)

    def test_accounting_notification_and_audit_records_cannot_be_deleted(self):
        request = self.client.request().wsgi_request
        request.user = self.user
        for model_admin in (
            PaymentAdmin(Payment, backoffice_site),
            NotificationOutboxAdmin(NotificationOutbox, backoffice_site),
            OrderAuditLogAdmin(OrderAuditLog, backoffice_site),
        ):
            self.assertFalse(model_admin.has_delete_permission(request))
