"""Bounded uploads to private staging, followed by server-side image validation."""

import hashlib
import os
import warnings
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

STAGING_PREFIX = "pending/"


def atomic_image_operation(error_type):
    """Serialize completion while committing deliberate invalid-upload cleanup."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            error = None
            with transaction.atomic():
                # The same user can complete multiple images in one draft/session.
                # Serialize these too, so their ordering cannot race.
                get_user_model().objects.select_for_update().get(pk=kwargs["user"].pk)
                try:
                    result = function(*args, **kwargs)
                except error_type as exc:
                    error = exc
            if error:
                raise error
            return result

        return wrapped

    return decorate


ALLOWED_CONTENT_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}


@dataclass(frozen=True)
class ValidatedImageMetadata:
    filename: str
    content_type: str
    size: int
    extension: str
    width: int
    height: int


class ImageMetadataValidationError(ValueError):
    def __init__(self, message, code):
        self.message = message
        self.code = code
        super().__init__(message)


def validate_image_metadata(
    *,
    filename,
    content_type,
    size,
    width,
    height,
    max_bytes,
    max_dimension,
    max_pixels,
    max_bytes_message="處理後的圖片仍然過大，請選擇較小的圖片。",
):
    """Validate browser supplied metadata before a presigned URL is issued."""
    raw_name = str(filename or "").strip()
    safe_name = os.path.basename(raw_name)
    if not safe_name or safe_name != raw_name:
        raise ImageMetadataValidationError("檔案名稱無效。", "invalid_filename")

    normalized_type = str(content_type or "").split(";", 1)[0].lower().strip()
    if normalized_type not in ALLOWED_CONTENT_TYPES:
        raise ImageMetadataValidationError(
            "僅支援 JPG、PNG 或 WebP 圖片。", "unsupported_type"
        )
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_CONTENT_TYPES[normalized_type]:
        raise ImageMetadataValidationError(
            "圖片副檔名與格式不一致。", "extension_mismatch"
        )

    try:
        normalized_size = int(size)
        normalized_width = int(width)
        normalized_height = int(height)
    except (TypeError, ValueError) as exc:
        raise ImageMetadataValidationError(
            "圖片尺寸資料無效。", "invalid_dimensions"
        ) from exc
    if normalized_size <= 0:
        raise ImageMetadataValidationError("無法上傳空白圖片檔案。", "invalid_size")
    if normalized_width <= 0 or normalized_height <= 0:
        raise ImageMetadataValidationError("圖片尺寸資料無效。", "invalid_dimensions")
    if normalized_size > max_bytes:
        raise ImageMetadataValidationError(max_bytes_message, "file_too_large")
    if normalized_width > max_dimension or normalized_height > max_dimension:
        raise ImageMetadataValidationError(
            "圖片邊長超過安全上限。", "dimensions_too_large"
        )
    if normalized_width * normalized_height > max_pixels:
        raise ImageMetadataValidationError(
            "圖片像素數超過安全上限。", "pixels_too_large"
        )
    return ValidatedImageMetadata(
        filename=safe_name[:255],
        content_type=normalized_type,
        size=normalized_size,
        extension=extension,
        width=normalized_width,
        height=normalized_height,
    )


def storage_connection():
    """Return the configured django-storages S3/R2 client and bucket."""
    return default_storage.connection.meta.client, default_storage.bucket_name


def staging_bucket(public_bucket):
    bucket = settings.ADMIN_IMAGE_STAGING_BUCKET
    if not bucket or bucket == public_bucket:
        raise ImageMetadataValidationError(
            "請先設定獨立且非公開的圖片暫存儲存桶。", "staging_not_configured"
        )
    return bucket


@transaction.atomic
def reserve_upload_quota(user, size):
    """Persistent, shared per-user issuance quotas, serialized across all routes."""
    from .models import RateLimitBucket

    get_user_model().objects.select_for_update().get(pk=user.pk)
    now = timezone.now()
    window = now - timedelta(
        seconds=int(now.timestamp()) % 3600, microseconds=now.microsecond
    )
    identity = hashlib.sha256(f"admin-images:{user.pk}".encode()).hexdigest()
    for scope, increment, maximum in (
        ("admin-image-count", 1, settings.ADMIN_IMAGE_UPLOADS_PER_HOUR),
        ("admin-image-bytes", size, settings.ADMIN_IMAGE_BYTES_PER_HOUR),
    ):
        bucket, _ = RateLimitBucket.objects.get_or_create(
            scope=scope,
            key_hash=identity,
            window_started_at=window,
            defaults={"count": 0},
        )
        if bucket.count + increment > maximum:
            raise ImageMetadataValidationError(
                "圖片上傳額度已用完，請稍後再試。", "upload_quota_exceeded"
            )
        bucket.count += increment
        bucket.save(update_fields=("count",))


def generate_presigned_put(
    *, client, bucket, object_key, content_type, size, expires_in
):
    if not object_key.startswith(STAGING_PREFIX):
        raise ImageMetadataValidationError("圖片暫存路徑無效。", "invalid_object_key")
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": staging_bucket(bucket),
            "Key": object_key,
            "ContentType": content_type,
            "ContentLength": size,
        },
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )


def delete_image_object(object_key):
    if object_key.startswith(STAGING_PREFIX):
        client, public_bucket = storage_connection()
        client.delete_object(Bucket=staging_bucket(public_bucket), Key=object_key)
    else:
        default_storage.delete(object_key)


def verify_and_publish_image(
    *, client, bucket, object_key, content_type, size, width, height, max_bytes
):
    """Read one bounded snapshot; publish only decoded, re-encoded bytes at a new key.

    A later PUT to the original signed URL cannot change the published snapshot.
    Bucket lifecycle expiry is still required: URLs may recreate deleted staging keys.
    """
    from uuid import uuid4

    private_bucket = staging_bucket(bucket)
    if not object_key.startswith(STAGING_PREFIX):
        raise ImageMetadataValidationError("請重新上傳圖片。", "invalid_object_key")
    response = client.get_object(Bucket=private_bucket, Key=object_key)
    stream = response["Body"]
    try:
        raw = stream.read(max_bytes + 1)
    finally:
        stream.close()
    if len(raw) != size or len(raw) > max_bytes:
        raise ImageMetadataValidationError("圖片大小不一致。", "size_mismatch")
    expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[
        content_type
    ]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format != expected_format:
                    raise ImageMetadataValidationError(
                        "圖片實際格式不一致。", "invalid_image"
                    )
                w, h = source.size
                if (w, h) != (width, height):
                    raise ImageMetadataValidationError(
                        "圖片實際尺寸不一致。", "dimensions_mismatch"
                    )
                if (
                    max(w, h) > settings.ADMIN_IMAGE_MAX_DIMENSION
                    or w * h > settings.ADMIN_IMAGE_MAX_OUTPUT_PIXELS
                ):
                    raise ImageMetadataValidationError(
                        "圖片尺寸超過安全上限。", "dimensions_too_large"
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise ImageMetadataValidationError(
                        "不接受動畫圖片。", "animated_image"
                    )
                source.load()
                oriented = ImageOps.exif_transpose(source)
                has_alpha = (
                    "A" in oriented.getbands() or "transparency" in oriented.info
                )
                mode = "RGBA" if has_alpha and expected_format != "JPEG" else "RGB"
                # A fresh image has no EXIF, XMP, ICC, comments or decoder metadata.
                clean = Image.new(mode, oriented.size)
                clean.paste(oriented.convert(mode))
                output = BytesIO()
                options = {"quality": 88} if expected_format in {"JPEG", "WEBP"} else {}
                clean.save(output, format=expected_format, **options)
                clean_size = clean.size
    except ImageMetadataValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
    ) as exc:
        raise ImageMetadataValidationError(
            "圖片損壞或無法驗證。", "invalid_image"
        ) from exc
    data = output.getvalue()
    if len(data) > max_bytes:
        raise ImageMetadataValidationError(
            "圖片重新編碼後超過大小上限。", "file_too_large"
        )
    # Independent UUID prevents competing completions from writing the same public key.
    prefix, extension = object_key.removeprefix(STAGING_PREFIX).rsplit(".", 1)
    public_key = f"{prefix.rsplit('/', 1)[0]}/{uuid4().hex}.{extension}"
    client.put_object(
        Bucket=bucket, Key=public_key, Body=data, ContentType=content_type
    )
    try:
        client.delete_object(Bucket=private_bucket, Key=object_key)
    except Exception:
        # The private bucket lifecycle rule is the cleanup fallback.
        import logging

        logging.getLogger("restfull.image_uploads").warning(
            "staging_image_cleanup_failed"
        )
    return public_key, len(data), *clean_size


def normalized_head_metadata(metadata):
    try:
        size = int(metadata.get("ContentLength", -1))
    except (TypeError, ValueError):
        size = -1
    content_type = str(metadata.get("ContentType", "")).split(";", 1)[0].lower().strip()
    return size, content_type
