from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from .direct_image_uploads import DirectImageError, category_config, mark_attached, resolve_token


class DirectImageWidget(forms.Widget):
    template_name = "admin/widgets/direct_image.html"

    def __init__(self, *, category, profile, attrs=None):
        self.category, self.profile = category, profile
        super().__init__(attrs)

    def format_value(self, value):
        if value in (None, ""):
            return ""
        value = getattr(value, "name", value)
        value = str(value)
        return value if value.startswith("upload:") else f"existing:{value}"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        raw = getattr(value, "name", value) or ""
        context["widget"].update({
            "category": self.category,
            "profile": self.profile,
            "current_url": getattr(value, "url", "") if value else "",
            "has_current": bool(raw),
            "presign_url": reverse("admin:direct_image_presign"),
            "complete_url": reverse("admin:direct_image_complete"),
            "max_input_bytes": settings.ADMIN_IMAGE_MAX_INPUT_BYTES,
            "max_output_bytes": settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES,
            "max_input_pixels": settings.ADMIN_IMAGE_MAX_INPUT_PIXELS,
            "max_input_dimension": settings.ADMIN_IMAGE_MAX_INPUT_DIMENSION,
            "photo_long_edge": settings.ADMIN_IMAGE_PHOTO_LONG_EDGE,
            "artwork_long_edge": settings.ADMIN_IMAGE_ARTWORK_LONG_EDGE,
        })
        return context


class DirectImageFormField(forms.Field):
    def __init__(self, *, category, profile, **kwargs):
        kwargs.setdefault("required", False)
        kwargs["widget"] = DirectImageWidget(category=category, profile=profile)
        self.category, self.user, self.upload_id = category, None, None
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        initial_name = str(getattr(self.initial, "name", self.initial) or "")
        if not value:
            return ""
        if value.startswith("existing:"):
            key = value.removeprefix("existing:")
            if key != initial_name:
                raise ValidationError("目前圖片資料無效，請重新載入頁面。")
            return key
        if not value.startswith("upload:") or not self.user:
            raise ValidationError("圖片資料無效，請重新選擇圖片。")
        try:
            upload = resolve_token(token=value.removeprefix("upload:"), user=self.user, category=self.category)
        except DirectImageError as exc:
            raise ValidationError(exc.message) from exc
        self.upload_id = upload.pk
        return upload.object_key


class DirectImageAdminFormMixin:
    direct_image_fields = {}

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        for name, category in self.direct_image_fields.items():
            if name not in self.fields:
                continue
            old = self.fields[name]
            _permission, _prefix, profile = category_config(category)
            field = DirectImageFormField(category=category, profile=profile, label=old.label, help_text=old.help_text)
            field.initial = getattr(self.instance, name, "")
            field.user = getattr(request, "user", None)
            self.fields[name] = field

    @property
    def direct_upload_ids(self):
        return [field.upload_id for field in self.fields.values() if isinstance(field, DirectImageFormField) and field.upload_id]

    def mark_direct_uploads_attached(self):
        mark_attached(self.direct_upload_ids)

    class Media:
        js = ("admin/js/direct-images.js",)
