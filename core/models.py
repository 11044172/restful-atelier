from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .validators import validate_image_upload


class SiteSettings(models.Model):
    brand_name = models.CharField("主要品牌名稱", max_length=120, default="Rfull")
    public_name = models.CharField("公開顯示名稱", max_length=120, default="靜院居家")
    english_name = models.CharField("英文名稱", max_length=120, default="RESTFULL ATELIER")
    phone_primary = models.CharField("電話 1", max_length=40, default="+886-37-750006", blank=True)
    phone_secondary = models.CharField("電話 2", max_length=40, default="+886-932526160", blank=True)
    general_email = models.EmailField("一般、購物與訂單 Email", default="rfullshop@gmail.com", blank=True)
    design_email = models.EmailField("設計諮詢 Email", default="vicky725705@gmail.com", blank=True)
    media_email = models.EmailField("媒體合作 Email", default="vicky725705@gmail.com", blank=True)
    business_email = models.EmailField("商業合作 Email", default="vicky725705@gmail.com", blank=True)
    facebook_url = models.URLField("Facebook URL", blank=True)
    instagram_url = models.URLField("Instagram URL", blank=True)
    line_official_url = models.URLField("LINE 官方帳號網址", blank=True)
    line_add_friend_url = models.URLField("LINE 加好友網址", blank=True)
    line_service_label = models.CharField("LINE 客服標示", max_length=120, default="LINE 客服", blank=True)
    line_service_hours = models.CharField("LINE客服時間", max_length=200, default="週一至週五 10:00–18:00", blank=True)
    line_after_hours_note = models.CharField("非營業時間說明", max_length=255, default="非營業時間訊息將於下一工作日回覆", blank=True)
    bank_name = models.CharField("銀行名稱", max_length=120, blank=True)
    bank_code = models.CharField("銀行代碼", max_length=30, blank=True)
    bank_account_number = models.CharField("銀行帳號", max_length=80, blank=True)
    bank_account_name = models.CharField("戶名", max_length=120, blank=True)
    taiwan_pay_qr = models.ImageField("Taiwan Pay QR", upload_to="payments/taiwan-pay/", blank=True, validators=[validate_image_upload])
    checkout_enabled = models.BooleanField("啟用結帳與訂單功能", default=True)
    order_notification_email = models.EmailField("訂單通知 Email", default="rfullshop@gmail.com", blank=True)
    meta_description = models.CharField("預設 meta description", max_length=255, default="室內設計、生活器物與閱讀提案，整理屬於自己的生活節奏。")
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        verbose_name = "網站設定"
        verbose_name_plural = "網站設定"

    def __str__(self):
        return "Rfull 網站設定"

    def clean(self):
        if self.pk and self.__class__.objects.exclude(pk=self.pk).exists():
            raise ValidationError("網站設定只能建立一筆。")
        if self.checkout_enabled:
            from .line_config import line_settings_configured

            if not line_settings_configured() or not self.line_url:
                raise ValidationError({"checkout_enabled": "請先完成 LINE Login、Messaging API 與加好友網址設定，再啟用結帳功能。"})

    @classmethod
    def load(cls):
        return cls.objects.first() or cls()

    @property
    def line_url(self):
        explicit_url = self.line_add_friend_url or self.line_official_url
        if explicit_url:
            return explicit_url
        basic_id = settings.LINE_OFFICIAL_ACCOUNT_BASIC_ID.strip()
        if not basic_id:
            return ""
        return f"https://line.me/R/ti/p/{quote(basic_id, safe='@')}"

    def checkout_available(self, *, debug=False):
        from .line_config import line_settings_configured

        return self.checkout_enabled and line_settings_configured() and bool(self.line_url)
