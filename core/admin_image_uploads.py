"""Shared, metadata-only primitives for browser-to-R2 admin image uploads.

Domain services remain responsible for ownership, upload sessions and model
associations.  This module is deliberately unaware of Product, projects and
publications so no image bytes ever pass through Django.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from django.core.files.storage import default_storage

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


def generate_presigned_put(*, client, bucket, object_key, content_type, expires_in):
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": object_key, "ContentType": content_type},
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )


def normalized_head_metadata(metadata):
    try:
        size = int(metadata.get("ContentLength", -1))
    except (TypeError, ValueError):
        size = -1
    content_type = str(metadata.get("ContentType", "")).split(";", 1)[0].lower().strip()
    return size, content_type
