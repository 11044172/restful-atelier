from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory, ProductImage


class ProductAdminPublishTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "publish-admin", "publish@example.com", "password"
        )
        cls.category = ProductCategory.objects.create(
            name="一括公開分類", slug="bulk-publish"
        )
        cls.product = cls.create_publishable_product(
            name="公開可能商品", sku="BULK-PUBLISH-001"
        )

    @classmethod
    def create_publishable_product(cls, *, name, sku):
        product = Product.objects.create(
            category=cls.category,
            name=name,
            sku=sku,
            description="完成済みの商品説明です。",
            price=Decimal(1200),
            stock=7,
        )
        ProductImage.objects.create(
            product=product,
            image=f"products/{sku.lower()}.jpg",
        )
        return product

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin:catalog_product_changelist")

    def publish(self, *products, client=None, select_across=False, follow=False):
        return (client or self.client).post(
            self.url,
            {
                "action": "publish_selected_products",
                "_selected_action": [str(product.pk) for product in products],
                "select_across": "1" if select_across else "0",
                "index": "0",
            },
            follow=follow,
        )

    def test_action_is_available_on_product_changelist(self):
        response = self.client.get(self.url)
        self.assertContains(response, "將選取的商品批次公開")

    def test_selected_publishable_product_is_published(self):
        response = self.publish(self.product)
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_published)

    def test_multiple_selected_products_are_published(self):
        second = self.create_publishable_product(
            name="二つ目の商品", sku="BULK-PUBLISH-002"
        )

        self.publish(self.product, second)

        self.assertTrue(Product.objects.get(pk=self.product.pk).is_published)
        self.assertTrue(Product.objects.get(pk=second.pk).is_published)

    def test_select_across_publishes_all_products_in_queryset(self):
        second = self.create_publishable_product(
            name="全件選択対象", sku="BULK-PUBLISH-003"
        )

        self.publish(self.product, select_across=True)

        self.assertTrue(Product.objects.get(pk=self.product.pk).is_published)
        self.assertTrue(Product.objects.get(pk=second.pk).is_published)

    def test_product_missing_requirements_remains_unpublished(self):
        invalid = Product.objects.create(
            category=self.category,
            name="説明なし商品",
            sku="BULK-PUBLISH-004",
            description="",
            price=Decimal(900),
        )
        ProductImage.objects.create(
            product=invalid,
            image="products/bulk-publish-004.jpg",
        )

        response = self.publish(invalid, follow=True)

        invalid.refresh_from_db()
        self.assertFalse(invalid.is_published)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any(f"ID {invalid.pk}" in message for message in messages))
        self.assertTrue(any("商品說明" in message for message in messages))

    def test_valid_products_publish_when_another_selection_is_invalid(self):
        invalid = Product.objects.create(
            category=self.category,
            name="画像なし商品",
            sku="BULK-PUBLISH-005",
            description="説明あり",
            price=Decimal(800),
        )

        self.publish(self.product, invalid)

        self.assertTrue(Product.objects.get(pk=self.product.pk).is_published)
        self.assertFalse(Product.objects.get(pk=invalid.pk).is_published)

    def test_action_changes_only_publication_state_and_updated_at(self):
        before_product = Product.objects.filter(pk=self.product.pk).values().get()
        before_images = list(
            ProductImage.objects.filter(product=self.product).order_by("pk").values()
        )

        self.publish(self.product)

        after_product = Product.objects.filter(pk=self.product.pk).values().get()
        after_images = list(
            ProductImage.objects.filter(product=self.product).order_by("pk").values()
        )
        changed_fields = {
            field
            for field, value in before_product.items()
            if after_product[field] != value
        }
        self.assertEqual(changed_fields, {"is_published", "updated_at"})
        self.assertEqual(after_images, before_images)

    def test_already_published_product_is_not_saved_again(self):
        self.product.is_published = True
        self.product.save(update_fields=("is_published", "updated_at"))
        published_at = Product.objects.get(pk=self.product.pk).updated_at

        self.publish(self.product)

        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at, published_at)

    def test_user_without_change_permission_cannot_run_action(self):
        viewer = get_user_model().objects.create_user(
            "publish-viewer", password="password", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_product"))
        client = Client()
        client.force_login(viewer)

        changelist = client.get(self.url)
        self.assertNotContains(changelist, "將選取的商品批次公開")
        response = self.publish(self.product, client=client)

        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(Product.objects.get(pk=self.product.pk).is_published)
