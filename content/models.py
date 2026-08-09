from django.db import models
from django.urls import reverse

from core.validators import validate_image_upload


class InteriorProject(models.Model):
    title = models.CharField("タイトル", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    english_title = models.CharField("英語タイトル", max_length=240, blank=True)
    project_type = models.CharField("作品分類", max_length=120, blank=True)
    location = models.CharField("場所", max_length=160, blank=True)
    year = models.PositiveIntegerField("年", null=True, blank=True)
    area = models.CharField("面積", max_length=100, blank=True)
    style = models.CharField("スタイル", max_length=160, blank=True)
    description = models.TextField("説明")
    concept_title = models.CharField("コンセプト見出し", max_length=300, blank=True)
    design_notes = models.JSONField("設計ノート", default=list, blank=True)
    materials = models.JSONField("素材一覧", default=list, blank=True)
    featured_image = models.ImageField("メイン画像", upload_to="projects/%Y/%m/", blank=True, validators=[validate_image_upload])
    image_label = models.CharField("仮画像ラベル", max_length=180, blank=True)
    tone = models.CharField("仮画像色", max_length=40, default="bamboo", blank=True)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("表示順", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "-year", "title")
        verbose_name = "室内設計作品"
        verbose_name_plural = "室内設計作品"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:project_detail", args=[self.slug])


class InteriorProjectImage(models.Model):
    project = models.ForeignKey(InteriorProject, verbose_name="作品", on_delete=models.CASCADE, related_name="images")
    image = models.ImageField("画像", upload_to="projects/gallery/%Y/%m/", blank=True, validators=[validate_image_upload])
    alt_text = models.CharField("代替テキスト", max_length=255)
    caption = models.CharField("キャプション", max_length=255, blank=True)
    tone = models.CharField("仮画像色", max_length=40, default="linen", blank=True)
    sort_order = models.PositiveIntegerField("表示順", default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "作品画像"
        verbose_name_plural = "作品画像"


class Publication(models.Model):
    issue_number = models.CharField("号数", max_length=40)
    title = models.CharField("タイトル", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    subtitle = models.CharField("サブタイトル", max_length=300, blank=True)
    description = models.TextField("説明", blank=True)
    page_count = models.PositiveIntegerField("ページ数", null=True, blank=True)
    published_date = models.DateField("刊行日", null=True, blank=True)
    cover_image = models.ImageField("表紙画像", upload_to="publications/%Y/%m/", blank=True, validators=[validate_image_upload])
    tone = models.CharField("仮画像色", max_length=40, default="rice", blank=True)
    featured = models.BooleanField("特集表示", default=False)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("表示順", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "-published_date", "issue_number")
        verbose_name = "出版物"
        verbose_name_plural = "出版物"

    def __str__(self):
        return f"{self.issue_number} {self.title}"

    def get_absolute_url(self):
        return reverse("content:publication_detail", args=[self.slug])


class PolicyPage(models.Model):
    title = models.CharField("タイトル", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    body = models.TextField("本文", blank=True)
    published = models.BooleanField("公開", default=False)
    sort_order = models.PositiveIntegerField("表示順", default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "title")
        verbose_name = "ポリシーページ"
        verbose_name_plural = "ポリシーページ"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("content:policy", args=[self.slug])
