import os
import warnings
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_FORMATS = {"JPEG": (".jpg", ".jpeg"), "PNG": (".png",), "WEBP": (".webp",)}
FORMAT_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _decode_image(upload):
    max_bytes = settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024
    if getattr(upload, "size", 0) > max_bytes:
        raise ValidationError(f"圖片大小不可超過 {settings.MAX_IMAGE_UPLOAD_MB} MB。")
    try:
        position = upload.tell()
    except Exception:
        position = 0
    try:
        upload.seek(0)
        raw = upload.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValidationError(f"圖片大小不可超過 {settings.MAX_IMAGE_UPLOAD_MB} MB。")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(BytesIO(raw))
            source.load()  # Force a full decode; verify() alone does not decode pixels.
        if source.format not in ALLOWED_FORMATS:
            raise ValidationError("僅接受 JPEG、PNG 或 WebP 圖片。SVG、GIF 與其他格式不開放上傳。")
        width, height = source.size
        max_width = settings.MAX_IMAGE_WIDTH
        max_height = settings.MAX_IMAGE_HEIGHT
        if width <= 0 or height <= 0 or width > max_width or height > max_height or width * height > settings.MAX_IMAGE_PIXELS:
            raise ValidationError(f"圖片尺寸不可超過 {max_width}×{max_height}，且像素總數不得超過 {settings.MAX_IMAGE_PIXELS:,}。")
        if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
            raise ValidationError("不接受動畫圖片。")
        extension = os.path.splitext(getattr(upload, "name", ""))[1].lower()
        if extension not in ALLOWED_FORMATS[source.format]:
            raise ValidationError("圖片副檔名與實際格式不一致。")
        supplied_type = getattr(upload, "content_type", "")
        if supplied_type and supplied_type != FORMAT_CONTENT_TYPES[source.format]:
            raise ValidationError("圖片 Content-Type 與實際格式不一致。")
        return source.copy(), source.format
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("圖片檔案無法驗證、已損壞或尺寸不安全。") from exc
    finally:
        try:
            upload.seek(position)
        except Exception:
            pass


def validate_image_upload(upload):
    _decode_image(upload)


def sanitize_image_field(instance, field_name):
    """Decode, strip metadata and re-encode a newly assigned image before storage."""
    field = getattr(instance, field_name)
    if not field or getattr(field, "_committed", True):
        return
    image, original_format = _decode_image(field.file)
    image = ImageOps.exif_transpose(image)
    if original_format == "JPEG":
        image = image.convert("RGB")
    elif image.mode not in {"RGB", "RGBA", "L", "LA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    output = BytesIO()
    save_options = {"optimize": True}
    if original_format == "JPEG":
        save_options.update(quality=88, progressive=True)
    elif original_format == "WEBP":
        save_options.update(quality=88, method=6)
    image.save(output, format=original_format, **save_options)
    name = os.path.basename(field.name)
    setattr(instance, field_name, ContentFile(output.getvalue(), name=name))


def make_thumbnail_content(image_field, *, size=(640, 640)):
    image_field.seek(0)
    with Image.open(image_field) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="WEBP", quality=82, method=6)
    image_field.seek(0)
    stem = os.path.splitext(os.path.basename(image_field.name))[0]
    return ContentFile(output.getvalue(), name=f"{stem}-thumb.webp")
