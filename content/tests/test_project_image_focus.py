import json
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from content.admin import PROJECT_UPLOAD_SESSION_REGISTRY
from content.models import InteriorProject, InteriorProjectImage
from content.project_image_service import image_payload
from core.admin_forms import InteriorProjectAdminForm


class ProjectImageFocusModelTests(TestCase):
    def test_focus_defaults_properties_and_gallery_ratios(self):
        project = InteriorProject.objects.create(
            title="Focus", slug="focus", description="Description"
        )
        portrait = InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/portrait.jpg",
            width=3000,
            height=4000,
        )
        unknown = InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/unknown.jpg",
        )

        self.assertEqual(
            (project.featured_image_focus_x, project.featured_image_focus_y),
            (50, 50),
        )
        self.assertEqual((portrait.focus_x, portrait.focus_y), (50, 50))
        self.assertEqual(project.featured_image_position, "50% 50%")
        self.assertEqual(portrait.focus_position, "50% 50%")
        self.assertEqual(portrait.aspect_ratio, "3000 / 4000")
        self.assertEqual(
            portrait.orientation_class, "project-gallery-image--portrait"
        )
        self.assertEqual(unknown.aspect_ratio, "4 / 3")
        self.assertEqual(unknown.orientation_class, "project-gallery-image--unknown")

    def test_model_and_form_reject_focus_outside_zero_to_one_hundred(self):
        project = InteriorProject(
            title="Invalid",
            slug="invalid",
            description="Description",
            featured_image_focus_x=101,
        )
        with self.assertRaises(ValidationError):
            project.full_clean()

        form = InteriorProjectAdminForm(
            data={
                "title": "Invalid",
                "slug": "invalid",
                "description": "Description",
                "featured_image": "",
                "featured_image_focus_x": "-1",
                "featured_image_focus_y": "50",
                "tone": "bamboo",
                "sort_order": "0",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("featured_image_focus_x", form.errors)

        with self.assertRaises(IntegrityError), transaction.atomic():
            InteriorProject.objects.create(
                title="DB constraint",
                slug="db-constraint",
                description="Description",
                featured_image_focus_y=101,
            )

    def test_image_payload_contains_focus_and_source_dimensions(self):
        image = InteriorProjectImage.objects.create(
            image="projects/gallery/payload.jpg",
            width=2160,
            height=3840,
            focus_x=25,
            focus_y=70,
        )

        payload = image_payload(image)

        self.assertEqual(payload["focus_x"], 25)
        self.assertEqual(payload["focus_y"], 70)
        self.assertEqual(payload["width"], 2160)
        self.assertEqual(payload["height"], 3840)


class ProjectImageFocusMigrationTests(TransactionTestCase):
    migrate_from = [("content", "0007_interior_project_image_manager")]
    migrate_to = [("content", "0008_interiorproject_featured_image_focus_x_and_more")]

    def test_existing_rows_migrate_to_center_focus(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldProject = old_apps.get_model("content", "InteriorProject")
        OldImage = old_apps.get_model("content", "InteriorProjectImage")
        project = OldProject.objects.create(
            title="Legacy", slug="legacy", description="Description"
        )
        image = OldImage.objects.create(
            project=project,
            image="projects/gallery/legacy.jpg",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewProject = new_apps.get_model("content", "InteriorProject")
        NewImage = new_apps.get_model("content", "InteriorProjectImage")

        self.assertEqual(
            (
                NewProject.objects.get(pk=project.pk).featured_image_focus_x,
                NewProject.objects.get(pk=project.pk).featured_image_focus_y,
            ),
            (50, 50),
        )
        self.assertEqual(
            (
                NewImage.objects.get(pk=image.pk).focus_x,
                NewImage.objects.get(pk=image.pk).focus_y,
            ),
            (50, 50),
        )


class ProjectImageFocusAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "focus-admin", "focus@example.com", "password"
        )
        self.client.force_login(self.user)

    def register_session(self, project=None, *, client=None, user=None):
        client = client or self.client
        user = user or self.user
        upload_session = uuid4()
        session = client.session
        registry = session.get(PROJECT_UPLOAD_SESSION_REGISTRY, {})
        registry[str(upload_session)] = {
            "user_id": user.pk,
            "project_id": project.pk if project else None,
            "created_at": timezone.now().timestamp(),
        }
        session[PROJECT_UPLOAD_SESSION_REGISTRY] = registry
        session.save()
        return upload_session

    def post_metadata(self, image, upload_session, project, **values):
        return self.client.post(
            reverse("admin:content_project_image_metadata", args=[image.pk]),
            json.dumps(
                {
                    "upload_session": str(upload_session),
                    "project_id": project.pk if project else None,
                    **values,
                }
            ),
            content_type="application/json",
        )

    def test_admin_config_and_featured_widget_enable_visual_focal_editor(self):
        project = InteriorProject.objects.create(
            title="Admin", slug="admin-focus", description="Description"
        )
        image = InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/admin.jpg",
            width=3000,
            height=4000,
            focus_x=23,
            focus_y=77,
        )

        response = self.client.get(
            reverse("admin:content_interiorproject_change", args=[project.pk])
        )
        config = response.context["project_image_config"]

        self.assertEqual(config["focalPoint"]["xField"], "focus_x")
        self.assertEqual(config["focalPoint"]["yField"], "focus_y")
        self.assertEqual(config["images"][0]["focus_x"], 23)
        self.assertEqual(config["images"][0]["focus_y"], 77)
        self.assertContains(response, 'data-focal-point="true"')
        self.assertContains(response, 'type="hidden" name="featured_image_focus_x"')
        self.assertContains(response, 'type="hidden" name="featured_image_focus_y"')
        self.assertContains(response, "form-row hidden field-featured_image_focus_x")
        self.assertContains(response, "form-row hidden field-featured_image_focus_y")
        self.assertNotContains(response, ">focus_x<")
        self.assertEqual(config["images"][0]["id"], image.pk)

    def test_metadata_api_saves_boundaries_and_rejects_invalid_values(self):
        project = InteriorProject.objects.create(
            title="API", slug="api-focus", description="Description"
        )
        image = InteriorProjectImage.objects.create(
            project=project, image="projects/gallery/api.jpg"
        )
        upload_session = self.register_session(project)

        valid = self.post_metadata(
            image, upload_session, project, focus_x=0, focus_y=100
        )
        self.assertEqual(valid.status_code, 200)
        image.refresh_from_db()
        self.assertEqual((image.focus_x, image.focus_y), (0, 100))

        for field, value in (
            ("focus_x", "not-a-number"),
            ("focus_x", None),
            ("focus_x", -1),
            ("focus_y", 101),
        ):
            with self.subTest(field=field, value=value):
                coordinates = {"focus_x": image.focus_x, "focus_y": image.focus_y}
                coordinates[field] = value
                response = self.post_metadata(
                    image,
                    upload_session,
                    project,
                    **coordinates,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], f"invalid_{field}")

    def test_focus_only_update_preserves_text_metadata(self):
        project = InteriorProject.objects.create(
            title="Preserve", slug="preserve-focus", description="Description"
        )
        image = InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/preserve.jpg",
            alt_text="Living room",
            caption="Morning",
            tone="fog",
        )
        upload_session = self.register_session(project)

        response = self.post_metadata(
            image, upload_session, project, focus_x=37, focus_y=68
        )

        self.assertEqual(response.status_code, 200)
        image.refresh_from_db()
        self.assertEqual((image.focus_x, image.focus_y), (37, 68))
        self.assertEqual(
            (image.alt_text, image.caption, image.tone),
            ("Living room", "Morning", "fog"),
        )

    def test_metadata_rejects_foreign_project_and_other_users_upload_session(self):
        project = InteriorProject.objects.create(
            title="Owned", slug="owned-focus", description="Description"
        )
        other_project = InteriorProject.objects.create(
            title="Other", slug="other-focus", description="Description"
        )
        image = InteriorProjectImage.objects.create(
            project=other_project, image="projects/gallery/foreign.jpg"
        )
        upload_session = self.register_session(project)
        denied = self.post_metadata(
            image, upload_session, project, focus_x=10, focus_y=20
        )
        self.assertEqual(denied.status_code, 403)

        other_user = get_user_model().objects.create_superuser(
            "other-focus-admin", "other-focus@example.com", "password"
        )
        other_client = Client()
        other_client.force_login(other_user)
        other_session = self.register_session(client=other_client, user=other_user)
        temporary = InteriorProjectImage.objects.create(
            image="projects/gallery/temporary.jpg",
            upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
            upload_session=other_session,
            uploaded_by=other_user,
        )
        denied_session = self.post_metadata(
            temporary, self.register_session(), None, focus_x=10, focus_y=20
        )
        self.assertEqual(denied_session.status_code, 403)

    def test_manager_uses_pointer_keyboard_reset_and_deferred_persistence(self):
        source = (
            Path(settings.BASE_DIR) / "static/admin/js/admin-image-manager.js"
        ).read_text()

        self.assertIn('surface.addEventListener("pointerdown"', source)
        self.assertIn('surface.addEventListener("pointermove"', source)
        self.assertIn('surface.addEventListener("pointerup", finishPointer)', source)
        self.assertIn('surface.addEventListener("keydown"', source)
        self.assertIn("event.shiftKey ? 5 : 1", source)
        self.assertIn('editor.querySelector("[data-focal-reset]")', source)
        pointer_move = source.split('surface.addEventListener("pointermove"', 1)[
            1
        ].split("});", 1)[0]
        self.assertNotIn("persistMetadata", pointer_move)


class ProjectImageFocusPublicTests(TestCase):
    def test_public_fixed_crops_use_focus_and_gallery_uses_source_ratio(self):
        project = InteriorProject.objects.create(
            title="Public focus",
            slug="public-focus",
            description="Description",
            featured_image="projects/featured.jpg",
            featured_image_focus_x=25,
            featured_image_focus_y=70,
            published=True,
        )
        portrait = InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/portrait.jpg",
            width=2160,
            height=3840,
            focus_x=15,
            focus_y=80,
        )
        InteriorProjectImage.objects.create(
            project=project,
            image="projects/gallery/legacy.jpg",
            width=None,
            height=None,
        )

        listing = self.client.get(reverse("content:project_list"))
        detail = self.client.get(project.get_absolute_url())
        detail_html = detail.content.decode()

        self.assertContains(listing, "object-position:25% 70%")
        self.assertGreaterEqual(detail_html.count("object-position:25% 70%"), 2)
        self.assertIn("--ratio:2160 / 3840", detail_html)
        self.assertIn("project-gallery-image--portrait", detail_html)
        self.assertIn("object-position:15% 80%", detail_html)
        self.assertIn("--ratio:4 / 3", detail_html)
        self.assertIn("project-gallery-image--unknown", detail_html)
        self.assertEqual(portrait.aspect_ratio, "2160 / 3840")
