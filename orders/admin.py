from decimal import Decimal, InvalidOperation

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse

from .line_messaging import schedule_payment_request, schedule_shipping_notification, retry_notification
from .models import LineCustomer, LineNotification, LineWebhookEvent, Order, OrderItem, Payment, PaymentMethod
from .operations import cancel_order, complete_order, confirm_manual_payment, confirm_shipping_and_request_payment, mark_preparing, mark_shipped


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


class LineNotificationInline(admin.TabularInline):
    model = LineNotification
    extra = 0
    can_delete = False
    fields = ("notification_type", "status", "sent_at", "failed_at", "retry_count", "http_status", "error_message")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    change_form_template = "admin/orders/order/change_form.html"
    list_display = ("public_number", "customer_name", "created_at", "status", "line_state", "friend_state", "subtotal", "shipping_fee", "final_total", "payment_state", "shipment_state", "last_line_notification")
    list_filter = ("status", "created_at", "paid_at", "shipped_at")
    list_select_related = ("line_customer",)
    search_fields = ("public_number", "customer_name", "phone", "email", "tracking_number")
    date_hierarchy = "created_at"
    readonly_fields = ("public_number", "status", "line_customer", "line_display_name", "line_friendship", "notification_summary", "idempotency_key", "subtotal", "final_total", "payment_link_version", "payment_request_total", "created_at", "updated_at", "inventory_reserved", "inventory_released")
    inlines = (OrderItemInline, PaymentInline, LineNotificationInline)
    fieldsets = (
        ("注文", {"fields": ("public_number", "status", "created_at", "updated_at")}),
        ("LINE", {"fields": ("line_customer", "line_display_name", "line_friendship", "notification_summary")}),
        ("顧客・配送", {"fields": ("customer_name", "phone", "email", "shipping_information", "customer_note")}),
        ("金額", {"fields": ("subtotal", "shipping_fee", "final_total")}),
        ("入金・発送", {"fields": ("paid_at", "shipped_at", "carrier", "tracking_number", "tracking_url")}),
        ("管理", {"fields": ("admin_note", "idempotency_key", "payment_link_version", "payment_request_total", "inventory_reserved", "inventory_released")}),
    )

    @admin.display(description="入金", boolean=True)
    def payment_state(self, obj):
        return obj.is_paid

    @admin.display(description="発送")
    def shipment_state(self, obj):
        return "発送済み" if obj.shipped_at else "未発送"

    @admin.display(description="LINE連携")
    def line_state(self, obj):
        return obj.line_customer.display_name if obj.line_customer_id else "未連携"

    @admin.display(description="友だち", boolean=True)
    def friend_state(self, obj):
        return bool(obj.line_customer_id and obj.line_customer.is_friend and not obj.line_customer.is_blocked)

    @admin.display(description="LINE表示名")
    def line_display_name(self, obj):
        return obj.line_customer.display_name if obj.line_customer_id else "—"

    @admin.display(description="LINE通知可否")
    def line_friendship(self, obj):
        if not obj.line_customer_id:
            return "未連携"
        return "通知可能" if obj.line_customer.is_friend and not obj.line_customer.is_blocked else "LINE通知不可"

    @admin.display(description="通知状態")
    def notification_summary(self, obj):
        states = {item.notification_type: item.get_status_display() for item in obj.line_notifications.all()}
        labels = dict(LineNotification.Type.choices)
        return " / ".join(f"{labels[key]}: {states.get(key, '未送信')}" for key in labels)

    @admin.display(description="最終LINE通知")
    def last_line_notification(self, obj):
        latest = obj.line_notifications.filter(sent_at__isnull=False).order_by("-sent_at").first()
        return latest.sent_at if latest else "—"

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
            operation(int(object_id))
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
            self.message_user(request, "運費を0以上の整数で入力してください。", level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        order.shipping_fee = shipping_fee
        try:
            order.full_clean()
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        order.save(update_fields=("shipping_fee", "final_total", "updated_at"))
        return self._run(request, object_id, confirm_shipping_and_request_payment, "運費を確定し、支払い通知処理を実行しました。")

    def resend_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        return self._run(request, object_id, lambda pk: schedule_payment_request(pk, force=True), "支払い通知を明示的に再送しました。")

    def confirm_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        payment = order.payments.exclude(status=Payment.Status.CONFIRMED).order_by("-created_at").first()
        operation = (lambda pk: confirm_manual_payment(pk, payment.pk)) if payment else (lambda pk: (_ for _ in ()).throw(ValidationError("先に支払いinlineへ手動支払いを登録してください。")))
        return self._run(request, object_id, operation, "入金を確認しました。")

    def ship_order(self, request, object_id):
        return self._run(request, object_id, mark_shipped, "発送済みに更新し、LINE通知処理を実行しました。")

    def resend_shipping(self, request, object_id):
        return self._run(request, object_id, lambda pk: schedule_shipping_notification(pk, force=True), "発送通知を明示的に再送しました。")

    def prepare_order(self, request, object_id):
        return self._run(request, object_id, mark_preparing, "発送準備中へ更新しました。")

    def complete(self, request, object_id):
        return self._run(request, object_id, complete_order, "注文を完了へ更新しました。")

    def cancel(self, request, object_id):
        return self._run(request, object_id, cancel_order, "注文をキャンセルし、対象在庫を復元しました。")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("display_name", "code", "enabled", "provider", "sort_order")
    list_editable = ("enabled", "sort_order")
    list_filter = ("enabled", "code")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "method", "amount", "status", "paid_at", "created_at")
    list_filter = ("status", "method", "created_at")
    search_fields = ("order__public_number", "provider_reference")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LineCustomer)
class LineCustomerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "masked_id", "is_friend", "is_blocked", "last_login_at", "friend_checked_at")
    search_fields = ("display_name",)
    list_filter = ("is_friend", "is_blocked")
    readonly_fields = ("masked_id", "display_name", "picture_url", "is_friend", "is_blocked", "last_login_at", "friend_checked_at", "followed_at", "unfollowed_at", "created_at", "updated_at")
    fields = readonly_fields

    @admin.display(description="LINE user ID（mask）")
    def masked_id(self, obj):
        return obj.masked_user_id

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LineNotification)
class LineNotificationAdmin(admin.ModelAdmin):
    list_display = ("order", "notification_type", "status", "sent_at", "failed_at", "retry_count", "http_status")
    list_filter = ("notification_type", "status", "created_at")
    search_fields = ("order__public_number",)
    readonly_fields = ("order", "line_customer", "notification_type", "status", "dedupe_key", "api_retry_key", "sent_at", "failed_at", "retry_count", "http_status", "error_message", "created_at", "updated_at")
    actions = ("retry_failed",)

    @admin.action(description="失敗したLINE通知を安全に再試行")
    def retry_failed(self, request, queryset):
        count = 0
        for notification in queryset.filter(status=LineNotification.Status.FAILED):
            retry_notification(notification.pk)
            count += 1
        self.message_user(request, f"{count}件を再試行しました。")


@admin.register(LineWebhookEvent)
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
