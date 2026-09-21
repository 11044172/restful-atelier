import json

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from .direct_image_uploads import (
    DirectImageError,
    category_config,
    mark_attached,
    resolve_token,
)


class DirectImageWidget(forms.Widget):
    template_name = "admin/widgets/direct_image.html"

    def __init__(self, *, category, profile, focal_point=None, attrs=None):
        self.category, self.profile = category, profile
        self.focal_point = focal_point
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
        context["widget"].update(
            {
                "category": self.category,
                "profile": self.profile,
                "current_url": getattr(value, "url", "") if value else "",
                "has_current": bool(raw),
                "presign_url": reverse("admin:direct_image_presign"),
                "complete_url": reverse("admin:direct_image_complete"),
                "delete_url": reverse("admin:direct_image_delete"),
                "max_input_bytes": settings.ADMIN_IMAGE_MAX_INPUT_BYTES,
                "max_output_bytes": settings.ADMIN_IMAGE_MAX_OUTPUT_BYTES,
                "max_input_pixels": settings.ADMIN_IMAGE_MAX_INPUT_PIXELS,
                "max_input_dimension": settings.ADMIN_IMAGE_MAX_INPUT_DIMENSION,
                "photo_long_edge": settings.ADMIN_IMAGE_PHOTO_LONG_EDGE,
                "artwork_long_edge": settings.ADMIN_IMAGE_ARTWORK_LONG_EDGE,
                "focal_point": self.focal_point,
            }
        )
        return context


class DirectImageFormField(forms.Field):
    def __init__(self, *, category, profile, focal_point=None, **kwargs):
        kwargs.setdefault("required", False)
        kwargs["widget"] = DirectImageWidget(
            category=category,
            profile=profile,
            focal_point=focal_point,
        )
        self.category, self.user, self.upload_id, self.upload = (
            category,
            None,
            None,
            None,
        )
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
            upload = resolve_token(
                token=value.removeprefix("upload:"),
                user=self.user,
                category=self.category,
            )
        except DirectImageError as exc:
            raise ValidationError(exc.message) from exc
        self.upload_id = upload.pk
        self.upload = upload
        return upload.object_key


class DirectImageAdminFormMixin:
    direct_image_fields = {}
    direct_image_focal_fields = {}
    direct_image_focal_previews = {}
    direct_image_dimension_fields = {}

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        self.request = request
        super().__init__(*args, **kwargs)
        for width_name, height_name in self.direct_image_dimension_fields.values():
            self.fields.pop(width_name, None)
            self.fields.pop(height_name, None)
        for x_name, y_name in self.direct_image_focal_fields.values():
            if x_name in self.fields:
                self.fields[x_name].widget = forms.HiddenInput()
                self.fields[x_name].required = False
            if y_name in self.fields:
                self.fields[y_name].widget = forms.HiddenInput()
                self.fields[y_name].required = False
        for name, category in self.direct_image_fields.items():
            if name not in self.fields:
                continue
            old = self.fields[name]
            _permission, _prefix, profile = category_config(category)
            focal_fields = self.direct_image_focal_fields.get(name)
            focal_point = None
            if focal_fields:
                x_name, y_name = focal_fields
                focal_point = {
                    "x_input_id": self[x_name].id_for_label,
                    "y_input_id": self[y_name].id_for_label,
                    "previews_json": json.dumps(
                        self.direct_image_focal_previews.get(name, []),
                        ensure_ascii=False,
                    ),
                }
            field = DirectImageFormField(
                category=category,
                profile=profile,
                focal_point=focal_point,
                label=old.label,
                help_text=old.help_text,
            )
            field.initial = getattr(self.instance, name, "")
            field.user = getattr(request, "user", None)
            self.fields[name] = field

    def clean(self):
        cleaned_data = super().clean()
        for x_name, y_name in self.direct_image_focal_fields.values():
            for field_name in (x_name, y_name):
                if (
                    field_name in self.fields
                    and field_name not in self.errors
                    and cleaned_data.get(field_name) is None
                ):
                    cleaned_data[field_name] = getattr(
                        self.instance, field_name, 50
                    )

        for image_name, dimension_names in self.direct_image_dimension_fields.items():
            if image_name not in self.fields or image_name in self.errors:
                continue
            image_field = self.fields[image_name]
            upload = getattr(image_field, "upload", None)
            width_name, height_name = dimension_names
            if upload:
                setattr(self.instance, width_name, upload.width)
                setattr(self.instance, height_name, upload.height)
            elif cleaned_data.get(image_name) == "":
                setattr(self.instance, width_name, None)
                setattr(self.instance, height_name, None)
        return cleaned_data

    @property
    def direct_upload_ids(self):
        return [
            field.upload_id
            for field in self.fields.values()
            if isinstance(field, DirectImageFormField) and field.upload_id
        ]

    def mark_direct_uploads_attached(self):
        mark_attached(self.direct_upload_ids)

    class Media:
        js = ("admin/js/admin-image-manager.js",)


def mark_form_direct_uploads_attached(form):
    marker = getattr(form, "mark_direct_uploads_attached", None)
    if callable(marker):
        marker()
