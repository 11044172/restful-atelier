from django.db import models


class InquiryCategory(models.Model):
    display_name = models.CharField("表示名", max_length=120)
    slug = models.SlugField("slug", unique=True)
    recipient_email = models.EmailField("送信先 Email")
    active = models.BooleanField("有効", default=True)
    sort_order = models.PositiveIntegerField("表示順", default=0)

    class Meta:
        ordering = ("sort_order", "display_name")
        verbose_name = "問い合わせ分類"
        verbose_name_plural = "問い合わせ分類"

    def __str__(self):
        return self.display_name


class Inquiry(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "新規"
        IN_PROGRESS = "in_progress", "対応中"
        COMPLETED = "completed", "完了"
        SPAM = "spam", "スパム"

    category = models.ForeignKey(InquiryCategory, verbose_name="分類", on_delete=models.PROTECT, related_name="inquiries")
    name = models.CharField("姓名", max_length=160)
    phone = models.CharField("電話", max_length=60)
    email = models.EmailField("Email")
    budget_range = models.CharField("予算区間", max_length=160, blank=True)
    project_location = models.CharField("案件地点", max_length=240, blank=True)
    expected_timing = models.CharField("予定時期", max_length=200, blank=True)
    message = models.TextField("メッセージ")
    privacy_agreed = models.BooleanField("個人資料処理に同意")
    newsletter_opt_in = models.BooleanField("電子報購読", default=False)
    status = models.CharField("ステータス", max_length=24, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField("対応完了日時", null=True, blank=True)
    admin_note = models.TextField("管理メモ", blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "問い合わせ"
        verbose_name_plural = "問い合わせ"
        indexes = [models.Index(fields=("status", "created_at"))]

    def __str__(self):
        return f"{self.name} / {self.category} / {self.created_at:%Y-%m-%d}"
