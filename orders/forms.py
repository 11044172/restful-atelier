import re

from django import forms

from .models import Payment, PaymentMethod


class ShippingConfirmationForm(forms.Form):
    shipping_fee = forms.DecimalField(
        label="運費",
        min_value=0,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(attrs={"min": "0", "step": "1", "inputmode": "numeric"}),
    )


class ShippingRevisionForm(ShippingConfirmationForm):
    acknowledge_reissue = forms.BooleanField(
        label="我已確認舊的未付款交易與付款連結將失效，並以新金額重新通知顧客。",
        required=True,
    )


class ShippingDispatchForm(forms.Form):
    carrier = forms.CharField(label="物流公司", max_length=120)
    tracking_number = forms.CharField(label="追蹤號碼", max_length=160, required=False)
    tracking_url = forms.URLField(label="追蹤網址", required=False)


class ManualPaymentConfirmationForm(forms.Form):
    payment = forms.ModelChoiceField(label="手動付款記錄", queryset=Payment.objects.none())

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment"].queryset = Payment.objects.filter(
            order=order,
            method__code__in=(PaymentMethod.Method.TAIWAN_PAY, PaymentMethod.Method.BANK_TRANSFER),
            amount=order.final_total,
        ).exclude(status=Payment.Status.CONFIRMED).select_related("method")


class CheckoutForm(forms.Form):
    INVOICE_CHOICES = (
        ("personal", "個人電子發票（綠界載具）"),
        ("mobile_barcode", "手機條碼載具"),
        ("company", "公司用電子發票"),
    )

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
    invoice_type = forms.ChoiceField(
        label="發票類型",
        choices=INVOICE_CHOICES,
        initial="personal",
        required=False,
        widget=forms.RadioSelect(attrs={"data-invoice-type": ""}),
    )
    invoice_carrier_number = forms.CharField(
        label="手機條碼",
        max_length=8,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "/ABC1234"}),
    )
    invoice_customer_identifier = forms.CharField(
        label="統一編號",
        max_length=8,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )
    invoice_customer_name = forms.CharField(
        label="發票抬頭／公司名稱",
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    policies_accepted = forms.BooleanField(label="我已閱讀並同意購物須知、付款、配送、預購、退換貨與隱私權政策。", required=True)
    idempotency_key = forms.CharField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("送出內容無效，請重新操作。")
        return ""

    def clean(self):
        data = super().clean()
        structured = [data.get("recipient_name"), data.get("postal_code"), data.get("city"), data.get("district"), data.get("street_address")]
        if not data.get("shipping_information") and not all(structured):
            raise forms.ValidationError("請完整填寫收件人、郵遞區號、縣市、鄉鎮市區與街道地址。")
        invoice_type = data.get("invoice_type") or "personal"
        data["invoice_type"] = invoice_type
        carrier_number = (data.get("invoice_carrier_number") or "").upper()
        customer_identifier = (data.get("invoice_customer_identifier") or "").strip()
        customer_name = (data.get("invoice_customer_name") or "").strip()
        if invoice_type == "mobile_barcode":
            if not re.fullmatch(r"/[0-9A-Z+\-.]{7}", carrier_number):
                self.add_error("invoice_carrier_number", "手機條碼須為 / 開頭，後接 7 碼大寫英數字或 + - .。")
        elif invoice_type == "company":
            if not re.fullmatch(r"\d{8}", customer_identifier):
                self.add_error("invoice_customer_identifier", "統一編號必須為 8 碼數字。")
            if not customer_name:
                self.add_error("invoice_customer_name", "請填寫發票抬頭／公司名稱。")
        data["invoice_carrier_number"] = carrier_number if invoice_type == "mobile_barcode" else ""
        data["invoice_customer_identifier"] = customer_identifier if invoice_type == "company" else ""
        data["invoice_customer_name"] = customer_name if invoice_type == "company" else ""
        return data


class PaymentSelectionForm(forms.Form):
    payment_variant = forms.ChoiceField(label="付款方式", choices=(), widget=forms.HiddenInput)
    final_terms_accepted = forms.BooleanField(label="我已確認最終訂單金額並同意現行購物與退換貨政策。", required=True)

    def __init__(self, *args, variants=(), **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "standard": "使用 ECPay 付款",
            "installment": "信用卡分期付款",
        }
        self.fields["payment_variant"].choices = [
            (variant, labels[variant]) for variant in variants if variant in labels
        ]
