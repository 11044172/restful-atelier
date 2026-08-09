from django.contrib import admin
from django.utils.html import format_html

from .models import Product, ProductCategory, ProductImage, ProductSpecification


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("preview", "image", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    ordering = ("sort_order",)

    @admin.display(description="プレビュー")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:72px;height:72px;object-fit:cover" alt="">', obj.image.url)
        return "—"


class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 1
    fields = ("label", "value", "sort_order")
    ordering = ("sort_order",)


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "english_name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("name", "english_name", "description")
    prepopulated_fields = {"slug": ("english_name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "category", "price", "stock", "is_preorder", "is_published", "sort_order", "updated_at")
    list_filter = ("is_published", "is_preorder", "category")
    search_fields = ("name", "sku", "description", "maker", "series")
    list_editable = ("stock", "is_published", "sort_order")
    list_select_related = ("category",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ProductImageInline, ProductSpecificationInline)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("基本", {"fields": ("category", "name", "slug", "sku", "maker", "series", "subcategory")}),
        ("内容", {"fields": ("short_description", "description", "care", "shipping_note", "maker_story")}),
        ("販売", {"fields": ("price", "stock", "is_preorder", "preorder_note", "badge_label", "is_published", "sort_order")}),
        ("仮画像", {"fields": ("image_label", "tone"), "description": "正式画像が未登録の場合のみ表示されます。"}),
        ("日時", {"fields": ("created_at", "updated_at")}),
    )
