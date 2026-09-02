from django.contrib import admin
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .forms import ProductAdminForm
from .models import Product, ProductCategory, ProductImage, ProductSpecification


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("preview", "image", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    ordering = ("sort_order",)
    verbose_name_plural = "商品照片（可一次新增多張；替代文字留空時會自動使用商品名稱）"

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
    form = ProductAdminForm
    change_form_template = "admin/catalog/product/change_form.html"
    list_display = ("thumbnail", "display_name", "sku", "category", "publication_state", "readiness_state", "price", "stock", "preview_link", "updated_at")
    list_filter = ("is_published", "is_preorder", "category")
    search_fields = ("name", "sku", "description", "maker", "series")
    list_editable = ("stock",)
    list_select_related = ("category",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ProductImageInline, ProductSpecificationInline)
    readonly_fields = ("preview_link", "created_at", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("category", "name", "slug", "sku", "maker", "series", "subcategory"), "description": "草稿可先留空；slug 與暫用 SKU 會自動建立。"}),
        ("商品資訊", {"fields": ("short_description", "description", "care", "shipping_note", "maker_story")}),
        ("販售設定", {"fields": ("price", "stock", "sale_starts_at", "sale_ends_at", "is_preorder", "preorder_note", "preorder_limit", "preorder_delivery_estimate", "badge_label")}),
        ("公開設定", {"fields": ("is_published", "preview_link")}),
        ("圖片預留設定", {"classes": ("collapse",), "fields": ("image_label", "tone"), "description": "僅在尚未上傳正式圖片時顯示。"}),
        ("管理資訊", {"classes": ("collapse",), "fields": ("sort_order", "created_at", "updated_at")}),
    )
    list_per_page = 25

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")

    @admin.display(description="商品名稱", ordering="name")
    def display_name(self, obj):
        return str(obj)

    @admin.display(description="")
    def thumbnail(self, obj):
        image = obj.primary_image
        if image and image.image:
            return format_html('<img class="admin-thumbnail" src="{}" alt="">', image.image.url)
        return format_html('<span class="admin-thumbnail-placeholder">{}</span>', obj.name[:1] or "草")

    @admin.display(description="狀態", ordering="is_published")
    def publication_state(self, obj):
        if not obj.is_published:
            label, css = "草稿", "draft"
        elif Product.objects.published().filter(pk=obj.pk).exists():
            label, css = "公開中", "published"
        else:
            label, css = "停止販售", "stopped"
        return format_html('<span class="status-pill status-{}">{}</span>', css, label)

    @admin.display(description="公開前檢查")
    def readiness_state(self, obj):
        missing = []
        if not obj.name: missing.append("商品名稱")
        if not obj.sku or obj.sku.startswith("DRAFT-"): missing.append("SKU")
        if obj.price is None: missing.append("售價")
        if not obj.description: missing.append("說明")
        if not obj.primary_image: missing.append("圖片")
        return "準備完成" if not missing else "尚缺：" + "、".join(missing)

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        if not obj or not obj.pk or not Product.objects.published().filter(pk=obj.pk).exists():
            return "公開準備完成後顯示"
        return format_html('<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>', obj.get_absolute_url())
