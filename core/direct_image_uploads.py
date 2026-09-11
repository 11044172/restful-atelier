import logging
import os
import re
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage

from .models import DirectImageUpload

logger = logging.getLogger("restfull.image_uploads")

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
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
    "project.gallery_image": ("content.change_interiorprojectimage", "projects/gallery", "photo"),
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
    if not user.is_active or not user.is_staff or not (
        user.has_perm(permission) or user.has_perm(f"{app}.add_{model}")
    ):
        raise DirectImageError("您沒有上傳此圖片的權限。", "permission_denied", 403)


def validate_metadata(*, filename, content_type, size, width, height):
    safe_name = os.path.basename(str(filename or "")).strip()
    if not safe_name or safe_name != str(filename or "").strip():
        raise DirectImageError("檔案名稱無效。", "invalid_filename")
    normalized_type = str(content_type or "").split(";", 1)[0].lower().strip()
    if normalized_type not in ALLOWED_CONTENT_TYPES:
        raise DirectImageError("僅支援 JPG、PNG 或 WebP 圖片。", "unsupported_type")
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_CONTENT_TYPES[normalized_type]:
        raise DirectImageError("圖片格式與副檔名不一致。", "extension_mismatch")
    try:
        size, width, height = int(size), int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise DirectImageError("圖片尺寸資料無效。", "invalid_metadata") from exc
    if size <= 0 or width <= 0 or height <= 0:
        raise DirectImageError("圖片尺寸資料無效。", "invalid_metadata")
    if size > settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES:
        raise DirectImageError("處理後的圖片仍然過大，請選擇較小的圖片。", "file_too_large")
    if width > settings.ADMIN_IMAGE_MAX_DIMENSION or height > settings.ADMIN_IMAGE_MAX_DIMENSION:
        raise DirectImageError("圖片邊長超過安全上限。", "dimensions_too_large")
    if width * height > settings.ADMIN_IMAGE_MAX_OUTPUT_PIXELS:
        raise DirectImageError("圖片像素數超過安全上限。", "pixels_too_large")
    return safe_name[:255], normalized_type, size, width, height, extension


def _storage_client():
    try:
        return default_storage.connection.meta.client, default_storage.bucket_name
    except AttributeError as exc:
        raise DirectImageError("圖片儲存服務目前無法使用。", "storage_not_configured", 503) from exc


def create_upload(*, user, category, filename, content_type, size, width, height):
    require_category_permission(user, category)
    _permission, prefix, _profile = category_config(category)
    filename, content_type, size, width, height, extension = validate_metadata(
        filename=filename, content_type=content_type, size=size, width=width, height=height
    )
    from django.utils import timezone
    key = f"{prefix}/{timezone.now():%Y/%m}/{uuid4().hex}{extension}"
    upload = DirectImageUpload.objects.create(
        object_key=key, category=category, original_filename=filename,
        content_type=content_type, file_size=size, width=width, height=height,
        uploaded_by=user,
    )
    try:
        client, bucket = _storage_client()
        url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=PRESIGN_EXPIRES_SECONDS,
            HttpMethod="PUT",
        )
    except Exception as exc:
        upload.delete()
        logger.exception("image_presign_failed category=%s user_id=%s", category, user.pk)
        raise DirectImageError("圖片上傳準備失敗，請重試。", "presign_failed", 503) from exc
    logger.info("image_presigned category=%s width=%s height=%s bytes=%s mime=%s user_id=%s", category, width, height, size, content_type, user.pk)
    return upload, url


def complete_upload(*, user, category, upload_id, object_key):
    require_category_permission(user, category)
    if not SAFE_KEY.fullmatch(str(object_key or "")) or ".." in str(object_key):
        raise DirectImageError("圖片識別資料無效。", "invalid_object_key")
    try:
        upload = DirectImageUpload.objects.get(
            pk=upload_id, object_key=object_key, category=category,
            uploaded_by=user, status=DirectImageUpload.Status.PENDING,
        )
    except DirectImageUpload.DoesNotExist as exc:
        raise DirectImageError("找不到這次圖片上傳，請重新選擇圖片。", "upload_not_owned", 404) from exc
    try:
        client, bucket = _storage_client()
        metadata = client.head_object(Bucket=bucket, Key=object_key)
    except Exception as exc:
        logger.exception("image_head_failed upload_id=%s category=%s", upload.pk, category)
        raise DirectImageError("無法確認圖片已上傳，請按重試。", "head_failed", 503) from exc
    actual_size = int(metadata.get("ContentLength", -1))
    actual_type = str(metadata.get("ContentType", "")).split(";", 1)[0].lower().strip()
    if actual_size != upload.file_size or actual_size <= 0 or actual_size > settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES:
        raise DirectImageError("上傳後的圖片大小不一致，請重新選擇圖片。", "size_mismatch")
    if actual_type != upload.content_type or actual_type not in ALLOWED_CONTENT_TYPES:
        raise DirectImageError("上傳後的圖片格式不一致，請重新選擇圖片。", "mime_mismatch")
    upload.status = DirectImageUpload.Status.READY
    upload.save(update_fields=("status", "updated_at"))
    token = signing.dumps({"id": upload.pk, "key": upload.object_key, "category": category}, salt=TOKEN_SALT, compress=True)
    logger.info("image_completed category=%s width=%s height=%s bytes=%s mime=%s upload_id=%s", category, upload.width, upload.height, upload.file_size, upload.content_type, upload.pk)
    return upload, token


def resolve_token(*, token, user, category):
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature as exc:
        raise DirectImageError("圖片確認資料已失效，請重新選擇圖片。", "invalid_token") from exc
    try:
        upload = DirectImageUpload.objects.get(
            pk=payload["id"], object_key=payload["key"], category=category,
            uploaded_by=user, status__in=(DirectImageUpload.Status.READY, DirectImageUpload.Status.ATTACHED),
        )
    except (KeyError, DirectImageUpload.DoesNotExist) as exc:
        raise DirectImageError("無法確認圖片，請重新選擇圖片。", "upload_not_owned") from exc
    return upload


def mark_attached(upload_ids):
    DirectImageUpload.objects.filter(pk__in=set(upload_ids), status=DirectImageUpload.Status.READY).update(status=DirectImageUpload.Status.ATTACHED)
