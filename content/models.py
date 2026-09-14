from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.validators import sanitize_image_field, validate_image_upload


def image_focus_position(x, y):
    return f"{x}% {y}%"


def image_aspect_ratio(width, height, *, fallback):
    if width and height:
        return f"{width} / {height}"
    return fallback


def image_orientation(width, height):
    if not width or not height:
        return "unknown"
    if width == height:
        return "square"
    return "portrait" if width < height else "landscape"


class InteriorProject(models.Model):
    title = models.CharField("作品標題", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    english_title = models.CharField("英文標題", max_length=240, blank=True)
    project_type = models.CharField("作品分類", max_length=120, blank=True)
    location = models.CharField("地點", max_length=160, blank=True)
    year = models.PositiveIntegerField("年份", null=True, blank=True)
    area = models.CharField("面積", max_length=100, blank=True)
    style = models.CharField("設計風格", max_length=160, blank=True)
    description = models.TextField("作品說明")
    concept_title = models.CharField("設計概念標題", max_length=300, blank=True)
    design_notes = models.JSONField("設計筆記", default=list, blank=True)
    materials = models.JSONField("材質列表", default=list, blank=True)
    featured_image = models.ImageField(
        "主要圖片",
        upload_to="projects/%Y/%m/",
        blank=True,
        validators=[validate_image_upload],
    )
    featured_image_focus_x = models.PositiveSmallIntegerField(
        "主要圖片焦點 X",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    featured_image_focus_y = models.PositiveSmallIntegerField(
        "主要圖片焦點 Y",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    image_label = models.CharField("預留圖片文字", max_length=180, blank=True)
    tone = models.CharField("預留圖片色調", max_length=40, default="bamboo", blank=True)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ("sort_order", "-year", "title")
        verbose_name = "室內設計作品"
        verbose_name_plural = "室內設計作品"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(featured_image_focus_x__range=(0, 100)),
                name="content_project_featured_focus_x_range",
            ),
            models.CheckConstraint(
                condition=models.Q(featured_image_focus_y__range=(0, 100)),
                name="content_project_featured_focus_y_range",
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:project_detail", args=[self.slug])

    @property
    def featured_image_position(self):
        return image_focus_position(
            self.featured_image_focus_x, self.featured_image_focus_y
        )

    def save(self, *args, **kwargs):
        self.design_notes = self.design_notes or []
        self.materials = self.materials or []
        sanitize_image_field(self, "featured_image")
        super().save(*args, **kwargs)


class InteriorProjectImage(models.Model):
    class UploadStatus(models.TextChoices):
        PENDING = "pending", "上傳待確認"
        TEMPORARY = "temporary", "暫存"
        ATTACHED = "attached", "已連結作品"
        DELETION_PENDING = "deletion_pending", "等待刪除"

    project = models.ForeignKey(
        InteriorProject,
        verbose_name="作品",
        on_delete=models.CASCADE,
        related_name="images",
        null=True,
        blank=True,
    )
    image = models.ImageField(
        "圖片",
        upload_to="projects/gallery/%Y/%m/",
        blank=True,
        validators=[validate_image_upload],
    )
    alt_text = models.CharField("替代文字", max_length=255, blank=True)
    caption = models.CharField("圖片說明", max_length=255, blank=True)
    tone = models.CharField("預留圖片色調", max_length=40, default="linen", blank=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    upload_status = models.CharField(
        "上傳狀態",
        max_length=24,
        choices=UploadStatus.choices,
        default=UploadStatus.ATTACHED,
    )
    upload_session = models.UUIDField(
        "上傳工作階段", null=True, blank=True, db_index=True
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="上傳者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_project_images",
    )
    original_filename = models.CharField("原始檔名", max_length=255, blank=True)
    content_type = models.CharField("Content-Type", max_length=64, blank=True)
    file_size = models.PositiveBigIntegerField("檔案大小", null=True, blank=True)
    width = models.PositiveIntegerField("寬度", null=True, blank=True)
    height = models.PositiveIntegerField("高度", null=True, blank=True)
    focus_x = models.PositiveSmallIntegerField(
        "圖片焦點 X",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    focus_y = models.PositiveSmallIntegerField(
        "圖片焦點 Y",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField("建立時間", default=timezone.now, editable=False)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "作品圖片"
        verbose_name_plural = "作品圖片"
        indexes = [
            models.Index(
                fields=("project", "upload_status", "sort_order"),
                name="content_ipi_project_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(focus_x__range=(0, 100)),
                name="content_project_image_focus_x_range",
            ),
            models.CheckConstraint(
                condition=models.Q(focus_y__range=(0, 100)),
                name="content_project_image_focus_y_range",
            ),
        ]

    @property
    def focus_position(self):
        return image_focus_position(self.focus_x, self.focus_y)

    @property
    def aspect_ratio(self):
        return image_aspect_ratio(self.width, self.height, fallback="4 / 3")

    @property
    def orientation_class(self):
        return f"project-gallery-image--{image_orientation(self.width, self.height)}"

    def save(self, *args, **kwargs):
        if not (self.alt_text or "").strip():
            self.alt_text = (
                (self.project.title if self.project_id else "") or "作品圖片"
            ).strip()
        sanitize_image_field(self, "image")
        super().save(*args, **kwargs)


class Publication(models.Model):
    issue_number = models.CharField("期號", max_length=40)
    title = models.CharField("標題", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    subtitle = models.CharField("副標題", max_length=300, blank=True)
    description = models.TextField("出版說明", blank=True)
    page_count = models.PositiveIntegerField("頁數", null=True, blank=True)
    published_date = models.DateField("出版日期", null=True, blank=True)
    cover_image = models.ImageField(
        "封面圖片",
        upload_to="publications/%Y/%m/",
        blank=True,
        validators=[validate_image_upload],
    )
    cover_image_width = models.PositiveIntegerField(
        "封面圖片寬度",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    cover_image_height = models.PositiveIntegerField(
        "封面圖片高度",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    cover_image_focus_x = models.PositiveSmallIntegerField(
        "封面圖片焦點 X",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    cover_image_focus_y = models.PositiveSmallIntegerField(
        "封面圖片焦點 Y",
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tone = models.CharField("預留圖片色調", max_length=40, default="rice", blank=True)
    featured = models.BooleanField("精選顯示", default=False)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ("sort_order", "-published_date", "issue_number")
        verbose_name = "出版刊物"
        verbose_name_plural = "出版刊物"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cover_image_focus_x__range=(0, 100)),
                name="content_publication_cover_focus_x_range",
            ),
            models.CheckConstraint(
                condition=models.Q(cover_image_focus_y__range=(0, 100)),
                name="content_publication_cover_focus_y_range",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cover_image_width__isnull=True)
                    | models.Q(cover_image_width__gte=1)
                ),
                name="content_publication_cover_width_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cover_image_height__isnull=True)
                    | models.Q(cover_image_height__gte=1)
                ),
                name="content_publication_cover_height_positive",
            ),
        ]

    def __str__(self):
        return f"{self.issue_number} {self.title}"

    def get_absolute_url(self):
        return reverse("content:publication_detail", args=[self.slug])

    @property
    def cover_image_position(self):
        return image_focus_position(
            self.cover_image_focus_x, self.cover_image_focus_y
        )

    @property
    def cover_image_aspect_ratio(self):
        return image_aspect_ratio(
            self.cover_image_width,
            self.cover_image_height,
            fallback="3 / 4.25",
        )

    @property
    def cover_image_detail_aspect_ratio(self):
        return image_aspect_ratio(
            self.cover_image_width,
            self.cover_image_height,
            fallback="16 / 9",
        )

    @property
    def cover_image_orientation_class(self):
        return (
            "publication-image--"
            f"{image_orientation(self.cover_image_width, self.cover_image_height)}"
        )

    def save(self, *args, **kwargs):
        sanitize_image_field(self, "cover_image")
        super().save(*args, **kwargs)


class PolicyPage(models.Model):
    title = models.CharField("頁面標題", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    body = models.TextField("內文", blank=True)
    version = models.CharField("版本", max_length=40, blank=True)
    effective_date = models.DateField("生效日期", null=True, blank=True)
    legal_reviewed = models.BooleanField("業者法務已確認", default=False)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ("sort_order", "title")
        verbose_name = "政策頁面"
        verbose_name_plural = "政策頁面"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:policy", args=[self.slug])

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.published and (
            not self.body.strip() or not self.version.strip() or not self.effective_date
        ):
            raise ValidationError("公開政策頁面必須有本文、版本與生效日期。")
