from decimal import Decimal, InvalidOperation

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .models import LineCustomer, LineNotification, LineWebhookEvent, NotificationOutbox, Order, OrderAuditLog, OrderItem, Payment, PaymentMethod, PolicyAcceptance
from .notifications import enqueue_order_notifications
from .operations import cancel_order, complete_order, confirm_manual_payment, confirm_shipping_and_request_payment, mark_preparing, mark_shipped, record_refund


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product", "product_name_snapshot", "sku_snapshot", "unit_price_snapshot", "quantity", "line_total", "stock_was_reserved")
    readonly_fields = fields


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("method", "provider", "amount", "currency", "status", "provider_reference", "paid_at", "note")
    readonly_fields = ("status", "paid_at")


class LineNotificationInline(admin.TabularInline):
    model = LineNotification
    extra = 0
    can_delete = False
    fields = ("notification_type", "status", "sent_at", "failed_at", "retry_count", "http_status", "error_message")
    readonly_fields = fields


class OrderAuditInline(admin.TabularInline):
    model = OrderAuditLog
    extra = 0
    can_delete = False
    fields = ("created_at", "event", "actor", "actor_label", "from_status", "to_status", "changes")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order, site=backoffice_site)
class OrderAdmin(admin.ModelAdmin):
    change_form_template = "admin/orders/order/change_form.html"
    list_display = ("public_number", "customer_name", "created_at", "status_badge", "contact_state", "subtotal", "shipping_fee", "final_total", "payment_state", "shipment_state", "last_line_notification")
    list_filter = ("status", "created_at", "paid_at", "shipped_at")
    list_select_related = ("line_customer",)
    search_fields = ("public_number", "customer_name", "phone", "email", "tracking_number")
    date_hierarchy = "created_at"
    readonly_fields = ("public_number", "status", "line_customer", "line_display_name", "line_friendship", "notification_summary", "idempotency_key", "subtotal", "final_total", "payment_link_version", "payment_request_total", "created_at", "updated_at", "inventory_reserved", "inventory_released")
    inlines = (OrderItemInline, PaymentInline, LineNotificationInline, OrderAuditInline)
    fieldsets = (
        ("訂單資訊", {"fields": ("public_number", "status", "created_at", "updated_at")}),
        ("LINE", {"fields": ("line_customer", "line_display_name", "line_friendship", "notification_summary")}),
        ("顧客與配送", {"fields": ("customer_name", "phone", "email", "recipient_name", "postal_code", "city", "district", "street_address", "delivery_note", "shipping_information", "customer_note")}),
        ("金額", {"fields": ("subtotal", "shipping_fee", "final_total")}),
        ("付款與出貨", {"fields": ("paid_at", "shipped_at", "carrier", "tracking_number", "tracking_url")}),
        ("管理資訊", {"fields": ("admin_note", "idempotency_key", "payment_link_version", "payment_request_total", "inventory_reserved", "inventory_released")}),
    )
    list_per_page = 25

    def get_queryset(self, request):
        confirmed = Payment.objects.filter(order_id=OuterRef("pk"), status=Payment.Status.CONFIRMED)
        last_sent = LineNotification.objects.filter(
            order_id=OuterRef("pk"), sent_at__isnull=False
        ).order_by("-sent_at").values("sent_at")[:1]
        return super().get_queryset(request).prefetch_related("notification_outbox").annotate(
            has_confirmed_payment=Exists(confirmed),
            last_notification_at=Subquery(last_sent),
        )

    @admin.display(description="訂單狀態", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="status-pill status-{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description="已付款", boolean=True)
    def payment_state(self, obj):
        annotated = getattr(obj, "has_confirmed_payment", None)
        return annotated if annotated is not None else obj.is_paid

    @admin.display(description="出貨狀態")
    def shipment_state(self, obj):
        return "已出貨" if obj.shipped_at else "尚未出貨"

    @admin.display(description="LINE 連結")
    def line_state(self, obj):
        return obj.line_customer.display_name if obj.line_customer_id else "尚未連結"

    @admin.display(description="LINE 好友", boolean=True)
    def friend_state(self, obj):
        return bool(obj.line_customer_id and obj.line_customer.is_friend and not obj.line_customer.is_blocked)

    @admin.display(description="LINE 顯示名稱")
    def line_display_name(self, obj):
        return obj.line_customer.display_name if obj.line_customer_id else "—"

    @admin.display(description="LINE 通知狀態")
    def line_friendship(self, obj):
        if not obj.line_customer_id:
            return "尚未連結"
        return "可傳送通知" if obj.line_customer.is_friend and not obj.line_customer.is_blocked else "無法傳送 LINE 通知"

    @admin.display(description="通知摘要")
    def notification_summary(self, obj):
        states = {item.notification_type: item.get_status_display() for item in obj.line_notifications.all()}
        labels = dict(LineNotification.Type.choices)
        return " / ".join(f"{labels[key]}: {states.get(key, '尚未傳送')}" for key in labels)

    @admin.display(description="最後一次 LINE 通知")
    def last_line_notification(self, obj):
        if hasattr(obj, "last_notification_at"):
            return obj.last_notification_at or "—"
        latest = obj.line_notifications.filter(sent_at__isnull=False).order_by("-sent_at").first()
        return latest.sent_at if latest else "—"

    @admin.display(description="連絡状況")
    def contact_state(self, obj):
        jobs = sorted(obj.notification_outbox.all(), key=lambda job: job.created_at, reverse=True)[:12]
        if any(job.status == NotificationOutbox.Status.SENT for job in jobs):
            return "送達記録あり"
        dead_channels = {job.channel for job in jobs if job.status == NotificationOutbox.Status.DEAD}
        if {NotificationOutbox.Channel.LINE, NotificationOutbox.Channel.EMAIL}.issubset(dead_channels):
            return "要対応・連絡不能"
        if jobs:
            return "送信待ち／再試行"
        return "通知未作成"

    def get_urls(self):
        custom = [
            path("<path:object_id>/confirm-shipping/", self.admin_site.admin_view(self.confirm_shipping), name="orders_order_confirm_shipping"),
            path("<path:object_id>/resend-payment/", self.admin_site.admin_view(self.resend_payment), name="orders_order_resend_payment"),
            path("<path:object_id>/confirm-payment/", self.admin_site.admin_view(self.confirm_payment), name="orders_order_confirm_payment"),
            path("<path:object_id>/mark-shipped/", self.admin_site.admin_view(self.ship_order), name="orders_order_mark_shipped"),
            path("<path:object_id>/resend-shipping/", self.admin_site.admin_view(self.resend_shipping), name="orders_order_resend_shipping"),
            path("<path:object_id>/mark-preparing/", self.admin_site.admin_view(self.prepare_order), name="orders_order_mark_preparing"),
            path("<path:object_id>/complete/", self.admin_site.admin_view(self.complete), name="orders_order_complete"),
            path("<path:object_id>/cancel/", self.admin_site.admin_view(self.cancel), name="orders_order_cancel"),
        ]
        return custom + super().get_urls()

    def _run(self, request, object_id, operation, success):
        order = get_object_or_404(Order, pk=object_id)
        if not self.has_change_permission(request, order):
            raise PermissionDenied
        if request.method != "POST":
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        try:
            operation(int(object_id), actor=request.user)
        except (ValidationError, Payment.DoesNotExist) as exc:
            self.message_user(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc), level=messages.ERROR)
        else:
            self.message_user(request, success, level=messages.SUCCESS)
        return redirect(reverse("admin:orders_order_change", args=[object_id]))

    def confirm_shipping(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        if not self.has_change_permission(request, order):
            raise PermissionDenied
        if request.method != "POST":
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        raw_shipping_fee = request.POST.get("shipping_fee", "").strip().replace(",", "")
        try:
            shipping_fee = Decimal(raw_shipping_fee)
        except (InvalidOperation, ValueError):
            self.message_user(request, "請輸入 0 或正整數的運費。", level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        order.shipping_fee = shipping_fee
        try:
            order.full_clean()
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        order.save(update_fields=("shipping_fee", "final_total", "updated_at"))
        return self._run(request, object_id, confirm_shipping_and_request_payment, "已確定運費並排入 LINE／Email 付款通知。")

    def resend_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        return self._run(request, object_id, lambda pk, actor=None: enqueue_order_notifications(pk, "payment_request", force=True), "已重新排入 LINE／Email 付款通知。")

    def confirm_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        payment = order.payments.exclude(status=Payment.Status.CONFIRMED).order_by("-created_at").first()
        operation = (lambda pk, actor=None: confirm_manual_payment(pk, payment.pk, actor=actor)) if payment else (lambda pk, actor=None: (_ for _ in ()).throw(ValidationError("請先在付款記錄中新增一筆手動付款資料。")))
        return self._run(request, object_id, operation, "已確認付款。")

    def ship_order(self, request, object_id):
        return self._run(request, object_id, mark_shipped, "已更新為已出貨並執行 LINE 通知。")

    def resend_shipping(self, request, object_id):
        return self._run(request, object_id, lambda pk, actor=None: enqueue_order_notifications(pk, "order_shipped", force=True), "已重新排入 LINE／Email 出貨通知。")

    def prepare_order(self, request, object_id):
        return self._run(request, object_id, mark_preparing, "已更新為出貨準備中。")

    def complete(self, request, object_id):
        return self._run(request, object_id, complete_order, "已將訂單更新為完成。")

    def cancel(self, request, object_id):
        return self._run(request, object_id, cancel_order, "已取消訂單並還原相關庫存。")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


@admin.register(PaymentMethod, site=backoffice_site)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("display_name", "code", "enabled", "provider", "sort_order")
    list_editable = ("enabled", "sort_order")
    list_filter = ("enabled", "code")


@admin.register(Payment, site=backoffice_site)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "method", "amount", "status", "paid_at", "created_at")
    list_filter = ("status", "method", "created_at")
    search_fields = ("order__public_number", "provider_reference")
    readonly_fields = ("created_at", "updated_at", "confirmed_at", "cancelled_at", "refunded_at")
    actions = ("record_remaining_full_refund",)

    @admin.action(description="残額を全額退款として監査付きで登記")
    def record_remaining_full_refund(self, request, queryset):
        completed = 0
        for payment in queryset:
            reason = payment.refund_reason.strip()
            increment = (payment.amount or Decimal("0")) - payment.refunded_amount
            if increment <= 0 or not reason:
                self.message_user(request, f"{payment}: 退款理由を先に入力してください。", level=messages.ERROR)
                continue
            try:
                record_refund(payment.pk, amount=increment, reason=reason, actor=request.user)
            except ValidationError as exc:
                self.message_user(request, f"{payment}: {'; '.join(exc.messages)}", level=messages.ERROR)
            else:
                completed += 1
        self.message_user(request, f"退款記録 {completed} 件を処理しました。")


@admin.register(NotificationOutbox, site=backoffice_site)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = ("order", "channel", "event_type", "status", "attempt_count", "next_attempt_at", "sent_at")
    list_filter = ("channel", "event_type", "status")
    search_fields = ("order__public_number", "dedupe_key", "last_error")
    readonly_fields = ("order", "channel", "event_type", "dedupe_key", "payload", "attempt_count", "last_error", "response_metadata", "sent_at", "created_at", "updated_at")
    actions = ("retry_jobs",)

    @admin.action(description="選択通知を安全に再送待ちへ戻す")
    def retry_jobs(self, request, queryset):
        count = queryset.filter(status__in=(NotificationOutbox.Status.DEAD, NotificationOutbox.Status.RETRY)).update(status=NotificationOutbox.Status.PENDING, next_attempt_at=timezone.now(), last_error="")
        self.message_user(request, f"{count} 件を再送待ちにしました。")


@admin.register(PolicyAcceptance, site=backoffice_site)
class PolicyAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("order", "document_type", "version", "accepted_at")
    search_fields = ("order__public_number", "document_type", "version")
    readonly_fields = ("order", "line_customer", "document_type", "version", "accepted_at", "ip_address", "user_agent")

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(OrderAuditLog, site=backoffice_site)
class OrderAuditLogAdmin(admin.ModelAdmin):
    list_display = ("order", "event", "actor", "actor_label", "from_status", "to_status", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("order__public_number", "actor_label")
    readonly_fields = ("order", "event", "actor", "actor_label", "from_status", "to_status", "changes", "metadata", "created_at")

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(LineCustomer, site=backoffice_site)
class LineCustomerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "masked_id", "is_friend", "is_blocked", "last_login_at", "friend_checked_at")
    search_fields = ("display_name",)
    list_filter = ("is_friend", "is_blocked")
    readonly_fields = ("masked_id", "display_name", "picture_url", "is_friend", "is_blocked", "last_login_at", "friend_checked_at", "followed_at", "unfollowed_at", "created_at", "updated_at")
    fields = readonly_fields

    @admin.display(description="LINE user ID（已遮罩）")
    def masked_id(self, obj):
        return obj.masked_user_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LineNotification, site=backoffice_site)
class LineNotificationAdmin(admin.ModelAdmin):
    list_display = ("order", "notification_type", "status", "sent_at", "failed_at", "retry_count", "http_status")
    list_filter = ("notification_type", "status", "created_at")
    search_fields = ("order__public_number",)
    readonly_fields = ("order", "line_customer", "notification_type", "status", "dedupe_key", "api_retry_key", "sent_at", "failed_at", "retry_count", "http_status", "error_message", "created_at", "updated_at")
    actions = ("retry_failed",)

    @admin.action(description="安全重試傳送失敗的 LINE 通知")
    def retry_failed(self, request, queryset):
        count = 0
        for notification in queryset.filter(status=LineNotification.Status.FAILED):
            jobs = enqueue_order_notifications(
                notification.order_id,
                notification.notification_type,
                channels=("line",),
                force=True,
            )
            count += len(jobs)
        self.message_user(request, f"已將 {count} 筆通知安全排入 Outbox。")


@admin.register(LineWebhookEvent, site=backoffice_site)
class LineWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("masked_event_id", "event_type", "processed_at")
    readonly_fields = ("masked_event_id", "event_type", "processed_at")
    fields = readonly_fields

    @admin.display(description="Event ID")
    def masked_event_id(self, obj):
        return f"{obj.webhook_event_id[:8]}…"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
