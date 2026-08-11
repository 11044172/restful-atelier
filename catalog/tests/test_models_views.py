from decimal import Decimal
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage, ProductSpecification
from PIL import Image


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

    def assert_search_finds_product(self, query):
        response = self.client.get(reverse("catalog:search"), {"q": query})
        self.assertContains(response, self.product.name)

    def test_search_uses_product_name(self):
        self.assert_search_finds_product("測試")

    def test_search_uses_category_name(self):
        self.assert_search_finds_product("茶道")

    def test_search_uses_description(self):
        self.assert_search_finds_product("安靜")

    def test_search_uses_specification(self):
        self.assert_search_finds_product("粗陶")

    def test_unpublished_product_is_not_public(self):
        self.product.is_published = False
        self.product.save()
        self.assertEqual(self.client.get(self.product.get_absolute_url()).status_code, 404)

    def test_invalid_image_is_rejected(self):
        image = ProductImage(product=self.product, alt_text="broken", image=SimpleUploadedFile("bad.jpg", b"not-an-image", content_type="image/jpeg"))
        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_animated_and_extension_mismatch_images_are_rejected(self):
        frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
        animated = BytesIO()
        frames[0].save(animated, format="GIF", save_all=True, append_images=frames[1:])
        png = BytesIO()
        Image.new("RGB", (10, 10), "red").save(png, format="PNG")
        uploads = [
            SimpleUploadedFile("animated.gif", animated.getvalue(), content_type="image/gif"),
            SimpleUploadedFile("mismatch.jpg", png.getvalue(), content_type="image/jpeg"),
        ]
        for upload in uploads:
            with self.subTest(upload=upload.name):
                image = ProductImage(product=self.product, alt_text="unsafe", image=upload)
                with self.assertRaises(ValidationError):
                    image.full_clean()

    def test_oversized_dimensions_are_rejected(self):
        data = BytesIO()
        Image.new("RGB", (8001, 1), "white").save(data, format="PNG")
        image = ProductImage(product=self.product, alt_text="wide", image=SimpleUploadedFile("wide.png", data.getvalue(), content_type="image/png"))
        with self.assertRaises(ValidationError):
            image.full_clean()
