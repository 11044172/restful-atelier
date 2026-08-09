from django import forms


class CheckoutForm(forms.Form):
    customer_name = forms.CharField(label="姓名", max_length=160, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    phone = forms.CharField(label="電話", max_length=60, widget=forms.TextInput(attrs={"autocomplete": "tel"}))
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    shipping_information = forms.CharField(label="配送資訊", widget=forms.Textarea(attrs={"rows": 4, "autocomplete": "street-address"}))
    customer_note = forms.CharField(label="備考", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    idempotency_key = forms.CharField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""
