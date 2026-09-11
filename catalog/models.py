from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

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
        product_images = ProductImage.objects.filter(
            product_id=OuterRef("pk"),
            upload_status=ProductImage.UploadStatus.ATTACHED,
        ).exclude(image="")
        return self.filter(
            is_published=True,
            category__is_active=True,
            price__isnull=False,
        ).exclude(
            name="",
        ).exclude(
            sku="",
        ).exclude(
            sku__istartswith="DRAFT-",
        ).exclude(
            description="",
        ).filter(
            Exists(product_images),
            Q(sale_starts_at__isnull=True) | Q(sale_starts_at__lte=now),
            Q(sale_ends_at__isnull=True) | Q(sale_ends_at__gt=now),
        )


class Product(models.Model):
    category = models.ForeignKey(ProductCategory, verbose_name="商品分類", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    name = models.CharField("商品名稱", max_length=220, blank=True)
    slug = models.SlugField("slug", max_length=240, unique=True, blank=True)
    sku = models.CharField("SKU", max_length=80, unique=True, blank=True)
    maker = models.CharField("製作者／品牌", max_length=160, blank=True)
    series = models.CharField("系列", max_length=160, blank=True)
    subcategory = models.CharField("篩選項目", max_length=120, blank=True)
    short_description = models.CharField("簡短說明", max_length=300, blank=True)
    description = models.TextField("商品說明", blank=True)
    price = models.DecimalField("售價", max_digits=12, decimal_places=0, null=True, blank=True)
    stock = models.PositiveIntegerField("庫存", default=0, blank=True)
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
        return (self.name or "").strip() or f"未完成商品 #{self.pk or '新增'}"

    @staticmethod
    def new_draft_slug():
        return f"draft-{uuid4().hex}"

    @staticmethod
    def new_draft_sku():
        return f"DRAFT-{uuid4().hex[:12].upper()}"

    def _unique_name_slug(self):
        base = slugify(self.name)[:220] or f"product-{self.pk or uuid4().hex[:12]}"
        candidate = base
        suffix = 2
        queryset = type(self).objects.exclude(pk=self.pk)
        while queryset.filter(slug=candidate).exists():
            candidate = f"{base[:230 - len(str(suffix))]}-{suffix}"
            suffix += 1
        return candidate

    def ensure_identifiers(self):
        changed = set()
        has_name = bool((self.name or "").strip())
        if not self.slug:
            self.slug = self._unique_name_slug() if has_name else self.new_draft_slug()
            changed.add("slug")
        elif self.slug.startswith("draft-") and has_name:
            was_already_published = bool(
                self.pk
                and type(self).objects.filter(pk=self.pk, is_published=True).exists()
            )
            if not was_already_published:
                self.slug = self._unique_name_slug()
                changed.add("slug")
        if not self.sku:
            self.sku = self.new_draft_sku()
            changed.add("sku")
        return changed

    def save(self, *args, **kwargs):
        changed_identifiers = self.ensure_identifiers()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and changed_identifiers:
            kwargs["update_fields"] = set(update_fields) | changed_identifiers
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:product", args=[self.slug])

    @property
    def ordered_images(self):
        """Return displayable images with a legacy primary image kept first.

        New management operations keep ``sort_order`` and ``is_primary`` in sync.
        Moving a legacy primary to the front here preserves its established main
        image until an administrator explicitly reorders that product.
        """
        images = [
            image
            for image in self.images.all()
            if image.upload_status == ProductImage.UploadStatus.ATTACHED and image.image
        ]
        primary_index = next((index for index, image in enumerate(images) if image.is_primary), None)
        if primary_index not in (None, 0):
            images.insert(0, images.pop(primary_index))
        return images

    @property
    def primary_image(self):
        images = self.ordered_images
        return images[0] if images else None

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
        if self.price is not None and self.price < 0:
            errors["price"] = "售價不可小於 0。"
        if self.is_published:
            if not (self.name or "").strip():
                errors["name"] = "商品尚未完成，請先填寫商品名稱。"
            if not (self.sku or "").strip() or self.sku.upper().startswith("DRAFT-"):
                errors["sku"] = "公開商品前請填寫正式 SKU。"
            if not (self.description or "").strip():
                errors["description"] = "商品尚未完成，公開前請先填寫商品說明。"
            if self.price is None:
                errors["price"] = "公開商品前請設定價格。"
            if self.category_id is None:
                errors["category"] = "公開商品前請選擇商品分類。"
            has_image = getattr(self, "_admin_has_product_image", None)
            if has_image is None:
                has_image = bool(
                    self.pk
                    and self.images.filter(
                        upload_status=ProductImage.UploadStatus.ATTACHED
                    ).exclude(image="").exists()
                )
            if not has_image:
                errors["__all__"] = "公開商品前請至少上傳一張商品照片。"
            if self.is_preorder:
                if not self.preorder_limit:
                    errors["preorder_limit"] = "公開預購商品前請設定大於 0 的預購上限。"
                if not (self.preorder_delivery_estimate or "").strip():
                    errors["preorder_delivery_estimate"] = "公開預購商品前請填寫預計交付時間。"
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
    class UploadStatus(models.TextChoices):
        PENDING = "pending", "上傳待確認"
        TEMPORARY = "temporary", "暫存"
        ATTACHED = "attached", "已連結商品"
        DELETION_PENDING = "deletion_pending", "等待刪除"

    product = models.ForeignKey(
        Product,
        verbose_name="商品",
        on_delete=models.CASCADE,
        related_name="images",
        null=True,
        blank=True,
    )
    image = models.ImageField("圖片", upload_to="products/%Y/%m/", blank=True, validators=[validate_image_upload])
    thumbnail = models.ImageField("縮圖", upload_to="products/thumbnails/%Y/%m/", blank=True, editable=False)
    alt_text = models.CharField("替代文字", max_length=255, blank=True)
    sort_order = models.PositiveIntegerField("顯示順序", default=0)
    is_primary = models.BooleanField("主要圖片", default=False)
    upload_status = models.CharField(
        "上傳狀態",
        max_length=24,
        choices=UploadStatus.choices,
        default=UploadStatus.ATTACHED,
    )
    upload_session = models.UUIDField("上傳工作階段", null=True, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="上傳者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_product_images",
    )
    original_filename = models.CharField("原始檔名", max_length=255, blank=True)
    content_type = models.CharField("Content-Type", max_length=64, blank=True)
    file_size = models.PositiveBigIntegerField("檔案大小", null=True, blank=True)
    width = models.PositiveIntegerField("寬度", null=True, blank=True)
    height = models.PositiveIntegerField("高度", null=True, blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "商品圖片"
        verbose_name_plural = "商品圖片"
        constraints = [
            models.UniqueConstraint(
                fields=("product",),
                condition=Q(is_primary=True, upload_status="attached"),
                name="one_primary_image_per_product",
            )
        ]
        indexes = [
            models.Index(
                fields=("product", "upload_status", "sort_order"),
                name="catalog_pi_product_5f29_idx",
            )
        ]

    def __str__(self):
        return self.alt_text or (f"{self.product} 圖片" if self.product_id else "暫存商品圖片")

    def save(self, *args, **kwargs):
        if not (self.alt_text or "").strip():
            self.alt_text = (
                (self.product.name if self.product_id else "") or "商品照片"
            ).strip()
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
