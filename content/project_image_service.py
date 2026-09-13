"""Interior project gallery operations using browser-to-R2 direct uploads."""

import logging
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.admin_image_uploads import (
    ALLOWED_CONTENT_TYPES,
    ImageMetadataValidationError,
    generate_presigned_put,
    normalized_head_metadata,
    storage_connection,
    validate_image_metadata,
)

from .models import InteriorProjectImage

logger = logging.getLogger("content.project_images")
MAX_FILE_BYTES = settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES
PRESIGN_EXPIRES_SECONDS = 600
DIRECT_OBJECT_KEY_PATTERN = re.compile(
    r"^projects/gallery/\d{4}/\d{2}/[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)$"
)
TONE_MAX_LENGTH = InteriorProjectImage._meta.get_field("tone").max_length
TEXT_MAX_LENGTH = 255


@dataclass
class ProjectImageError(Exception):
    message: str
    code: str = "invalid_request"
    status: int = 400

    def __str__(self):
        return self.message


def parse_upload_session(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProjectImageError("上傳工作階段無效。", "invalid_upload_session") from exc


def validate_upload_metadata(filename, content_type, size, width, height):
    try:
        metadata = validate_image_metadata(
            filename=filename,
            content_type=content_type,
            size=size,
            width=width,
            height=height,
            max_bytes=MAX_FILE_BYTES,
            max_dimension=settings.ADMIN_IMAGE_MAX_DIMENSION,
            max_pixels=settings.ADMIN_IMAGE_MAX_OUTPUT_PIXELS,
        )
    except ImageMetadataValidationError as exc:
        raise ProjectImageError(exc.message, exc.code) from exc
    return metadata


def build_object_key(extension):
    return f"projects/gallery/{timezone.now():%Y/%m}/{uuid4().hex}{extension}"


def is_direct_object_key(value):
    return bool(DIRECT_OBJECT_KEY_PATTERN.fullmatch(str(value or "")))


def _storage_client():
    try:
        client, bucket = storage_connection()
    except AttributeError as exc:
        raise ProjectImageError(
            "無法確認 R2 上傳設定。", "storage_not_configured", 503
        ) from exc
    if not bucket:
        raise ProjectImageError("無法確認 R2 上傳設定。", "storage_not_configured", 503)
    return client, bucket


def head_object(object_key):
    client, bucket = _storage_client()
    return client.head_object(Bucket=bucket, Key=object_key)


def delete_object(object_key):
    default_storage.delete(object_key)


def create_presigned_upload(
    *, user, upload_session, filename, content_type, size, width, height
):
    metadata = validate_upload_metadata(filename, content_type, size, width, height)
    object_key = build_object_key(metadata.extension)
    image = InteriorProjectImage.objects.create(
        image=object_key,
        upload_status=InteriorProjectImage.UploadStatus.PENDING,
        upload_session=upload_session,
        uploaded_by=user,
        original_filename=metadata.filename,
        content_type=metadata.content_type,
        file_size=metadata.size,
        width=metadata.width,
        height=metadata.height,
        alt_text=metadata.filename.rsplit(".", 1)[0][:TEXT_MAX_LENGTH],
    )
    try:
        client, bucket = _storage_client()
        upload_url = generate_presigned_put(
            client=client,
            bucket=bucket,
            object_key=object_key,
            content_type=metadata.content_type,
            expires_in=PRESIGN_EXPIRES_SECONDS,
        )
    except ProjectImageError:
        image.delete()
        raise
    except Exception as exc:
        image.delete()
        logger.exception(
            "project_image_presign_failed key=%s user_id=%s", object_key, user.pk
        )
        raise ProjectImageError(
            "準備上傳失敗，請重試。", "presign_failed", 503
        ) from exc
    logger.info(
        "project_image_presigned width=%s height=%s bytes=%s mime=%s user_id=%s",
        metadata.width,
        metadata.height,
        metadata.size,
        metadata.content_type,
        user.pk,
    )
    return image, upload_url


def _delete_invalid_upload(image, *, reason):
    try:
        delete_object(image.image.name)
    except Exception:
        image.upload_status = InteriorProjectImage.UploadStatus.DELETION_PENDING
        image.save(update_fields=("upload_status",))
        logger.exception(
            "invalid_project_image_cleanup_failed image_id=%s reason=%s",
            image.pk,
            reason,
        )
    else:
        image.delete()


def complete_upload(*, user, upload_session, object_key, project=None):
    if not is_direct_object_key(object_key):
        raise ProjectImageError("圖片識別資料無效。", "invalid_object_key")
    try:
        image = InteriorProjectImage.objects.get(
            image=object_key,
            project__isnull=True,
            upload_session=upload_session,
            uploaded_by=user,
            upload_status=InteriorProjectImage.UploadStatus.PENDING,
        )
    except InteriorProjectImage.DoesNotExist as exc:
        raise ProjectImageError(
            "無法確認此上傳項目。", "upload_not_owned", 404
        ) from exc
    try:
        metadata = head_object(object_key)
    except Exception as exc:
        logger.exception("project_image_head_failed image_id=%s", image.pk)
        raise ProjectImageError(
            "無法確認已上傳的圖片，請重試。", "head_failed", 503
        ) from exc
    actual_size, actual_type = normalized_head_metadata(metadata)
    if (
        actual_size <= 0
        or actual_size > MAX_FILE_BYTES
        or actual_size != image.file_size
    ):
        _delete_invalid_upload(image, reason="size_mismatch")
        raise ProjectImageError("無法確認圖片檔案大小。", "size_mismatch")
    if actual_type not in ALLOWED_CONTENT_TYPES or actual_type != image.content_type:
        _delete_invalid_upload(image, reason="mime_mismatch")
        raise ProjectImageError("無法確認圖片格式。", "mime_mismatch")

    image.file_size = actual_size
    image.content_type = actual_type
    image.project = project
    image.upload_status = (
        InteriorProjectImage.UploadStatus.ATTACHED
        if project
        else InteriorProjectImage.UploadStatus.TEMPORARY
    )
    if project:
        current_max = project.images.filter(
            upload_status=InteriorProjectImage.UploadStatus.ATTACHED
        ).aggregate(value=Max("sort_order"))["value"]
    else:
        current_max = InteriorProjectImage.objects.filter(
            project__isnull=True,
            uploaded_by=user,
            upload_session=upload_session,
            upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
        ).aggregate(value=Max("sort_order"))["value"]
    image.sort_order = (current_max if current_max is not None else -1) + 1
    image.save(
        update_fields=(
            "file_size",
            "content_type",
            "project",
            "upload_status",
            "sort_order",
        )
    )
    if project:
        normalize_project_images(project)
        image.refresh_from_db(fields=("sort_order",))
    return image


@transaction.atomic
def normalize_project_images(project, ordered_ids=None):
    queryset = InteriorProjectImage.objects.select_for_update().filter(
        project=project,
        upload_status=InteriorProjectImage.UploadStatus.ATTACHED,
    )
    images = list(queryset.order_by("sort_order", "pk"))
    by_id = {image.pk: image for image in images}
    if ordered_ids is not None:
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(by_id):
            raise ProjectImageError("要排序的圖片與作品不一致。", "invalid_image_set")
        images = [by_id[image_id] for image_id in ordered_ids]
    for index, image in enumerate(images):
        if image.sort_order != index:
            InteriorProjectImage.objects.filter(pk=image.pk).update(sort_order=index)
    return images


@transaction.atomic
def reorder_temporary_images(*, user, upload_session, ordered_ids):
    queryset = InteriorProjectImage.objects.select_for_update().filter(
        project__isnull=True,
        uploaded_by=user,
        upload_session=upload_session,
        upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
    )
    images = {image.pk: image for image in queryset}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(images):
        raise ProjectImageError("要排序的圖片不一致。", "invalid_image_set")
    for index, image_id in enumerate(ordered_ids):
        InteriorProjectImage.objects.filter(pk=image_id).update(sort_order=index)


@transaction.atomic
def attach_temporary_images(*, project, user, upload_session, ordered_ids):
    queryset = InteriorProjectImage.objects.select_for_update().filter(
        project__isnull=True,
        uploaded_by=user,
        upload_session=upload_session,
        upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
    )
    images = {image.pk: image for image in queryset}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(images):
        raise ProjectImageError("無法確認作品圖片的所屬關係。", "invalid_image_set")
    for index, image_id in enumerate(ordered_ids):
        InteriorProjectImage.objects.filter(pk=image_id).update(
            project=project,
            upload_status=InteriorProjectImage.UploadStatus.ATTACHED,
            sort_order=index,
        )
    normalize_project_images(project, ordered_ids)


def update_image_metadata(image, *, alt_text, caption, tone):
    values = {
        "alt_text": str(alt_text or "").strip()[:TEXT_MAX_LENGTH],
        "caption": str(caption or "").strip()[:TEXT_MAX_LENGTH],
        "tone": str(tone or "").strip()[:TONE_MAX_LENGTH],
    }
    if not values["alt_text"]:
        values["alt_text"] = "作品圖片"
    InteriorProjectImage.objects.filter(pk=image.pk).update(**values)
    for field, value in values.items():
        setattr(image, field, value)
    return image


def mark_and_delete_image(image):
    project = image.project
    image.upload_status = InteriorProjectImage.UploadStatus.DELETION_PENDING
    image.save(update_fields=("upload_status",))
    try:
        if image.image:
            delete_object(image.image.name)
    except Exception as exc:
        logger.exception("project_image_delete_failed image_id=%s", image.pk)
        raise ProjectImageError(
            "刪除圖片失敗，系統稍後會自動重試。", "delete_failed", 503
        ) from exc
    with transaction.atomic():
        image.delete()
        if project:
            normalize_project_images(project)


def image_payload(image):
    try:
        image_url = image.image.url
    except Exception:
        logger.exception("project_image_url_failed image_id=%s", image.pk)
        image_url = ""
    return {
        "id": image.pk,
        "url": image_url,
        "alt_text": image.alt_text,
        "caption": image.caption,
        "tone": image.tone,
        "sort_order": image.sort_order,
        "filename": image.original_filename or image.alt_text,
        "size": image.file_size,
    }
