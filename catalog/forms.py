from django import forms

from .models import Product, ProductImage


class ProductAdminForm(forms.ModelForm):
    """Allow incomplete drafts while giving Product.clean the inline image state."""

    product_image_session = forms.UUIDField(required=False, widget=forms.HiddenInput)
    product_image_order = forms.CharField(required=False, widget=forms.HiddenInput)

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
        raw_order = cleaned_data.get("product_image_order", "")
        try:
            image_ids = [int(value) for value in raw_order.split(",") if value]
        except (TypeError, ValueError):
            self.add_error("product_image_order", "商品圖片排序資料無效。")
            image_ids = []
        if len(image_ids) != len(set(image_ids)):
            self.add_error("product_image_order", "商品圖片重複。")

        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        upload_session = cleaned_data.get("product_image_session")
        if upload_session and user and user.is_authenticated:
            if self.instance.pk:
                valid_ids = set(
                    ProductImage.objects.filter(
                        product=self.instance,
                        upload_status=ProductImage.UploadStatus.ATTACHED,
                    ).values_list("pk", flat=True)
                )
            else:
                valid_ids = set(
                    ProductImage.objects.filter(
                        product__isnull=True,
                        uploaded_by=user,
                        upload_session=upload_session,
                        upload_status=ProductImage.UploadStatus.TEMPORARY,
                    ).values_list("pk", flat=True)
                )
            if valid_ids != set(image_ids):
                self.add_error(
                    "product_image_order",
                    "無法確認商品圖片的所屬關係，請重新載入頁面。",
                )
                image_ids = []
        elif image_ids:
            self.add_error("product_image_session", "上傳工作階段無效。")
            image_ids = []

        self.cleaned_product_image_ids = image_ids
        self.instance._admin_has_product_image = bool(image_ids)
        return cleaned_data
