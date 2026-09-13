import logging
import re
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage

from .admin_image_uploads import (
    ALLOWED_CONTENT_TYPES,
    ImageMetadataValidationError,
    generate_presigned_put,
    normalized_head_metadata,
    storage_connection,
    validate_image_metadata,
)
from .models import DirectImageUpload

logger = logging.getLogger("restfull.image_uploads")

PRESIGN_EXPIRES_SECONDS = 600
TOKEN_MAX_AGE_SECONDS = 24 * 60 * 60
TOKEN_SALT = "admin-direct-image-v1"

IMAGE_CATEGORIES = {
    "site.brand_logo": ("core.change_sitesettings", "site/branding", "artwork"),
    "site.shop_logo": ("core.change_sitesettings", "site/branding", "artwork"),
    "site.taiwan_pay_qr": ("core.change_sitesettings", "payments/taiwan-pay", "qr"),
    "site.default_og_image": ("core.change_sitesettings", "site/seo", "photo"),
    "site.home_hero_image": ("core.change_sitesettings", "site/home", "photo"),
    "site.shop_hero_image": ("core.change_sitesettings", "site/shop", "photo"),
    "site.shop_story_image": ("core.change_sitesettings", "site/shop", "photo"),
    "site.about_image": ("core.change_sitesettings", "site/about", "photo"),
    "project.featured_image": ("content.change_interiorproject", "projects", "photo"),
    "project.gallery_image": (
        "content.change_interiorprojectimage",
        "projects/gallery",
        "photo",
    ),
    "publication.cover_image": ("content.change_publication", "publications", "photo"),
    "payment.qr_image": ("orders.change_paymentmethod", "payments/methods", "qr"),
}
SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9/_-]*\.(?:jpg|jpeg|png|webp)$")


class DirectImageError(Exception):
    def __init__(self, message, code="invalid_request", status=400):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)


def category_config(category):
    try:
        return IMAGE_CATEGORIES[str(category)]
    except KeyError as exc:
        raise DirectImageError("無法識別圖片用途。", "invalid_category") from exc


def require_category_permission(user, category):
    permission, _prefix, _profile = category_config(category)
    app, action_model = permission.split(".", 1)
    model = action_model.removeprefix("change_")
    if (
        not user.is_active
        or not user.is_staff
        or not (user.has_perm(permission) or user.has_perm(f"{app}.add_{model}"))
    ):
        raise DirectImageError("您沒有上傳此圖片的權限。", "permission_denied", 403)


def validate_metadata(*, filename, content_type, size, width, height):
    try:
        metadata = validate_image_metadata(
            filename=filename,
            content_type=content_type,
            size=size,
            width=width,
            height=height,
            max_bytes=settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES,
            max_dimension=settings.ADMIN_IMAGE_MAX_DIMENSION,
            max_pixels=settings.ADMIN_IMAGE_MAX_OUTPUT_PIXELS,
        )
    except ImageMetadataValidationError as exc:
        code = (
            "invalid_metadata"
            if exc.code in ("invalid_size", "invalid_dimensions")
            else exc.code
        )
        message = exc.message
        if exc.code == "extension_mismatch":
            message = "圖片格式與副檔名不一致。"
        raise DirectImageError(message, code) from exc
    return (
        metadata.filename,
        metadata.content_type,
        metadata.size,
        metadata.width,
        metadata.height,
        metadata.extension,
    )


def _storage_client():
    try:
        return storage_connection()
    except AttributeError as exc:
        raise DirectImageError(
            "圖片儲存服務目前無法使用。", "storage_not_configured", 503
        ) from exc


def create_upload(*, user, category, filename, content_type, size, width, height):
    require_category_permission(user, category)
    _permission, prefix, _profile = category_config(category)
    filename, content_type, size, width, height, extension = validate_metadata(
        filename=filename,
        content_type=content_type,
        size=size,
        width=width,
        height=height,
    )
    from django.utils import timezone

    key = f"{prefix}/{timezone.now():%Y/%m}/{uuid4().hex}{extension}"
    upload = DirectImageUpload.objects.create(
        object_key=key,
        category=category,
        original_filename=filename,
        content_type=content_type,
        file_size=size,
        width=width,
        height=height,
        uploaded_by=user,
    )
    try:
        client, bucket = _storage_client()
        url = generate_presigned_put(
            client=client,
            bucket=bucket,
            object_key=key,
            content_type=content_type,
            expires_in=PRESIGN_EXPIRES_SECONDS,
        )
    except Exception as exc:
        upload.delete()
        logger.exception(
            "image_presign_failed category=%s user_id=%s", category, user.pk
        )
        raise DirectImageError(
            "圖片上傳準備失敗，請重試。", "presign_failed", 503
        ) from exc
    logger.info(
        "image_presigned category=%s width=%s height=%s bytes=%s mime=%s user_id=%s",
        category,
        width,
        height,
        size,
        content_type,
        user.pk,
    )
    return upload, url


def complete_upload(*, user, category, upload_id, object_key):
    require_category_permission(user, category)
    if not SAFE_KEY.fullmatch(str(object_key or "")) or ".." in str(object_key):
        raise DirectImageError("圖片識別資料無效。", "invalid_object_key")
    try:
        upload = DirectImageUpload.objects.get(
            pk=upload_id,
            object_key=object_key,
            category=category,
            uploaded_by=user,
            status=DirectImageUpload.Status.PENDING,
        )
    except DirectImageUpload.DoesNotExist as exc:
        raise DirectImageError(
            "找不到這次圖片上傳，請重新選擇圖片。", "upload_not_owned", 404
        ) from exc
    try:
        client, bucket = _storage_client()
        metadata = client.head_object(Bucket=bucket, Key=object_key)
    except Exception as exc:
        logger.exception(
            "image_head_failed upload_id=%s category=%s", upload.pk, category
        )
        raise DirectImageError(
            "無法確認圖片已上傳，請按重試。", "head_failed", 503
        ) from exc
    actual_size, actual_type = normalized_head_metadata(metadata)
    if (
        actual_size != upload.file_size
        or actual_size <= 0
        or actual_size > settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES
    ):
        _delete_invalid_upload(upload, reason="size_mismatch")
        raise DirectImageError(
            "上傳後的圖片大小不一致，請重新選擇圖片。", "size_mismatch"
        )
    if actual_type != upload.content_type or actual_type not in ALLOWED_CONTENT_TYPES:
        _delete_invalid_upload(upload, reason="mime_mismatch")
        raise DirectImageError(
            "上傳後的圖片格式不一致，請重新選擇圖片。", "mime_mismatch"
        )
    upload.status = DirectImageUpload.Status.READY
    upload.save(update_fields=("status", "updated_at"))
    token = signing.dumps(
        {"id": upload.pk, "key": upload.object_key, "category": category},
        salt=TOKEN_SALT,
        compress=True,
    )
    logger.info(
        "image_completed category=%s width=%s height=%s bytes=%s mime=%s upload_id=%s",
        category,
        upload.width,
        upload.height,
        upload.file_size,
        upload.content_type,
        upload.pk,
    )
    return upload, token


def _delete_invalid_upload(upload, *, reason):
    try:
        default_storage.delete(upload.object_key)
    except Exception:
        upload.status = DirectImageUpload.Status.DELETION_PENDING
        upload.save(update_fields=("status", "updated_at"))
        logger.exception(
            "invalid_direct_image_cleanup_failed upload_id=%s reason=%s",
            upload.pk,
            reason,
        )
    else:
        upload.delete()


def delete_upload(*, user, category, upload_id, object_key):
    """Delete an unattached direct upload selected in a single-image widget."""
    require_category_permission(user, category)
    if not SAFE_KEY.fullmatch(str(object_key or "")) or ".." in str(object_key):
        raise DirectImageError("圖片識別資料無效。", "invalid_object_key")
    try:
        upload = DirectImageUpload.objects.get(
            pk=upload_id,
            object_key=object_key,
            category=category,
            uploaded_by=user,
            status__in=(
                DirectImageUpload.Status.PENDING,
                DirectImageUpload.Status.READY,
            ),
        )
    except DirectImageUpload.DoesNotExist as exc:
        raise DirectImageError("找不到這次圖片上傳。", "upload_not_owned", 404) from exc
    upload.status = DirectImageUpload.Status.DELETION_PENDING
    upload.save(update_fields=("status", "updated_at"))
    try:
        default_storage.delete(upload.object_key)
    except Exception as exc:
        logger.exception("direct_image_delete_failed upload_id=%s", upload.pk)
        raise DirectImageError("刪除圖片失敗，請重試。", "delete_failed", 503) from exc
    upload.delete()


def resolve_token(*, token, user, category):
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature as exc:
        raise DirectImageError(
            "圖片確認資料已失效，請重新選擇圖片。", "invalid_token"
        ) from exc
    try:
        upload = DirectImageUpload.objects.get(
            pk=payload["id"],
            object_key=payload["key"],
            category=category,
            uploaded_by=user,
            status__in=(
                DirectImageUpload.Status.READY,
                DirectImageUpload.Status.ATTACHED,
            ),
        )
    except (KeyError, DirectImageUpload.DoesNotExist) as exc:
        raise DirectImageError(
            "無法確認圖片，請重新選擇圖片。", "upload_not_owned"
        ) from exc
    return upload


def mark_attached(upload_ids):
    DirectImageUpload.objects.filter(
        pk__in=set(upload_ids), status=DirectImageUpload.Status.READY
    ).update(status=DirectImageUpload.Status.ATTACHED)
