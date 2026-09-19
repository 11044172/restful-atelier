import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage
from catalog.views import shop
from core.direct_image_uploads import TOKEN_SALT
from core.models import DirectImageUpload


class CategoryVisualPublicTests(TestCase):
    def category(self, slug, **values):
        return ProductCategory.objects.create(
            name=values.pop("name", slug.title()),
            slug=slug,
            english_name=values.pop("english_name", slug.upper()),
            **values,
        )

    def product(self, category, index, *, published=True, status="attached"):
        product = Product.objects.create(
            category=category,
            name=f"Product {index}",
            slug=f"{category.slug}-product-{index}",
            sku=f"{category.slug.upper()}-{index}",
            description="Description",
            price=Decimal("1000"),
            is_published=published,
            sort_order=index,
        )
        ProductImage.objects.create(
            product=product,
            image=f"products/{category.slug}-{index}.jpg",
            thumbnail=f"products/thumbnails/{category.slug}-{index}.webp",
            upload_status=status,
            sort_order=0,
            is_primary=status == ProductImage.UploadStatus.ATTACHED,
        )
        return product

    def test_dedicated_thumbnail_has_priority_and_uses_focus_and_alt(self):
        category = self.category(
            "dedicated",
            name="專用圖分類",
            thumbnail_image="catalog/categories/dedicated.jpg",
            thumbnail_alt="專用的分類圖片",
            thumbnail_focus_x=25,
            thumbnail_focus_y=70,
        )
        self.product(category, 1)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-card__visual--dedicated")
        self.assertContains(response, "catalog/categories/dedicated.jpg")
        self.assertContains(response, 'alt="專用的分類圖片"')
        self.assertContains(response, "object-position:25% 70%")
        self.assertNotContains(response, "category-collage")

    def test_three_published_products_render_three_distinct_primary_images(self):
        category = self.category("three")
        for index in range(1, 5):
            self.product(category, index)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-collage--three")
        for index in range(1, 4):
            self.assertContains(response, f"three-{index}.webp", count=2)
        self.assertContains(response, "three-4.webp", count=1)
        self.assertEqual(response.content.count(b'class="category-collage__item"'), 3)

    def test_two_published_products_render_asymmetric_two_image_collage(self):
        category = self.category("two")
        self.product(category, 1)
        self.product(category, 2)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-collage--two")
        self.assertEqual(response.content.count(b'class="category-collage__item"'), 2)

    def test_one_published_product_renders_full_card_image(self):
        category = self.category("one")
        self.product(category, 1)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-collage--one")
        self.assertEqual(response.content.count(b'class="category-collage__item"'), 1)

    def test_no_products_uses_existing_placeholder(self):
        self.category("empty", english_name="EMPTY CATEGORY")

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-card__visual--placeholder")
        self.assertContains(response, "RESTFULL ATELIER")
        self.assertContains(response, "EMPTY CATEGORY")

    def test_unpublished_products_are_not_used(self):
        category = self.category("private")
        self.product(category, 1, published=False)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-card__visual--placeholder")
        self.assertNotContains(response, "private-1.webp")
        self.assertContains(response, "0 件商品")

    def test_products_without_attached_images_are_not_used(self):
        category = self.category("temporary")
        self.product(
            category,
            1,
            status=ProductImage.UploadStatus.TEMPORARY,
        )

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-card__visual--placeholder")
        self.assertNotContains(response, "temporary-1.webp")

    def test_thumbnail_alt_falls_back_to_category_name(self):
        self.category(
            "fallback-alt",
            name="自然替代文字",
            thumbnail_image="catalog/categories/fallback.jpg",
        )

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, 'alt="自然替代文字"')

    def test_collage_images_are_decorative(self):
        category = self.category("decorative")
        self.product(category, 1)

        response = self.client.get(reverse("catalog:shop"))

        self.assertContains(response, "category-collage--one")
        self.assertContains(response, 'aria-hidden="true"')
        self.assertContains(response, 'alt="" loading="lazy"')


class CategoryVisualQueryTests(TestCase):
    def test_category_count_does_not_change_shop_query_count(self):
        category = ProductCategory.objects.create(name="Primary", slug="primary")
        product = Product.objects.create(
            category=category,
            name="Primary product",
            slug="primary-product",
            sku="PRIMARY-1",
            description="Description",
            price=Decimal("1000"),
            is_published=True,
        )
        ProductImage.objects.create(product=product, image="products/primary.jpg")
        for index in range(12):
            ProductCategory.objects.create(name=f"Category {index}", slug=f"category-{index}")
        request = RequestFactory().get(reverse("catalog:shop"))

        with patch("catalog.views.render", return_value=Mock()) as mocked_render:
            with self.assertNumQueries(3):
                shop(request)
        context = mocked_render.call_args.args[2]
        with self.assertNumQueries(0):
            html = render_to_string("catalog/shop.html", context)
        self.assertIn("category-collage--one", html)


class CategoryThumbnailModelTests(TestCase):
    def test_focus_position_and_validation(self):
        category = ProductCategory(
            name="Focus",
            slug="focus",
            thumbnail_focus_x=0,
            thumbnail_focus_y=100,
        )
        category.full_clean()
        self.assertEqual(category.thumbnail_position, "0% 100%")

        category.thumbnail_focus_x = 101
        with self.assertRaises(ValidationError):
            category.full_clean()


class CategoryThumbnailAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "category-admin", "category@example.com", "password"
        )
        self.client.force_login(self.user)
        self.category = ProductCategory.objects.create(
            name="茶器", slug="tea-tools", english_name="TEA TOOLS"
        )

    def ready_upload(self):
        key = "catalog/categories/2026/09/category.jpg"
        upload = DirectImageUpload.objects.create(
            object_key=key,
            category="catalog.category_thumbnail",
            original_filename="category.jpg",
            content_type="image/jpeg",
            file_size=1234,
            width=1200,
            height=900,
            uploaded_by=self.user,
            status=DirectImageUpload.Status.READY,
        )
        token = signing.dumps(
            {"id": upload.pk, "key": key, "category": upload.category},
            salt=TOKEN_SALT,
            compress=True,
        )
        return upload, f"upload:{token}"

    def payload(self, image_value):
        return {
            "name": self.category.name,
            "slug": self.category.slug,
            "english_name": self.category.english_name,
            "description": "",
            "thumbnail_image": image_value,
            "thumbnail_alt": "分類專用圖",
            "thumbnail_focus_x": "20",
            "thumbnail_focus_y": "65",
            "subcategories": json.dumps([], ensure_ascii=False),
            "tone": "linen",
            "sort_order": "0",
            "is_active": "on",
            "_save": "儲存",
        }

    def test_admin_uses_direct_image_widget_with_focal_preview(self):
        response = self.client.get(
            reverse("admin:catalog_productcategory_change", args=[self.category.pk])
        )

        self.assertContains(response, 'data-category="catalog.category_thumbnail"')
        self.assertContains(response, 'data-focal-point="true"')
        self.assertContains(response, '商品分類卡片預覽')
        self.assertContains(response, 'type="hidden" name="thumbnail_focus_x"')
        self.assertContains(response, 'admin-image-manager.')

    @patch("core.direct_image_uploads._storage_client")
    def test_category_presign_uses_existing_direct_r2_flow(self, storage):
        r2 = Mock()
        r2.generate_presigned_url.return_value = "https://r2.example/put"
        storage.return_value = (r2, "bucket")

        response = self.client.post(
            reverse("admin:direct_image_presign"),
            json.dumps(
                {
                    "category": "catalog.category_thumbnail",
                    "filename": "category.jpg",
                    "content_type": "image/jpeg",
                    "size": 1234,
                    "width": 1200,
                    "height": 900,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["object_key"].startswith("catalog/categories/"))
        r2.generate_presigned_url.assert_called_once()

    def test_admin_save_marks_upload_attached(self):
        upload, token = self.ready_upload()

        response = self.client.post(
            reverse("admin:catalog_productcategory_change", args=[self.category.pk]),
            self.payload(token),
        )

        self.assertEqual(response.status_code, 302)
        upload.refresh_from_db()
        self.category.refresh_from_db()
        self.assertEqual(upload.status, DirectImageUpload.Status.ATTACHED)
        self.assertEqual(self.category.thumbnail_image.name, upload.object_key)
        self.assertEqual(self.category.thumbnail_position, "20% 65%")

    def test_removing_thumbnail_returns_to_automatic_collage(self):
        self.category.thumbnail_image = "catalog/categories/existing.jpg"
        self.category.save()
        product = Product.objects.create(
            category=self.category,
            name="Cup",
            slug="category-cup",
            sku="CATEGORY-CUP",
            description="Description",
            price=Decimal("1000"),
            is_published=True,
        )
        ProductImage.objects.create(product=product, image="products/category-cup.jpg")

        response = self.client.post(
            reverse("admin:catalog_productcategory_change", args=[self.category.pk]),
            self.payload(""),
        )

        self.assertEqual(response.status_code, 302)
        self.category.refresh_from_db()
        self.assertFalse(self.category.thumbnail_image)
        shop_response = self.client.get(reverse("catalog:shop"))
        self.assertContains(shop_response, "category-collage--one")
        self.assertContains(shop_response, "products/category-cup.jpg")


class CategoryThumbnailMigrationTests(TransactionTestCase):
    migrate_from = [("catalog", "0006_productimage_dimensions")]
    migrate_to = [("catalog", "0007_productcategory_thumbnail_alt_and_more")]

    def test_existing_categories_receive_safe_image_defaults(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldCategory = old_apps.get_model("catalog", "ProductCategory")
        category = OldCategory.objects.create(name="Legacy", slug="legacy")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewCategory = new_apps.get_model("catalog", "ProductCategory")
        migrated = NewCategory.objects.get(pk=category.pk)

        self.assertEqual(migrated.thumbnail_image.name, "")
        self.assertEqual(migrated.thumbnail_alt, "")
        self.assertEqual(
            (migrated.thumbnail_focus_x, migrated.thumbnail_focus_y),
            (50, 50),
        )
