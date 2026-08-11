from django import forms


class CheckoutForm(forms.Form):
    customer_name = forms.CharField(label="姓名", max_length=160, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    phone = forms.CharField(label="電話", max_length=60, widget=forms.TextInput(attrs={"autocomplete": "tel"}))
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    recipient_name = forms.CharField(label="收件人", max_length=160, required=False, widget=forms.TextInput(attrs={"autocomplete": "shipping name"}))
    postal_code = forms.CharField(label="郵遞區號", max_length=12, required=False, widget=forms.TextInput(attrs={"autocomplete": "shipping postal-code", "inputmode": "numeric"}))
    city = forms.CharField(label="縣市", max_length=60, required=False, widget=forms.TextInput(attrs={"autocomplete": "shipping address-level1"}))
    district = forms.CharField(label="鄉鎮市區", max_length=80, required=False, widget=forms.TextInput(attrs={"autocomplete": "shipping address-level2"}))
    street_address = forms.CharField(label="街道地址", max_length=300, required=False, widget=forms.TextInput(attrs={"autocomplete": "shipping street-address"}))
    shipping_information = forms.CharField(required=False, widget=forms.HiddenInput)
    delivery_note = forms.CharField(label="配送備註", max_length=300, required=False, widget=forms.TextInput())
    customer_note = forms.CharField(label="備註", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    policies_accepted = forms.BooleanField(label="我已閱讀並同意購物須知、付款、配送、預購、退換貨與隱私權政策。", required=True)
    idempotency_key = forms.CharField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""

    def clean(self):
        data = super().clean()
        structured = [data.get("recipient_name"), data.get("postal_code"), data.get("city"), data.get("district"), data.get("street_address")]
        if not data.get("shipping_information") and not all(structured):
            raise forms.ValidationError("請完整填寫收件人、郵遞區號、縣市、鄉鎮市區與街道地址。")
        return data


class PaymentSelectionForm(forms.Form):
    payment_method = forms.ModelChoiceField(label="付款方式", queryset=None)
    final_terms_accepted = forms.BooleanField(label="我已確認最終訂單金額並同意現行購物與退換貨政策。", required=True)

    def __init__(self, *args, methods=(), **kwargs):
        super().__init__(*args, **kwargs)
        from .models import PaymentMethod
        ids = [method.pk for method in methods]
        self.fields["payment_method"].queryset = PaymentMethod.objects.filter(pk__in=ids, enabled=True).order_by("sort_order", "pk")
