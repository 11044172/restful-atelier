import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from catalog.forms import ProductCategoryAdminForm
from catalog.models import ProductCategory
from content.models import InteriorProject
from core.admin_forms import InteriorProjectAdminForm


class FriendlyJsonAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser(
            "json-admin", "json@example.com", "password"
        )

    def test_existing_lists_expand_and_round_trip_unicode_and_special_characters(self):
        category = ProductCategory(name="分類", slug="category", subcategories=["茶器", "A & B", '引號 " 測試'])
        form = ProductCategoryAdminForm(instance=category)
        html = str(form["subcategories"])
        self.assertIn("茶器", html)
        self.assertIn("A &amp; B", html)
        self.assertNotIn("textarea", html)

        values = ["茶器", "新增／限定", "emoji 🍵", '引號 " 與 <標籤>']
        bound = ProductCategoryAdminForm(data={"name":"分類","slug":"category","english_name":"","description":"","subcategories":json.dumps(values, ensure_ascii=False),"tone":"sand","sort_order":0,"is_active":True}, instance=category)
        self.assertTrue(bound.is_valid(), bound.errors)
        self.assertEqual(bound.save().subcategories, values)

    def test_project_lists_edit_independently_without_data_loss(self):
        project = InteriorProject(title="住宅", slug="home", description="說明", design_notes=["原始筆記一", "原始筆記二"], materials=["木", "石"])
        data = {"title":"住宅","slug":"home","english_title":"","project_type":"","location":"","year":"","area":"","style":"","description":"說明","concept_title":"","design_notes":json.dumps(["更新筆記", "第二行"]),"materials":json.dumps(project.materials),"featured_image":"","image_label":"","tone":"bamboo","published":False,"sort_order":0}
        form = InteriorProjectAdminForm(data=data, instance=project)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.design_notes, ["更新筆記", "第二行"])
        self.assertEqual(saved.materials, ["木", "石"])

    def test_optional_and_invalid_values(self):
        base = {"name":"分類","slug":"category","english_name":"","description":"","tone":"sand","sort_order":0,"is_active":True}
        empty = ProductCategoryAdminForm(data={**base,"subcategories":"[]"})
        self.assertTrue(empty.is_valid(), empty.errors)
        self.assertEqual(empty.cleaned_data["subcategories"], [])
        for value in ('{"key":"value"}', '["ok", 3]', "not-json"):
            with self.subTest(value=value):
                form = ProductCategoryAdminForm(data={**base,"subcategories":value})
                self.assertFalse(form.is_valid())
                self.assertIn("subcategories", form.errors)

    def test_admin_pages_show_repeatable_controls_instead_of_json_textareas(self):
        self.client.force_login(self.admin_user)
        category = ProductCategory.objects.create(name="分類", slug="category", subcategories=["茶器"])
        project = InteriorProject.objects.create(title="住宅", slug="home", description="說明", design_notes=["筆記"], materials=["木"])
        for url in (reverse("admin:catalog_productcategory_change", args=[category.pk]), reverse("admin:content_interiorproject_change", args=[project.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "新增項目")
            self.assertNotContains(response, '<textarea name="subcategories"')
            self.assertNotContains(response, '<textarea name="design_notes"')

    def _project_post_data(self, project, *, design_notes, materials):
        return {
            "title": project.title,
            "slug": project.slug,
            "english_title": project.english_title,
            "project_type": project.project_type,
            "location": project.location,
            "year": project.year or "",
            "area": project.area,
            "style": project.style,
            "description": project.description,
            "concept_title": project.concept_title,
            "design_notes": json.dumps(design_notes, ensure_ascii=False),
            "materials": json.dumps(materials, ensure_ascii=False),
            "featured_image": "",
            "featured_image_focus_x": project.featured_image_focus_x,
            "featured_image_focus_y": project.featured_image_focus_y,
            "image_label": project.image_label,
            "tone": project.tone,
            "sort_order": project.sort_order,
            "_save": "儲存",
        }

    def _post_project_lists(self, project, *, design_notes, materials):
        self.client.force_login(self.admin_user)
        url = reverse("admin:content_interiorproject_change", args=[project.pk])
        posted = self._project_post_data(
            project, design_notes=design_notes, materials=materials
        )
        response = self.client.post(url, posted)
        self.assertRedirects(
            response,
            reverse("admin:content_interiorproject_changelist"),
        )

        project.refresh_from_db()
        self.assertEqual(project.design_notes, design_notes)
        self.assertEqual(project.materials, materials)

        reload_response = self.client.get(url)
        self.assertEqual(reload_response.status_code, 200)
        for value in design_notes + materials:
            self.assertContains(reload_response, value)

    def test_project_list_admin_post_edits_existing_item_and_persists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="edit-item", description="說明",
            design_notes=["壁面いっぱいの本棚", "窓辺のデスク"],
            materials=["木", "石"],
        )
        self._post_project_lists(
            project,
            design_notes=["造作本棚", "窓辺のデスク"],
            materials=["木", "石"],
        )

    def test_project_list_admin_post_adds_item_and_persists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="add-item", description="說明",
            design_notes=["筆記"], materials=["木"],
        )
        self._post_project_lists(
            project,
            design_notes=["筆記", "新しい筆記"],
            materials=["木", "ガラス"],
        )

    def test_project_list_admin_post_deletes_item_and_persists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="delete-item", description="說明",
            design_notes=["残す", "削除する"], materials=["木", "削除する材質"],
        )
        self._post_project_lists(
            project, design_notes=["残す"], materials=["木"]
        )

    def test_project_list_admin_post_reorders_items_and_persists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="reorder-items", description="說明",
            design_notes=["A", "B", "C"], materials=["1", "2", "3"],
        )
        self._post_project_lists(
            project,
            design_notes=["C", "A", "B"],
            materials=["3", "1", "2"],
        )

    def test_project_list_admin_post_combines_changes_and_persists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="combined-items", description="說明",
            design_notes=["A", "B", "C"], materials=["1", "2", "3"],
        )
        self._post_project_lists(
            project,
            design_notes=["C 更新", "D"],
            materials=["3", "1 更新", "4"],
        )

    def test_project_list_admin_post_can_persist_empty_lists(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="empty-items", description="說明",
            design_notes=["筆記"], materials=["木"],
        )
        self._post_project_lists(project, design_notes=[], materials=[])

    def test_invalid_list_post_does_not_show_success_or_change_database(self):
        project = InteriorProject.objects.create(
            title="住宅", slug="invalid-items", description="說明",
            design_notes=["元の筆記"], materials=["元の材質"],
        )
        self.client.force_login(self.admin_user)
        data = self._project_post_data(
            project, design_notes=["更新予定"], materials=["更新予定"]
        )
        data["design_notes"] = "not-json"
        response = self.client.post(
            reverse("admin:content_interiorproject_change", args=[project.pk]), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "項目資料無法讀取")
        self.assertFalse(
            any(
                message.level == messages.SUCCESS
                for message in get_messages(response.wsgi_request)
            )
        )
        project.refresh_from_db()
        self.assertEqual(project.design_notes, ["元の筆記"])
        self.assertEqual(project.materials, ["元の材質"])

    def test_shared_list_editor_waits_for_dom_and_syncs_on_submit(self):
        source = (
            Path(settings.BASE_DIR) / "static/admin/js/string-list-editor.js"
        ).read_text()
        self.assertIn('document.readyState === "loading"', source)
        self.assertIn('document.addEventListener("DOMContentLoaded", boot', source)
        self.assertIn('addEventListener("submit", sync', source)

    def test_product_category_uses_the_same_fixed_list_save_path(self):
        category = ProductCategory.objects.create(
            name="分類", slug="shared-list", subcategories=["A", "B"]
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin:catalog_productcategory_change", args=[category.pk]),
            {
                "name": category.name,
                "slug": category.slug,
                "english_name": "",
                "description": "",
                "thumbnail_image": "",
                "thumbnail_alt": "",
                "thumbnail_focus_x": 50,
                "thumbnail_focus_y": 50,
                "tone": "sand",
                "subcategories": json.dumps(["B", "C"], ensure_ascii=False),
                "sort_order": 0,
                "is_active": "on",
                "_save": "儲存",
            },
        )
        self.assertRedirects(
            response, reverse("admin:catalog_productcategory_changelist")
        )
        category.refresh_from_db()
        self.assertEqual(category.subcategories, ["B", "C"])
