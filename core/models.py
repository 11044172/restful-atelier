from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .validators import sanitize_image_field, validate_image_upload


class SiteSettings(models.Model):
    class ImagePosition(models.TextChoices):
        CENTER = "center", "置中"
        TOP = "top", "靠上"
        BOTTOM = "bottom", "靠下"
        LEFT = "left", "靠左"
        RIGHT = "right", "靠右"

    brand_name = models.CharField("主要品牌名稱", max_length=120, default="Rfull")
    public_name = models.CharField("公開顯示名稱", max_length=120, default="靜院居家")
    english_name = models.CharField("英文名稱", max_length=120, default="RESTFULL ATELIER")
    brand_logo = models.ImageField("網站標誌", upload_to="site/branding/", blank=True, validators=[validate_image_upload])
    brand_logo_alt = models.CharField("網站標誌替代文字", max_length=160, default="Rfull Home", blank=True)
    shop_logo = models.ImageField("購物網站標誌", upload_to="site/branding/", blank=True, validators=[validate_image_upload])
    header_tagline = models.CharField("頁首品牌短句", max_length=120, default="室內・居家・生活", blank=True)
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
    business_legal_name = models.CharField("商家法定名稱", max_length=180, blank=True)
    business_representative = models.CharField("負責人", max_length=120, blank=True)
    business_address = models.CharField("營業地址", max_length=300, blank=True)
    returns_contact = models.CharField("退換貨／諮詢窗口", max_length=300, blank=True)
    business_hours = models.CharField("營業時間", max_length=200, blank=True)
    privacy_contact_email = models.EmailField("個資權利聯絡信箱", blank=True)
    customer_data_retention_days = models.PositiveIntegerField("顧客個資保存天數", default=2555)
    meta_description = models.CharField("預設 meta description", max_length=255, default="室內設計、生活器物與閱讀提案，整理屬於自己的生活節奏。")
    default_og_image = models.ImageField("預設 OGP／社群分享圖片", upload_to="site/seo/", blank=True, validators=[validate_image_upload])

    home_hero_image = models.ImageField("首頁 Hero 圖片", upload_to="site/home/", blank=True, validators=[validate_image_upload])
    home_hero_alt = models.CharField("首頁 Hero 圖片替代文字", max_length=255, default="靜院居家空間與生活提案", blank=True)
    home_hero_position = models.CharField("首頁 Hero 圖片位置", max_length=10, choices=ImagePosition.choices, default=ImagePosition.CENTER)
    home_hero_eyebrow = models.CharField("首頁 Hero 小標", max_length=160, default="靜院選集・Rfull", blank=True)
    home_hero_title = models.CharField("首頁 Hero 標題", max_length=120, default="靜處安身", blank=True)
    home_hero_title_accent = models.CharField("首頁 Hero 第二行標題", max_length=120, default="院宅清歡", blank=True)
    home_hero_description = models.TextField("首頁 Hero 說明", default="可以安身的處所，可以清歡的宅第。\n靜院是室內設計的規劃者，\n更是生活家居的實踐家。", blank=True)
    home_shop_cta_label = models.CharField("首頁購物按鈕文字", max_length=120, default="進入 靜及第 Online Shop →", blank=True)
    home_works_cta_label = models.CharField("首頁作品按鈕文字", max_length=120, default="室內設計作品集", blank=True)
    home_featured_title = models.CharField("首頁精選作品標題", max_length=180, default="讓光，慢慢住進來", blank=True)
    home_featured_aside = models.CharField("首頁精選作品短文", max_length=220, default="一處住宅，也是一種生活練習。", blank=True)
    home_brand_quote = models.CharField("首頁品牌主張", max_length=220, default="讓家成為一個\n可以慢下來的地方。", blank=True)
    home_brand_message = models.CharField("首頁品牌說明", max_length=255, default="我們不只選擇器物，也整理生活的節奏。", blank=True)
    home_brand_cta_label = models.CharField("首頁品牌連結文字", max_length=120, default="讀我們的故事 →", blank=True)

    works_intro_title = models.CharField("室內設計頁標題", max_length=160, default="靜院室內設計", blank=True)
    works_intro_statement = models.CharField("室內設計頁主張", max_length=160, default="自己是\n自己的典範", blank=True)
    works_consultation_text = models.CharField("室內設計頁諮詢文案", max_length=220, default="一個好住的家，從理解你的日常開始。", blank=True)
    works_consultation_cta = models.CharField("室內設計頁諮詢按鈕", max_length=100, default="設計諮詢 →", blank=True)

    publications_intro_text = models.TextField("出版品頁介紹", default="我們將空間裡微小的發現，編成一本可以帶回家、慢慢閱讀的書。", blank=True)
    publications_headline = models.CharField("出版品頁主標", max_length=160, default="每季\n一本\n安靜的書", blank=True)
    publications_note_quote = models.CharField("出版品頁引言", max_length=220, default="清歡不是什麼都沒有，\n而是知道什麼已經足夠。", blank=True)
    publications_note_text = models.CharField("出版品頁引言說明", max_length=255, default="記錄光落下的位置，也記錄器物被每日使用後的表情。", blank=True)

    shop_hero_image = models.ImageField("購物首頁 Hero 圖片", upload_to="site/shop/", blank=True, validators=[validate_image_upload])
    shop_hero_alt = models.CharField("購物首頁 Hero 圖片替代文字", max_length=255, default="靜及第生活器物選", blank=True)
    shop_hero_position = models.CharField("購物首頁 Hero 圖片位置", max_length=10, choices=ImagePosition.choices, default=ImagePosition.CENTER)
    shop_hero_eyebrow = models.CharField("購物首頁 Hero 小標", max_length=160, default="RFULL・OBJECTS", blank=True)
    shop_hero_title = models.CharField("購物首頁 Hero 標題", max_length=160, default="慢生活的\n器物清單", blank=True)
    shop_hero_description = models.TextField("購物首頁 Hero 說明", default="從一只晨飲杯到一方亞麻桌巾，\n每一樣都經過靜院的手，\n住進我們自己的家中使用。", blank=True)
    shop_featured_title = models.CharField("購物首頁精選標題", max_length=180, default="這個月特別想介紹的", blank=True)
    shop_featured_aside = models.CharField("購物首頁精選短文", max_length=220, default="適合在日常裡，慢慢開始使用。", blank=True)
    shop_story_image = models.ImageField("購物首頁季節文章圖片", upload_to="site/shop/", blank=True, validators=[validate_image_upload])
    shop_story_alt = models.CharField("購物首頁季節文章圖片替代文字", max_length=255, default="清晨的一杯茶", blank=True)
    shop_story_position = models.CharField("購物首頁季節文章圖片位置", max_length=10, choices=ImagePosition.choices, default=ImagePosition.CENTER)
    shop_story_title = models.CharField("購物首頁季節文章標題", max_length=160, default="清晨的\n一杯茶", blank=True)
    shop_story_quote = models.CharField("購物首頁季節文章引言", max_length=180, default="器物不是裝飾。", blank=True)
    shop_story_text = models.TextField("購物首頁季節文章內文", default="每天拿起、清洗、收回原位的過程，才會讓它慢慢成為生活的一部分。", blank=True)

    about_image = models.ImageField("關於頁主要圖片", upload_to="site/about/", blank=True, validators=[validate_image_upload])
    about_image_alt = models.CharField("關於頁圖片替代文字", max_length=255, default="靜院工作室", blank=True)
    about_image_position = models.CharField("關於頁圖片位置", max_length=10, choices=ImagePosition.choices, default=ImagePosition.CENTER)
    about_title = models.CharField("關於頁標題", max_length=240, default="靜院可以是\n室內設計的規劃者，\n更是生活家居的實踐家。", blank=True)
    about_intro = models.TextField("關於頁介紹", default="從 2019 年第一個住宅案開始，我們相信家不只是空間，而是一個人對自己最誠實的投影。\n\n所以我們從規劃動線開始，也從挑選一只茶杯開始——因為每一件進入你家的東西，都會和你一起呼吸。\n\n這也是為什麼，在靜院，設計與選物從不分開。", blank=True)
    about_timeline_heading = models.CharField("關於頁沿革標題", max_length=100, default="2019 —", blank=True)
    about_timeline_text = models.TextField("關於頁沿革內文", default="從第一個住宅案，到第一本季刊、第一只被帶回家的杯子。靜院仍在練習，如何把空間、器物與人的日常，安靜地放在一起。", blank=True)

    contact_title = models.CharField("聯絡頁標題", max_length=160, default="聯絡我們", blank=True)
    contact_lead = models.CharField("聯絡頁引言", max_length=220, default="歡迎來信，\n我們會靜靜地回覆你。", blank=True)

    footer_message = models.TextField("頁尾品牌介紹", default="自己是自己的典範。\n從一扇窗、一只器皿、一盞茶開始，\n把家養成自己的道場。", blank=True)
    footer_copyright = models.CharField("頁尾版權文字", max_length=160, default="© 2026 靜院居家", blank=True)
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

    def save(self, *args, **kwargs):
        # Admin uploads are metadata-only R2 keys. Keep this guard solely for
        # non-admin legacy callers that still assign an UploadedFile directly.
        for field_name in (
            "brand_logo", "shop_logo", "default_og_image", "home_hero_image",
            "shop_hero_image", "shop_story_image", "about_image", "taiwan_pay_qr",
        ):
            sanitize_image_field(self, field_name)
        super().save(*args, **kwargs)


class DirectImageUpload(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待上傳"
        READY = "ready", "已確認"
        ATTACHED = "attached", "已使用"
        DELETION_PENDING = "deletion_pending", "等待清理"

    object_key = models.CharField(max_length=255, unique=True)
    category = models.CharField(max_length=80)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=64)
    file_size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="direct_image_uploads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=("status", "created_at"))]


class RateLimitBucket(models.Model):
    scope = models.CharField("範圍", max_length=40)
    key_hash = models.CharField("識別雜湊", max_length=64)
    window_started_at = models.DateTimeField("計時開始", db_index=True)
    count = models.PositiveIntegerField("次數", default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("scope", "key_hash", "window_started_at"), name="unique_rate_limit_bucket")]
        indexes = [models.Index(fields=("scope", "window_started_at"))]


class PrivacyRequest(models.Model):
    class RequestType(models.TextChoices):
        ACCESS = "access", "查詢／匯出"
        CORRECTION = "correction", "更正"
        RESTRICT = "restrict", "停止處理"
        ANONYMIZE = "anonymize", "匿名化／刪除"

    class Status(models.TextChoices):
        RECEIVED = "received", "已受理"
        VERIFYING = "verifying", "本人確認中"
        PROCESSING = "processing", "處理中"
        COMPLETED = "completed", "完成"
        REJECTED = "rejected", "拒絕／依法保留"

    request_type = models.CharField("申請類型", max_length=20, choices=RequestType.choices)
    email = models.EmailField("聯絡 Email")
    order_reference = models.CharField("訂單編號", max_length=32, blank=True)
    status = models.CharField("狀態", max_length=20, choices=Status.choices, default=Status.RECEIVED)
    details = models.TextField("申請內容", blank=True)
    operator_note = models.TextField("處理紀錄", blank=True)
    verified_at = models.DateTimeField("本人確認時間", null=True, blank=True)
    completed_at = models.DateTimeField("完成時間", null=True, blank=True)
    created_at = models.DateTimeField("受理時間", auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
