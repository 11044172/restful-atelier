import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.forms import ProductCategoryAdminForm
from catalog.models import ProductCategory
from core.admin_forms import InteriorProjectAdminForm
from content.models import InteriorProject


class FriendlyJsonAdminTests(TestCase):
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
        user = get_user_model().objects.create_superuser("json-admin", "json@example.com", "password")
        self.client.force_login(user)
        category = ProductCategory.objects.create(name="分類", slug="category", subcategories=["茶器"])
        project = InteriorProject.objects.create(title="住宅", slug="home", description="說明", design_notes=["筆記"], materials=["木"])
        for url in (reverse("admin:catalog_productcategory_change", args=[category.pk]), reverse("admin:content_interiorproject_change", args=[project.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "新增項目")
            self.assertNotContains(response, '<textarea name="subcategories"')
            self.assertNotContains(response, '<textarea name="design_notes"')
