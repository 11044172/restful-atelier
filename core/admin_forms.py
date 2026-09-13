from django import forms

from content.models import InteriorProject, InteriorProjectImage, Publication
from orders.models import PaymentMethod

from .admin_json_fields import StringListFormField
from .direct_image_forms import DirectImageAdminFormMixin
from .models import SiteSettings


class SiteSettingsAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {
        "brand_logo": "site.brand_logo",
        "shop_logo": "site.shop_logo",
        "taiwan_pay_qr": "site.taiwan_pay_qr",
        "default_og_image": "site.default_og_image",
        "home_hero_image": "site.home_hero_image",
        "shop_hero_image": "site.shop_hero_image",
        "shop_story_image": "site.shop_story_image",
        "about_image": "site.about_image",
    }

    class Meta:
        model = SiteSettings
        fields = "__all__"


class InteriorProjectAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    design_notes = StringListFormField(label="設計筆記")
    materials = StringListFormField(label="材質列表")
    project_image_session = forms.UUIDField(required=False, widget=forms.HiddenInput)
    project_image_order = forms.CharField(required=False, widget=forms.HiddenInput)
    direct_image_fields = {"featured_image": "project.featured_image"}

    class Meta:
        model = InteriorProject
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        raw_order = cleaned_data.get("project_image_order", "")
        try:
            image_ids = [int(value) for value in raw_order.split(",") if value]
        except (TypeError, ValueError):
            self.add_error("project_image_order", "作品圖片排序資料無效。")
            image_ids = []
        if len(image_ids) != len(set(image_ids)):
            self.add_error("project_image_order", "作品圖片重複。")

        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        upload_session = cleaned_data.get("project_image_session")
        if upload_session and user and user.is_authenticated:
            if self.instance.pk:
                valid_ids = set(
                    InteriorProjectImage.objects.filter(
                        project=self.instance,
                        upload_status=InteriorProjectImage.UploadStatus.ATTACHED,
                    ).values_list("pk", flat=True)
                )
            else:
                valid_ids = set(
                    InteriorProjectImage.objects.filter(
                        project__isnull=True,
                        uploaded_by=user,
                        upload_session=upload_session,
                        upload_status=InteriorProjectImage.UploadStatus.TEMPORARY,
                    ).values_list("pk", flat=True)
                )
            if valid_ids != set(image_ids):
                self.add_error(
                    "project_image_order",
                    "無法確認作品圖片的所屬關係，請重新載入頁面。",
                )
                image_ids = []
        elif image_ids:
            self.add_error("project_image_session", "上傳工作階段無效。")
            image_ids = []
        self.cleaned_project_image_ids = image_ids
        return cleaned_data


class InteriorProjectImageAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {"image": "project.gallery_image"}

    class Meta:
        model = InteriorProjectImage
        fields = ("project", "image", "alt_text", "caption", "tone", "sort_order")


class PublicationAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {"cover_image": "publication.cover_image"}

    class Meta:
        model = Publication
        fields = "__all__"


class PaymentMethodAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {"qr_image": "payment.qr_image"}

    class Meta:
        model = PaymentMethod
        fields = "__all__"


def request_bound_form(form_class, request):
    class RequestBoundForm(form_class):
        def __init__(self, *args, **kwargs):
            kwargs["request"] = request
            super().__init__(*args, **kwargs)

    return RequestBoundForm
