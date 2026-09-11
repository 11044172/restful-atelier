import json
import logging
from django.conf import settings
from uuid import uuid4

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .forms import ProductAdminForm, ProductCategoryAdminForm
from .models import Product, ProductCategory, ProductImage, ProductSpecification
from .product_image_service import (
    PRESIGN_EXPIRES_SECONDS,
    ProductImageError,
    attach_temporary_images,
    complete_upload,
    create_presigned_upload,
    image_payload,
    mark_and_delete_image,
    normalize_product_images,
    parse_upload_session,
    reorder_temporary_images,
)

logger = logging.getLogger("catalog.product_images")
UPLOAD_SESSION_REGISTRY = "catalog_product_image_upload_sessions"


class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 1
    fields = ("label", "value", "sort_order")
    ordering = ("sort_order",)


@admin.register(ProductCategory, site=backoffice_site)
class ProductCategoryAdmin(admin.ModelAdmin):
    form = ProductCategoryAdminForm
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
    inlines = (ProductSpecificationInline,)
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "product-images/presign/",
                self.admin_site.admin_view(self.product_image_presign),
                name="catalog_product_image_presign",
            ),
            path(
                "product-images/complete/",
                self.admin_site.admin_view(self.product_image_complete),
                name="catalog_product_image_complete",
            ),
            path(
                "product-images/<int:image_id>/delete/",
                self.admin_site.admin_view(self.product_image_delete),
                name="catalog_product_image_delete",
            ),
            path(
                "product-images/reorder/",
                self.admin_site.admin_view(self.product_image_reorder),
                name="catalog_product_image_reorder",
            ),
        ]
        return custom_urls + urls

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)

        class RequestBoundProductForm(form_class):
            def __init__(inner_self, *args, **form_kwargs):
                inner_self.request = request
                super().__init__(*args, **form_kwargs)

        return RequestBoundProductForm

    def _new_upload_session(self, request, product_id):
        registry = request.session.get(UPLOAD_SESSION_REGISTRY, {})
        cutoff = timezone.now().timestamp() - 24 * 60 * 60
        registry = {
            key: value
            for key, value in registry.items()
            if value.get("created_at", 0) >= cutoff and value.get("user_id") == request.user.pk
        }
        upload_session = uuid4()
        registry[str(upload_session)] = {
            "user_id": request.user.pk,
            "product_id": product_id,
            "created_at": timezone.now().timestamp(),
        }
        request.session[UPLOAD_SESSION_REGISTRY] = registry
        request.session.modified = True
        return upload_session

    def _session_context(self, request, upload_session, product_id):
        registry = request.session.get(UPLOAD_SESSION_REGISTRY, {})
        record = registry.get(str(upload_session))
        if not record or record.get("user_id") != request.user.pk:
            raise ProductImageError("無法確認上傳工作階段。", "invalid_upload_session", 403)
        if record.get("created_at", 0) < timezone.now().timestamp() - 24 * 60 * 60:
            raise ProductImageError("上傳工作階段已逾期。", "expired_upload_session", 403)
        recorded_product_id = record.get("product_id")
        if recorded_product_id != product_id:
            raise ProductImageError("上傳工作階段所屬商品不一致。", "session_product_mismatch", 403)
        if product_id is None:
            if not self.has_add_permission(request):
                raise ProductImageError("您沒有新增商品圖片的權限。", "permission_denied", 403)
            return None
        product = Product.objects.filter(pk=product_id).first()
        if not product:
            raise ProductImageError("找不到指定商品。", "product_not_found", 404)
        if not self.has_change_permission(request, product):
            raise ProductImageError("您沒有修改商品圖片的權限。", "permission_denied", 403)
        return product

    @staticmethod
    def _json_body(request):
        if request.method != "POST":
            raise ProductImageError("必須使用 POST 請求。", "method_not_allowed", 405)
        if len(request.body) > 32 * 1024:
            raise ProductImageError("請求內容過大。", "request_too_large", 413)
        try:
            data = json.loads(request.body or "{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ProductImageError("請求格式無效。", "invalid_json") from exc
        if not isinstance(data, dict):
            raise ProductImageError("請求格式無效。", "invalid_json")
        return data

    @staticmethod
    def _product_id(data):
        value = data.get("product_id")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ProductImageError("商品 ID 無效。", "invalid_product") from exc

    def _api(self, callback):
        try:
            response = callback()
        except ProductImageError as exc:
            response = JsonResponse({"error": exc.message, "code": exc.code}, status=exc.status)
        except Exception:
            logger.exception("Unexpected product image API error")
            response = JsonResponse(
                {"error": "圖片處理失敗，請重試。", "code": "internal_error"},
                status=500,
            )
        response["Cache-Control"] = "no-store"
        return response

    def product_image_presign(self, request):
        def action():
            data = self._json_body(request)
            upload_session = parse_upload_session(data.get("upload_session"))
            product_id = self._product_id(data)
            self._session_context(request, upload_session, product_id)
            image, upload_url = create_presigned_upload(
                user=request.user,
                upload_session=upload_session,
                filename=data.get("filename"),
                content_type=data.get("content_type"),
                size=data.get("size"),
                width=data.get("width"),
                height=data.get("height"),
            )
            return JsonResponse(
                {
                    "upload_url": upload_url,
                    "object_key": image.image.name,
                    "pending_image_id": image.pk,
                    "expires_in": PRESIGN_EXPIRES_SECONDS,
                }
            )

        return self._api(action)

    def product_image_complete(self, request):
        def action():
            data = self._json_body(request)
            upload_session = parse_upload_session(data.get("upload_session"))
            product_id = self._product_id(data)
            product = self._session_context(request, upload_session, product_id)
            image = complete_upload(
                user=request.user,
                upload_session=upload_session,
                object_key=data.get("object_key"),
                product=product,
            )
            return JsonResponse({"image": image_payload(image)})

        return self._api(action)

    def product_image_delete(self, request, image_id):
        def action():
            data = self._json_body(request)
            upload_session = parse_upload_session(data.get("upload_session"))
            product_id = self._product_id(data)
            product = self._session_context(request, upload_session, product_id)
            image = ProductImage.objects.filter(pk=image_id).first()
            if not image:
                raise ProductImageError("找不到指定圖片。", "image_not_found", 404)
            if product:
                owned = (
                    image.product_id == product.pk
                    and image.upload_status
                    in (
                        ProductImage.UploadStatus.ATTACHED,
                        ProductImage.UploadStatus.DELETION_PENDING,
                    )
                )
            else:
                owned = (
                    image.product_id is None
                    and image.uploaded_by_id == request.user.pk
                    and image.upload_session == upload_session
                    and image.upload_status
                    in (ProductImage.UploadStatus.PENDING, ProductImage.UploadStatus.TEMPORARY)
                )
            if not owned:
                raise ProductImageError("您無法刪除此圖片。", "image_not_owned", 403)
            mark_and_delete_image(image)
            return JsonResponse({"deleted": True, "image_id": image_id})

        return self._api(action)

    def product_image_reorder(self, request):
        def action():
            data = self._json_body(request)
            upload_session = parse_upload_session(data.get("upload_session"))
            product_id = self._product_id(data)
            product = self._session_context(request, upload_session, product_id)
            raw_ids = data.get("images")
            if not isinstance(raw_ids, list):
                raise ProductImageError("圖片排序資料無效。", "invalid_image_set")
            try:
                image_ids = [int(value) for value in raw_ids]
            except (TypeError, ValueError) as exc:
                raise ProductImageError("圖片排序資料無效。", "invalid_image_set") from exc
            if product:
                normalize_product_images(product, image_ids)
            else:
                reorder_temporary_images(
                    user=request.user,
                    upload_session=upload_session,
                    ordered_ids=image_ids,
                )
            return JsonResponse({"images": image_ids})

        return self._api(action)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        product_id = int(object_id) if object_id else None
        if request.method == "POST" and request.POST.get("product_image_session"):
            try:
                upload_session = parse_upload_session(request.POST["product_image_session"])
            except ProductImageError:
                upload_session = self._new_upload_session(request, product_id)
        else:
            upload_session = self._new_upload_session(request, product_id)

        product = self.get_object(request, object_id) if object_id else None
        if product:
            images = [image_payload(image) for image in product.ordered_images]
        else:
            images = [
                image_payload(image)
                for image in ProductImage.objects.filter(
                    product__isnull=True,
                    uploaded_by=request.user,
                    upload_session=upload_session,
                    upload_status=ProductImage.UploadStatus.TEMPORARY,
                ).order_by("sort_order", "pk")
            ]
        context = {
            "product_image_config": {
                "uploadSession": str(upload_session),
                "productId": product_id,
                "images": images,
                "presignUrl": reverse("admin:catalog_product_image_presign"),
                "completeUrl": reverse("admin:catalog_product_image_complete"),
                "reorderUrl": reverse("admin:catalog_product_image_reorder"),
                "deleteUrlTemplate": reverse(
                    "admin:catalog_product_image_delete", args=[999999999]
                ).replace("999999999", "__IMAGE_ID__"),
                "limits": {
                    "maxInputBytes": settings.ADMIN_IMAGE_MAX_INPUT_BYTES,
                    "maxOutputBytes": settings.PRODUCT_IMAGE_MAX_BYTES,
                    "maxInputPixels": settings.ADMIN_IMAGE_MAX_INPUT_PIXELS,
                    "maxInputDimension": settings.ADMIN_IMAGE_MAX_INPUT_DIMENSION,
                    "longEdge": settings.ADMIN_IMAGE_PHOTO_LONG_EDGE,
                },
            }
        }
        if extra_context:
            context.update(extra_context)
        return super().changeform_view(request, object_id, form_url, context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            image_ids = getattr(form, "cleaned_product_image_ids", [])
            upload_session = form.cleaned_data.get("product_image_session")
            if upload_session and image_ids:
                attach_temporary_images(
                    product=obj,
                    user=request.user,
                    upload_session=upload_session,
                    ordered_ids=image_ids,
                )

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
