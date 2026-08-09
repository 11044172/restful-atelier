from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage, ProductSpecification


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="茶道茶品", slug="tea", english_name="TEA", description="茶器", subcategories=["茶器"])
        cls.product = Product.objects.create(category=cls.category, name="測試茶杯", slug="test-cup", sku="TEST-001", description="安靜的陶杯", price=Decimal("1280"), stock=3, is_published=True)
        ProductSpecification.objects.create(product=cls.product, label="材質", value="粗陶")

    def test_decimal_price_and_stock_label(self):
        self.assertIsInstance(self.product.price, Decimal)
        self.product.stock = 1
        self.assertEqual(self.product.stock_label, "僅剩 1 件")

    def test_search_uses_name_category_description_and_specification(self):
        for query in ("測試", "茶道", "安靜", "粗陶"):
            response = self.client.get(reverse("catalog:search"), {"q": query})
            self.assertContains(response, self.product.name)

    def test_unpublished_product_is_not_public(self):
        self.product.is_published = False
        self.product.save()
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)

    def test_invalid_image_is_rejected(self):
        image = ProductImage(product=self.product, alt_text="broken", image=SimpleUploadedFile("bad.jpg", b"not-an-image", content_type="image/jpeg"))
        with self.assertRaises(ValidationError):
            image.full_clean()
