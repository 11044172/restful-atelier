from pathlib import Path
from unittest.mock import Mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from content.models import Publication
from core.admin_forms import PublicationAdminForm
from core.direct_image_uploads import TOKEN_SALT
from core.models import DirectImageUpload


class PublicationImageModelTests(TestCase):
    def test_focus_defaults_ratios_orientation_and_position(self):
        portrait = Publication.objects.create(
            issue_number="01",
            title="Portrait",
            slug="portrait",
            cover_image_width=3000,
            cover_image_height=4000,
        )
        landscape = Publication.objects.create(
            issue_number="02",
            title="Landscape",
            slug="landscape",
            cover_image_width=3840,
            cover_image_height=2160,
            cover_image_focus_x=25,
            cover_image_focus_y=70,
        )
        square = Publication.objects.create(
            issue_number="03",
            title="Square",
            slug="square",
            cover_image_width=3000,
            cover_image_height=3000,
        )
        unknown = Publication.objects.create(
            issue_number="04", title="Unknown", slug="unknown"
        )

        self.assertEqual(
            (portrait.cover_image_focus_x, portrait.cover_image_focus_y), (50, 50)
        )
        self.assertEqual(portrait.cover_image_aspect_ratio, "3000 / 4000")
        self.assertEqual(
            portrait.cover_image_orientation_class, "publication-image--portrait"
        )
        self.assertEqual(landscape.cover_image_aspect_ratio, "3840 / 2160")
        self.assertEqual(
            landscape.cover_image_orientation_class,
            "publication-image--landscape",
        )
        self.assertEqual(landscape.cover_image_position, "25% 70%")
        self.assertEqual(square.cover_image_aspect_ratio, "3000 / 3000")
        self.assertEqual(
            square.cover_image_orientation_class, "publication-image--square"
        )
        self.assertEqual(unknown.cover_image_aspect_ratio, "3 / 4.25")
        self.assertEqual(unknown.cover_image_detail_aspect_ratio, "16 / 9")
        self.assertEqual(
            unknown.cover_image_orientation_class, "publication-image--unknown"
        )

    def test_focus_and_dimensions_validate_at_model_and_database_layers(self):
        for x, y in ((0, 100), (100, 0)):
            publication = Publication(
                issue_number="valid",
                title="Valid",
                slug=f"valid-{x}-{y}",
                cover_image_focus_x=x,
                cover_image_focus_y=y,
                cover_image_width=1,
                cover_image_height=1,
            )
            publication.full_clean()

        for field, value in (
            ("cover_image_focus_x", -1),
            ("cover_image_focus_y", 101),
            ("cover_image_width", 0),
            ("cover_image_height", 0),
        ):
            with self.subTest(field=field, value=value):
                publication = Publication(
                    issue_number="invalid",
                    title="Invalid",
                    slug=f"invalid-{field}-{value}",
                    **{field: value},
                )
                with self.assertRaises(ValidationError):
                    publication.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            Publication.objects.create(
                issue_number="db",
                title="DB constraint",
                slug="db-constraint",
                cover_image_focus_x=101,
            )


class PublicationImageMigrationTests(TransactionTestCase):
    migrate_from = [("content", "0008_interiorproject_featured_image_focus_x_and_more")]
    migrate_to = [("content", "0009_publication_cover_image_focus_x_and_more")]

    def test_existing_publications_migrate_centered_with_unknown_dimensions(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldPublication = old_apps.get_model("content", "Publication")
        publication = OldPublication.objects.create(
            issue_number="legacy", title="Legacy", slug="legacy-publication"
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewPublication = new_apps.get_model("content", "Publication")
        migrated = NewPublication.objects.get(pk=publication.pk)

        self.assertEqual(
            (migrated.cover_image_focus_x, migrated.cover_image_focus_y), (50, 50)
        )
        self.assertIsNone(migrated.cover_image_width)
        self.assertIsNone(migrated.cover_image_height)


class PublicationDirectUploadMetadataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "publication-focus-admin", "publication-focus@example.com", "password"
        )

    def ready_upload(self, *, key, width, height):
        upload = DirectImageUpload.objects.create(
            object_key=key,
            category="publication.cover_image",
            original_filename="cover.jpg",
            content_type="image/jpeg",
            file_size=1234,
            width=width,
            height=height,
            uploaded_by=self.user,
            status=DirectImageUpload.Status.READY,
        )
        token = signing.dumps(
            {"id": upload.pk, "key": key, "category": upload.category},
            salt=TOKEN_SALT,
            compress=True,
        )
        return upload, f"upload:{token}"

    def test_direct_upload_saves_browser_dimensions_for_all_orientations(self):
        for index, (width, height) in enumerate(
            ((4000, 3000), (3000, 4000), (3000, 3000)), start=1
        ):
            with self.subTest(width=width, height=height):
                key = f"publications/2026/09/{index:032d}.jpg"
                upload, token = self.ready_upload(
                    key=key, width=width, height=height
                )
                form = PublicationAdminForm(
                    data={
                        "issue_number": f"0{index}",
                        "title": f"Publication {index}",
                        "slug": f"publication-{index}",
                        "cover_image": token,
                        "cover_image_focus_x": "50",
                        "cover_image_focus_y": "50",
                        "tone": "rice",
                        "sort_order": "0",
                    },
                    request=Mock(user=self.user),
                )
                self.assertTrue(form.is_valid(), form.errors)
                publication = form.save()
                form.mark_direct_uploads_attached()
                upload.refresh_from_db()

                self.assertEqual(publication.cover_image.name, key)
                self.assertEqual(
                    (publication.cover_image_width, publication.cover_image_height),
                    (width, height),
                )
                self.assertEqual(upload.status, DirectImageUpload.Status.ATTACHED)

    def test_removing_cover_clears_dimensions_without_changing_focus(self):
        publication = Publication.objects.create(
            issue_number="01",
            title="Remove",
            slug="remove-publication",
            cover_image="publications/existing.jpg",
            cover_image_width=1200,
            cover_image_height=800,
            cover_image_focus_x=30,
            cover_image_focus_y=65,
        )
        form = PublicationAdminForm(
            data={
                "issue_number": "01",
                "title": "Remove",
                "slug": "remove-publication",
                "cover_image": "",
                "cover_image_focus_x": "30",
                "cover_image_focus_y": "65",
                "tone": "rice",
                "sort_order": "0",
            },
            instance=publication,
            request=Mock(user=self.user),
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        publication.refresh_from_db()
        self.assertEqual(publication.cover_image.name, "")
        self.assertIsNone(publication.cover_image_width)
        self.assertIsNone(publication.cover_image_height)
        self.assertEqual(
            (publication.cover_image_focus_x, publication.cover_image_focus_y),
            (30, 65),
        )


class PublicationImageAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "publication-ui-admin", "publication-ui@example.com", "password"
        )
        self.client.force_login(self.user)

    def test_admin_reuses_focal_editor_and_direct_r2_upload_widget(self):
        response = self.client.get(reverse("admin:content_publication_add"))

        self.assertContains(response, 'data-category="publication.cover_image"')
        self.assertContains(response, 'data-focal-point="true"')
        self.assertContains(response, "出版品列表預覽")
        self.assertContains(response, "出版品頁面預覽")
        self.assertContains(response, 'type="hidden" name="cover_image_focus_x"')
        self.assertContains(response, 'type="hidden" name="cover_image_focus_y"')
        self.assertNotContains(response, 'name="cover_image_width"')
        self.assertNotContains(response, 'name="cover_image_height"')
        self.assertContains(response, "直接上傳至 Cloudflare R2")

    def test_shared_manager_keeps_pointer_keyboard_reset_and_configurable_previews(self):
        source = (
            Path(settings.BASE_DIR) / "static/admin/js/admin-image-manager.js"
        ).read_text()

        self.assertIn('JSON.parse(root.dataset.focalPreviews || "[]")', source)
        self.assertIn('surface.addEventListener("pointerdown"', source)
        self.assertIn('surface.addEventListener("pointermove"', source)
        self.assertIn('surface.addEventListener("pointerup", finishPointer)', source)
        self.assertIn('surface.addEventListener("keydown"', source)
        self.assertIn("event.shiftKey ? 5 : 1", source)
        self.assertIn('editor.querySelector("[data-focal-reset]")', source)

    def test_admin_form_accepts_boundaries_and_rejects_invalid_focus(self):
        base = {
            "issue_number": "01",
            "title": "Focus validation",
            "slug": "publication-focus-validation",
            "cover_image": "",
            "tone": "rice",
            "sort_order": "0",
        }
        valid = PublicationAdminForm(
            data={
                **base,
                "cover_image_focus_x": "0",
                "cover_image_focus_y": "100",
            }
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        saved = valid.save()
        self.assertEqual(
            (saved.cover_image_focus_x, saved.cover_image_focus_y), (0, 100)
        )

        for field, value in (
            ("cover_image_focus_x", "not-a-number"),
            ("cover_image_focus_x", "-1"),
            ("cover_image_focus_y", "101"),
        ):
            with self.subTest(field=field, value=value):
                invalid = PublicationAdminForm(
                    data={
                        **base,
                        "cover_image_focus_x": "50",
                        "cover_image_focus_y": "50",
                        field: value,
                    }
                )
                self.assertFalse(invalid.is_valid())
                self.assertIn(field, invalid.errors)


class PublicationImagePublicTests(TestCase):
    def test_known_dimensions_render_natural_ratios_orientations_and_focus(self):
        publications = [
            Publication.objects.create(
                issue_number="P",
                title="Portrait",
                slug="public-portrait",
                cover_image="publications/portrait.jpg",
                cover_image_width=3000,
                cover_image_height=4000,
                cover_image_focus_x=20,
                cover_image_focus_y=75,
                published=True,
            ),
            Publication.objects.create(
                issue_number="L",
                title="Landscape",
                slug="public-landscape",
                cover_image="publications/landscape.jpg",
                cover_image_width=3840,
                cover_image_height=2160,
                published=True,
            ),
            Publication.objects.create(
                issue_number="S",
                title="Square",
                slug="public-square",
                cover_image="publications/square.jpg",
                cover_image_width=3000,
                cover_image_height=3000,
                published=True,
            ),
        ]

        listing_html = self.client.get(reverse("content:publication_list")).content.decode()
        self.assertIn("--ratio:3000 / 4000", listing_html)
        self.assertIn("--ratio:3840 / 2160", listing_html)
        self.assertIn("--ratio:3000 / 3000", listing_html)
        self.assertIn("publication-image--portrait", listing_html)
        self.assertIn("publication-image--landscape", listing_html)
        self.assertIn("publication-image--square", listing_html)
        self.assertIn("object-position:20% 75%", listing_html)

        for publication in publications:
            with self.subTest(slug=publication.slug):
                detail_html = self.client.get(publication.get_absolute_url()).content.decode()
                self.assertIn(
                    f"--ratio:{publication.cover_image_width} / {publication.cover_image_height}",
                    detail_html,
                )
                self.assertIn(publication.cover_image_orientation_class, detail_html)

    def test_unknown_dimensions_keep_safe_context_specific_fallbacks(self):
        publication = Publication.objects.create(
            issue_number="U",
            title="Unknown",
            slug="public-unknown",
            cover_image="publications/unknown.jpg",
            cover_image_focus_x=35,
            cover_image_focus_y=60,
            published=True,
        )

        listing_html = self.client.get(reverse("content:publication_list")).content.decode()
        detail_html = self.client.get(publication.get_absolute_url()).content.decode()

        self.assertIn("--ratio:3 / 4.25", listing_html)
        self.assertIn("--ratio:16 / 9", detail_html)
        self.assertIn("publication-image--unknown", listing_html)
        self.assertIn("object-position:35% 60%", listing_html)
        self.assertIn("object-position:35% 60%", detail_html)
