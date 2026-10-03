from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from catalog.models import Product, ProductCategory, ProductImage
from orders.models import (
    Invoice,
    LineCustomer,
    LineNotification,
    NotificationOutbox,
    Order,
    OrderAuditLog,
    OrderInvoiceProfile,
    OrderItem,
    Payment,
    PaymentMethod,
    PolicyAcceptance,
)

EXECUTE_OPTIONS = {
    "execute": True,
    "confirm": "RESET-TEST-ORDERS",
    "backup_confirmed": True,
}


class ResetTestOrdersCommandTests(TestCase):
    def setUp(self):
        self.category = ProductCategory.objects.create(name="居家", slug="home")
        self.product_a = Product.objects.create(
            category=self.category,
            name="花器 A",
            slug="vase-a",
            sku="ABC001",
            price=Decimal(1000),
            stock=7,
        )
        self.product_b = Product.objects.create(
            category=self.category,
            name="Chair B",
            slug="chair-b",
            sku="CHR001",
            price=Decimal(2000),
            stock=2,
        )
        self.image = ProductImage.objects.create(
            product=self.product_a,
            image="catalog/products/a.jpg",
            alt_text="花器 A",
        )

    def make_order(
        self,
        *,
        suffix,
        inventory_reserved=True,
        inventory_released=False,
        items=None,
    ):
        order = Order.objects.create(
            idempotency_key=f"reset-{suffix}",
            customer_name="Test Customer",
            phone="0900000000",
            email="test@example.com",
            shipping_information="Taipei",
            subtotal=Decimal(1000),
            inventory_reserved=inventory_reserved,
            inventory_released=inventory_released,
        )
        for product, quantity, stock_was_reserved in items or []:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name_snapshot=product.name,
                sku_snapshot=product.sku,
                unit_price_snapshot=product.price,
                quantity=quantity,
                line_total=product.price * quantity,
                stock_was_reserved=stock_was_reserved,
            )
        return order

    def execute(self, *, stdout=None):
        call_command(
            "reset_test_orders", stdout=stdout or StringIO(), **EXECUTE_OPTIONS
        )

    def test_restores_reserved_inventory_and_deletes_order(self):
        self.make_order(suffix="normal", items=[(self.product_a, 3, True)])

        self.execute()

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 10)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_does_not_restore_already_released_inventory(self):
        self.make_order(
            suffix="released",
            inventory_released=True,
            items=[(self.product_a, 3, True)],
        )

        self.execute()

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)

    def test_does_not_restore_item_that_did_not_reserve_stock(self):
        self.make_order(suffix="preorder", items=[(self.product_a, 3, False)])

        self.execute()

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)

    def test_aggregates_multiple_orders_for_the_same_product(self):
        self.make_order(suffix="one", items=[(self.product_a, 2, True)])
        self.make_order(suffix="two", items=[(self.product_a, 3, True)])

        self.execute()

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 12)

    def test_restores_multiple_products_independently(self):
        self.make_order(
            suffix="products",
            items=[(self.product_a, 3, True), (self.product_b, 2, True)],
        )

        self.execute()

        self.product_a.refresh_from_db()
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_a.stock, 10)
        self.assertEqual(self.product_b.stock, 4)

    def test_dry_run_changes_nothing_and_reports_inventory(self):
        order = self.make_order(suffix="dry", items=[(self.product_a, 3, True)])
        Payment.objects.create(order=order, amount=1000)
        profile = OrderInvoiceProfile.objects.create(
            order=order,
            email=order.email,
            carrier_type="1",
            configuration_snapshot={"environment": "stage"},
        )
        Invoice.objects.create(
            order=order,
            profile=profile,
            relate_number="RFRESETDRYRUN",
            sales_amount=1000,
        )
        NotificationOutbox.objects.create(
            order=order,
            channel=NotificationOutbox.Channel.EMAIL,
            event_type="order_received",
            dedupe_key="dry-run-outbox",
        )
        OrderAuditLog.objects.create(order=order, event="dry_run")
        output = StringIO()

        call_command("reset_test_orders", stdout=output)

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(OrderInvoiceProfile.objects.count(), 1)
        self.assertEqual(NotificationOutbox.objects.count(), 1)
        self.assertEqual(OrderAuditLog.objects.count(), 1)
        self.assertIn("7  +3  10", output.getvalue())
        self.assertIn("DRY RUN ONLY", output.getvalue())
        self.assertIn("No database changes have been made.", output.getvalue())

    @patch("orders.ecpay_invoice.urlopen")
    @patch("orders.line_messaging.urlopen")
    @patch("orders.notifications.send_mail")
    def test_execute_does_not_call_external_services_or_enqueue_notifications(
        self, send_mail, line_urlopen, invoice_urlopen
    ):
        self.make_order(suffix="external", items=[(self.product_a, 1, True)])

        self.execute()

        send_mail.assert_not_called()
        line_urlopen.assert_not_called()
        invoice_urlopen.assert_not_called()
        self.assertEqual(NotificationOutbox.objects.count(), 0)

    def test_exception_rolls_back_inventory_and_deletion(self):
        order = self.make_order(suffix="rollback", items=[(self.product_a, 3, True)])

        with (
            patch(
                "orders.management.commands.reset_test_orders.Command._delete_related_data",
                side_effect=RuntimeError("injected failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected failure"),
        ):
            self.execute()

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertEqual(OrderItem.objects.count(), 1)

    def test_deletes_all_order_relations_but_preserves_master_data(self):
        customer = LineCustomer.objects.create(
            line_user_id="U-reset-test",
            display_name="Test Customer",
        )
        method = PaymentMethod.objects.create(
            code=PaymentMethod.Method.BANK_TRANSFER,
            display_name="銀行轉帳",
        )
        order = self.make_order(suffix="relations", items=[(self.product_a, 1, True)])
        order.line_customer = customer
        order.save(update_fields=("line_customer", "updated_at"))
        profile = OrderInvoiceProfile.objects.create(
            order=order,
            email=order.email,
            carrier_type="1",
            configuration_snapshot={"environment": "stage"},
        )
        Payment.objects.create(order=order, method=method, amount=1000)
        Invoice.objects.create(
            order=order,
            profile=profile,
            relate_number="RFRESETRELATIONS",
            sales_amount=1000,
        )
        LineNotification.objects.create(
            order=order,
            line_customer=customer,
            notification_type=LineNotification.Type.ORDER_RECEIVED,
            dedupe_key="reset-line",
        )
        NotificationOutbox.objects.create(
            order=order,
            channel=NotificationOutbox.Channel.EMAIL,
            event_type="order_received",
            dedupe_key="reset-outbox",
        )
        PolicyAcceptance.objects.create(
            order=order,
            line_customer=customer,
            document_type="privacy",
            version="test",
        )
        OrderAuditLog.objects.create(order=order, event="test")

        self.execute()

        for model in (
            Order,
            OrderItem,
            Payment,
            Invoice,
            OrderInvoiceProfile,
            LineNotification,
            NotificationOutbox,
            PolicyAcceptance,
            OrderAuditLog,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertTrue(Product.objects.filter(pk=self.product_a.pk).exists())
        self.assertTrue(ProductCategory.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(ProductImage.objects.filter(pk=self.image.pk).exists())
        self.assertTrue(PaymentMethod.objects.filter(pk=method.pk).exists())
        self.assertTrue(LineCustomer.objects.filter(pk=customer.pk).exists())

    def test_execute_requires_confirmation_phrase_and_backup_acknowledgement(self):
        self.make_order(suffix="guard", items=[(self.product_a, 1, True)])

        with self.assertRaises(CommandError):
            call_command("reset_test_orders", execute=True, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "reset_test_orders",
                execute=True,
                confirm="RESET-TEST-ORDERS",
                stdout=StringIO(),
            )

        self.assertEqual(Order.objects.count(), 1)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)

    @override_settings(ECPAY_ENV="production", ECPAY_MERCHANT_ID="PROD123")
    def test_stops_for_possible_real_production_ecpay_payment(self):
        order = self.make_order(suffix="ecpay", items=[(self.product_a, 1, True)])
        Payment.objects.create(
            order=order,
            provider="ecpay",
            amount=1000,
            status=Payment.Status.AWAITING_CONFIRMATION,
            provider_reference="REAL-TRADE-NO",
            provider_event_id="ecpay:test:real:review",
            provider_metadata={
                "callback": {"MerchantID": "PROD123", "SimulatePaid": "0"}
            },
        )

        with self.assertRaises(CommandError):
            self.execute()

        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)

    def test_stops_when_order_item_product_was_deleted(self):
        order = self.make_order(
            suffix="missing-product", items=[(self.product_a, 1, True)]
        )
        OrderItem.objects.filter(order=order).update(product=None)

        with self.assertRaises(CommandError):
            self.execute()

        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_stops_for_production_invoice_with_external_activity(self):
        order = self.make_order(
            suffix="production-invoice", items=[(self.product_a, 1, True)]
        )
        profile = OrderInvoiceProfile.objects.create(
            order=order,
            email=order.email,
            carrier_type="1",
            configuration_snapshot={"environment": "production"},
        )
        Invoice.objects.create(
            order=order,
            profile=profile,
            relate_number="RFRESETPRODINVOICE",
            sales_amount=1000,
            status=Invoice.Status.ISSUING,
            attempt_count=1,
        )

        with self.assertRaises(CommandError):
            self.execute()

        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 7)
