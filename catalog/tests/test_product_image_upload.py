import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.admin import UPLOAD_SESSION_REGISTRY
from catalog.models import Product, ProductImage
from catalog.product_image_service import (
    MAX_FILE_BYTES,
    PRESIGN_EXPIRES_SECONDS,
    attach_temporary_images,
)


class ProductImageApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "image-admin", "images@example.com", "password"
        )
        self.client.force_login(self.user)
        self.session_id = self.register_session()
        self.presign_url = reverse("admin:catalog_product_image_presign")
        self.complete_url = reverse("admin:catalog_product_image_complete")
        self.reorder_url = reverse("admin:catalog_product_image_reorder")

    def register_session(self, product=None, *, client=None, user=None):
        client = client or self.client
        user = user or self.user
        upload_session = uuid4()
        session = client.session
        session[UPLOAD_SESSION_REGISTRY] = {
            str(upload_session): {
                "user_id": user.pk,
                "product_id": product.pk if product else None,
                "created_at": timezone.now().timestamp(),
            }
        }
        session.save()
        return upload_session

    def post_json(self, url, data, *, client=None):
        return (client or self.client).post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def presign_payload(self, **overrides):
        data = {
            "filename": "example.jpg",
            "content_type": "image/jpeg",
            "size": 5_340_000,
            "upload_session": str(self.session_id),
            "product_id": None,
        }
        data.update(overrides)
        return data

    def pending_image(self, **overrides):
        values = {
            "image": "products/2026/09/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
            "upload_status": ProductImage.UploadStatus.PENDING,
            "upload_session": self.session_id,
            "uploaded_by": self.user,
            "original_filename": "example.jpg",
            "content_type": "image/jpeg",
            "file_size": 100,
        }
        values.update(overrides)
        return ProductImage.objects.create(**values)

    def test_presign_requires_login(self):
        response = Client().post(
            self.presign_url,
            data=json.dumps(self.presign_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_presign_rejects_staff_without_product_permission(self):
        staff = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        client = Client()
        client.force_login(staff)
        upload_session = self.register_session(client=client, user=staff)
        response = self.post_json(
            self.presign_url,
            self.presign_payload(upload_session=str(upload_session)),
            client=client,
        )
        self.assertEqual(response.status_code, 403)

    @patch("catalog.product_image_service._storage_client")
    def test_presign_allows_supported_types_and_uses_ten_minute_expiry(self, storage_client):
        client = Mock()
        client.generate_presigned_url.return_value = "https://r2.example/upload"
        storage_client.return_value = (client, "existing-bucket")
        cases = (
            ("photo.jpg", "image/jpeg"),
            ("photo.jpeg", "image/jpeg"),
            ("photo.png", "image/png"),
            ("photo.webp", "image/webp"),
        )
        for filename, content_type in cases:
            with self.subTest(filename=filename):
                response = self.post_json(
                    self.presign_url,
                    self.presign_payload(filename=filename, content_type=content_type),
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["expires_in"], PRESIGN_EXPIRES_SECONDS)
                self.assertRegex(
                    payload["object_key"],
                    rf"^products/\d{{4}}/\d{{2}}/[0-9a-f]{{32}}\.{filename.rsplit('.', 1)[1]}$",
                )
                self.assertNotIn(filename, payload["object_key"])
        self.assertTrue(
            all(call.kwargs["ExpiresIn"] == 600 for call in client.generate_presigned_url.call_args_list)
        )

    @patch("catalog.product_image_service._storage_client")
    def test_presign_rejects_unsupported_type_and_oversize_before_r2(self, storage_client):
        unsupported = self.post_json(
            self.presign_url,
            self.presign_payload(filename="bad.gif", content_type="image/gif"),
        )
        oversized = self.post_json(
            self.presign_url,
            self.presign_payload(size=MAX_FILE_BYTES + 1),
        )
        mismatch = self.post_json(
            self.presign_url,
            self.presign_payload(filename="photo.png", content_type="image/jpeg"),
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(mismatch.status_code, 400)
        storage_client.assert_not_called()

    def test_presign_rejects_another_upload_session(self):
        response = self.post_json(
            self.presign_url,
            self.presign_payload(upload_session=str(uuid4())),
        )
        self.assertEqual(response.status_code, 403)

    def test_presign_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        upload_session = self.register_session(client=client)
        response = self.post_json(
            self.presign_url,
            self.presign_payload(upload_session=str(upload_session)),
            client=client,
        )
        self.assertEqual(response.status_code, 403)

    @patch("catalog.product_image_service.head_object")
    def test_complete_confirms_head_and_creates_temporary_record(self, head):
        image = self.pending_image()
        head.return_value = {"ContentLength": 100, "ContentType": "image/jpeg"}
        response = self.post_json(
            self.complete_url,
            {
                "object_key": image.image.name,
                "upload_session": str(self.session_id),
                "product_id": None,
            },
        )
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual(image.upload_status, ProductImage.UploadStatus.TEMPORARY)
        self.assertIsNone(image.product_id)
        head.assert_called_once_with(image.image.name)

    @patch("catalog.product_image_service.head_object")
    def test_complete_attaches_to_the_session_product(self, head):
        product = Product.objects.create(name="Target")
        session_id = self.register_session(product)
        image = self.pending_image(upload_session=session_id)
        head.return_value = {"ContentLength": 100, "ContentType": "image/jpeg"}
        response = self.post_json(
            self.complete_url,
            {
                "object_key": image.image.name,
                "upload_session": str(session_id),
                "product_id": product.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual(image.product_id, product.pk)
        self.assertEqual(image.upload_status, ProductImage.UploadStatus.ATTACHED)
        self.assertTrue(image.is_primary)

    @patch("catalog.product_image_service.head_object", side_effect=RuntimeError("missing"))
    def test_complete_keeps_pending_record_when_head_fails(self, _head):
        image = self.pending_image()
        response = self.post_json(
            self.complete_url,
            {"object_key": image.image.name, "upload_session": str(self.session_id)},
        )
        self.assertEqual(response.status_code, 503)
        image.refresh_from_db()
        self.assertEqual(image.upload_status, ProductImage.UploadStatus.PENDING)

    @patch("catalog.product_image_service.delete_object")
    @patch("catalog.product_image_service.head_object")
    def test_complete_removes_size_or_mime_mismatch(self, head, delete_object):
        cases = (
            ({"ContentLength": MAX_FILE_BYTES + 1, "ContentType": "image/jpeg"}, "size_mismatch"),
            ({"ContentLength": 100, "ContentType": "image/png"}, "mime_mismatch"),
        )
        for metadata, code in cases:
            with self.subTest(code=code):
                image = self.pending_image(
                    image=f"products/2026/09/{uuid4().hex}.jpg"
                )
                head.return_value = metadata
                response = self.post_json(
                    self.complete_url,
                    {"object_key": image.image.name, "upload_session": str(self.session_id)},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], code)
                self.assertFalse(ProductImage.objects.filter(pk=image.pk).exists())
        self.assertEqual(delete_object.call_count, 2)

    def test_complete_rejects_invalid_key_and_another_session(self):
        invalid = self.post_json(
            self.complete_url,
            {"object_key": "../../secret.jpg", "upload_session": str(self.session_id)},
        )
        image = self.pending_image(upload_session=uuid4())
        other_session = self.post_json(
            self.complete_url,
            {"object_key": image.image.name, "upload_session": str(self.session_id)},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(other_session.status_code, 404)

    def test_reorder_rejects_images_from_another_product(self):
        product = Product.objects.create(name="One")
        other = Product.objects.create(name="Two")
        session_id = self.register_session(product)
        first = ProductImage.objects.create(product=product, image="products/one.jpg")
        foreign = ProductImage.objects.create(product=other, image="products/two.jpg")
        response = self.post_json(
            self.reorder_url,
            {
                "upload_session": str(session_id),
                "product_id": product.pk,
                "images": [first.pk, foreign.pk],
            },
        )
        self.assertEqual(response.status_code, 400)

    @patch("catalog.product_image_service.delete_object")
    def test_reorder_main_and_delete_promotes_next_image(self, _delete_object):
        product = Product.objects.create(name="Product")
        session_id = self.register_session(product)
        first = ProductImage.objects.create(product=product, image="products/first.jpg", sort_order=0)
        second = ProductImage.objects.create(product=product, image="products/second.jpg", sort_order=1)
        response = self.post_json(
            self.reorder_url,
            {
                "upload_session": str(session_id),
                "product_id": product.pk,
                "images": [second.pk, first.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        self.assertTrue(second.is_primary)
        delete_url = reverse("admin:catalog_product_image_delete", args=[second.pk])
        deleted = self.post_json(
            delete_url,
            {"upload_session": str(session_id), "product_id": product.pk},
        )
        self.assertEqual(deleted.status_code, 200)
        first.refresh_from_db()
        self.assertTrue(first.is_primary)
        self.assertEqual(product.primary_image.pk, first.pk)

    @patch("catalog.product_image_service.delete_object", side_effect=RuntimeError("R2 unavailable"))
    def test_delete_failure_keeps_retryable_database_record(self, _delete_object):
        product = Product.objects.create(name="Product")
        session_id = self.register_session(product)
        image = ProductImage.objects.create(product=product, image="products/keep.jpg")
        response = self.post_json(
            reverse("admin:catalog_product_image_delete", args=[image.pk]),
            {"upload_session": str(session_id), "product_id": product.pk},
        )
        self.assertEqual(response.status_code, 503)
        image.refresh_from_db()
        self.assertEqual(image.upload_status, ProductImage.UploadStatus.DELETION_PENDING)

    @patch("catalog.product_image_service.delete_object")
    def test_delete_rejects_other_product_and_user_without_change_permission(self, _delete):
        product = Product.objects.create(name="Target")
        other = Product.objects.create(name="Other")
        session_id = self.register_session(product)
        image = ProductImage.objects.create(product=other, image="products/other.jpg")
        wrong_product = self.post_json(
            reverse("admin:catalog_product_image_delete", args=[image.pk]),
            {"upload_session": str(session_id), "product_id": product.pk},
        )
        self.assertEqual(wrong_product.status_code, 403)

        staff = get_user_model().objects.create_user("no-change", password="password", is_staff=True)
        client = Client()
        client.force_login(staff)
        staff_session = self.register_session(product, client=client, user=staff)
        forbidden = self.post_json(
            reverse("admin:catalog_product_image_delete", args=[image.pk]),
            {"upload_session": str(staff_session), "product_id": product.pk},
            client=client,
        )
        self.assertEqual(forbidden.status_code, 403)


class ProductImageOwnershipAndCleanupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner")
        self.other = get_user_model().objects.create_user("other")

    def test_attach_temporary_images_checks_user_session_and_sets_order(self):
        product = Product.objects.create(name="Product")
        session_id = uuid4()
        first = ProductImage.objects.create(
            image="products/temp-one.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            upload_session=session_id,
            uploaded_by=self.user,
        )
        second = ProductImage.objects.create(
            image="products/temp-two.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            upload_session=session_id,
            uploaded_by=self.user,
        )
        attach_temporary_images(
            product=product,
            user=self.user,
            upload_session=session_id,
            ordered_ids=[second.pk, first.pk],
        )
        self.assertEqual([image.pk for image in product.ordered_images], [second.pk, first.pk])
        self.assertTrue(product.primary_image.is_primary)

        foreign = ProductImage.objects.create(
            image="products/foreign.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            upload_session=uuid4(),
            uploaded_by=self.other,
        )
        with self.assertRaises(Exception):
            attach_temporary_images(
                product=product,
                user=self.user,
                upload_session=session_id,
                ordered_ids=[foreign.pk],
            )

    @patch("catalog.management.commands.cleanup_orphan_product_images.delete_object")
    def test_cleanup_deletes_only_old_orphans_and_continues_after_failure(self, delete_object):
        old = ProductImage.objects.create(
            image="products/old.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            uploaded_by=self.user,
        )
        failed = ProductImage.objects.create(
            image="products/failed.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            uploaded_by=self.user,
        )
        recent = ProductImage.objects.create(
            image="products/recent.jpg",
            upload_status=ProductImage.UploadStatus.TEMPORARY,
            uploaded_by=self.user,
        )
        attached = ProductImage.objects.create(
            product=Product.objects.create(name="Attached"),
            image="products/attached.jpg",
        )
        old_time = timezone.now() - timedelta(hours=25)
        ProductImage.objects.filter(pk__in=[old.pk, failed.pk]).update(created_at=old_time)
        delete_object.side_effect = [None, RuntimeError("R2 unavailable")]

        call_command("cleanup_orphan_product_images")

        self.assertFalse(ProductImage.objects.filter(pk=old.pk).exists())
        self.assertTrue(ProductImage.objects.filter(pk=failed.pk).exists())
        self.assertTrue(ProductImage.objects.filter(pk=recent.pk).exists())
        self.assertTrue(ProductImage.objects.filter(pk=attached.pk).exists())

    def test_existing_image_name_and_url_are_unchanged(self):
        product = Product.objects.create(name="Legacy")
        image = ProductImage.objects.create(
            product=product,
            image="products/2025/01/legacy-object.jpg",
            thumbnail="products/thumbnails/2025/01/legacy-thumb.webp",
            is_primary=True,
        )
        image.refresh_from_db()
        self.assertEqual(image.image.name, "products/2025/01/legacy-object.jpg")
        self.assertEqual(image.image.url, "/media/products/2025/01/legacy-object.jpg")
        self.assertEqual(image.thumbnail.name, "products/thumbnails/2025/01/legacy-thumb.webp")
