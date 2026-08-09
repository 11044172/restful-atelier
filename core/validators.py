from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError


def validate_image_upload(upload):
    max_bytes = settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024
    if upload.size > max_bytes:
        raise ValidationError(f"圖片大小不可超過 {settings.MAX_IMAGE_UPLOAD_MB} MB。")
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    content_type = getattr(upload, "content_type", None)
    if content_type and content_type not in allowed_types:
        raise ValidationError("僅接受 JPEG、PNG、WebP 或 GIF 圖片。")
    try:
        from PIL import Image

        position = upload.tell()
        upload.seek(0)
        image = Image.open(BytesIO(upload.read()))
        image.verify()
        upload.seek(position)
    except Exception as exc:
        raise ValidationError("圖片檔案無法驗證或已損壞。") from exc
