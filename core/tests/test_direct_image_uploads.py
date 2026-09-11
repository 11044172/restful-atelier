import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from content.models import InteriorProject, InteriorProjectImage, Publication
from core.admin_forms import (
    InteriorProjectAdminForm, InteriorProjectImageAdminForm, PaymentMethodAdminForm,
    PublicationAdminForm, SiteSettingsAdminForm,
)
from core.direct_image_uploads import TOKEN_SALT
from core.models import DirectImageUpload, SiteSettings
from django.core import signing
from orders.models import PaymentMethod
from core.logging_filters import redact_signed_paths


@override_settings(
    ADMIN_IMAGE_MAX_INPUT_BYTES=30 * 1024 * 1024,
    ADMIN_IMAGE_MAX_OUTPUT_BYTES=10 * 1024 * 1024,
    ADMIN_IMAGE_MAX_INPUT_PIXELS=32_000_000,
    ADMIN_IMAGE_MAX_OUTPUT_PIXELS=16_000_000,
    ADMIN_IMAGE_MAX_INPUT_DIMENSION=12_000,
    ADMIN_IMAGE_MAX_DIMENSION=8_000,
    ADMIN_IMAGE_PHOTO_LONG_EDGE=2_800,
    ADMIN_IMAGE_ARTWORK_LONG_EDGE=2_000,
)
class DirectImageUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.user)
        self.presign = reverse("admin:direct_image_presign")
        self.complete = reverse("admin:direct_image_complete")

    def post(self, url, payload, client=None):
        return (client or self.client).post(url, json.dumps(payload), content_type="application/json")

    def metadata(self, **overrides):
        value = {"category":"site.home_hero_image", "filename":"hero.jpg", "content_type":"image/jpeg", "size":1234, "width":1200, "height":800}
        value.update(overrides)
        return value

    def ready_upload(self, category, key):
        upload = DirectImageUpload.objects.create(
            object_key=key, category=category, original_filename="image.jpg",
            content_type="image/jpeg", file_size=1234, width=1200, height=800,
            uploaded_by=self.user, status=DirectImageUpload.Status.READY,
        )
        token = signing.dumps({"id":upload.pk,"key":key,"category":category}, salt=TOKEN_SALT, compress=True)
        return upload, f"upload:{token}"

    @patch("core.direct_image_uploads._storage_client")
    def test_presign_requires_admin_and_valid_metadata(self, storage):
        anonymous = self.post(self.presign, self.metadata(), Client())
        self.assertEqual(anonymous.status_code, 302)
        staff = get_user_model().objects.create_user("staff", password="password", is_staff=True)
        client = Client(); client.force_login(staff)
        self.assertEqual(self.post(self.presign, self.metadata(), client).status_code, 403)
        for overrides, code in (({"content_type":"image/gif", "filename":"x.gif"}, "unsupported_type"), ({"size":10*1024*1024+1}, "file_too_large"), ({"width":8001}, "dimensions_too_large"), ({"width":5000,"height":5000}, "pixels_too_large"), ({"filename":"../hero.jpg"}, "invalid_filename")):
            with self.subTest(code=code):
                response = self.post(self.presign, self.metadata(**overrides))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], code)
        storage.assert_not_called()

    @patch("core.direct_image_uploads._storage_client")
    def test_put_is_presigned_and_head_verified_without_get_object(self, storage):
        client = Mock()
        client.generate_presigned_url.return_value = "https://r2.example/put"
        client.head_object.return_value = {"ContentLength":1234, "ContentType":"image/jpeg"}
        storage.return_value = (client, "bucket")
        prepared = self.post(self.presign, self.metadata())
        self.assertEqual(prepared.status_code, 200)
        payload = prepared.json()
        self.assertNotIn("..", payload["object_key"])
        completed = self.post(self.complete, {"category":"site.home_hero_image", "upload_id":payload["upload_id"], "object_key":payload["object_key"]})
        self.assertEqual(completed.status_code, 200)
        client.head_object.assert_called_once_with(Bucket="bucket", Key=payload["object_key"])
        self.assertFalse(hasattr(client, "get_object") and client.get_object.called)

    def test_safe_token_saves_all_admin_image_models_and_marks_attached(self):
        cases = []
        site = SiteSettings(checkout_enabled=False)
        cases.append((SiteSettingsAdminForm, site, "home_hero_image", "site.home_hero_image", "site/home/2026/09/a.jpg", {"checkout_enabled":False}))
        project = InteriorProject(title="Project", slug="project", description="Description")
        cases.append((InteriorProjectAdminForm, project, "featured_image", "project.featured_image", "projects/2026/09/b.jpg", {}))
        publication = Publication(issue_number="01", title="Publication", slug="publication")
        cases.append((PublicationAdminForm, publication, "cover_image", "publication.cover_image", "publications/2026/09/c.jpg", {}))
        payment = PaymentMethod(code=PaymentMethod.Method.TAIWAN_PAY, display_name="Taiwan Pay")
        cases.append((PaymentMethodAdminForm, payment, "qr_image", "payment.qr_image", "payments/methods/2026/09/d.jpg", {}))
        for form_class, instance, field, category, key, extra in cases:
            with self.subTest(field=field):
                upload, token = self.ready_upload(category, key)
                data = {f.name:getattr(instance, f.name) for f in instance._meta.fields if f.editable and f.name != field}
                data.update(extra); data[field] = token
                form = form_class(data=data, instance=instance, request=Mock(user=self.user))
                self.assertTrue(form.is_valid(), form.errors)
                saved = form.save(); form.mark_direct_uploads_attached(); upload.refresh_from_db()
                self.assertEqual(getattr(saved, field).name, key)
                self.assertEqual(upload.status, DirectImageUpload.Status.ATTACHED)

        project = InteriorProject.objects.create(title="Gallery", slug="gallery", description="D")
        upload, token = self.ready_upload("project.gallery_image", "projects/gallery/2026/09/e.jpg")
        form = InteriorProjectImageAdminForm(data={"project":project.pk,"image":token,"alt_text":"Gallery","caption":"","tone":"linen","sort_order":0}, request=Mock(user=self.user))
        self.assertTrue(form.is_valid(), form.errors); image=form.save(); form.mark_direct_uploads_attached()
        self.assertEqual(image.image.name, upload.object_key)

    def test_existing_image_unchanged_delete_and_form_error_reuse(self):
        site = SiteSettings.objects.create(checkout_enabled=False, home_hero_image="site/home/existing.jpg")
        base = {f.name:getattr(site,f.name) for f in site._meta.fields if f.editable and f.name != "home_hero_image"}
        unchanged = SiteSettingsAdminForm(data={**base,"home_hero_image":"existing:site/home/existing.jpg"}, instance=site, request=Mock(user=self.user))
        self.assertTrue(unchanged.is_valid(), unchanged.errors); unchanged.save()
        self.assertEqual(site.home_hero_image.name, "site/home/existing.jpg")
        deleted = SiteSettingsAdminForm(data={**base,"home_hero_image":""}, instance=site, request=Mock(user=self.user))
        self.assertTrue(deleted.is_valid(), deleted.errors); deleted.save()
        self.assertEqual(site.home_hero_image.name, "")

        upload, token = self.ready_upload("project.featured_image", "projects/2026/09/reuse.jpg")
        invalid = InteriorProjectAdminForm(data={"title":"","slug":"reuse","description":"D","featured_image":token,"sort_order":0}, request=Mock(user=self.user))
        self.assertFalse(invalid.is_valid()); upload.refresh_from_db(); self.assertEqual(upload.status, DirectImageUpload.Status.READY)
        valid = InteriorProjectAdminForm(data={"title":"Reuse","slug":"reuse","description":"D","featured_image":token,"sort_order":0}, request=Mock(user=self.user))
        self.assertTrue(valid.is_valid(), valid.errors); valid.save(); valid.mark_direct_uploads_attached()
        upload.refresh_from_db(); self.assertEqual(upload.status, DirectImageUpload.Status.ATTACHED)

    def test_design_notes_none_normalizes_to_empty_lists(self):
        project = InteriorProject.objects.create(title="Notes", slug="notes", description="D", design_notes=None, materials=None)
        self.assertEqual(project.design_notes, [])
        self.assertEqual(project.materials, [])

    def test_signed_payment_paths_are_redacted_from_logs(self):
        self.assertEqual(redact_signed_paths("Not Found: /pay/secret-token/"), "Not Found: /pay/<redacted>/")
        self.assertEqual(redact_signed_paths("/shop/cancel/secret-token/"), "/shop/cancel/<redacted>/")
