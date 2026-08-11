from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.validators import make_thumbnail_content, sanitize_image_field, validate_image_upload


class ProductCategory(models.Model):
    name = models.CharField("商品分類名稱", max_length=120)
    slug = models.SlugField("slug", max_length=140, unique=True)
    english_name = models.CharField("英文名稱", max_length=160, blank=True)
    description = models.TextField("分類說明", blank=True)
    subcategories = models.JSONField("篩選項目", default=list, blank=True)
    tone = models.CharField("預留圖片色調", max_length=40, default="linen", blank=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    is_active = models.BooleanField("啟用", default=True)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "商品分類"
        verbose_name_plural = "商品分類"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:category", args=[self.slug])


class ProductQuerySet(models.QuerySet):
    def published(self):
        now = timezone.now()
        return self.filter(is_published=True, category__is_active=True).filter(
            Q(sale_starts_at__isnull=True) | Q(sale_starts_at__lte=now),
            Q(sale_ends_at__isnull=True) | Q(sale_ends_at__gt=now),
        )


class Product(models.Model):
    category = models.ForeignKey(ProductCategory, verbose_name="商品分類", on_delete=models.PROTECT, related_name="products")
    name = models.CharField("商品名稱", max_length=220)
    slug = models.SlugField("slug", max_length=240, unique=True)
    sku = models.CharField("SKU", max_length=80, unique=True)
    maker = models.CharField("製作者／品牌", max_length=160, blank=True)
    series = models.CharField("系列", max_length=160, blank=True)
    subcategory = models.CharField("篩選項目", max_length=120, blank=True)
    short_description = models.CharField("簡短說明", max_length=300, blank=True)
    description = models.TextField("商品說明")
    price = models.DecimalField("售價", max_digits=12, decimal_places=0)
    stock = models.PositiveIntegerField("庫存", default=0)
    is_preorder = models.BooleanField("預購商品", default=False)
    preorder_note = models.TextField("預購說明", blank=True)
    preorder_limit = models.PositiveIntegerField("預購上限", null=True, blank=True)
    preorder_delivery_estimate = models.CharField("預計交付時間", max_length=180, blank=True)
    care = models.TextField("使用與保養", blank=True)
    shipping_note = models.TextField("配送與退換貨說明", blank=True)
    maker_story = models.TextField("製作者介紹", blank=True)
    badge_label = models.CharField("卡片標籤", max_length=80, blank=True)
    image_label = models.CharField("預留圖片文字", max_length=160, blank=True)
    tone = models.CharField("預留圖片色調", max_length=40, default="linen", blank=True)
    is_published = models.BooleanField("公開", default=False)
    sale_starts_at = models.DateTimeField("開始販售", null=True, blank=True)
    sale_ends_at = models.DateTimeField("結束販售", null=True, blank=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "商品"
        verbose_name_plural = "商品"
        indexes = [models.Index(fields=("is_published", "sort_order")), models.Index(fields=("sku",)), models.Index(fields=("sale_starts_at", "sale_ends_at"))]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_nonnegative"),
            models.CheckConstraint(condition=Q(stock__gte=0), name="product_stock_nonnegative"),
            models.CheckConstraint(condition=Q(sale_ends_at__isnull=True) | Q(sale_starts_at__isnull=True) | Q(sale_ends_at__gt=models.F("sale_starts_at")), name="product_sale_window_valid"),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:product", args=[self.slug])

    @property
    def primary_image(self):
        images = list(self.images.all())
        return next((image for image in images if image.is_primary), images[0] if images else None)

    @property
    def available_for_order(self):
        now = timezone.now()
        in_window = (not self.sale_starts_at or self.sale_starts_at <= now) and (not self.sale_ends_at or self.sale_ends_at > now)
        preorder_available = self.is_preorder and bool(self.preorder_limit)
        return self.is_published and in_window and (preorder_available or self.stock > 0)

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.sale_starts_at and self.sale_ends_at and self.sale_ends_at <= self.sale_starts_at:
            errors["sale_ends_at"] = "結束販售時間必須晚於開始時間。"
        if self.is_preorder:
            if not self.preorder_limit:
                errors["preorder_limit"] = "預購商品必須設定大於 0 的預購上限。"
            if not self.preorder_delivery_estimate.strip():
                errors["preorder_delivery_estimate"] = "預購商品必須填寫預計交付時間。"
        if self.is_published:
            if not self.name.strip(): errors["name"] = "公開商品必須填寫名稱。"
            if not self.sku.strip(): errors["sku"] = "公開商品必須填寫 SKU。"
            if not self.description.strip(): errors["description"] = "公開商品必須填寫說明。"
        if errors:
            raise ValidationError(errors)

    @property
    def stock_label(self):
        if self.is_preorder:
            return self.preorder_note or "預購"
        if self.stock == 0:
            return "售罄"
        if self.stock <= 2:
            return f"僅剩 {self.stock} 件"
        return "現貨"

    def line_total(self, quantity):
        return self.price * Decimal(quantity)


class ProductImage(models.Model):
    product = models.ForeignKey(Product, verbose_name="商品", on_delete=models.CASCADE, related_name="images")
    image = models.ImageField("圖片", upload_to="products/%Y/%m/", blank=True, validators=[validate_image_upload])
    thumbnail = models.ImageField("縮圖", upload_to="products/thumbnails/%Y/%m/", blank=True, editable=False)
    alt_text = models.CharField("替代文字", max_length=255)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    is_primary = models.BooleanField("主要圖片", default=False)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "商品圖片"
        verbose_name_plural = "商品圖片"
        constraints = [models.UniqueConstraint(fields=("product",), condition=Q(is_primary=True), name="one_primary_image_per_product")]

    def __str__(self):
        return self.alt_text or f"{self.product} 圖片"

    def save(self, *args, **kwargs):
        sanitize_image_field(self, "image")
        if self.image and not getattr(self.image, "_committed", True):
            self.thumbnail = make_thumbnail_content(self.image.file)
        super().save(*args, **kwargs)


class ProductSpecification(models.Model):
    product = models.ForeignKey(Product, verbose_name="商品", on_delete=models.CASCADE, related_name="specifications")
    label = models.CharField("規格名稱", max_length=100)
    value = models.CharField("規格內容", max_length=500)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "商品規格"
        verbose_name_plural = "商品規格"

    def __str__(self):
        return f"{self.label}: {self.value}"
