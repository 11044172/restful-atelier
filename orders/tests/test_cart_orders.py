import re
import time
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductCategory
from core.models import SiteSettings
from orders.cart import Cart
from orders.models import LineCustomer, NotificationOutbox, Order, Payment, PaymentMethod
from unittest.mock import patch
from orders.services import CartValidationError, create_order_from_cart


class OrderFlowTests(TestCase):
    def setUp(self):
        self.category = ProductCategory.objects.create(name="居家", slug="home", english_name="HOME")
        self.product = Product.objects.create(category=self.category, name="陶杯", slug="cup", sku="CUP-1", description="desc", price=Decimal("1280"), stock=3, is_published=True)
        self.request = SimpleNamespace(session={})
        self.cart = Cart(self.request)
        self.cleaned = {"idempotency_key": "token-1", "customer_name": "王小明", "phone": "0900", "email": "buyer@example.com", "shipping_information": "台灣 任意地域", "customer_note": ""}

    def create_order(self, quantity=2):
        self.cart.add(self.product, quantity)
        return create_order_from_cart(cart=self.cart, cleaned_data=self.cleaned)[0]

    def test_cart_add_update_remove_and_db_price(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 2, "price": 1})
        response = self.client.get(reverse("orders:cart"))
        self.assertContains(response, "NT$ 2,560")
        self.client.post(reverse("orders:cart_update", args=[self.product.pk]), {"quantity": 1})
        self.assertContains(self.client.get(reverse("orders:cart")), "NT$ 1,280")
        self.client.post(reverse("orders:cart_update", args=[self.product.pk]), {"action": "remove"})
        self.assertContains(self.client.get(reverse("orders:cart")), "購物車目前是空的")

    def test_cart_rejects_stock_overage(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 9})
        self.assertNotIn(str(self.product.pk), self.client.session.get("cart", {}))

    def test_order_snapshots_price_and_reserves_stock(self):
        order = self.create_order()
        item = order.items.get()
        self.product.refresh_from_db()
        self.assertEqual(order.subtotal, Decimal("2560"))
        self.assertIsNone(order.shipping_fee)
        self.assertIsNone(order.final_total)
        self.assertEqual(item.unit_price_snapshot, Decimal("1280"))
        self.assertEqual(item.product_name_snapshot, "陶杯")
        self.assertEqual(self.product.stock, 1)
        self.product.price = Decimal("9999")
        self.product.save()
        item.refresh_from_db()
        self.assertEqual(item.line_total, Decimal("2560"))

    def test_order_number_is_public_unique_and_readable(self):
        order = self.create_order(1)
        self.assertRegex(order.public_number, r"^RF-\d{8}-[A-F0-9]{6}$")
        self.assertNotEqual(order.public_number, str(order.pk))

    def test_shipping_fee_calculates_final_total_from_snapshot(self):
        order = self.create_order(1)
        self.product.price = Decimal("9999")
        self.product.save()
        order.shipping_fee = Decimal("220")
        order.save()
        self.assertEqual(order.final_total, Decimal("1500"))

    def test_idempotency_prevents_duplicate_order_and_stock_deduction(self):
        self.cart.add(self.product, 1)
        first, created = create_order_from_cart(cart=self.cart, cleaned_data=self.cleaned)
        second, created_again = create_order_from_cart(cart=self.cart, cleaned_data=self.cleaned)
        self.product.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.product.stock, 2)

    def test_server_rechecks_stock_during_order(self):
        self.cart.add(self.product, 3)
        self.product.stock = 1
        self.product.save()
        with self.assertRaises(CartValidationError):
            create_order_from_cart(cart=self.cart, cleaned_data=self.cleaned)
        self.assertEqual(Order.objects.count(), 0)

    def test_cancel_restores_inventory_once(self):
        order = self.create_order(2)
        order.status = Order.Status.CANCELLED
        order.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        order.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_preorder_does_not_decrement_normal_stock(self):
        self.product.is_preorder = True
        self.product.stock = 0
        self.product.preorder_limit = 10
        self.product.preorder_delivery_estimate = "2026年10月"
        self.product.save()
        self.create_order(5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)

    def test_payment_requires_final_total_and_confirmation_marks_paid(self):
        order = self.create_order(1)
        method = PaymentMethod.objects.create(code="bank_transfer", display_name="銀行轉帳", enabled=True)
        payment = Payment(order=order, method=method, amount=Decimal("1280"), status=Payment.Status.CONFIRMED)
        with self.assertRaises(ValidationError):
            payment.full_clean()
        order.shipping_fee = Decimal("100")
        order.status = Order.Status.AWAITING_PAYMENT
        order.save()
        payment.amount = order.final_total
        payment.full_clean()
        payment.save()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertIsNotNone(order.paid_at)

    @override_settings(
        LINE_LOGIN_CHANNEL_ID="login-id", LINE_LOGIN_CHANNEL_SECRET="login-secret",
        LINE_LOGIN_CALLBACK_URL="https://restfull-xhex.onrender.com/auth/line/callback/",
        LINE_MESSAGING_CHANNEL_ACCESS_TOKEN="message-token", LINE_MESSAGING_CHANNEL_SECRET="message-secret",
        LINE_OFFICIAL_ACCOUNT_BASIC_ID="@rfull",
    )
    def test_checkout_double_post_uses_one_order(self):
        SiteSettings.objects.create(checkout_enabled=True, line_official_url="https://line.me/R/test")
        customer = LineCustomer.objects.create(line_user_id="U-test", display_name="王小明", is_friend=True)
        session = self.client.session
        session["line_customer_id"] = customer.pk
        session["line_friend_checked_at"] = time.time()
        session["line_friend_verified"] = True
        session.save()
        self.client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        response = self.client.get(reverse("orders:checkout"))
        token = self.client.session["checkout_token"]
        data = {**self.cleaned, "idempotency_key": token, "website": "", "policies_accepted": "on"}
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(reverse("orders:checkout"), data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(NotificationOutbox.objects.count(), 2)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("orders:cart_add", args=[self.product.slug]), {"quantity": 1})
        self.assertEqual(response.status_code, 403)
