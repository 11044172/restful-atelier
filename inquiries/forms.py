from django import forms

from .models import Inquiry, InquiryCategory


class InquiryForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    class Meta:
        model = Inquiry
        fields = ("name", "phone", "email", "category", "budget_range", "project_location", "expected_timing", "message", "privacy_agreed", "newsletter_opt_in")
        labels = {
            "name": "姓名",
            "phone": "電話",
            "email": "Email",
            "category": "詢問類型",
            "budget_range": "預算區間",
            "project_location": "案件地點",
            "expected_timing": "預計施作時間",
            "message": "訊息內容",
            "privacy_agreed": "我已閱讀並同意個人資料處理聲明",
            "newsletter_opt_in": "希望收到電子報（任意）",
        }
        widgets = {"message": forms.Textarea(attrs={"rows": 7})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = InquiryCategory.objects.filter(active=True).order_by("sort_order", "display_name")
        for name in ("name", "phone", "email", "category", "message", "privacy_agreed"):
            self.fields[name].required = True
        self.fields["name"].widget.attrs["autocomplete"] = "name"
        self.fields["phone"].widget.attrs["autocomplete"] = "tel"
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""

    def clean_privacy_agreed(self):
        if not self.cleaned_data.get("privacy_agreed"):
            raise forms.ValidationError("必須同意個人資料處理聲明。")
        return True
