from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage, ProductSpecification
from PIL import Image


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = ProductCategory.objects.create(name="茶道茶品", slug="tea", english_name="TEA", description="茶器", subcategories=["茶器"])
        cls.product = Product.objects.create(category=cls.category, name="測試茶杯", slug="test-cup", sku="TEST-001", description="安靜的陶杯", price=Decimal("1280"), stock=3, is_published=True)
        ProductImage.objects.create(product=cls.product, image="products/test-cup.jpg", alt_text="測試茶杯")
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

    def test_incomplete_product_can_be_saved_as_draft(self):
        draft = Product()

        draft.full_clean()
        draft.save()

        self.assertFalse(draft.is_published)
        self.assertTrue(draft.slug.startswith("draft-"))
        self.assertTrue(draft.sku.startswith("DRAFT-"))
        self.assertEqual(str(draft), f"未完成商品 #{draft.pk}")

    def test_draft_accepts_images_and_can_be_completed_later(self):
        draft = Product.objects.create()
        image = ProductImage.objects.create(
            product=draft,
            image="products/draft-photo.jpg",
        )

        draft.name = "明式大頭佛"
        draft.category = self.category
        draft.description = "尺寸 33×21cm"
        draft.price = Decimal("3300")
        draft.stock = 1
        draft.sku = "BUDDHA-001"
        draft.full_clean()
        draft.save()

        image.refresh_from_db()
        self.assertEqual(image.alt_text, "商品照片")
        self.assertEqual(draft.name, "明式大頭佛")
        self.assertFalse(draft.is_published)
        self.assertEqual(draft.images.get().pk, image.pk)

    def test_incomplete_product_cannot_be_published(self):
        draft = Product.objects.create()
        draft.is_published = True

        with self.assertRaises(ValidationError) as error:
            draft.full_clean()

        messages = error.exception.message_dict
        self.assertIn("商品尚未完成，請先填寫商品名稱。", messages["name"])
        self.assertIn("公開商品前請設定價格。", messages["price"])
        self.assertIn("公開商品前請至少上傳一張商品照片。", messages["__all__"])

    def test_complete_product_can_be_published(self):
        draft = Product.objects.create()
        image = ProductImage.objects.create(product=draft, image="products/buddha.jpg")
        draft.category = self.category
        draft.name = "明式大頭佛"
        draft.sku = "BUDDHA-002"
        draft.description = "尺寸 33×21cm"
        draft.price = Decimal("3300")
        draft.is_published = True

        draft.full_clean()
        draft.save()

        self.assertEqual(Product.objects.published().get(pk=draft.pk), draft)
        self.assertEqual(draft.images.get().pk, image.pk)
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 200)

    def test_incomplete_published_flag_never_exposes_product(self):
        draft = Product.objects.create()
        Product.objects.filter(pk=draft.pk).update(is_published=True)

        self.assertFalse(Product.objects.published().filter(pk=draft.pk).exists())
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)

    def test_existing_published_slug_is_never_rewritten(self):
        legacy = Product.objects.create(
            category=self.category,
            name="既有商品",
            slug="legacy-public-url",
            sku="LEGACY-001",
            description="既有說明",
            price=Decimal("1000"),
            is_published=True,
        )
        ProductImage.objects.create(
            product=legacy,
            image="products/legacy.jpg",
            alt_text="既有商品",
        )
        Product.objects.filter(pk=legacy.pk).update(slug="draft-legacy-public-url")
        legacy.refresh_from_db()

        legacy.name = "既有商品（更新）"
        legacy.save()

        legacy.refresh_from_db()
        self.assertEqual(legacy.slug, "draft-legacy-public-url")

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


class AdminProductDraftWorkflowTests(TestCase):
    def setUp(self):
        self.media_root = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_root.cleanup)
        self.user = get_user_model().objects.create_superuser(
            "catalog-admin", "catalog@example.com", "strong-test-password"
        )
        self.client.force_login(self.user)
        self.category = ProductCategory.objects.create(name="佛像", slug="buddha")

    @staticmethod
    def image_upload(name, color):
        data = BytesIO()
        Image.new("RGB", (20, 20), color).save(data, format="JPEG")
        return SimpleUploadedFile(name, data.getvalue(), content_type="image/jpeg")

    @staticmethod
    def base_payload():
        return {
            "category": "",
            "name": "",
            "slug": "",
            "sku": "",
            "maker": "",
            "series": "",
            "subcategory": "",
            "short_description": "",
            "description": "",
            "care": "",
            "shipping_note": "",
            "maker_story": "",
            "price": "",
            "stock": "",
            "sale_starts_at_0": "",
            "sale_starts_at_1": "",
            "sale_ends_at_0": "",
            "sale_ends_at_1": "",
            "preorder_note": "",
            "preorder_limit": "",
            "preorder_delivery_estimate": "",
            "badge_label": "",
            "sort_order": "0",
            "image_label": "",
            "tone": "linen",
            "images-TOTAL_FORMS": "2",
            "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "1000",
            "images-0-alt_text": "",
            "images-0-sort_order": "1",
            "images-0-is_primary": "on",
            "images-1-alt_text": "",
            "images-1-sort_order": "2",
            "specifications-TOTAL_FORMS": "1",
            "specifications-INITIAL_FORMS": "0",
            "specifications-MIN_NUM_FORMS": "0",
            "specifications-MAX_NUM_FORMS": "1000",
            "specifications-0-label": "",
            "specifications-0-value": "",
            "specifications-0-sort_order": "0",
            "_save": "儲存",
        }

    def test_admin_can_upload_photos_first_then_complete_and_publish(self):
        add_page = self.client.get(reverse("admin:catalog_product_add"))
        self.assertContains(add_page, "data-product-image-manager")
        self.assertContains(add_page, "multiple data-image-input")
        self.assertContains(add_page, "新增商品圖片")
        self.assertContains(add_page, "一次最多可選擇 10 張圖片")
        self.assertContains(add_page, "第一張會作為主要圖片")
        self.assertNotContains(add_page, "商品画像")
        self.assertNotContains(add_page, "商品画像を追加")
        self.assertNotContains(add_page, 'name="images-0-image"')
        self.assertNotContains(add_page, 'enctype="multipart/form-data"')
        upload_session = add_page.context["product_image_config"]["uploadSession"]
        first = ProductImage.objects.create(
            image="products/2026/09/11111111111111111111111111111111.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            upload_session=upload_session,
            uploaded_by=self.user,
            sort_order=0,
        )
        second = ProductImage.objects.create(
            image="products/2026/09/22222222222222222222222222222222.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            upload_session=upload_session,
            uploaded_by=self.user,
            sort_order=1,
        )
        payload = self.base_payload()
        payload.update({
            "product_image_session": upload_session,
            "product_image_order": f"{first.pk},{second.pk}",
        })

        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 302)
        product = Product.objects.get()
        original_image_ids = list(product.images.order_by("pk").values_list("pk", flat=True))
        self.assertFalse(product.is_published)
        self.assertEqual(len(original_image_ids), 2)
        self.assertTrue(product.slug.startswith("draft-"))
        self.assertContains(
            self.client.get(reverse("admin:catalog_product_change", args=[product.pk])),
            "可先上傳商品照片並儲存為草稿",
        )

        payload = self.base_payload()
        change_page = self.client.get(reverse("admin:catalog_product_change", args=[product.pk]))
        change_session = change_page.context["product_image_config"]["uploadSession"]
        payload.update({
            "category": str(self.category.pk),
            "name": "明式大頭佛",
            "slug": product.slug,
            "sku": "BUDDHA-ADMIN-001",
            "description": "明式大頭佛，尺寸 33×21cm。",
            "price": "3300",
            "stock": "1",
            "is_published": "on",
            "product_image_session": change_session,
            "product_image_order": ",".join(str(value) for value in original_image_ids),
        })

        response = self.client.post(
            reverse("admin:catalog_product_change", args=[product.pk]), payload
        )

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertTrue(product.is_published)
        self.assertEqual(product.name, "明式大頭佛")
        self.assertEqual(product.price, Decimal("3300"))
        self.assertEqual(
            list(product.images.order_by("pk").values_list("pk", flat=True)),
            original_image_ids,
        )
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 200)
