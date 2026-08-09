import secrets
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from core.validators import validate_image_upload


def generate_order_number():
    return f"RF-{timezone.localdate():%Y%m%d}-{secrets.token_hex(3).upper()}"


def generate_access_token():
    return secrets.token_urlsafe(24)


class Order(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "注文受付"
        SHIPPING_REVIEW = "shipping_review", "送料確認中"
        AWAITING_PAYMENT = "awaiting_payment", "支払い待ち"
        PAID = "paid", "入金済み"
        PREPARING = "preparing", "発送準備中"
        SHIPPED = "shipped", "発送済み"
        COMPLETED = "completed", "完了"
        CANCELLED = "cancelled", "キャンセル"

    public_number = models.CharField("注文番号", max_length=32, unique=True, default=generate_order_number, editable=False)
    access_token = models.CharField("顧客用アクセスキー", max_length=64, unique=True, default=generate_access_token, editable=False)
    idempotency_key = models.CharField("二重送信防止キー", max_length=128, unique=True, editable=False)
    line_customer = models.ForeignKey(
        "LineCustomer", verbose_name="LINE購入者", on_delete=models.PROTECT,
        related_name="orders", null=True, blank=True,
    )
    customer_name = models.CharField("姓名", max_length=160)
    phone = models.CharField("電話", max_length=60)
    email = models.EmailField("Email")
    shipping_information = models.TextField("配送先情報")
    customer_note = models.TextField("備考", blank=True)
    subtotal = models.DecimalField("商品合計", max_digits=12, decimal_places=0)
    shipping_fee = models.DecimalField("送料", max_digits=12, decimal_places=0, null=True, blank=True)
    final_total = models.DecimalField("最終金額", max_digits=12, decimal_places=0, null=True, blank=True, editable=False)
    status = models.CharField("ステータス", max_length=32, choices=Status.choices, default=Status.RECEIVED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField("入金確認日時", null=True, blank=True)
    shipped_at = models.DateTimeField("発送日時", null=True, blank=True)
    carrier = models.CharField("配送会社", max_length=120, blank=True)
    tracking_number = models.CharField("追跡番号", max_length=160, blank=True)
    tracking_url = models.URLField("追跡URL", blank=True)
    payment_link_version = models.PositiveIntegerField("支払いリンク版", default=0, editable=False)
    payment_request_total = models.DecimalField("支払い案内時金額", max_digits=12, decimal_places=0, null=True, blank=True, editable=False)
    admin_note = models.TextField("管理メモ", blank=True)
    inventory_reserved = models.BooleanField("在庫確保済み", default=False, editable=False)
    inventory_released = models.BooleanField("在庫復元済み", default=False, editable=False)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "注文"
        verbose_name_plural = "注文"
        indexes = [models.Index(fields=("status", "created_at")), models.Index(fields=("email",))]

    def __str__(self):
        return f"{self.public_number} / {self.customer_name}"

    def clean(self):
        if self.shipping_fee is not None and self.shipping_fee < 0:
            raise ValidationError({"shipping_fee": "送料は0以上で入力してください。"})
        if self.status in {self.Status.AWAITING_PAYMENT, self.Status.PAID, self.Status.PREPARING, self.Status.SHIPPED, self.Status.COMPLETED} and self.shipping_fee is None:
            raise ValidationError({"shipping_fee": "このステータスへ進める前に送料を確定してください。"})
        if self.pk and self.status in {self.Status.SHIPPED, self.Status.COMPLETED} and not self.is_paid:
            raise ValidationError({"status": "入金確認前の注文は発送済みにできません。"})

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        self.final_total = self.subtotal + self.shipping_fee if self.shipping_fee is not None else None
        super().save(*args, **kwargs)
        if self.status == self.Status.CANCELLED and previous_status != self.Status.CANCELLED:
            self.restore_inventory()

    def restore_inventory(self):
        if not self.inventory_reserved or self.inventory_released:
            return False
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.inventory_released or not locked.inventory_reserved:
                self.inventory_released = locked.inventory_released
                return False
            for item in locked.items.select_related("product"):
                if item.product_id and item.stock_was_reserved:
                    from catalog.models import Product

                    Product.objects.select_for_update().filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
            type(self).objects.filter(pk=self.pk).update(inventory_released=True)
            self.inventory_released = True
        return True

    @property
    def is_paid(self):
        return self.payments.filter(status=Payment.Status.CONFIRMED).exists()


class OrderItem(models.Model):
    order = models.ForeignKey(Order, verbose_name="注文", on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", verbose_name="商品", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")
    product_name_snapshot = models.CharField("商品名スナップショット", max_length=220)
    sku_snapshot = models.CharField("SKUスナップショット", max_length=80, blank=True)
    unit_price_snapshot = models.DecimalField("単価スナップショット", max_digits=12, decimal_places=0)
    quantity = models.PositiveIntegerField("数量")
    line_total = models.DecimalField("小計", max_digits=12, decimal_places=0)
    stock_was_reserved = models.BooleanField("通常在庫を確保", default=False, editable=False)

    class Meta:
        verbose_name = "注文明細"
        verbose_name_plural = "注文明細"

    def __str__(self):
        return f"{self.product_name_snapshot} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total = self.unit_price_snapshot * Decimal(self.quantity)
        super().save(*args, **kwargs)


class LineCustomer(models.Model):
    line_user_id = models.CharField("LINE user ID", max_length=64, unique=True, editable=False)
    display_name = models.CharField("LINE表示名", max_length=160)
    picture_url = models.URLField("プロフィール画像URL", blank=True)
    is_friend = models.BooleanField("友だち", default=False)
    is_blocked = models.BooleanField("ブロック", default=False)
    last_login_at = models.DateTimeField("最終ログイン", null=True, blank=True)
    friend_checked_at = models.DateTimeField("友だち確認日時", null=True, blank=True)
    followed_at = models.DateTimeField("友だち追加日時", null=True, blank=True)
    unfollowed_at = models.DateTimeField("ブロック日時", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "LINE顧客"
        verbose_name_plural = "LINE顧客"

    def __str__(self):
        return self.display_name or "LINE顧客"

    @property
    def masked_user_id(self):
        if len(self.line_user_id) < 10:
            return "••••"
        return f"{self.line_user_id[:4]}…{self.line_user_id[-4:]}"


class LineNotification(models.Model):
    class Type(models.TextChoices):
        ORDER_RECEIVED = "order_received", "注文受付"
        PAYMENT_REQUEST = "payment_request", "支払い案内"
        PAYMENT_CONFIRMED = "payment_confirmed", "入金確認"
        ORDER_SHIPPED = "order_shipped", "発送通知"

    class Status(models.TextChoices):
        PENDING = "pending", "送信中"
        SENT = "sent", "送信済み"
        FAILED = "failed", "失敗"

    order = models.ForeignKey(Order, verbose_name="注文", on_delete=models.CASCADE, related_name="line_notifications")
    line_customer = models.ForeignKey(LineCustomer, verbose_name="LINE顧客", on_delete=models.PROTECT, related_name="notifications")
    notification_type = models.CharField("通知種別", max_length=32, choices=Type.choices)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.PENDING)
    dedupe_key = models.CharField("重複防止キー", max_length=160)
    api_retry_key = models.UUIDField("LINE retry key", default=uuid.uuid4, unique=True, editable=False)
    sent_at = models.DateTimeField("送信日時", null=True, blank=True)
    failed_at = models.DateTimeField("失敗日時", null=True, blank=True)
    retry_count = models.PositiveIntegerField("再試行回数", default=0)
    http_status = models.PositiveSmallIntegerField("HTTP status", null=True, blank=True)
    error_message = models.CharField("エラー", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "LINE通知"
        verbose_name_plural = "LINE通知"
        constraints = [
            models.UniqueConstraint(
                fields=("order", "notification_type", "dedupe_key"),
                name="unique_line_notification_delivery",
            )
        ]

    def __str__(self):
        return f"{self.order.public_number} / {self.get_notification_type_display()}"


class LineWebhookEvent(models.Model):
    webhook_event_id = models.CharField("Webhook event ID", max_length=128, unique=True)
    event_type = models.CharField("イベント種別", max_length=40)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-processed_at",)
        verbose_name = "LINE Webhookイベント"
        verbose_name_plural = "LINE Webhookイベント"


class PaymentMethod(models.Model):
    class Method(models.TextChoices):
        TAIWAN_PAY = "taiwan_pay", "台灣 Pay"
        CREDIT_CARD = "credit_card", "クレジットカード"
        BANK_TRANSFER = "bank_transfer", "銀行振込"
        PAYPAL = "paypal", "PayPal"

    code = models.CharField("方式", max_length=32, choices=Method.choices, unique=True)
    enabled = models.BooleanField("利用可能", default=False)
    display_name = models.CharField("表示名", max_length=120)
    instructions = models.TextField("案内", blank=True)
    provider = models.CharField("Provider", max_length=120, blank=True)
    qr_image = models.ImageField("QR画像", upload_to="payments/methods/", blank=True, validators=[validate_image_upload])
    sort_order = models.PositiveIntegerField("表示順", default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "支払方法設定"
        verbose_name_plural = "支払方法設定"

    def __str__(self):
        return self.display_name


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "保留"
        AWAITING_CONFIRMATION = "awaiting_confirmation", "確認待ち"
        CONFIRMED = "confirmed", "確認済み"
        FAILED = "failed", "失敗"
        CANCELLED = "cancelled", "キャンセル"

    order = models.ForeignKey(Order, verbose_name="注文", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, verbose_name="支払方法", on_delete=models.PROTECT, null=True, blank=True, related_name="payments")
    provider = models.CharField("Provider", max_length=120, blank=True)
    amount = models.DecimalField("金額", max_digits=12, decimal_places=0, null=True, blank=True)
    status = models.CharField("支払ステータス", max_length=32, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField("Provider参照番号", max_length=255, blank=True)
    provider_event_id = models.CharField("Provider event ID", max_length=255, null=True, blank=True, unique=True)
    currency = models.CharField("通貨", max_length=3, default="TWD")
    paid_at = models.DateTimeField("入金日時", null=True, blank=True)
    note = models.TextField("メモ", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "支払い"
        verbose_name_plural = "支払い"

    def __str__(self):
        return f"{self.order.public_number} / {self.get_status_display()}"

    def clean(self):
        if self.status == self.Status.CONFIRMED:
            if self.amount is None:
                raise ValidationError({"amount": "入金確認には金額が必要です。"})
            if self.order.final_total is None:
                raise ValidationError("送料と最終金額を確定してから入金確認してください。")
            if self.amount != self.order.final_total:
                raise ValidationError({"amount": "入金額が注文の最終金額と一致しません。"})
            if self.currency != "TWD":
                raise ValidationError({"currency": "注文通貨はTWDです。"})

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if self.status == self.Status.CONFIRMED and previous_status != self.Status.CONFIRMED:
            self.clean()
        if self.status == self.Status.CONFIRMED and not self.paid_at:
            self.paid_at = timezone.now()
        super().save(*args, **kwargs)
        if self.status == self.Status.CONFIRMED:
            updates = {"paid_at": self.paid_at}
            if self.order.status in {Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.AWAITING_PAYMENT}:
                updates["status"] = Order.Status.PAID
            Order.objects.filter(pk=self.order_id).update(**updates)
            if previous_status != self.Status.CONFIRMED:
                from .line_messaging import schedule_payment_confirmed

                transaction.on_commit(lambda order_id=self.order_id: schedule_payment_confirmed(order_id))
