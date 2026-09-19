import csv
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage


class ProductAdminExportTests(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "export-admin", "export@example.com", "password"
        )
        cls.category = ProductCategory.objects.create(
            name="茶道茶品", slug="export-tea"
        )
        cls.draft = Product.objects.create(
            category=cls.category,
            name="茶碗，白",
            sku="DRAFT-A57508095B9D",
            description="",
            short_description="短い説明",
            subcategory="茶碗",
            maker="靜院",
            series="白系",
            price=Decimal(1280),
            stock=3,
        )
        cls.first_image = ProductImage.objects.create(
            product=cls.draft,
            image="products/export-first.jpg",
            sort_order=20,
            is_primary=True,
        )
        cls.second_image = ProductImage.objects.create(
            product=cls.draft,
            image="products/export-second.jpg",
            sort_order=10,
        )
        ProductImage.objects.create(
            product=cls.draft,
            image="products/export-pending.jpg",
            sort_order=0,
            upload_status=ProductImage.UploadStatus.PENDING,
        )
        cls.complete = Product.objects.create(
            category=cls.category,
            name="完成商品",
            sku="SKU-002",
            description="一行目\n二行目",
            price=Decimal(2500),
            stock=8,
            is_published=True,
        )
        ProductImage.objects.create(
            product=cls.complete,
            image="products/export-complete.jpg",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin:catalog_product_changelist")

    def export(self, *products, client=None):
        return (client or self.client).post(
            self.url,
            {
                "action": "export_product_data",
                "_selected_action": [str(product.pk) for product in products],
                "index": "0",
            },
        )

    @staticmethod
    def rows(response):
        text = response.content.decode("utf-8-sig")
        return list(csv.DictReader(StringIO(text)))

    def test_action_returns_csv_response(self):
        response = self.export(self.draft)
        self.assertEqual(response.status_code, 200)
        self.assertIn("product_id", response.content.decode("utf-8-sig"))

    def test_content_type_is_csv(self):
        response = self.export(self.draft)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")

    def test_content_disposition_has_expected_filename(self):
        disposition = self.export(self.draft)["Content-Disposition"]
        self.assertRegex(
            disposition,
            r'^attachment; filename="product_data_\d{8}_\d{6}\.csv"$',
        )

    def test_filename_has_no_internal_service_wording(self):
        filename = self.export(self.draft)["Content-Disposition"].lower()
        self.assertNotIn("chatgpt", filename)
        self.assertNotIn("gpt", filename)
        self.assertNotIn("ai", filename)

    def test_csv_starts_with_utf8_bom(self):
        self.assertTrue(self.export(self.draft).content.startswith(b"\xef\xbb\xbf"))

    def test_product_id_is_exported(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["product_id"], str(self.draft.pk))

    def test_product_name_is_exported(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["name"], self.draft.name)

    def test_category_name_is_exported(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["category"], self.category.name)

    def test_price_and_stock_are_exported(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["price"], "1280")
        self.assertEqual(row["stock"], "3")

    def test_draft_sku_needs_sku(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["current_sku"], "DRAFT-A57508095B9D")
        self.assertEqual(row["needs_sku"], "true")

    def test_formal_sku_does_not_need_sku(self):
        row = self.rows(self.export(self.complete))[0]
        self.assertEqual(row["needs_sku"], "false")

    def test_empty_description_needs_description(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(row["needs_description"], "true")

    def test_existing_description_does_not_need_description(self):
        row = self.rows(self.export(self.complete))[0]
        self.assertEqual(row["needs_description"], "false")
        self.assertEqual(row["current_description"], "一行目\n二行目")

    def test_multiple_selected_products_are_exported(self):
        rows = self.rows(self.export(self.draft, self.complete))
        self.assertEqual(
            {row["product_id"] for row in rows},
            {str(self.draft.pk), str(self.complete.pk)},
        )

    def test_primary_image_url_is_absolute(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(
            row["primary_image_url"],
            "http://testserver/media/products/export-first.jpg",
        )

    def test_image_urls_follow_ordered_images_and_exclude_pending(self):
        row = self.rows(self.export(self.draft))[0]
        self.assertEqual(
            row["image_urls"],
            "http://testserver/media/products/export-first.jpg"
            " | http://testserver/media/products/export-second.jpg",
        )
        self.assertNotIn("pending", row["image_urls"])

    def test_product_without_image_exports_without_error(self):
        product = Product.objects.create(name="無圖片")
        response = self.export(product)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows(response)[0]["primary_image_url"], "")

    def test_draft_sku_and_empty_description_are_blockers(self):
        blockers = self.rows(self.export(self.draft))[0]["publication_blockers"]
        self.assertIn("SKU", blockers)
        self.assertIn("商品說明", blockers)

    def test_missing_category_is_blocker(self):
        product = Product.objects.create(name="無分類", sku="SKU-003", description="ok", price=1)
        blockers = self.rows(self.export(product))[0]["publication_blockers"]
        self.assertIn("商品分類", blockers)

    def test_missing_image_is_blocker(self):
        product = Product.objects.create(
            category=self.category, name="無圖片", sku="SKU-004", description="ok", price=1
        )
        blockers = self.rows(self.export(product))[0]["publication_blockers"]
        self.assertIn("商品圖片", blockers)

    def test_preorder_blockers_match_product_clean_requirements(self):
        product = Product.objects.create(
            category=self.category,
            name="預購商品",
            sku="SKU-005",
            description="ok",
            price=1,
            is_preorder=True,
        )
        ProductImage.objects.create(product=product, image="products/preorder.jpg")
        blockers = self.rows(self.export(product))[0]["publication_blockers"]
        self.assertIn("預購上限", blockers)
        self.assertIn("預計交付時間", blockers)

    def test_export_does_not_change_product_values(self):
        before = Product.objects.filter(pk=self.draft.pk).values().get()
        self.export(self.draft)
        after = Product.objects.filter(pk=self.draft.pk).values().get()
        self.assertEqual(after, before)

    def test_export_does_not_change_updated_at(self):
        before = Product.objects.get(pk=self.draft.pk).updated_at
        self.export(self.draft)
        self.assertEqual(Product.objects.get(pk=self.draft.pk).updated_at, before)

    def test_export_does_not_change_product_images(self):
        before = list(
            ProductImage.objects.filter(product=self.draft).order_by("pk").values()
        )
        self.export(self.draft)
        after = list(
            ProductImage.objects.filter(product=self.draft).order_by("pk").values()
        )
        self.assertEqual(after, before)

    def test_user_without_product_permission_cannot_export(self):
        user = get_user_model().objects.create_user(
            "no-product-permission", password="password", is_staff=True
        )
        client = Client()
        client.force_login(user)
        response = self.export(self.draft, client=client)
        self.assertEqual(response.status_code, 403)

    def test_view_permission_allows_export(self):
        user = get_user_model().objects.create_user(
            "product-viewer", password="password", is_staff=True
        )
        user.user_permissions.add(Permission.objects.get(codename="view_product"))
        client = Client()
        client.force_login(user)
        response = self.export(self.draft, client=client)
        self.assertEqual(response.status_code, 200)
