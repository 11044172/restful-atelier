from django.contrib import admin
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .models import Product, ProductCategory, ProductImage, ProductSpecification


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("preview", "image", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    ordering = ("sort_order",)

    @admin.display(description="圖片預覽")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:72px;height:72px;object-fit:cover" alt="">', obj.image.url)
        return "—"


class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 1
    fields = ("label", "value", "sort_order")
    ordering = ("sort_order",)


@admin.register(ProductCategory, site=backoffice_site)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "english_name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("name", "english_name", "description")
    prepopulated_fields = {"slug": ("english_name",)}


@admin.register(Product, site=backoffice_site)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("thumbnail", "name", "sku", "category", "price", "stock", "is_preorder", "is_published", "sort_order", "updated_at")
    list_filter = ("is_published", "is_preorder", "category")
    search_fields = ("name", "sku", "description", "maker", "series")
    list_editable = ("stock", "is_published", "sort_order")
    list_select_related = ("category",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ProductImageInline, ProductSpecificationInline)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("category", "name", "slug", "sku", "maker", "series", "subcategory")}),
        ("商品內容", {"fields": ("short_description", "description", "care", "shipping_note", "maker_story")}),
        ("販售設定", {"fields": ("price", "stock", "is_preorder", "preorder_note", "badge_label", "is_published", "sort_order")}),
        ("預留圖片", {"fields": ("image_label", "tone"), "description": "僅在尚未上傳正式圖片時顯示。"}),
        ("建立與更新時間", {"fields": ("created_at", "updated_at")}),
    )
    list_per_page = 25

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")

    @admin.display(description="")
    def thumbnail(self, obj):
        image = obj.primary_image
        if image and image.image:
            return format_html('<img class="admin-thumbnail" src="{}" alt="">', image.image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.name[:1])
