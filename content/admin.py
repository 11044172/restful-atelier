from django.contrib import admin
from django.utils.html import format_html

from core.admin_site import backoffice_site
from core.admin_forms import (
    InteriorProjectAdminForm, InteriorProjectImageAdminForm, PublicationAdminForm,
    request_bound_form,
)

from .models import InteriorProject, InteriorProjectImage, PolicyPage, Publication


class InteriorProjectImageInline(admin.TabularInline):
    model = InteriorProjectImage
    form = InteriorProjectImageAdminForm
    extra = 1
    fields = ("preview", "image", "alt_text", "caption", "tone", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="圖片預覽")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:72px;height:72px;object-fit:cover" alt="">', obj.image.url)
        return "—"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form = request_bound_form(formset.form, request)
        return formset


@admin.register(InteriorProject, site=backoffice_site)
class InteriorProjectAdmin(admin.ModelAdmin):
    form = InteriorProjectAdminForm
    list_display = ("thumbnail", "title", "project_type", "location", "year", "published", "sort_order", "updated_at")
    list_filter = ("published", "project_type", "year")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "english_title", "location", "description", "style")
    prepopulated_fields = {"slug": ("english_title",)}
    inlines = (InteriorProjectImageInline,)
    readonly_fields = ("preview_link", "created_at", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("title", "slug", "english_title", "project_type", "location", "year", "area", "style")}),
        ("本文與內容", {"fields": ("description", "concept_title", "design_notes", "materials")}),
        ("圖片", {"fields": ("featured_image", "image_label", "tone")}),
        ("公開設定", {"fields": ("published", "preview_link")}),
        ("管理資訊", {"classes": ("collapse",), "fields": ("sort_order", "created_at", "updated_at")}),
    )

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.featured_image:
            return format_html('<img class="admin-thumbnail" src="{}" alt="">', obj.featured_image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.title[:1])

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>', obj.get_absolute_url()) if obj and obj.pk and obj.published else "公開後顯示"

    def get_form(self, request, obj=None, change=False, **kwargs):
        return request_bound_form(super().get_form(request, obj, change=change, **kwargs), request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.mark_direct_uploads_attached()

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        for inline_form in formset.forms:
            if hasattr(inline_form, "mark_direct_uploads_attached"):
                inline_form.mark_direct_uploads_attached()


@admin.register(Publication, site=backoffice_site)
class PublicationAdmin(admin.ModelAdmin):
    form = PublicationAdminForm
    list_display = ("thumbnail", "issue_number", "title", "published_date", "featured", "published", "sort_order")
    list_filter = ("published", "featured")
    list_editable = ("featured", "published", "sort_order")
    search_fields = ("issue_number", "title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("preview_link", "created_at", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("issue_number", "title", "slug", "subtitle", "page_count", "published_date")}),
        ("本文與內容", {"fields": ("description",)}),
        ("圖片", {"fields": ("cover_image", "tone")}),
        ("公開設定", {"fields": ("featured", "published", "preview_link")}),
        ("管理資訊", {"classes": ("collapse",), "fields": ("sort_order", "created_at", "updated_at")}),
    )

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.cover_image:
            return format_html('<img class="admin-thumbnail admin-thumbnail-cover" src="{}" alt="">', obj.cover_image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.issue_number[:1])

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>', obj.get_absolute_url()) if obj and obj.pk and obj.published else "公開後顯示"

    def get_form(self, request, obj=None, change=False, **kwargs):
        return request_bound_form(super().get_form(request, obj, change=change, **kwargs), request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.mark_direct_uploads_attached()


@admin.register(PolicyPage, site=backoffice_site)
class PolicyPageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "version", "effective_date", "legal_reviewed", "published", "sort_order", "updated_at")
    list_filter = ("published", "legal_reviewed")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("preview_link", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("title", "slug")}),
        ("本文與內容", {"fields": ("body",)}),
        ("公開設定", {"fields": ("version", "effective_date", "legal_reviewed", "published", "preview_link")}),
        ("管理資訊", {"classes": ("collapse",), "fields": ("sort_order", "updated_at")}),
    )

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>', obj.get_absolute_url()) if obj and obj.pk and obj.published else "公開後顯示"
