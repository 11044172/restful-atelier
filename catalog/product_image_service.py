"""Metadata-only product image operations backed by the existing default R2 storage."""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import ProductImage


logger = logging.getLogger("catalog.product_images")

MAX_FILE_BYTES = 20 * 1024 * 1024
PRESIGN_EXPIRES_SECONDS = 600
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
DIRECT_OBJECT_KEY_PATTERN = re.compile(
    r"^products/\d{4}/\d{2}/[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)$"
)


@dataclass
class ProductImageError(Exception):
    message: str
    code: str = "invalid_request"
    status: int = 400

    def __str__(self):
        return self.message


def parse_upload_session(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProductImageError("アップロードセッションが無効です。", "invalid_upload_session") from exc


def validate_upload_metadata(filename, content_type, size):
    safe_name = os.path.basename(str(filename or "")).strip()
    if not safe_name or safe_name != str(filename or "").strip():
        raise ProductImageError("ファイル名が無効です。", "invalid_filename")
    normalized_type = str(content_type or "").lower().strip()
    if normalized_type not in ALLOWED_CONTENT_TYPES:
        raise ProductImageError("対応していない画像形式です。", "unsupported_type")
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_CONTENT_TYPES[normalized_type]:
        raise ProductImageError("画像の拡張子と形式が一致しません。", "extension_mismatch")
    try:
        normalized_size = int(size)
    except (TypeError, ValueError) as exc:
        raise ProductImageError("画像サイズが無効です。", "invalid_size") from exc
    if normalized_size <= 0:
        raise ProductImageError("空の画像はアップロードできません。", "invalid_size")
    if normalized_size > MAX_FILE_BYTES:
        raise ProductImageError("画像は1枚20MB以下にしてください。", "file_too_large")
    return safe_name[:255], normalized_type, normalized_size, extension


def build_object_key(extension):
    now = timezone.now()
    return f"products/{now:%Y/%m}/{uuid4().hex}{extension}"


def is_direct_object_key(value):
    return bool(DIRECT_OBJECT_KEY_PATTERN.fullmatch(str(value or "")))


def _storage_client():
    """Reuse django-storages' configured S3/R2 client and bucket."""
    try:
        client = default_storage.connection.meta.client
        bucket_name = default_storage.bucket_name
    except AttributeError as exc:
        raise ProductImageError(
            "R2アップロード設定を確認できませんでした。",
            "storage_not_configured",
            503,
        ) from exc
    if not bucket_name:
        raise ProductImageError(
            "R2アップロード設定を確認できませんでした。",
            "storage_not_configured",
            503,
        )
    return client, bucket_name


def create_presigned_upload(*, user, upload_session, filename, content_type, size):
    filename, content_type, size, extension = validate_upload_metadata(
        filename, content_type, size
    )
    object_key = build_object_key(extension)
    image = ProductImage.objects.create(
        image=object_key,
        upload_status=ProductImage.UploadStatus.PENDING,
        upload_session=upload_session,
        uploaded_by=user,
        original_filename=filename,
        content_type=content_type,
        file_size=size,
    )
    try:
        client, bucket_name = _storage_client()
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRES_SECONDS,
            HttpMethod="PUT",
        )
    except ProductImageError:
        image.delete()
        raise
    except Exception as exc:
        image.delete()
        logger.exception("Presigned URL generation failed key=%s user_id=%s", object_key, user.pk)
        raise ProductImageError(
            "アップロードの準備に失敗しました。再試行してください。",
            "presign_failed",
            503,
        ) from exc
    return image, upload_url


def head_object(object_key):
    client, bucket_name = _storage_client()
    return client.head_object(Bucket=bucket_name, Key=object_key)


def delete_object(object_key):
    default_storage.delete(object_key)


def _delete_invalid_upload(image, *, reason):
    try:
        delete_object(image.image.name)
    except Exception:
        image.upload_status = ProductImage.UploadStatus.DELETION_PENDING
        image.save(update_fields=("upload_status",))
        logger.exception("Invalid R2 object cleanup failed image_id=%s reason=%s", image.pk, reason)
    else:
        image.delete()


def complete_upload(*, user, upload_session, object_key, product=None):
    if not is_direct_object_key(object_key):
        logger.warning("Rejected invalid product image object key user_id=%s", user.pk)
        raise ProductImageError("object keyが無効です。", "invalid_object_key")
    try:
        image = ProductImage.objects.get(
            image=object_key,
            upload_session=upload_session,
            uploaded_by=user,
            upload_status=ProductImage.UploadStatus.PENDING,
            product__isnull=True,
        )
    except ProductImage.DoesNotExist as exc:
        raise ProductImageError(
            "このアップロードを確認できませんでした。",
            "upload_not_owned",
            404,
        ) from exc
    try:
        metadata = head_object(object_key)
    except Exception as exc:
        logger.exception("R2 HEAD failed image_id=%s key=%s", image.pk, object_key)
        raise ProductImageError(
            "アップロード済み画像を確認できませんでした。再試行してください。",
            "head_failed",
            503,
        ) from exc

    try:
        actual_size = int(metadata.get("ContentLength", -1))
    except (TypeError, ValueError):
        actual_size = -1
    actual_type = str(metadata.get("ContentType", "")).split(";", 1)[0].lower().strip()
    if actual_size <= 0 or actual_size > MAX_FILE_BYTES or actual_size != image.file_size:
        logger.warning(
            "Product image size mismatch image_id=%s expected=%s actual=%s",
            image.pk,
            image.file_size,
            actual_size,
        )
        _delete_invalid_upload(image, reason="size_mismatch")
        raise ProductImageError("画像サイズを確認できませんでした。", "size_mismatch")
    if actual_type not in ALLOWED_CONTENT_TYPES or actual_type != image.content_type:
        logger.warning(
            "Product image MIME mismatch image_id=%s expected=%s actual=%s",
            image.pk,
            image.content_type,
            actual_type,
        )
        _delete_invalid_upload(image, reason="mime_mismatch")
        raise ProductImageError("画像形式を確認できませんでした。", "mime_mismatch")

    image.file_size = actual_size
    image.content_type = actual_type
    image.product = product
    image.upload_status = (
        ProductImage.UploadStatus.ATTACHED if product else ProductImage.UploadStatus.TEMPORARY
    )
    if product:
        current_max = product.images.filter(
            upload_status=ProductImage.UploadStatus.ATTACHED
        ).aggregate(value=Max("sort_order"))["value"]
        image.sort_order = (current_max if current_max is not None else -1) + 1
    else:
        current_max = ProductImage.objects.filter(
            upload_session=upload_session,
            uploaded_by=user,
            upload_status=ProductImage.UploadStatus.TEMPORARY,
        ).aggregate(value=Max("sort_order"))["value"]
        image.sort_order = (current_max if current_max is not None else -1) + 1
    image.save(
        update_fields=("file_size", "content_type", "product", "upload_status", "sort_order")
    )
    if product:
        normalize_product_images(product)
        image.refresh_from_db(fields=("sort_order", "is_primary"))
    return image


@transaction.atomic
def normalize_product_images(product, ordered_ids=None):
    queryset = ProductImage.objects.select_for_update().filter(
        product=product,
        upload_status=ProductImage.UploadStatus.ATTACHED,
    )
    images = list(queryset.order_by("sort_order", "pk"))
    if ordered_ids is not None:
        by_id = {image.pk: image for image in images}
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(by_id):
            raise ProductImageError(
                "並び替え対象の画像が商品と一致しません。",
                "invalid_image_set",
            )
        images = [by_id[image_id] for image_id in ordered_ids]
    elif images:
        legacy_primary = next((image for image in images if image.is_primary), None)
        if legacy_primary:
            images.remove(legacy_primary)
            images.insert(0, legacy_primary)

    queryset.update(is_primary=False)
    for index, image in enumerate(images):
        ProductImage.objects.filter(pk=image.pk).update(
            sort_order=index,
            is_primary=index == 0,
        )
    return images


@transaction.atomic
def reorder_temporary_images(*, user, upload_session, ordered_ids):
    queryset = ProductImage.objects.select_for_update().filter(
        product__isnull=True,
        uploaded_by=user,
        upload_session=upload_session,
        upload_status=ProductImage.UploadStatus.TEMPORARY,
    )
    images = {image.pk: image for image in queryset}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(images):
        raise ProductImageError("並び替え対象の画像が一致しません。", "invalid_image_set")
    for index, image_id in enumerate(ordered_ids):
        ProductImage.objects.filter(pk=image_id).update(sort_order=index, is_primary=False)


@transaction.atomic
def attach_temporary_images(*, product, user, upload_session, ordered_ids):
    queryset = ProductImage.objects.select_for_update().filter(
        product__isnull=True,
        uploaded_by=user,
        upload_session=upload_session,
        upload_status=ProductImage.UploadStatus.TEMPORARY,
    )
    images = {image.pk: image for image in queryset}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(images):
        raise ProductImageError("商品画像の所有関係を確認できません。", "invalid_image_set")
    for index, image_id in enumerate(ordered_ids):
        ProductImage.objects.filter(pk=image_id).update(
            product=product,
            upload_status=ProductImage.UploadStatus.ATTACHED,
            sort_order=index,
            is_primary=False,
        )
    normalize_product_images(product, ordered_ids)


def mark_and_delete_image(image):
    product = image.product
    image.upload_status = ProductImage.UploadStatus.DELETION_PENDING
    image.is_primary = False
    image.save(update_fields=("upload_status", "is_primary"))
    try:
        if image.image:
            delete_object(image.image.name)
        if image.thumbnail:
            delete_object(image.thumbnail.name)
    except Exception as exc:
        logger.exception("R2 delete failed image_id=%s key=%s", image.pk, image.image.name)
        raise ProductImageError(
            "画像の削除に失敗しました。後で自動的に再試行します。",
            "delete_failed",
            503,
        ) from exc
    with transaction.atomic():
        image.delete()
        if product:
            normalize_product_images(product)


def image_payload(image):
    try:
        image_url = image.image.url
    except Exception:
        logger.exception("Product image URL generation failed image_id=%s", image.pk)
        image_url = ""
    return {
        "id": image.pk,
        "url": image_url,
        "alt_text": image.alt_text,
        "sort_order": image.sort_order,
        "is_main": image.is_primary,
        "filename": image.original_filename,
        "size": image.file_size,
    }
