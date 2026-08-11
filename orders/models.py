import secrets
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from core.validators import sanitize_image_field, validate_image_upload


def generate_order_number():
    return f"RF-{timezone.localdate():%Y%m%d}-{secrets.token_hex(3).upper()}"


def generate_access_token():
    return secrets.token_urlsafe(24)


class Order(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "訂單已成立"
        SHIPPING_REVIEW = "shipping_review", "運費確認中"
        AWAITING_PAYMENT = "awaiting_payment", "等待付款"
        PAID = "paid", "已付款"
        PREPARING = "preparing", "出貨準備中"
        SHIPPED = "shipped", "已出貨"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"
        REFUND_PENDING = "refund_pending", "退款處理中"
        REFUNDED = "refunded", "已退款"

    public_id = models.UUIDField("內部安全識別碼", default=uuid.uuid4, unique=True, editable=False)
    public_number = models.CharField("訂單編號", max_length=32, unique=True, default=generate_order_number, editable=False)
    access_token = models.CharField("顧客存取金鑰", max_length=64, unique=True, default=generate_access_token, editable=False)
    idempotency_key = models.CharField("防止重複送出金鑰", max_length=128, unique=True, editable=False)
    line_customer = models.ForeignKey(
        "LineCustomer", verbose_name="LINE 購買者", on_delete=models.PROTECT,
        related_name="orders", null=True, blank=True,
    )
    customer_name = models.CharField("姓名", max_length=160)
    phone = models.CharField("電話", max_length=60)
    email = models.EmailField("Email")
    shipping_information = models.TextField("收件與配送資訊")
    recipient_name = models.CharField("收件人", max_length=160, blank=True)
    postal_code = models.CharField("郵遞區號", max_length=12, blank=True)
    city = models.CharField("縣市", max_length=60, blank=True)
    district = models.CharField("鄉鎮市區", max_length=80, blank=True)
    street_address = models.CharField("街道地址", max_length=300, blank=True)
    delivery_note = models.CharField("配送備註", max_length=300, blank=True)
    customer_note = models.TextField("顧客備註", blank=True)
    subtotal = models.DecimalField("商品小計", max_digits=12, decimal_places=0)
    shipping_fee = models.DecimalField("運費", max_digits=12, decimal_places=0, null=True, blank=True)
    final_total = models.DecimalField("訂單總額", max_digits=12, decimal_places=0, null=True, blank=True, editable=False)
    status = models.CharField("訂單狀態", max_length=32, choices=Status.choices, default=Status.RECEIVED)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)
    paid_at = models.DateTimeField("付款確認時間", null=True, blank=True)
    shipped_at = models.DateTimeField("出貨時間", null=True, blank=True)
    carrier = models.CharField("物流公司", max_length=120, blank=True)
    tracking_number = models.CharField("追蹤號碼", max_length=160, blank=True)
    tracking_url = models.URLField("追蹤網址", blank=True)
    payment_link_version = models.PositiveIntegerField("付款連結版本", default=0, editable=False)
    cancel_link_version = models.PositiveIntegerField("取消連結版本", default=0, editable=False)
    payment_request_total = models.DecimalField("付款通知金額", max_digits=12, decimal_places=0, null=True, blank=True, editable=False)
    admin_note = models.TextField("管理備註", blank=True)
    inventory_reserved = models.BooleanField("庫存已保留", default=False, editable=False)
    inventory_released = models.BooleanField("庫存已還原", default=False, editable=False)
    cancelled_at = models.DateTimeField("取消時間", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "訂單"
        verbose_name_plural = "訂單"
        indexes = [models.Index(fields=("status", "created_at")), models.Index(fields=("email",))]

    def __str__(self):
        return f"{self.public_number} / {self.customer_name}"

    def clean(self):
        if self.shipping_fee is not None and self.shipping_fee < 0:
            raise ValidationError({"shipping_fee": "運費必須為 0 或正數。"})
        if self.status in {self.Status.AWAITING_PAYMENT, self.Status.PAID, self.Status.PREPARING, self.Status.SHIPPED, self.Status.COMPLETED} and self.shipping_fee is None:
            raise ValidationError({"shipping_fee": "請先確定運費，再將訂單更新為此狀態。"})
        if self.pk and self.status in {self.Status.SHIPPED, self.Status.COMPLETED} and not self.is_paid:
            raise ValidationError({"status": "尚未確認付款的訂單不能標記為已出貨。"})

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
    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", verbose_name="商品", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")
    product_name_snapshot = models.CharField("商品名稱快照", max_length=220)
    sku_snapshot = models.CharField("SKU 快照", max_length=80, blank=True)
    unit_price_snapshot = models.DecimalField("單價快照", max_digits=12, decimal_places=0)
    quantity = models.PositiveIntegerField("數量")
    line_total = models.DecimalField("小計", max_digits=12, decimal_places=0)
    stock_was_reserved = models.BooleanField("已保留一般庫存", default=False, editable=False)

    class Meta:
        verbose_name = "訂單明細"
        verbose_name_plural = "訂單明細"

    def __str__(self):
        return f"{self.product_name_snapshot} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total = self.unit_price_snapshot * Decimal(self.quantity)
        super().save(*args, **kwargs)


class LineCustomer(models.Model):
    line_user_id = models.CharField("LINE user ID", max_length=64, unique=True, editable=False)
    display_name = models.CharField("LINE 顯示名稱", max_length=160)
    picture_url = models.URLField("大頭貼網址", blank=True)
    is_friend = models.BooleanField("已加好友", default=False)
    is_blocked = models.BooleanField("已封鎖", default=False)
    last_login_at = models.DateTimeField("最後登入時間", null=True, blank=True)
    friend_checked_at = models.DateTimeField("好友狀態確認時間", null=True, blank=True)
    followed_at = models.DateTimeField("加好友時間", null=True, blank=True)
    unfollowed_at = models.DateTimeField("封鎖時間", null=True, blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        verbose_name = "LINE 顧客"
        verbose_name_plural = "LINE 顧客"

    def __str__(self):
        return self.display_name or "LINE 顧客"

    @property
    def masked_user_id(self):
        if len(self.line_user_id) < 10:
            return "••••"
        return f"{self.line_user_id[:4]}…{self.line_user_id[-4:]}"


class LineNotification(models.Model):
    class Type(models.TextChoices):
        ORDER_RECEIVED = "order_received", "訂單成立"
        PAYMENT_REQUEST = "payment_request", "付款通知"
        PAYMENT_CONFIRMED = "payment_confirmed", "付款確認"
        ORDER_SHIPPED = "order_shipped", "出貨通知"
        ORDER_CANCELLED = "order_cancelled", "取消通知"

    class Status(models.TextChoices):
        PENDING = "pending", "傳送中"
        SENT = "sent", "已傳送"
        FAILED = "failed", "傳送失敗"

    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.CASCADE, related_name="line_notifications")
    line_customer = models.ForeignKey(LineCustomer, verbose_name="LINE 顧客", on_delete=models.PROTECT, related_name="notifications")
    notification_type = models.CharField("通知類型", max_length=32, choices=Type.choices)
    status = models.CharField("傳送狀態", max_length=16, choices=Status.choices, default=Status.PENDING)
    dedupe_key = models.CharField("防止重複傳送金鑰", max_length=160)
    api_retry_key = models.UUIDField("LINE retry key", default=uuid.uuid4, unique=True, editable=False)
    sent_at = models.DateTimeField("傳送時間", null=True, blank=True)
    failed_at = models.DateTimeField("失敗時間", null=True, blank=True)
    retry_count = models.PositiveIntegerField("重試次數", default=0)
    http_status = models.PositiveSmallIntegerField("HTTP status", null=True, blank=True)
    error_message = models.CharField("錯誤訊息", max_length=255, blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

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


class NotificationOutbox(models.Model):
    class Channel(models.TextChoices):
        LINE = "line", "LINE"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        PENDING = "pending", "等待傳送"
        PROCESSING = "processing", "傳送中"
        RETRY = "retry", "等待重試"
        SENT = "sent", "已傳送"
        DEAD = "dead", "人工處理"

    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.CASCADE, related_name="notification_outbox")
    channel = models.CharField("管道", max_length=16, choices=Channel.choices)
    event_type = models.CharField("事件", max_length=40)
    dedupe_key = models.CharField("冪等金鑰", max_length=180)
    payload = models.JSONField("工作資料", default=dict, blank=True)
    status = models.CharField("狀態", max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField("嘗試次數", default=0)
    max_attempts = models.PositiveSmallIntegerField("最大嘗試次數", default=8)
    next_attempt_at = models.DateTimeField("下次嘗試", default=timezone.now)
    locked_at = models.DateTimeField("鎖定時間", null=True, blank=True)
    last_error = models.TextField("最後錯誤", blank=True)
    response_metadata = models.JSONField("回應資料", default=dict, blank=True)
    sent_at = models.DateTimeField("傳送時間", null=True, blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ("next_attempt_at", "pk")
        verbose_name = "通知佇列"
        verbose_name_plural = "通知佇列"
        constraints = [models.UniqueConstraint(fields=("channel", "event_type", "dedupe_key"), name="unique_outbox_delivery")]
        indexes = [models.Index(fields=("status", "next_attempt_at"))]


class LineWebhookEvent(models.Model):
    webhook_event_id = models.CharField("Webhook event ID", max_length=128, unique=True)
    event_type = models.CharField("事件類型", max_length=40)
    processed_at = models.DateTimeField("處理時間", auto_now_add=True)

    class Meta:
        ordering = ("-processed_at",)
        verbose_name = "LINE Webhook 事件"
        verbose_name_plural = "LINE Webhook 事件"


class PaymentMethod(models.Model):
    class Method(models.TextChoices):
        TAIWAN_PAY = "taiwan_pay", "台灣 Pay"
        CREDIT_CARD = "credit_card", "信用卡"
        BANK_TRANSFER = "bank_transfer", "銀行轉帳"
        PAYPAL = "paypal", "PayPal"

    code = models.CharField("付款方式", max_length=32, choices=Method.choices, unique=True)
    enabled = models.BooleanField("啟用", default=False)
    display_name = models.CharField("顯示名稱", max_length=120)
    instructions = models.TextField("付款說明", blank=True)
    provider = models.CharField("金流服務商", max_length=120, blank=True)
    qr_image = models.ImageField("QR 圖片", upload_to="payments/methods/", blank=True, validators=[validate_image_upload])
    sort_order = models.PositiveIntegerField("顯示順序", default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = "付款方式設定"
        verbose_name_plural = "付款方式設定"

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        sanitize_image_field(self, "qr_image")
        super().save(*args, **kwargs)


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待處理"
        AWAITING_CONFIRMATION = "awaiting_confirmation", "等待確認"
        CONFIRMED = "confirmed", "已確認"
        FAILED = "failed", "失敗"
        CANCELLED = "cancelled", "已取消"
        PARTIALLY_REFUNDED = "partially_refunded", "部分退款"
        REFUNDED = "refunded", "全額退款"
        OVERPAID = "overpaid", "過入金"
        CHARGEBACK = "chargeback", "爭議款／拒付"

    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, verbose_name="付款方式", on_delete=models.PROTECT, null=True, blank=True, related_name="payments")
    provider = models.CharField("金流服務商", max_length=120, blank=True)
    amount = models.DecimalField("付款金額", max_digits=12, decimal_places=0, null=True, blank=True)
    status = models.CharField("付款狀態", max_length=32, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField("金流參考編號", max_length=255, blank=True)
    provider_event_id = models.CharField("金流事件 ID", max_length=255, null=True, blank=True, unique=True)
    idempotency_key = models.CharField("付款冪等金鑰", max_length=160, null=True, blank=True, unique=True)
    currency = models.CharField("幣別", max_length=3, default="TWD")
    paid_at = models.DateTimeField("付款時間", null=True, blank=True)
    confirmed_at = models.DateTimeField("確認時間", null=True, blank=True)
    cancelled_at = models.DateTimeField("取消時間", null=True, blank=True)
    refunded_amount = models.DecimalField("退款金額", max_digits=12, decimal_places=0, default=0)
    refund_status = models.CharField("退款狀態", max_length=32, blank=True)
    refund_reason = models.TextField("退款原因", blank=True)
    refunded_at = models.DateTimeField("退款時間", null=True, blank=True)
    refund_operator = models.ForeignKey("auth.User", verbose_name="退款操作人", on_delete=models.SET_NULL, null=True, blank=True, related_name="operated_refunds")
    provider_metadata = models.JSONField("金流回應資料", default=dict, blank=True)
    note = models.TextField("備註", blank=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "付款記錄"
        verbose_name_plural = "付款記錄"
        constraints = [
            models.UniqueConstraint(fields=("order",), condition=Q(status="confirmed"), name="one_confirmed_payment_per_order"),
            models.CheckConstraint(condition=Q(refunded_amount__gte=0), name="payment_refund_amount_nonnegative"),
        ]

    def __str__(self):
        return f"{self.order.public_number} / {self.get_status_display()}"

    def clean(self):
        if self.status == self.Status.CONFIRMED:
            if self.amount is None:
                raise ValidationError({"amount": "確認付款時必須填寫金額。"})
            if self.order.final_total is None:
                raise ValidationError("請先確定運費與訂單總額，再確認付款。")
            if self.amount != self.order.final_total:
                raise ValidationError({"amount": "付款金額與訂單總額不一致。"})
            if self.currency != "TWD":
                raise ValidationError({"currency": "訂單幣別必須為 TWD。"})

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if self.status == self.Status.CONFIRMED and previous_status != self.Status.CONFIRMED:
            self.clean()
        if self.status == self.Status.CONFIRMED and not self.paid_at:
            self.paid_at = timezone.now()
        if self.status == self.Status.CONFIRMED and not self.confirmed_at:
            self.confirmed_at = timezone.now()
        super().save(*args, **kwargs)
        if self.status == self.Status.CONFIRMED:
            previous_order_status = self.order.status
            updates = {"paid_at": self.paid_at}
            if self.order.status in {Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.AWAITING_PAYMENT}:
                updates["status"] = Order.Status.PAID
            Order.objects.filter(pk=self.order_id).update(**updates)
            if previous_status != self.Status.CONFIRMED:
                OrderAuditLog.objects.create(
                    order_id=self.order_id,
                    event="payment_record_confirmed",
                    actor_label="payment-model",
                    from_status=previous_order_status,
                    to_status=updates.get("status", previous_order_status),
                    changes={"payment_id": self.pk, "amount": str(self.amount), "currency": self.currency},
                )
                from .notifications import enqueue_order_notifications

                transaction.on_commit(lambda order_id=self.order_id: enqueue_order_notifications(order_id, "payment_confirmed"))


class PolicyAcceptance(models.Model):
    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.PROTECT, related_name="policy_acceptances")
    line_customer = models.ForeignKey(LineCustomer, verbose_name="顧客", on_delete=models.PROTECT, null=True, blank=True, related_name="policy_acceptances")
    document_type = models.CharField("文件類型", max_length=80)
    version = models.CharField("版本", max_length=40)
    accepted_at = models.DateTimeField("同意時間", auto_now_add=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.CharField("瀏覽器", max_length=300, blank=True)

    class Meta:
        verbose_name = "政策同意記錄"
        verbose_name_plural = "政策同意記錄"
        constraints = [models.UniqueConstraint(fields=("order", "document_type", "version"), name="unique_order_policy_acceptance")]


class OrderAuditLog(models.Model):
    order = models.ForeignKey(Order, verbose_name="訂單", on_delete=models.PROTECT, related_name="audit_logs")
    event = models.CharField("操作", max_length=40)
    actor = models.ForeignKey("auth.User", verbose_name="後台操作人", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_audit_logs")
    actor_label = models.CharField("操作來源", max_length=160, blank=True)
    from_status = models.CharField("變更前狀態", max_length=32, blank=True)
    to_status = models.CharField("變更後狀態", max_length=32, blank=True)
    changes = models.JSONField("變更內容", default=dict, blank=True)
    metadata = models.JSONField("補充資料", default=dict, blank=True)
    created_at = models.DateTimeField("操作時間", auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "訂單監査記錄"
        verbose_name_plural = "訂單監査記錄"
        indexes = [models.Index(fields=("order", "created_at")), models.Index(fields=("event", "created_at"))]
