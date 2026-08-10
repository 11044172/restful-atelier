from django.contrib import admin
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .models import InteriorProject, InteriorProjectImage, PolicyPage, Publication


class InteriorProjectImageInline(admin.TabularInline):
    model = InteriorProjectImage
    extra = 1
    fields = ("preview", "image", "alt_text", "caption", "tone", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="圖片預覽")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:72px;height:72px;object-fit:cover" alt="">', obj.image.url)
        return "—"


@admin.register(InteriorProject, site=backoffice_site)
class InteriorProjectAdmin(admin.ModelAdmin):
    list_display = ("thumbnail", "title", "project_type", "location", "year", "published", "sort_order", "updated_at")
    list_filter = ("published", "project_type", "year")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "english_title", "location", "description", "style")
    prepopulated_fields = {"slug": ("english_title",)}
    inlines = (InteriorProjectImageInline,)

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.featured_image:
            return format_html('<img class="admin-thumbnail" src="{}" alt="">', obj.featured_image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.title[:1])


@admin.register(Publication, site=backoffice_site)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("thumbnail", "issue_number", "title", "published_date", "featured", "published", "sort_order")
    list_filter = ("published", "featured")
    list_editable = ("featured", "published", "sort_order")
    search_fields = ("issue_number", "title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.cover_image:
            return format_html('<img class="admin-thumbnail admin-thumbnail-cover" src="{}" alt="">', obj.cover_image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.issue_number[:1])


@admin.register(PolicyPage, site=backoffice_site)
class PolicyPageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "published", "sort_order", "updated_at")
    list_filter = ("published",)
    list_editable = ("published", "sort_order")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
