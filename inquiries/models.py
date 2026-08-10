from django.db import models


class InquiryCategory(models.Model):
    display_name = models.CharField("顯示名稱", max_length=120)
    slug = models.SlugField("slug", unique=True)
    recipient_email = models.EmailField("收件 Email")
    active = models.BooleanField("啟用", default=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)

    class Meta:
        ordering = ("sort_order", "display_name")
        verbose_name = "諮詢分類"
        verbose_name_plural = "諮詢分類"

    def __str__(self):
        return self.display_name


class Inquiry(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "新留言"
        IN_PROGRESS = "in_progress", "處理中"
        COMPLETED = "completed", "已完成"
        SPAM = "spam", "垃圾訊息"

    category = models.ForeignKey(InquiryCategory, verbose_name="分類", on_delete=models.PROTECT, related_name="inquiries")
    name = models.CharField("姓名", max_length=160)
    phone = models.CharField("電話", max_length=60)
    email = models.EmailField("Email")
    budget_range = models.CharField("預算範圍", max_length=160, blank=True)
    project_location = models.CharField("案件地点", max_length=240, blank=True)
    expected_timing = models.CharField("預計時程", max_length=200, blank=True)
    message = models.TextField("留言內容")
    privacy_agreed = models.BooleanField("同意個人資料處理")
    newsletter_opt_in = models.BooleanField("訂閱電子報", default=False)
    status = models.CharField("處理狀態", max_length=24, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    handled_at = models.DateTimeField("處理完成時間", null=True, blank=True)
    admin_note = models.TextField("管理備註", blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "客戶諮詢"
        verbose_name_plural = "客戶諮詢"
        indexes = [models.Index(fields=("status", "created_at"))]

    def __str__(self):
        return f"{self.name} / {self.category} / {self.created_at:%Y-%m-%d}"
