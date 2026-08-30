from django import forms

from .models import Product


class ProductAdminForm(forms.ModelForm):
    """Allow incomplete drafts while giving Product.clean the inline image state."""

    class Meta:
        model = Product
        fields = "__all__"

    def clean_slug(self):
        return self.cleaned_data.get("slug") or Product.new_draft_slug()

    def clean_sku(self):
        return self.cleaned_data.get("sku") or Product.new_draft_sku()

    def clean_stock(self):
        stock = self.cleaned_data.get("stock")
        return 0 if stock is None else stock

    def clean(self):
        cleaned_data = super().clean()
        total_forms = self.data.get("images-TOTAL_FORMS")
        if total_forms is not None:
            has_image = False
            existing = {
                str(image.pk): image
                for image in self.instance.images.all()
            } if self.instance.pk else {}
            try:
                form_count = int(total_forms)
            except (TypeError, ValueError):
                form_count = 0
            for index in range(form_count):
                prefix = f"images-{index}"
                if self.data.get(f"{prefix}-DELETE"):
                    continue
                upload = self.files.get(f"{prefix}-image")
                if upload:
                    has_image = True
                    break
                image = existing.get(self.data.get(f"{prefix}-id", ""))
                if image and image.image and not self.data.get(f"{prefix}-image-clear"):
                    has_image = True
                    break
            self.instance._admin_has_product_image = has_image
        return cleaned_data
