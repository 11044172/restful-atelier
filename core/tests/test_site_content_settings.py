import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import Product, ProductCategory, ProductImage
from content.models import InteriorProject, Publication
from core.models import SiteSettings


class SiteContentSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = SiteSettings.objects.create(checkout_enabled=False)
        cls.category = ProductCategory.objects.create(
            name="居家生活",
            slug="living",
            is_active=True,
        )
        cls.product = Product.objects.create(
            category=cls.category,
            name="測試器物",
            slug="test-object",
            sku="TEST-OBJECT",
            description="商品說明",
            price=1200,
            stock=1,
            is_published=True,
        )
        ProductImage.objects.create(
            product=cls.product,
            image="products/test-object.webp",
            alt_text="測試器物圖片",
            is_primary=True,
        )
        cls.project = InteriorProject.objects.create(
            title="測試住宅",
            slug="test-home",
            description="作品說明",
            published=True,
        )
        cls.publication = Publication.objects.create(
            issue_number="ISSUE 01",
            title="測試刊物",
            slug="test-journal",
            description="刊物說明",
            published=True,
        )

    def test_home_hero_uses_existing_fallback_when_image_is_not_set(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A QUIET ROOM / RFULL")
        self.assertNotContains(response, "/media/site/home/")

    def test_home_hero_uses_configured_image_alt_and_position(self):
        self.site.home_hero_image = "site/home/custom-hero.webp"
        self.site.home_hero_alt = "客製首頁照片"
        self.site.home_hero_position = SiteSettings.ImagePosition.TOP
        self.site.save()

        response = self.client.get(reverse("core:home"))

        self.assertContains(response, '/media/site/home/custom-hero.webp')
        self.assertContains(response, 'alt="客製首頁照片"')
        self.assertContains(response, 'style="object-position:top"')
        self.assertContains(response, 'fetchpriority="high"')

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": {
                    "bucket_name": "test-site-content",
                    "custom_domain": "cdn.example.test",
                    "querystring_auth": False,
                },
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_site_image_urls_use_configured_r2_cdn_storage(self):
        self.site.home_hero_image = "site/home/r2-hero.webp"
        self.site.default_og_image = "site/seo/r2-og.webp"
        self.site.save()

        response = self.client.get(reverse("core:home"))

        self.assertContains(
            response,
            "https://cdn.example.test/site/home/r2-hero.webp",
        )
        self.assertContains(
            response,
            "https://cdn.example.test/site/seo/r2-og.webp",
            count=2,
        )

    def test_site_copy_settings_replace_template_defaults(self):
        self.site.home_hero_title = "管理畫面首頁標題"
        self.site.about_title = "管理畫面關於標題"
        self.site.footer_message = "管理畫面頁尾介紹"
        self.site.save()

        self.assertContains(self.client.get(reverse("core:home")), "管理畫面首頁標題")
        about = self.client.get(reverse("core:about"))
        self.assertContains(about, "管理畫面關於標題")
        self.assertContains(about, "管理畫面頁尾介紹")

    def test_major_public_pages_return_200(self):
        urls = (
            reverse("core:home"),
            reverse("core:about"),
            reverse("content:project_list"),
            self.project.get_absolute_url(),
            reverse("content:publication_list"),
            self.publication.get_absolute_url(),
            reverse("catalog:shop"),
            self.product.get_absolute_url(),
            reverse("inquiries:contact"),
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_existing_site_settings_receive_safe_defaults(self):
        self.site.refresh_from_db()

        self.assertEqual(self.site.home_hero_image.name, "")
        self.assertEqual(self.site.home_hero_title, "靜處安身")
        self.assertEqual(self.site.about_image.name, "")
        self.assertEqual(self.site.shop_hero_position, "center")

    def test_missing_existing_r2_image_does_not_block_settings_update(self):
        self.site.brand_logo = "site/branding/missing-logo.jpg"
        self.site.home_hero_title = "既存画像に影響されない更新"

        with patch.object(
            self.site.brand_logo.storage,
            "size",
            side_effect=AssertionError("stored image must not be re-opened"),
        ) as storage_size:
            self.site.full_clean()

        storage_size.assert_not_called()

    def test_new_site_image_upload_is_still_validated_and_saved(self):
        image_data = BytesIO()
        Image.new("RGB", (48, 32), "white").save(image_data, format="JPEG")
        self.site.home_hero_image = SimpleUploadedFile(
            "admin-hero.jpg",
            image_data.getvalue(),
            content_type="image/jpeg",
        )

        self.site.full_clean()

        self.assertFalse(self.site.home_hero_image._committed)

    def test_admin_does_not_accept_multipart_image_body(self):
        user = get_user_model().objects.create_superuser(
            "image-admin",
            "image-admin@example.com",
            "strong-test-password",
        )
        self.client.force_login(user)
        self.site.brand_logo = "site/branding/missing-logo.jpg"
        self.site.save(update_fields=["brand_logo"])

        image_data = BytesIO()
        Image.new("RGB", (48, 32), "white").save(image_data, format="JPEG")
        post_data = model_to_dict(
            self.site,
            exclude={
                "brand_logo",
                "shop_logo",
                "taiwan_pay_qr",
                "default_og_image",
                "home_hero_image",
                "shop_hero_image",
                "shop_story_image",
                "about_image",
                "checkout_enabled",
            },
        )
        post_data["home_hero_image"] = SimpleUploadedFile(
            "admin-hero.jpg",
            image_data.getvalue(),
            content_type="image/jpeg",
        )

        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ), patch.object(
            self.site.brand_logo.storage,
            "size",
            side_effect=AssertionError("stored image must not be re-opened"),
        ) as storage_size:
            response = self.client.post(
                reverse("admin:core_sitesettings_change", args=[self.site.pk]),
                post_data,
            )

        self.assertEqual(response.status_code, 302)
        storage_size.assert_not_called()
        self.site.refresh_from_db()
        self.assertEqual(self.site.home_hero_image.name, "")

    def test_site_settings_admin_exposes_grouped_content_fields(self):
        user = get_user_model().objects.create_superuser(
            "content-admin",
            "content-admin@example.com",
            "strong-test-password",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:core_sitesettings_change", args=[self.site.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "首頁 Hero 圖片")
        self.assertContains(response, "購物首頁 Hero 圖片")
        self.assertContains(response, "關於頁主要圖片")
        self.assertContains(response, "預設 OGP／社群分享圖片")
