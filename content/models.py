from django.db import models
from django.urls import reverse

from core.validators import sanitize_image_field, validate_image_upload


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
    featured_image = models.ImageField("主要圖片", upload_to="projects/%Y/%m/", blank=True, validators=[validate_image_upload])
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

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:project_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        sanitize_image_field(self, "featured_image")
        super().save(*args, **kwargs)


class InteriorProjectImage(models.Model):
    project = models.ForeignKey(InteriorProject, verbose_name="作品", on_delete=models.CASCADE, related_name="images")
    image = models.ImageField("圖片", upload_to="projects/gallery/%Y/%m/", blank=True, validators=[validate_image_upload])
    alt_text = models.CharField("替代文字", max_length=255)
    caption = models.CharField("圖片說明", max_length=255, blank=True)
    tone = models.CharField("預留圖片色調", max_length=40, default="linen", blank=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "作品圖片"
        verbose_name_plural = "作品圖片"

    def save(self, *args, **kwargs):
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
    cover_image = models.ImageField("封面圖片", upload_to="publications/%Y/%m/", blank=True, validators=[validate_image_upload])
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

    def __str__(self):
        return f"{self.issue_number} {self.title}"

    def get_absolute_url(self):
        return reverse("content:publication_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        sanitize_image_field(self, "cover_image")
        super().save(*args, **kwargs)


class PolicyPage(models.Model):
    title = models.CharField("頁面標題", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    body = models.TextField("內文", blank=True)
    version = models.CharField("版本", max_length=40, blank=True)
    effective_date = models.DateField("生效日期", null=True, blank=True)
    legal_reviewed = models.BooleanField("事業者法務確認済み", default=False)
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

        if self.published and (not self.body.strip() or not self.version.strip() or not self.effective_date):
            raise ValidationError("公開政策頁面必須有本文、版本與生效日期。")
