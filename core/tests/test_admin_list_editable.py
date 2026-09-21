from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductCategory
from content.models import InteriorProject, Publication
from core.direct_image_forms import mark_form_direct_uploads_attached
from orders.models import PaymentMethod


class AdminListEditableTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "list-editor", "list-editor@example.com", "password"
        )
        self.client.force_login(self.user)

    def post_list_editable(self, url_name, obj, values):
        data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(obj.pk),
            "_save": "儲存",
        }
        data.update({f"form-0-{name}": value for name, value in values.items()})
        return self.client.post(reverse(url_name), data)

    def test_interior_project_changelist_updates_published_and_sort_order(self):
        project = InteriorProject.objects.create(
            title="Project",
            slug="list-editable-project",
            description="Description",
            published=False,
            sort_order=8,
        )

        response = self.post_list_editable(
            "admin:content_interiorproject_changelist",
            project,
            {"published": "on", "sort_order": "2"},
        )

        self.assertRedirects(
            response, reverse("admin:content_interiorproject_changelist")
        )
        project.refresh_from_db()
        self.assertTrue(project.published)
        self.assertEqual(project.sort_order, 2)

    def test_publication_changelist_updates_all_editable_fields(self):
        publication = Publication.objects.create(
            issue_number="01",
            title="Publication",
            slug="list-editable-publication",
            featured=False,
            published=True,
            sort_order=8,
        )

        response = self.post_list_editable(
            "admin:content_publication_changelist",
            publication,
            {"featured": "on", "sort_order": "3"},
        )

        self.assertRedirects(response, reverse("admin:content_publication_changelist"))
        publication.refresh_from_db()
        self.assertTrue(publication.featured)
        self.assertFalse(publication.published)
        self.assertEqual(publication.sort_order, 3)

    def test_product_category_changelist_updates_all_editable_fields(self):
        category = ProductCategory.objects.create(
            name="Category",
            slug="list-editable-category",
            sort_order=8,
            is_active=True,
        )

        response = self.post_list_editable(
            "admin:catalog_productcategory_changelist",
            category,
            {"sort_order": "4"},
        )

        self.assertRedirects(
            response, reverse("admin:catalog_productcategory_changelist")
        )
        category.refresh_from_db()
        self.assertFalse(category.is_active)
        self.assertEqual(category.sort_order, 4)

    def test_payment_method_changelist_updates_all_editable_fields(self):
        payment_method = PaymentMethod.objects.create(
            code=PaymentMethod.Method.BANK_TRANSFER,
            display_name="Bank transfer",
            enabled=False,
            sort_order=8,
        )

        response = self.post_list_editable(
            "admin:orders_paymentmethod_changelist",
            payment_method,
            {"enabled": "on", "sort_order": "5"},
        )

        self.assertRedirects(
            response, reverse("admin:orders_paymentmethod_changelist")
        )
        payment_method.refresh_from_db()
        self.assertTrue(payment_method.enabled)
        self.assertEqual(payment_method.sort_order, 5)

    def test_product_stock_changelist_still_saves(self):
        product = Product.objects.create(name="Cup", stock=7)

        response = self.post_list_editable(
            "admin:catalog_product_changelist", product, {"stock": "12"}
        )

        self.assertRedirects(response, reverse("admin:catalog_product_changelist"))
        product.refresh_from_db()
        self.assertEqual(product.stock, 12)

    def test_interior_project_ordering_remains_lowest_sort_order_first(self):
        for sort_order in (922, 2, 0, 921, 1):
            InteriorProject.objects.create(
                title=f"Project {sort_order}",
                slug=f"project-{sort_order}",
                description="Description",
                sort_order=sort_order,
            )

        self.assertEqual(
            list(
                InteriorProject.objects.values_list("sort_order", flat=True)
            ),
            [0, 1, 2, 921, 922],
        )

    def test_direct_upload_marker_only_runs_for_supporting_forms(self):
        supporting_form = Mock()
        mark_form_direct_uploads_attached(supporting_form)
        supporting_form.mark_direct_uploads_attached.assert_called_once_with()

        mark_form_direct_uploads_attached(object())
