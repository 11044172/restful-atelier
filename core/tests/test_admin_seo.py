from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage
from content.models import InteriorProject, PolicyPage, Publication
from core.models import SiteSettings
from inquiries.models import Inquiry, InquiryCategory
from orders.models import Order


class SeoAndAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("admin", "admin@example.com", "strong-test-password")
        cls.site = SiteSettings.objects.create()
        cls.category = ProductCategory.objects.create(name="居家", slug="home")
        cls.product = Product.objects.create(category=cls.category, name="商品", slug="product", sku="SKU", description="desc", price=100, stock=1, is_published=True)
        ProductImage.objects.create(product=cls.product, alt_text="商品画像", is_primary=True)
        cls.project = InteriorProject.objects.create(title="作品", slug="project", description="desc", published=True)
        cls.publication = Publication.objects.create(issue_number="1", title="刊物", slug="publication", published=True)
        cls.inquiry_category = InquiryCategory.objects.create(display_name="購物", slug="shop", recipient_email="shop@example.com")
        cls.inquiry = Inquiry.objects.create(category=cls.inquiry_category, name="顧客", phone="1", email="a@example.com", message="m", privacy_agreed=True)
        cls.order = Order.objects.create(idempotency_key="admin-order", customer_name="顧客", phone="1", email="a@example.com", shipping_information="address", subtotal=100)

    def test_noindex_by_default_and_dynamic_robots(self):
        self.assertContains(self.client.get(self.product.get_absolute_url()), "noindex,nofollow")
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /")

    @override_settings(SEO_INDEX_ENABLED=True)
    def test_production_indexing_and_sitemap(self):
        self.assertContains(self.client.get(self.product.get_absolute_url()), "index,follow")
        robots = self.client.get("/robots.txt")
        self.assertContains(robots, "Sitemap: https://restfull.com/sitemap.xml")
        sitemap = self.client.get("/sitemap.xml")
        self.assertContains(sitemap, self.product.get_absolute_url())
        self.assertContains(sitemap, self.project.get_absolute_url())

    def test_admin_operational_pages_load(self):
        self.client.force_login(self.user)
        for url in (
            reverse("admin:catalog_product_changelist"), reverse("admin:catalog_product_change", args=[self.product.pk]),
            reverse("admin:orders_order_changelist"), reverse("admin:orders_order_change", args=[self.order.pk]),
            reverse("admin:inquiries_inquiry_changelist"), reverse("admin:core_sitesettings_change", args=[self.site.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_launch_readiness_reports_missing_real_configuration(self):
        with self.assertRaises(CommandError):
            call_command("check_launch_readiness")

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["www.restfull.com", "restfull.com"])
    def test_www_redirects_to_canonical_apex(self):
        response = self.client.get("/about/?source=test", HTTP_HOST="www.restfull.com", secure=True)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://restfull.com/about/?source=test")
