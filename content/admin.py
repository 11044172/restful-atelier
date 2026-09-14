import json
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin_forms import (
    InteriorProjectAdminForm,
    PublicationAdminForm,
    request_bound_form,
)
from core.admin_site import backoffice_site

from .models import InteriorProject, InteriorProjectImage, PolicyPage, Publication
from .project_image_service import (
    PRESIGN_EXPIRES_SECONDS,
    ProjectImageError,
    attach_temporary_images,
    complete_upload,
    create_presigned_upload,
    image_payload,
    mark_and_delete_image,
    normalize_project_images,
    parse_upload_session,
    reorder_temporary_images,
    update_image_metadata,
)

logger = logging.getLogger("content.project_images")
PROJECT_UPLOAD_SESSION_REGISTRY = "content_project_image_upload_sessions"


@admin.register(InteriorProject, site=backoffice_site)
class InteriorProjectAdmin(admin.ModelAdmin):
    form = InteriorProjectAdminForm
    change_form_template = "admin/content/interiorproject/change_form.html"
    list_display = (
        "thumbnail",
        "title",
        "project_type",
        "location",
        "year",
        "published",
        "sort_order",
        "updated_at",
    )
    list_filter = ("published", "project_type", "year")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "english_title", "location", "description", "style")
    prepopulated_fields = {"slug": ("english_title",)}
    readonly_fields = ("preview_link", "created_at", "updated_at")
    fieldsets = (
        (
            "基本資訊",
            {
                "fields": (
                    "title",
                    "slug",
                    "english_title",
                    "project_type",
                    "location",
                    "year",
                    "area",
                    "style",
                )
            },
        ),
        (
            "本文與內容",
            {"fields": ("description", "concept_title", "design_notes", "materials")},
        ),
        (
            "圖片",
            {
                "fields": (
                    "featured_image",
                    "featured_image_focus_x",
                    "featured_image_focus_y",
                    "image_label",
                    "tone",
                )
            },
        ),
        ("公開設定", {"fields": ("published", "preview_link")}),
        (
            "管理資訊",
            {
                "classes": ("collapse",),
                "fields": ("sort_order", "created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.featured_image:
            return format_html(
                '<img class="admin-thumbnail" src="{}" alt="">', obj.featured_image.url
            )
        return format_html(
            '<span class="admin-thumbnail-placeholder">{}</span>', obj.title[:1]
        )

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return (
            format_html(
                '<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>',
                obj.get_absolute_url(),
            )
            if obj and obj.pk and obj.published
            else "公開後顯示"
        )

    def get_form(self, request, obj=None, change=False, **kwargs):
        return request_bound_form(
            super().get_form(request, obj, change=change, **kwargs), request
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "project-images/presign/",
                self.admin_site.admin_view(self.project_image_presign),
                name="content_project_image_presign",
            ),
            path(
                "project-images/complete/",
                self.admin_site.admin_view(self.project_image_complete),
                name="content_project_image_complete",
            ),
            path(
                "project-images/<int:image_id>/delete/",
                self.admin_site.admin_view(self.project_image_delete),
                name="content_project_image_delete",
            ),
            path(
                "project-images/<int:image_id>/metadata/",
                self.admin_site.admin_view(self.project_image_metadata),
                name="content_project_image_metadata",
            ),
            path(
                "project-images/reorder/",
                self.admin_site.admin_view(self.project_image_reorder),
                name="content_project_image_reorder",
            ),
        ]
        return custom_urls + urls

    def _new_upload_session(self, request, project_id):
        registry = request.session.get(PROJECT_UPLOAD_SESSION_REGISTRY, {})
        cutoff = timezone.now().timestamp() - 24 * 60 * 60
        registry = {
            key: value
            for key, value in registry.items()
            if value.get("created_at", 0) >= cutoff
            and value.get("user_id") == request.user.pk
        }
        upload_session = uuid4()
        registry[str(upload_session)] = {
            "user_id": request.user.pk,
            "project_id": project_id,
            "created_at": timezone.now().timestamp(),
        }
        request.session[PROJECT_UPLOAD_SESSION_REGISTRY] = registry
        request.session.modified = True
        return upload_session

    def _session_context(self, request, upload_session, project_id):
        registry = request.session.get(PROJECT_UPLOAD_SESSION_REGISTRY, {})
        record = registry.get(str(upload_session))
        if not record or record.get("user_id") != request.user.pk:
            raise ProjectImageError(
                "無法確認上傳工作階段。", "invalid_upload_session", 403
            )
        if record.get("created_at", 0) < timezone.now().timestamp() - 24 * 60 * 60:
            raise ProjectImageError(
                "上傳工作階段已逾期。", "expired_upload_session", 403
            )
        if record.get("project_id") != project_id:
            raise ProjectImageError(
                "上傳工作階段所屬作品不一致。", "session_project_mismatch", 403
            )
        if project_id is None:
            if not self.has_add_permission(request):
                raise ProjectImageError(
                    "您沒有新增作品圖片的權限。", "permission_denied", 403
                )
            return None
        project = InteriorProject.objects.filter(pk=project_id).first()
        if not project:
            raise ProjectImageError("找不到指定作品。", "project_not_found", 404)
        if not self.has_change_permission(request, project):
            raise ProjectImageError(
                "您沒有修改作品圖片的權限。", "permission_denied", 403
            )
        return project

    @staticmethod
    def _json_body(request):
        if request.method != "POST":
            raise ProjectImageError("必須使用 POST 請求。", "method_not_allowed", 405)
        if len(request.body) > 32 * 1024:
            raise ProjectImageError("請求內容過大。", "request_too_large", 413)
        try:
            data = json.loads(request.body or "{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ProjectImageError("請求格式無效。", "invalid_json") from exc
        if not isinstance(data, dict):
            raise ProjectImageError("請求格式無效。", "invalid_json")
        return data

    @staticmethod
    def _project_id(data):
        value = data.get("project_id")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ProjectImageError("作品 ID 無效。", "invalid_project") from exc

    def _api(self, callback):
        try:
            response = callback()
        except ProjectImageError as exc:
            response = JsonResponse(
                {"error": exc.message, "code": exc.code}, status=exc.status
            )
        except Exception:
            logger.exception("unexpected_project_image_api_error")
            response = JsonResponse(
                {"error": "圖片處理失敗，請重試。", "code": "internal_error"},
                status=500,
            )
        response["Cache-Control"] = "no-store"
        return response

    def _api_context(self, request, data):
        upload_session = parse_upload_session(data.get("upload_session"))
        project_id = self._project_id(data)
        project = self._session_context(request, upload_session, project_id)
        return upload_session, project

    def _owned_image(self, request, *, image_id, upload_session, project):
        image = InteriorProjectImage.objects.filter(pk=image_id).first()
        if not image:
            raise ProjectImageError("找不到指定圖片。", "image_not_found", 404)
        if project:
            owned = image.project_id == project.pk and image.upload_status in (
                InteriorProjectImage.UploadStatus.ATTACHED,
                InteriorProjectImage.UploadStatus.DELETION_PENDING,
            )
        else:
            owned = (
                image.project_id is None
                and image.uploaded_by_id == request.user.pk
                and image.upload_session == upload_session
                and image.upload_status
                in (
                    InteriorProjectImage.UploadStatus.PENDING,
                    InteriorProjectImage.UploadStatus.TEMPORARY,
                )
            )
        if not owned:
            raise ProjectImageError("您無法操作此圖片。", "image_not_owned", 403)
        return image

    def project_image_presign(self, request):
        def action():
            data = self._json_body(request)
            upload_session, _project = self._api_context(request, data)
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

    def project_image_complete(self, request):
        def action():
            data = self._json_body(request)
            upload_session, project = self._api_context(request, data)
            image = complete_upload(
                user=request.user,
                upload_session=upload_session,
                object_key=data.get("object_key"),
                project=project,
            )
            return JsonResponse({"image": image_payload(image)})

        return self._api(action)

    def project_image_delete(self, request, image_id):
        def action():
            data = self._json_body(request)
            upload_session, project = self._api_context(request, data)
            image = self._owned_image(
                request,
                image_id=image_id,
                upload_session=upload_session,
                project=project,
            )
            mark_and_delete_image(image)
            return JsonResponse({"deleted": True, "image_id": image_id})

        return self._api(action)

    def project_image_metadata(self, request, image_id):
        def action():
            data = self._json_body(request)
            upload_session, project = self._api_context(request, data)
            image = self._owned_image(
                request,
                image_id=image_id,
                upload_session=upload_session,
                project=project,
            )
            update_image_metadata(
                image,
                alt_text=(data["alt_text"] if "alt_text" in data else image.alt_text),
                caption=(data["caption"] if "caption" in data else image.caption),
                tone=(data["tone"] if "tone" in data else image.tone),
                focus_x=(data["focus_x"] if "focus_x" in data else image.focus_x),
                focus_y=(data["focus_y"] if "focus_y" in data else image.focus_y),
            )
            return JsonResponse({"image": image_payload(image)})

        return self._api(action)

    def project_image_reorder(self, request):
        def action():
            data = self._json_body(request)
            upload_session, project = self._api_context(request, data)
            raw_ids = data.get("images")
            if not isinstance(raw_ids, list):
                raise ProjectImageError("圖片排序資料無效。", "invalid_image_set")
            try:
                image_ids = [int(value) for value in raw_ids]
            except (TypeError, ValueError) as exc:
                raise ProjectImageError(
                    "圖片排序資料無效。", "invalid_image_set"
                ) from exc
            if project:
                normalize_project_images(project, image_ids)
            else:
                reorder_temporary_images(
                    user=request.user,
                    upload_session=upload_session,
                    ordered_ids=image_ids,
                )
            return JsonResponse({"images": image_ids})

        return self._api(action)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        project_id = int(object_id) if object_id else None
        if request.method == "POST" and request.POST.get("project_image_session"):
            try:
                upload_session = parse_upload_session(
                    request.POST["project_image_session"]
                )
            except ProjectImageError:
                upload_session = self._new_upload_session(request, project_id)
        else:
            upload_session = self._new_upload_session(request, project_id)
        project = self.get_object(request, object_id) if object_id else None
        if project:
            images = [
                image_payload(image)
                for image in project.images.filter(
                    upload_status=InteriorProjectImage.UploadStatus.ATTACHED
                ).order_by("sort_order", "pk")
            ]
        else:
            images = [
                image_payload(image)
                for image in InteriorProjectImage.objects.filter(
                    project__isnull=True,
                    uploaded_by=request.user,
                    upload_session=upload_session,
                    upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
                ).order_by("sort_order", "pk")
            ]
        context = {
            "project_image_config": {
                "mode": "multiple",
                "uploadSession": str(upload_session),
                "orderValue": ",".join(str(image["id"]) for image in images),
                "scope": {
                    "upload_session": str(upload_session),
                    "project_id": project_id,
                },
                "images": images,
                "presignUrl": reverse("admin:content_project_image_presign"),
                "completeUrl": reverse("admin:content_project_image_complete"),
                "reorderUrl": reverse("admin:content_project_image_reorder"),
                "deleteUrlTemplate": reverse(
                    "admin:content_project_image_delete", args=[999999999]
                ).replace("999999999", "__IMAGE_ID__"),
                "metadataUrlTemplate": reverse(
                    "admin:content_project_image_metadata", args=[999999999]
                ).replace("999999999", "__IMAGE_ID__"),
                "orderInputId": "id_project_image_order",
                "sessionInputId": "id_project_image_session",
                "labels": {
                    "fallbackFilename": "作品圖片",
                    "mainBadge": "",
                },
                "metadataFields": [
                    {
                        "name": "alt_text",
                        "label": "替代文字",
                        "type": "text",
                        "maxLength": 255,
                    },
                    {
                        "name": "caption",
                        "label": "圖片說明",
                        "type": "text",
                        "maxLength": 255,
                    },
                    {
                        "name": "tone",
                        "label": "預留色調",
                        "type": "select",
                        "options": ["linen", "bamboo", "rice", "fog"],
                    },
                ],
                "focalPoint": {
                    "xField": "focus_x",
                    "yField": "focus_y",
                    "defaultX": 50,
                    "defaultY": 50,
                    "previews": [
                        {"label": "列表預覽", "ratio": "5 / 3.4"},
                        {"label": "作品頁預覽", "ratio": "16 / 9"},
                    ],
                },
                "limits": {
                    "maxFiles": 10,
                    "maxInputBytes": settings.ADMIN_IMAGE_MAX_INPUT_BYTES,
                    "maxOutputBytes": settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES,
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
        form.mark_direct_uploads_attached()
        if not change:
            image_ids = getattr(form, "cleaned_project_image_ids", [])
            upload_session = form.cleaned_data.get("project_image_session")
            if upload_session and image_ids:
                attach_temporary_images(
                    project=obj,
                    user=request.user,
                    upload_session=upload_session,
                    ordered_ids=image_ids,
                )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")


@admin.register(Publication, site=backoffice_site)
class PublicationAdmin(admin.ModelAdmin):
    form = PublicationAdminForm
    list_display = (
        "thumbnail",
        "issue_number",
        "title",
        "published_date",
        "featured",
        "published",
        "sort_order",
    )
    list_filter = ("published", "featured")
    list_editable = ("featured", "published", "sort_order")
    search_fields = ("issue_number", "title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("preview_link", "created_at", "updated_at")
    fieldsets = (
        (
            "基本資訊",
            {
                "fields": (
                    "issue_number",
                    "title",
                    "slug",
                    "subtitle",
                    "page_count",
                    "published_date",
                )
            },
        ),
        ("本文與內容", {"fields": ("description",)}),
        ("圖片", {"fields": ("cover_image", "tone")}),
        ("公開設定", {"fields": ("featured", "published", "preview_link")}),
        (
            "管理資訊",
            {
                "classes": ("collapse",),
                "fields": ("sort_order", "created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="")
    def thumbnail(self, obj):
        if obj.cover_image:
            return format_html(
                '<img class="admin-thumbnail admin-thumbnail-cover" src="{}" alt="">',
                obj.cover_image.url,
            )
        return format_html(
            '<span class="admin-thumbnail-placeholder">{}</span>', obj.issue_number[:1]
        )

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return (
            format_html(
                '<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>',
                obj.get_absolute_url(),
            )
            if obj and obj.pk and obj.published
            else "公開後顯示"
        )

    def get_form(self, request, obj=None, change=False, **kwargs):
        return request_bound_form(
            super().get_form(request, obj, change=change, **kwargs), request
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.mark_direct_uploads_attached()


@admin.register(PolicyPage, site=backoffice_site)
class PolicyPageAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "version",
        "effective_date",
        "legal_reviewed",
        "published",
        "sort_order",
        "updated_at",
    )
    list_filter = ("published", "legal_reviewed")
    list_editable = ("published", "sort_order")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("preview_link", "updated_at")
    fieldsets = (
        ("基本資訊", {"fields": ("title", "slug")}),
        ("本文與內容", {"fields": ("body",)}),
        (
            "公開設定",
            {
                "fields": (
                    "version",
                    "effective_date",
                    "legal_reviewed",
                    "published",
                    "preview_link",
                )
            },
        ),
        (
            "管理資訊",
            {"classes": ("collapse",), "fields": ("sort_order", "updated_at")},
        ),
    )

    @admin.display(description="公開頁面")
    def preview_link(self, obj):
        return (
            format_html(
                '<a href="{}" target="_blank" rel="noopener">預覽 ↗</a>',
                obj.get_absolute_url(),
            )
            if obj and obj.pk and obj.published
            else "公開後顯示"
        )
