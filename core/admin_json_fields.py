import json

from django import forms
from django.core.exceptions import ValidationError


class StringListWidget(forms.Widget):
    template_name = "admin/widgets/string_list.html"

    def format_value(self, value):
        if value in (None, ""):
            value = []
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        serialized = self.format_value(value)
        try:
            items = json.loads(serialized)
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                items = []
        except (TypeError, ValueError):
            items = []
        context["widget"]["serialized"] = serialized
        context["widget"]["items"] = items
        return context

    class Media:
        js = ("admin/js/string-list-editor.js",)


class StringListFormField(forms.Field):
    widget = StringListWidget

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        if value in self.empty_values:
            return []
        if isinstance(value, list):
            result = value
        else:
            try:
                result = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError("項目資料無法讀取，請重新整理後再試。") from exc
        if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
            raise ValidationError("每一列都必須是文字。")
        return result
