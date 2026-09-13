import json
import re
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from content.admin import PROJECT_UPLOAD_SESSION_REGISTRY
from content.models import InteriorProject, InteriorProjectImage, Publication
from content.project_image_service import attach_temporary_images
from core.admin_forms import PublicationAdminForm
from core.models import DirectImageUpload


class ProjectImageAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "project-admin", "projects@example.com", "password"
        )
        self.client.force_login(self.user)
        self.presign_url = reverse("admin:content_project_image_presign")
        self.complete_url = reverse("admin:content_project_image_complete")
        self.reorder_url = reverse("admin:content_project_image_reorder")
        self.session_id = self.register_session()

    def register_session(
        self, project=None, *, client=None, user=None, created_at=None
    ):
        client = client or self.client
        user = user or self.user
        upload_session = uuid4()
        session = client.session
        session[PROJECT_UPLOAD_SESSION_REGISTRY] = {
            str(upload_session): {
                "user_id": user.pk,
                "project_id": project.pk if project else None,
                "created_at": created_at or timezone.now().timestamp(),
            }
        }
        session.save()
        return upload_session

    def post_json(self, url, payload, *, client=None):
        return (client or self.client).post(
            url, json.dumps(payload), content_type="application/json"
        )

    def scope(self, project=None, session_id=None):
        return {
            "upload_session": str(session_id or self.session_id),
            "project_id": project.pk if project else None,
        }

    def metadata(self, **overrides):
        payload = {
            **self.scope(),
            "filename": "room.jpg",
            "content_type": "image/jpeg",
            "size": 1234,
            "width": 1200,
            "height": 800,
        }
        payload.update(overrides)
        return payload

    def pending_image(self, **overrides):
        values = {
            "image": "projects/gallery/2026/09/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
            "upload_status": InteriorProjectImage.UploadStatus.PENDING,
            "upload_session": self.session_id,
            "uploaded_by": self.user,
            "original_filename": "room.jpg",
            "content_type": "image/jpeg",
            "file_size": 1234,
            "width": 1200,
            "height": 800,
        }
        values.update(overrides)
        return InteriorProjectImage.objects.create(**values)

    def test_add_page_has_featured_and_gallery_common_managers(self):
        response = self.client.get(reverse("admin:content_interiorproject_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-category="project.featured_image"')
        self.assertContains(response, 'data-config-id="project-image-config"')
        self.assertContains(response, "multiple data-image-input")
        self.assertContains(response, "admin/js/admin-image-manager")
        self.assertContains(response, '"sessionInputId": "id_project_image_session"')
        self.assertNotContains(response, 'name="images-0-image"')
        self.assertNotContains(response, 'enctype="multipart/form-data"')
        session_field = re.search(
            r'<input[^>]+name="project_image_session"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(session_field)
        self.assertRegex(session_field.group(0), r'value="[0-9a-f-]{36}"')
        manager_source = (
            Path(__file__).resolve().parents[2]
            / "static/admin/js/admin-image-manager.js"
        ).read_text()
        self.assertIn(
            'document.addEventListener("DOMContentLoaded", boot', manager_source
        )
        self.assertIn('request.open("PUT", uploadUrl, true)', manager_source)
        self.assertIn('window.addEventListener("pageshow", syncInputs)', manager_source)

    @patch("content.project_image_service._storage_client")
    def test_presign_supports_allowlist_and_direct_r2_put(self, storage):
        client = Mock()
        client.generate_presigned_url.return_value = "https://r2.example/put"
        storage.return_value = (client, "bucket")
        for filename, content_type in (
            ("a.jpg", "image/jpeg"),
            ("a.jpeg", "image/jpeg"),
            ("a.png", "image/png"),
            ("a.webp", "image/webp"),
        ):
            with self.subTest(filename=filename):
                response = self.post_json(
                    self.presign_url,
                    self.metadata(filename=filename, content_type=content_type),
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.json()["upload_url"].startswith("https://r2.example")
                )
        self.assertTrue(
            all(
                call.args[0] == "put_object"
                for call in client.generate_presigned_url.call_args_list
            )
        )

    @patch("content.project_image_service.head_object")
    def test_complete_head_verifies_and_creates_temporary_gallery_image(self, head):
        image = self.pending_image()
        head.return_value = {"ContentLength": 1234, "ContentType": "image/jpeg"}
        response = self.post_json(
            self.complete_url,
            {**self.scope(), "object_key": image.image.name},
        )
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual(
            image.upload_status, InteriorProjectImage.UploadStatus.TEMPORARY
        )
        self.assertIsNone(image.project_id)
        head.assert_called_once_with(image.image.name)

    @patch("content.project_image_service.head_object")
    def test_complete_attaches_immediately_on_change_page(self, head):
        project = InteriorProject.objects.create(
            title="Home", slug="home", description="D"
        )
        session_id = self.register_session(project)
        image = self.pending_image(upload_session=session_id)
        head.return_value = {"ContentLength": 1234, "ContentType": "image/jpeg"}
        response = self.post_json(
            self.complete_url,
            {**self.scope(project, session_id), "object_key": image.image.name},
        )
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual(image.project_id, project.pk)
        self.assertEqual(
            image.upload_status, InteriorProjectImage.UploadStatus.ATTACHED
        )

    def test_temporary_images_attach_in_selected_order_with_metadata(self):
        first = InteriorProjectImage.objects.create(
            image="projects/gallery/2026/09/one.jpg",
            upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
            upload_session=self.session_id,
            uploaded_by=self.user,
            alt_text="One",
            caption="First caption",
            tone="fog",
        )
        second = InteriorProjectImage.objects.create(
            image="projects/gallery/2026/09/two.jpg",
            upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
            upload_session=self.session_id,
            uploaded_by=self.user,
            alt_text="Two",
            caption="Second caption",
            tone="linen",
        )
        project = InteriorProject.objects.create(
            title="New", slug="new", description="D"
        )
        attach_temporary_images(
            project=project,
            user=self.user,
            upload_session=self.session_id,
            ordered_ids=[second.pk, first.pk],
        )
        images = list(project.images.all())
        self.assertEqual([image.pk for image in images], [second.pk, first.pk])
        self.assertEqual(images[0].caption, "Second caption")
        self.assertEqual(images[1].tone, "fog")

    def test_admin_add_attaches_temporary_images(self):
        image = InteriorProjectImage.objects.create(
            image="projects/gallery/2026/09/temp.jpg",
            upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
            upload_session=self.session_id,
            uploaded_by=self.user,
            alt_text="Living room",
        )
        response = self.client.post(
            reverse("admin:content_interiorproject_add"),
            {
                "title": "New project",
                "slug": "new-project",
                "description": "Description",
                "featured_image": "",
                "tone": "bamboo",
                "sort_order": "0",
                "project_image_session": str(self.session_id),
                "project_image_order": str(image.pk),
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        image.refresh_from_db()
        self.assertEqual(image.project.slug, "new-project")
        self.assertEqual(
            image.upload_status, InteriorProjectImage.UploadStatus.ATTACHED
        )

    def test_reorder_persists_normalized_order_and_existing_context(self):
        project = InteriorProject.objects.create(
            title="Order", slug="order", description="D"
        )
        first = InteriorProjectImage.objects.create(
            project=project, image="projects/a.jpg", alt_text="A", sort_order=8
        )
        second = InteriorProjectImage.objects.create(
            project=project, image="projects/b.jpg", alt_text="B", sort_order=3
        )
        session_id = self.register_session(project)
        response = self.post_json(
            self.reorder_url,
            {**self.scope(project, session_id), "images": [first.pk, second.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(project.images.values_list("pk", "sort_order")),
            [(first.pk, 0), (second.pk, 1)],
        )
        page = self.client.get(
            reverse("admin:content_interiorproject_change", args=[project.pk])
        )
        self.assertEqual(
            [item["id"] for item in page.context["project_image_config"]["images"]],
            [first.pk, second.pk],
        )

    @patch("content.project_image_service.delete_object")
    def test_delete_normalizes_order_and_rejects_another_project(self, _delete):
        project = InteriorProject.objects.create(
            title="One", slug="one", description="D"
        )
        other = InteriorProject.objects.create(title="Two", slug="two", description="D")
        first = InteriorProjectImage.objects.create(
            project=project, image="projects/a.jpg", sort_order=0
        )
        second = InteriorProjectImage.objects.create(
            project=project, image="projects/b.jpg", sort_order=9
        )
        foreign = InteriorProjectImage.objects.create(
            project=other, image="projects/c.jpg"
        )
        session_id = self.register_session(project)
        denied = self.post_json(
            reverse("admin:content_project_image_delete", args=[foreign.pk]),
            self.scope(project, session_id),
        )
        self.assertEqual(denied.status_code, 403)
        denied_reorder = self.post_json(
            self.reorder_url,
            {
                **self.scope(project, session_id),
                "images": [first.pk, second.pk, foreign.pk],
            },
        )
        self.assertEqual(denied_reorder.status_code, 400)
        deleted = self.post_json(
            reverse("admin:content_project_image_delete", args=[first.pk]),
            self.scope(project, session_id),
        )
        self.assertEqual(deleted.status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.sort_order, 0)

    def test_metadata_endpoint_preserves_all_editable_fields(self):
        project = InteriorProject.objects.create(
            title="Meta", slug="meta", description="D"
        )
        image = InteriorProjectImage.objects.create(
            project=project, image="projects/meta.jpg"
        )
        session_id = self.register_session(project)
        response = self.post_json(
            reverse("admin:content_project_image_metadata", args=[image.pk]),
            {
                **self.scope(project, session_id),
                "alt_text": "Quiet living room",
                "caption": "Morning light",
                "tone": "fog",
            },
        )
        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual(
            (image.alt_text, image.caption, image.tone),
            ("Quiet living room", "Morning light", "fog"),
        )

    @patch("content.project_image_service._storage_client")
    def test_security_rejects_login_permission_session_metadata_and_key(self, storage):
        anonymous = self.post_json(self.presign_url, self.metadata(), client=Client())
        self.assertEqual(anonymous.status_code, 302)
        staff = get_user_model().objects.create_user(
            "staff", password="password", is_staff=True
        )
        staff_client = Client()
        staff_client.force_login(staff)
        staff_session = self.register_session(client=staff_client, user=staff)
        forbidden = self.post_json(
            self.presign_url,
            self.metadata(upload_session=str(staff_session)),
            client=staff_client,
        )
        self.assertEqual(forbidden.status_code, 403)
        invalid_session = self.post_json(
            self.presign_url, self.metadata(upload_session=str(uuid4()))
        )
        self.assertEqual(invalid_session.status_code, 403)
        cases = (
            ({"filename": "bad.gif", "content_type": "image/gif"}, "unsupported_type"),
            (
                {"filename": "bad.png", "content_type": "image/jpeg"},
                "extension_mismatch",
            ),
            ({"size": 10 * 1024 * 1024 + 1}, "file_too_large"),
            ({"width": 8001}, "dimensions_too_large"),
            ({"width": 5000, "height": 5000}, "pixels_too_large"),
        )
        for values, code in cases:
            with self.subTest(code=code):
                response = self.post_json(self.presign_url, self.metadata(**values))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], code)
        invalid_key = self.post_json(
            self.complete_url, {**self.scope(), "object_key": "../../secret.jpg"}
        )
        self.assertEqual(invalid_key.status_code, 400)
        storage.assert_not_called()

    def test_expired_session_and_wrong_temporary_owner_are_rejected(self):
        expired = self.register_session(created_at=timezone.now().timestamp() - 90000)
        response = self.post_json(
            self.presign_url, self.metadata(upload_session=str(expired))
        )
        self.assertEqual(response.status_code, 403)
        other = get_user_model().objects.create_user("other")
        image = self.pending_image(uploaded_by=other)
        denied = self.post_json(
            reverse("admin:content_project_image_delete", args=[image.pk]), self.scope()
        )
        self.assertEqual(denied.status_code, 403)


class SingleImageAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "publication-admin", "publication@example.com", "password"
        )
        self.client.force_login(self.user)

    def test_publication_add_and_change_use_common_single_manager(self):
        add = self.client.get(reverse("admin:content_publication_add"))
        self.assertContains(add, 'data-category="publication.cover_image"')
        self.assertContains(add, "admin/js/admin-image-manager")
        self.assertNotContains(add, "admin/js/direct-images")
        publication = Publication.objects.create(
            issue_number="01",
            title="Journal",
            slug="journal",
            cover_image="publications/legacy.jpg",
        )
        change = self.client.get(
            reverse("admin:content_publication_change", args=[publication.pk])
        )
        self.assertContains(change, "publications/legacy.jpg")
        self.assertContains(change, 'data-current-url="/media/publications/legacy.jpg"')

    @patch("core.direct_image_uploads._storage_client")
    def test_featured_and_cover_use_presign_put_complete_head_flow(self, storage):
        r2 = Mock()
        r2.generate_presigned_url.return_value = "https://r2.example/put"
        r2.head_object.return_value = {
            "ContentLength": 1234,
            "ContentType": "image/jpeg",
        }
        storage.return_value = (r2, "bucket")
        for category, prefix in (
            ("project.featured_image", "projects/"),
            ("publication.cover_image", "publications/"),
        ):
            with self.subTest(category=category):
                prepared = self.client.post(
                    reverse("admin:direct_image_presign"),
                    json.dumps(
                        {
                            "category": category,
                            "filename": "image.jpg",
                            "content_type": "image/jpeg",
                            "size": 1234,
                            "width": 1200,
                            "height": 800,
                        }
                    ),
                    content_type="application/json",
                )
                self.assertEqual(prepared.status_code, 200)
                payload = prepared.json()
                self.assertTrue(payload["object_key"].startswith(prefix))
                completed = self.client.post(
                    reverse("admin:direct_image_complete"),
                    json.dumps(
                        {
                            "category": category,
                            "upload_id": payload["upload_id"],
                            "object_key": payload["object_key"],
                        }
                    ),
                    content_type="application/json",
                )
                self.assertEqual(completed.status_code, 200)
                self.assertIn("token", completed.json())
        self.assertEqual(r2.head_object.call_count, 2)

    def test_single_image_complete_rejects_csrf_and_invalid_object_key(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        denied = csrf_client.post(
            reverse("admin:direct_image_presign"),
            json.dumps(
                {
                    "category": "publication.cover_image",
                    "filename": "cover.jpg",
                    "content_type": "image/jpeg",
                    "size": 1234,
                    "width": 1200,
                    "height": 800,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        invalid = self.client.post(
            reverse("admin:direct_image_complete"),
            json.dumps(
                {
                    "category": "publication.cover_image",
                    "upload_id": 1,
                    "object_key": "../../unexpected.jpg",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["code"], "invalid_object_key")

    def test_publication_cover_replace_remove_and_invalid_token(self):
        publication = Publication.objects.create(
            issue_number="01",
            title="Journal",
            slug="journal",
            cover_image="publications/legacy.jpg",
        )
        base = {
            "issue_number": "01",
            "title": "Journal",
            "slug": "journal",
            "tone": "rice",
            "sort_order": 0,
        }
        invalid = PublicationAdminForm(
            data={**base, "cover_image": "upload:not-a-token"},
            instance=publication,
            request=Mock(user=self.user),
        )
        self.assertFalse(invalid.is_valid())
        removed = PublicationAdminForm(
            data={**base, "cover_image": ""},
            instance=publication,
            request=Mock(user=self.user),
        )
        self.assertTrue(removed.is_valid(), removed.errors)
        removed.save()
        publication.refresh_from_db()
        self.assertEqual(publication.cover_image.name, "")

    @patch("core.direct_image_uploads.default_storage.delete")
    def test_unattached_single_upload_can_be_deleted_only_by_owner(self, delete):
        upload = DirectImageUpload.objects.create(
            object_key="publications/2026/09/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
            category="publication.cover_image",
            original_filename="cover.jpg",
            content_type="image/jpeg",
            file_size=100,
            width=100,
            height=100,
            uploaded_by=self.user,
            status=DirectImageUpload.Status.READY,
        )
        other = get_user_model().objects.create_superuser(
            "other-publication-admin", "other-publication@example.com", "password"
        )
        other_client = Client()
        other_client.force_login(other)
        denied = other_client.post(
            reverse("admin:direct_image_delete"),
            json.dumps(
                {
                    "category": upload.category,
                    "upload_id": upload.pk,
                    "object_key": upload.object_key,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404)
        response = self.client.post(
            reverse("admin:direct_image_delete"),
            json.dumps(
                {
                    "category": upload.category,
                    "upload_id": upload.pk,
                    "object_key": upload.object_key,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DirectImageUpload.objects.filter(pk=upload.pk).exists())
        delete.assert_called_once_with(upload.object_key)
