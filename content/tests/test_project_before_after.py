import json
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from content.models import InteriorProject, InteriorProjectImage
from core.direct_image_uploads import TOKEN_SALT
from core.models import DirectImageUpload


class ProjectBeforeAfterDisplayTests(TestCase):
    def setUp(self):
        self.project = InteriorProject.objects.create(
            title="Comparison", slug="comparison", description="Description",
            published=True, before_image="projects/before/original.jpg",
            featured_image="projects/after.jpg", featured_image_focus_x=25,
        )
        self.gallery = InteriorProjectImage.objects.create(
            project=self.project, image="projects/gallery/room.jpg", caption="Room",
        )

    def test_comparison_uses_before_and_existing_featured_image(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, 'class="ba-grid"')
        self.assertContains(response, 'src="/media/projects/before/original.jpg"', count=1)
        self.assertContains(response, 'src="/media/projects/after.jpg"', count=2)
        self.assertContains(response, 'alt="Comparison / BEFORE"')
        self.assertContains(response, 'alt="Comparison / AFTER"')
        self.assertContains(response, 'object-position:25% 50%')
        self.assertContains(response, '<figcaption>BEFORE</figcaption>')
        self.assertContains(response, 'src="/media/projects/gallery/room.jpg"')

    def test_disabled_or_incomplete_pairs_hide_grid_and_preserve_design_text(self):
        for enabled, before, after in (
            (False, "before.jpg", "after.jpg"),
            (True, "", "after.jpg"),
            (True, "before.jpg", ""),
            (True, "", ""),
        ):
            with self.subTest(enabled=enabled, before=before, after=after):
                self.project.show_before_after = enabled
                self.project.before_image = before
                self.project.featured_image = after
                self.project.save()
                response = self.client.get(self.project.get_absolute_url())
                self.assertNotContains(response, 'class="ba-grid"')
                self.assertNotContains(response, ' / BEFORE"')
                self.assertContains(response, "before-after--text-only")
                self.assertContains(response, "03・DESIGN THOUGHT")
                self.assertContains(response, 'src="/media/projects/gallery/room.jpg"')

    def test_setting_is_independent_and_draft_stays_private(self):
        other = InteriorProject.objects.create(
            title="Other", slug="other", description="Description", published=True,
            before_image="projects/before/other.jpg", featured_image="projects/other.jpg",
        )
        self.project.show_before_after = False
        self.project.save()
        self.assertNotContains(self.client.get(self.project.get_absolute_url()), 'class="ba-grid"')
        self.assertContains(self.client.get(other.get_absolute_url()), 'class="ba-grid"')
        other.published = False
        other.save()
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 404)


@override_settings(ADMIN_IMAGE_STAGING_BUCKET="private-tests")
class ProjectBeforeAfterAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "before-admin", "before@example.com", "password"
        )
        self.client.force_login(self.user)
        self.project = InteriorProject.objects.create(
            title="Admin comparison", slug="admin-comparison", description="Description",
            featured_image="projects/after.jpg", before_image="projects/before/old.jpg",
        )
        self.change_url = reverse("admin:content_interiorproject_change", args=[self.project.pk])

    def data(self, **overrides):
        data = {
            "title": self.project.title, "slug": self.project.slug,
            "description": self.project.description, "sort_order": "0", "tone": "bamboo",
            "featured_image": "existing:projects/after.jpg",
            "before_image": "existing:projects/before/old.jpg", "show_before_after": "on",
        }
        data.update(overrides)
        return data

    def post_json(self, route, data, client=None):
        return (client or self.client).post(
            reverse(f"admin:direct_image_{route}"), json.dumps(data),
            content_type="application/json",
        )

    def ready_upload(self, category="project.before_image", user=None):
        upload = DirectImageUpload.objects.create(
            object_key="projects/before/2026/11/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
            category=category, original_filename="before.jpg", content_type="image/jpeg",
            file_size=1234, width=1200, height=800, uploaded_by=user or self.user,
            status=DirectImageUpload.Status.READY,
        )
        token = signing.dumps(
            {"id": upload.pk, "key": upload.object_key, "category": category}, salt=TOKEN_SALT,
        )
        return upload, f"upload:{token}"

    def test_admin_shows_current_before_preview_and_existing_upload_manager(self):
        for url in (self.change_url, reverse("admin:content_interiorproject_add")):
            response = self.client.get(url)
            self.assertContains(response, 'data-category="project.before_image"')
            self.assertContains(response, 'name="show_before_after"')
            self.assertContains(response, 'data-category="project.featured_image"')
            self.assertContains(response, 'data-config-id="project-image-config"')
        response = self.client.get(self.change_url)
        self.assertContains(response, 'data-current-url="/media/projects/before/old.jpg"')

    @patch("core.direct_image_uploads._storage_client")
    def test_before_upload_verifies_r2_bytes_and_admin_saves_token(self, storage):
        raw = BytesIO()
        Image.new("RGB", (1200, 800), "white").save(raw, format="JPEG")
        body = raw.getvalue()
        r2 = Mock()
        r2.generate_presigned_url.return_value = "https://r2.example/put"
        r2.head_object.return_value = {"ContentLength": len(body), "ContentType": "image/jpeg"}
        r2.get_object.return_value = {"Body": BytesIO(body)}
        storage.return_value = (r2, "public-tests")
        prepared = self.post_json("presign", {
            "category": "project.before_image", "filename": "before.jpg",
            "content_type": "image/jpeg", "size": len(body), "width": 1200, "height": 800,
        })
        self.assertEqual(prepared.status_code, 200)
        payload = prepared.json()
        self.assertTrue(payload["object_key"].startswith("pending/projects/before/"))
        completed = self.post_json("complete", {
            "category": "project.before_image", "upload_id": payload["upload_id"],
            "object_key": payload["object_key"],
        })
        self.assertEqual(completed.status_code, 200)
        published = completed.json()
        self.assertTrue(published["object_key"].startswith("projects/before/"))
        self.assertEqual(r2.put_object.call_args.kwargs["Bucket"], "public-tests")
        response = self.client.post(self.change_url, self.data(before_image=f"upload:{published['token']}"))
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.before_image.name, published["object_key"])
        self.assertEqual(self.project.featured_image.name, "projects/after.jpg")
        upload = DirectImageUpload.objects.get(pk=published["upload_id"])
        self.assertEqual(upload.status, DirectImageUpload.Status.ATTACHED)

    @patch("core.direct_image_uploads.delete_image_object")
    def test_keep_replace_clear_and_toggle_do_not_delete_existing_files_or_gallery(self, delete):
        gallery = InteriorProjectImage.objects.create(project=self.project, image="projects/gallery/kept.jpg")
        self.assertEqual(self.client.post(self.change_url, self.data()).status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.before_image.name, "projects/before/old.jpg")
        upload, token = self.ready_upload()
        self.assertEqual(self.client.post(self.change_url, self.data(before_image=token)).status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.before_image.name, upload.object_key)
        cleared = self.data(before_image="")
        cleared.pop("show_before_after")
        self.assertEqual(self.client.post(self.change_url, cleared).status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.before_image.name, "")
        self.assertFalse(self.project.show_before_after)
        self.assertEqual(self.project.featured_image.name, "projects/after.jpg")
        gallery.refresh_from_db()
        self.assertEqual(gallery.image.name, "projects/gallery/kept.jpg")
        delete.assert_not_called()

    def test_reject_forged_existing_key_wrong_category_and_other_users_token(self):
        other = get_user_model().objects.create_superuser("other-before", "other@example.com", "password")
        for category, user in (("project.featured_image", self.user), ("project.before_image", other)):
            upload, token = self.ready_upload(category, user)
            response = self.client.post(self.change_url, self.data(before_image=token))
            self.assertEqual(response.status_code, 200)
            self.assertIn("before_image", response.context["adminform"].form.errors)
            upload.delete()
        response = self.client.post(self.change_url, self.data(before_image="existing:projects/unowned.jpg"))
        self.assertIn("before_image", response.context["adminform"].form.errors)
        self.project.refresh_from_db()
        self.assertEqual(self.project.before_image.name, "projects/before/old.jpg")

    @patch("core.direct_image_uploads._storage_client")
    def test_before_upload_reuses_validation_permissions_and_csrf(self, storage):
        base = {"category": "project.before_image", "filename": "before.jpg",
                "content_type": "image/jpeg", "size": 1234, "width": 1200, "height": 800}
        for change in ({"size": 10 * 1024 * 1024 + 1}, {"width": 8001},
                       {"width": 5000, "height": 5000},
                       {"filename": "before.gif", "content_type": "image/gif"}):
            self.assertEqual(self.post_json("presign", {**base, **change}).status_code, 400)
        staff = get_user_model().objects.create_user("no-before-permission", is_staff=True)
        client = Client()
        client.force_login(staff)
        self.assertEqual(self.post_json("presign", base, client).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(self.post_json("presign", base, csrf_client).status_code, 403)
        storage.assert_not_called()


class ProjectBeforeAfterMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_project_and_gallery(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [("content", "0009_publication_cover_image_focus_x_and_more")]
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            project = old_apps.get_model("content", "InteriorProject").objects.create(
                title="Legacy", slug="legacy-before-after", description="Keep description",
                featured_image="projects/legacy.jpg", published=True,
                design_notes=["Keep notes"], materials=["Keep material"],
            )
            gallery = old_apps.get_model("content", "InteriorProjectImage").objects.create(
                project=project, image="projects/gallery/legacy.jpg", caption="Keep caption",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            migrated = apps.get_model("content", "InteriorProject").objects.get(pk=project.pk)
            self.assertTrue(migrated.show_before_after)
            self.assertEqual(migrated.before_image.name, "")
            self.assertEqual(migrated.featured_image.name, "projects/legacy.jpg")
            self.assertEqual(migrated.description, "Keep description")
            self.assertEqual(migrated.design_notes, ["Keep notes"])
            self.assertEqual(migrated.materials, ["Keep material"])
            self.assertTrue(migrated.published)
            kept = apps.get_model("content", "InteriorProjectImage").objects.get(pk=gallery.pk)
            self.assertEqual((kept.project_id, kept.image.name, kept.caption),
                             (project.pk, "projects/gallery/legacy.jpg", "Keep caption"))
        finally:
            MigrationExecutor(connection).migrate(latest)
