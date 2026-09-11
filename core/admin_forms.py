from django import forms

from content.models import InteriorProject, InteriorProjectImage, Publication
from orders.models import PaymentMethod

from .direct_image_forms import DirectImageAdminFormMixin
from .admin_json_fields import StringListFormField
from .models import SiteSettings


class SiteSettingsAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {
        "brand_logo": "site.brand_logo", "shop_logo": "site.shop_logo",
        "taiwan_pay_qr": "site.taiwan_pay_qr", "default_og_image": "site.default_og_image",
        "home_hero_image": "site.home_hero_image", "shop_hero_image": "site.shop_hero_image",
        "shop_story_image": "site.shop_story_image", "about_image": "site.about_image",
    }
    class Meta:
        model = SiteSettings
        fields = "__all__"


class InteriorProjectAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    design_notes = StringListFormField(label="設計筆記")
    materials = StringListFormField(label="材質列表")
    direct_image_fields = {"featured_image": "project.featured_image"}
    class Meta:
        model = InteriorProject
        fields = "__all__"


class InteriorProjectImageAdminForm(DirectImageAdminFormMixin, forms.ModelForm):
    direct_image_fields = {"image": "project.gallery_image"}
    class Meta:
        model = InteriorProjectImage
        fields = "__all__"


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
