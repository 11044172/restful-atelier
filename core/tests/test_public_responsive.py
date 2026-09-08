from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage
from inquiries.forms import InquiryForm
from orders.forms import CheckoutForm
from orders.models import Order


class PublicResponsiveMarkupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = ProductCategory.objects.create(
            name="居家生活",
            slug="home",
            is_active=True,
        )
        cls.product = Product.objects.create(
            category=category,
            name="很長也不應撐破版面的商品名稱",
            slug="responsive-product",
            sku="RESPONSIVE-001",
            description="用於確認行動版商品圖片與版面。",
            price=1280,
            stock=10,
            is_published=True,
        )
        for index in range(10):
            ProductImage.objects.create(
                product=cls.product,
                image=f"products/responsive-{index}.jpg",
                alt_text=f"商品圖片 {index + 1}",
                sort_order=index,
                is_primary=index == 0,
                upload_status=ProductImage.UploadStatus.ATTACHED,
            )

    def test_shop_mobile_menu_exposes_product_search(self):
        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, 'aria-label="行動版商品選單"')
        self.assertContains(
            response,
            f'href="{reverse("catalog:search")}"><small>搜尋</small>搜尋商品',
        )

    def test_product_detail_supports_ten_thumbnails(self):
        response = self.client.get(self.product.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-gallery-thumb", count=10)
        self.assertNotContains(response, "入金確認後")
        self.assertContains(response, "確認付款後")

    def test_order_complete_page_keeps_the_session_guarded_flow(self):
        order = Order.objects.create(
            idempotency_key="responsive-complete-page",
            customer_name="測試顧客",
            phone="0900000000",
            email="buyer@example.com",
            shipping_information="測試配送地址",
            subtotal=Decimal("1280"),
        )
        session = self.client.session
        session["completed_order"] = order.public_number
        session.save()

        response = self.client.get(
            reverse("orders:complete", args=[order.public_number])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "訂單已收到")
        self.assertContains(response, order.public_number)

    @override_settings(TURNSTILE_SITE_KEY="test-site-key")
    def test_public_forms_use_official_flexible_turnstile_size(self):
        contact_response = self.client.get(reverse("inquiries:contact"))
        turnstile_markup = (
            'class="cf-turnstile" data-sitekey="test-site-key" '
            'data-size="flexible" data-language="zh-TW"'
        )

        self.assertEqual(contact_response.status_code, 200)
        self.assertContains(contact_response, turnstile_markup)
        checkout_template = (
            Path(settings.BASE_DIR) / "templates/orders/checkout.html"
        ).read_text()
        self.assertIn('data-size="flexible" data-language="zh-TW"', checkout_template)

    def test_responsive_styles_cover_navigation_gallery_cart_and_ios_forms(self):
        global_css = (Path(settings.BASE_DIR) / "src/styles/global.css").read_text()
        app_css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text()

        self.assertNotIn("body { margin: 0; min-width: 320px; overflow-x: hidden", global_css)
        self.assertIn("overflow-y: auto; overscroll-behavior: contain", global_css)
        self.assertIn(".product-thumbnails button { flex: 0 0 78px", global_css)
        self.assertIn("@media (max-width: 420px)", global_css)
        self.assertIn(".cart-image { grid-column: 1 / span 4", global_css)
        self.assertIn(".search-close", global_css)
        self.assertIn("min-height: 44px", global_css)
        self.assertIn(".search-panel input{font-size:16px}", app_css)
        self.assertIn(".cf-turnstile{width:100%;max-width:100%", app_css)

    def test_honeypot_errors_are_localized(self):
        inquiry_form = InquiryForm()
        inquiry_form.cleaned_data = {"website": "bot"}
        checkout_form = CheckoutForm()
        checkout_form.cleaned_data = {"website": "bot"}

        for form in (inquiry_form, checkout_form):
            with self.assertRaisesMessage(ValidationError, "送出內容無效，請重新操作。"):
                form.clean_website()
