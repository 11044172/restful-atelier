import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import Product, ProductCategory, ProductImage


class ImportProductDataCommandTests(TestCase):
    headers = (
        "product_id",
        "name",
        "category",
        "subcategory",
        "maker",
        "series",
        "price",
        "stock",
        "is_preorder",
        "current_sku",
        "short_description",
        "current_description",
        "primary_image_url",
        "image_urls",
        "is_published",
        "needs_sku",
        "needs_description",
        "publication_blockers",
        "slug",
        "updated_at",
    )

    def setUp(self):
        self.category = ProductCategory.objects.create(name="家具", slug="furniture")
        self.product = Product.objects.create(
            category=self.category,
            name="既存商品",
            slug="existing-product",
            sku="OLD-SKU",
            short_description="既存短文",
            description="既存説明",
            price=Decimal(1200),
            stock=7,
            is_preorder=False,
            is_published=False,
        )
        self.image = ProductImage.objects.create(
            product=self.product,
            image="products/keep.jpg",
            thumbnail="products/thumbnails/keep.jpg",
            alt_text="保持する画像",
            sort_order=3,
            is_primary=True,
        )

    def csv_path(self, rows):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "products.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def row(self, **overrides):
        values = {field: "" for field in self.headers}
        values.update(
            {
                "product_id": self.product.pk,
                "name": "CSV商品名",
                "category": "CSV分類",
                "price": "999999",
                "stock": "0",
                "is_preorder": "true",
                "current_sku": "NEW-SKU",
                "short_description": "CSV短文",
                "current_description": "完成済み説明",
                "primary_image_url": "https://signed.example/primary",
                "image_urls": "https://signed.example/other",
                "is_published": "true",
                "slug": "csv-slug",
                "updated_at": "2099-01-01T00:00:00Z",
            }
        )
        values.update(overrides)
        return values

    def run_command(self, path, apply=False):
        stdout = StringIO()
        stderr = StringIO()
        call_command(
            "import_product_data",
            str(path),
            apply=apply,
            stdout=stdout,
            stderr=stderr,
        )
        return stdout.getvalue(), stderr.getvalue()

    def test_dry_run_identifies_by_product_id_without_changes(self):
        path = self.csv_path([self.row()])

        stdout, _ = self.run_command(path)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "OLD-SKU")
        self.assertEqual(self.product.description, "既存説明")
        self.assertIn("matched=1", stdout)
        self.assertIn("sku_changes=1", stdout)
        self.assertIn("description_changes=1", stdout)

    def test_apply_updates_only_sku_and_description(self):
        path = self.csv_path([self.row()])
        image_before = ProductImage.objects.values().get(pk=self.image.pk)

        self.run_command(path, apply=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "NEW-SKU")
        self.assertEqual(self.product.description, "完成済み説明")
        self.assertEqual(self.product.name, "既存商品")
        self.assertEqual(self.product.short_description, "既存短文")
        self.assertEqual(self.product.price, Decimal(1200))
        self.assertEqual(self.product.stock, 7)
        self.assertFalse(self.product.is_preorder)
        self.assertFalse(self.product.is_published)
        self.assertEqual(self.product.slug, "existing-product")
        self.assertEqual(
            ProductImage.objects.values().get(pk=self.image.pk), image_before
        )

    def test_blank_values_do_not_clear_existing_values(self):
        path = self.csv_path([self.row(current_sku="", current_description="")])

        stdout, stderr = self.run_command(path, apply=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "OLD-SKU")
        self.assertEqual(self.product.description, "既存説明")
        self.assertIn("blank current_sku skipped", stderr)
        self.assertIn("updated_products=0", stdout)

    def test_missing_product_is_not_created(self):
        path = self.csv_path([self.row(product_id=99999)])

        stdout, stderr = self.run_command(path, apply=True)

        self.assertEqual(Product.objects.count(), 1)
        self.assertIn("missing=1", stdout)
        self.assertIn("not found; no product was created", stderr)

    def test_test_values_are_skipped_but_safe_field_can_update(self):
        path = self.csv_path(
            [
                self.row(
                    current_sku="TEST-20260809-FUR-002",
                    current_description="完成済みの正規説明",
                )
            ]
        )

        _, stderr = self.run_command(path, apply=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "OLD-SKU")
        self.assertEqual(self.product.description, "完成済みの正規説明")
        self.assertIn("suspicious current_sku", stderr)

    def test_test_description_is_skipped(self):
        path = self.csv_path(
            [self.row(current_sku="NEW-SKU", current_description="確認用テスト説明")]
        )

        self.run_command(path, apply=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "NEW-SKU")
        self.assertEqual(self.product.description, "既存説明")

    def test_second_apply_is_idempotent(self):
        path = self.csv_path([self.row()])
        self.run_command(path, apply=True)

        stdout, _ = self.run_command(path, apply=True)

        self.assertIn("sku_changes=0", stdout)
        self.assertIn("description_changes=0", stdout)
        self.assertIn("updated_products=0", stdout)

    def test_duplicate_csv_sku_aborts_all_changes(self):
        other = Product.objects.create(
            category=self.category,
            name="別商品",
            slug="other-product",
            sku="OTHER-OLD",
            description="別の既存説明",
            price=Decimal(500),
        )
        path = self.csv_path(
            [
                self.row(),
                self.row(
                    product_id=other.pk,
                    current_sku="NEW-SKU",
                    current_description="別の完成済み説明",
                ),
            ]
        )

        with self.assertRaises(CommandError):
            self.run_command(path, apply=True)

        self.product.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.product.sku, "OLD-SKU")
        self.assertEqual(self.product.description, "既存説明")
        self.assertEqual(other.sku, "OTHER-OLD")
        self.assertEqual(other.description, "別の既存説明")

    def test_sku_owned_by_another_database_product_aborts(self):
        Product.objects.create(
            category=self.category,
            name="別商品",
            slug="other-product",
            sku="TAKEN-SKU",
            description="別の説明",
            price=Decimal(500),
        )
        path = self.csv_path([self.row(current_sku="TAKEN-SKU")])

        with self.assertRaises(CommandError):
            self.run_command(path, apply=True)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, "OLD-SKU")
        self.assertEqual(self.product.description, "既存説明")
