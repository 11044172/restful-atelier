from django.contrib import admin
from django.utils.html import format_html

from .models import InteriorProject, InteriorProjectImage, PolicyPage, Publication


class InteriorProjectImageInline(admin.TabularInline):
    model = InteriorProjectImage
    extra = 1
    fields = ("preview", "image", "alt_text", "caption", "tone", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="プレビュー")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:72px;height:72px;object-fit:cover" alt="">', obj.image.url)
        return "—"


@admin.register(InteriorProject)
class InteriorProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "project_type", "location", "year", "published", "sort_order", "updated_at")
    list_filter = ("published", "project_type", "year")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "english_title", "location", "description", "style")
    prepopulated_fields = {"slug": ("english_title",)}
    inlines = (InteriorProjectImageInline,)


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("issue_number", "title", "published_date", "featured", "published", "sort_order")
    list_filter = ("published", "featured")
    list_editable = ("featured", "published", "sort_order")
    search_fields = ("issue_number", "title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(PolicyPage)
class PolicyPageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "published", "sort_order", "updated_at")
    list_filter = ("published",)
    list_editable = ("published", "sort_order")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
