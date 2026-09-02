from decimal import Decimal

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .models import LineCustomer, LineNotification, LineWebhookEvent, NotificationOutbox, Order, OrderAuditLog, OrderItem, Payment, PaymentMethod, PolicyAcceptance
from .forms import ManualPaymentConfirmationForm, ShippingConfirmationForm, ShippingDispatchForm, ShippingRevisionForm
from .notifications import enqueue_order_notifications, retry_or_enqueue_order_notifications
from .operations import cancel_order, complete_order, confirm_manual_payment, confirm_shipping_and_request_payment, mark_preparing, mark_shipped, record_refund, revise_shipping_and_reissue_payment, start_shipping_review


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product", "product_name_snapshot", "sku_snapshot", "unit_price_snapshot", "quantity", "line_total", "stock_was_reserved")
    readonly_fields = fields


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = (
        "method", "payment_variant_label", "ecpay_payment_type", "normalized_payment_method",
        "actual_installments", "provider", "amount", "currency", "status",
        "merchant_trade_no", "provider_reference", "paid_at", "note",
    )
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="ECPay 入口")
    def payment_variant_label(self, obj):
        return {"standard": "一般付款", "installment": "信用卡分期"}.get(obj.payment_variant, "—")

    @admin.display(description="ECPay PaymentType")
    def ecpay_payment_type(self, obj):
        return obj.ecpay_payment_type or "—"

    @admin.display(description="表示用付款方式")
    def normalized_payment_method(self, obj):
        return obj.normalized_payment_method or "—"

    @admin.display(description="分期期數")
    def actual_installments(self, obj):
        return obj.actual_installments or "—"


class LineNotificationInline(admin.TabularInline):
    model = LineNotification
    extra = 0
    can_delete = False
    fields = ("notification_type", "status", "sent_at", "failed_at", "retry_count", "http_status", "error_message")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class OrderAuditInline(admin.TabularInline):
    model = OrderAuditLog
    extra = 0
    can_delete = False
    fields = ("created_at", "event", "actor", "actor_label", "from_status", "to_status", "changes")
    readonly_fields = fields
    classes = ("collapse",)
    verbose_name_plural = "訂單稽核記錄"

    def has_add_permission(self, request, obj=None):
        return False


class OrderWorkFilter(admin.SimpleListFilter):
    title = "作業分類"
    parameter_name = "work_queue"

    def lookups(self, request, model_admin):
        return (
            ("store", "店家需要處理"),
            ("customer", "等待顧客付款"),
            ("delivery", "已出貨・等待完成確認"),
            ("notification", "通知異常"),
        )

    def queryset(self, request, queryset):
        if self.value() == "store":
            return queryset.filter(status__in=(Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.PAID, Order.Status.PREPARING, Order.Status.REFUND_PENDING))
        if self.value() == "customer":
            return queryset.filter(status=Order.Status.AWAITING_PAYMENT)
        if self.value() == "delivery":
            return queryset.filter(status=Order.Status.SHIPPED)
        if self.value() == "notification":
            return queryset.filter(notification_outbox__status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD)).distinct()
        return queryset


class OutboxHealthFilter(admin.SimpleListFilter):
    title = "運作狀態"
    parameter_name = "health"

    def lookups(self, request, model_admin):
        return (("attention", "重試／人工處理"), ("queued", "尚未送出"), ("sent", "傳送成功"))

    def queryset(self, request, queryset):
        if self.value() == "attention":
            return queryset.filter(status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD))
        if self.value() == "queued":
            return queryset.filter(status__in=(NotificationOutbox.Status.PENDING, NotificationOutbox.Status.PROCESSING))
        if self.value() == "sent":
            return queryset.filter(status=NotificationOutbox.Status.SENT)
        return queryset


@admin.register(Order, site=backoffice_site)
class OrderAdmin(admin.ModelAdmin):
    change_form_template = "admin/orders/order/change_form.html"
    list_display = ("public_number", "customer_name", "next_action_label", "status_badge", "elapsed_time", "final_total_display", "contact_state")
    list_filter = (OrderWorkFilter, "status", "created_at")
    list_select_related = ("line_customer",)
    search_fields = ("public_number", "customer_name", "phone", "email", "tracking_number")
    date_hierarchy = "created_at"
    readonly_fields = ("public_number", "status", "line_customer", "line_display_name", "line_friendship", "notification_summary", "idempotency_key", "subtotal", "final_total", "payment_link_version", "payment_request_total", "created_at", "updated_at", "inventory_reserved", "inventory_released")
    inlines = (PaymentInline, LineNotificationInline, OrderAuditInline)
    fieldsets = (
        ("購買者與配送資訊", {"fields": ("customer_name", "phone", "email", "recipient_name", "postal_code", "city", "district", "street_address", "delivery_note", "shipping_information", "customer_note")}),
        ("金額與付款", {"fields": ("subtotal", "shipping_fee", "final_total", "paid_at")}),
        ("LINE／Email 通知", {"fields": ("line_customer", "line_display_name", "line_friendship", "notification_summary")}),
        ("管理備註", {"fields": ("admin_note",)}),
        ("系統資訊", {"classes": ("collapse",), "fields": ("public_number", "status", "created_at", "updated_at", "shipped_at", "carrier", "tracking_number", "tracking_url", "idempotency_key", "payment_link_version", "payment_request_total", "inventory_reserved", "inventory_released")}),
    )
    list_per_page = 25

    def get_queryset(self, request):
        confirmed = Payment.objects.filter(order_id=OuterRef("pk"), status=Payment.Status.CONFIRMED)
        notification_errors = NotificationOutbox.objects.filter(
            order_id=OuterRef("pk"),
            status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD),
        )
        last_sent = LineNotification.objects.filter(
            order_id=OuterRef("pk"), sent_at__isnull=False
        ).order_by("-sent_at").values("sent_at")[:1]
        return super().get_queryset(request).prefetch_related("notification_outbox").annotate(
            has_confirmed_payment=Exists(confirmed),
            has_notification_error=Exists(notification_errors),
            last_notification_at=Subquery(last_sent),
            _attention_priority=Case(
                When(has_notification_error=True, then=Value(0)),
                When(status__in=(Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.PAID, Order.Status.PREPARING, Order.Status.REFUND_PENDING), then=Value(1)),
                When(status=Order.Status.SHIPPED, then=Value(2)),
                When(status=Order.Status.AWAITING_PAYMENT, then=Value(3)),
                default=Value(4), output_field=IntegerField(),
            ),
        ).order_by("_attention_priority", "created_at")

    def get_ordering(self, request):
        notification_errors = NotificationOutbox.objects.filter(
            order_id=OuterRef("pk"),
            status__in=(NotificationOutbox.Status.RETRY, NotificationOutbox.Status.DEAD),
        )
        return (
            Case(
                When(Exists(notification_errors), then=Value(0)),
                When(status__in=(Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.PAID, Order.Status.PREPARING, Order.Status.REFUND_PENDING), then=Value(1)),
                When(status=Order.Status.SHIPPED, then=Value(2)),
                When(status=Order.Status.AWAITING_PAYMENT, then=Value(3)),
                default=Value(4), output_field=IntegerField(),
            ),
            "created_at",
        )

    @admin.display(description="目前需要處理")
    def next_action_label(self, obj):
        if any(job.status in (NotificationOutbox.Status.DEAD, NotificationOutbox.Status.RETRY) for job in obj.notification_outbox.all()):
            return "確認通知異常"
        return {
            Order.Status.RECEIVED: "開始確認運費",
            Order.Status.SHIPPING_REVIEW: "確認運費",
            Order.Status.AWAITING_PAYMENT: "等待顧客付款",
            Order.Status.PAID: "開始出貨準備",
            Order.Status.PREPARING: "輸入追蹤資訊並出貨",
            Order.Status.SHIPPED: "確認配送完成",
            Order.Status.REFUND_PENDING: "確認退款處理",
        }.get(obj.status, "處理完成")

    @admin.display(description="經過時間", ordering="created_at")
    def elapsed_time(self, obj):
        delta = timezone.now() - obj.created_at
        if delta.days:
            return f"{delta.days} 天"
        hours = max(0, int(delta.total_seconds() // 3600))
        return f"{hours} 小時" if hours else "未滿 1 小時"

    @admin.display(description="最終合計", ordering="final_total")
    def final_total_display(self, obj):
        return f"NT$ {obj.final_total:,.0f}" if obj.final_total is not None else "運費未確定"

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

    @admin.display(description="聯絡狀態")
    def contact_state(self, obj):
        jobs = sorted(obj.notification_outbox.all(), key=lambda job: job.created_at, reverse=True)[:12]
        if any(job.status == NotificationOutbox.Status.SENT for job in jobs):
            return "已有送達記錄"
        dead_channels = {job.channel for job in jobs if job.status == NotificationOutbox.Status.DEAD}
        if {NotificationOutbox.Channel.LINE, NotificationOutbox.Channel.EMAIL}.issubset(dead_channels):
            return "需要處理・無法聯絡"
        if jobs:
            return "等待傳送／重試"
        return "尚未建立通知"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        order = self.get_object(request, object_id)
        context = dict(extra_context or {})
        if order:
            order = Order.objects.select_related("line_customer").prefetch_related(
                "items", "payments__method", "notification_outbox", "audit_logs"
            ).get(pk=order.pk)
            eligible_payments = order.payments.filter(
                method__code__in=(PaymentMethod.Method.TAIWAN_PAY, PaymentMethod.Method.BANK_TRANSFER),
                amount=order.final_total,
            ).exclude(status=Payment.Status.CONFIRMED)
            confirmed_payment = order.payments.filter(status=Payment.Status.CONFIRMED).first()
            action_reason = {
                Order.Status.RECEIVED: "訂單已成立，請先開始確認運費。",
                Order.Status.SHIPPING_REVIEW: "店家需要確認運費，並向顧客傳送付款通知。",
                Order.Status.AWAITING_PAYMENT: "付款通知已登錄，目前等待顧客完成付款。",
                Order.Status.PAID: "款項已確認，請開始出貨準備。",
                Order.Status.PREPARING: "請輸入追蹤資訊，並傳送出貨通知。",
                Order.Status.SHIPPED: "商品已出貨，請確認配送是否完成。",
                Order.Status.COMPLETED: "所有訂單處理均已完成。",
                Order.Status.CANCELLED: "訂單已在未付款狀態下取消。",
                Order.Status.REFUND_PENDING: "需要確認退款處理是否完成。",
                Order.Status.REFUNDED: "退款處理已完成。",
            }.get(order.status, "請確認目前狀態。")
            context.update(
                operation_order=order,
                action_reason=action_reason,
                line_reachable=bool(order.line_customer_id and order.line_customer.is_friend and not order.line_customer.is_blocked),
                shipping_form=ShippingConfirmationForm(initial={"shipping_fee": order.shipping_fee}),
                shipping_revision_form=ShippingRevisionForm(initial={"shipping_fee": order.shipping_fee}),
                dispatch_form=ShippingDispatchForm(initial={"carrier": order.carrier, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url}),
                manual_payment_form=ManualPaymentConfirmationForm(order=order),
                eligible_manual_payments=eligible_payments,
                confirmed_payment=confirmed_payment,
                notification_jobs=order.notification_outbox.all().order_by("-created_at", "-pk")[:20],
                payment_notification_jobs=order.notification_outbox.filter(event_type="payment_request").order_by("-created_at", "-pk")[:10],
                shipping_notification_jobs=order.notification_outbox.filter(event_type="order_shipped").order_by("-created_at", "-pk")[:10],
                latest_payment_request=order.notification_outbox.filter(event_type="payment_request").order_by("-created_at", "-pk").first(),
                latest_shipping_notice=order.notification_outbox.filter(event_type="order_shipped").order_by("-created_at", "-pk").first(),
            )
        return super().change_view(request, object_id, form_url, extra_context=context)

    def get_urls(self):
        custom = [
            path("<path:object_id>/start-shipping-review/", self.admin_site.admin_view(self.start_review), name="orders_order_start_shipping_review"),
            path("<path:object_id>/confirm-shipping/", self.admin_site.admin_view(self.confirm_shipping), name="orders_order_confirm_shipping"),
            path("<path:object_id>/revise-shipping/", self.admin_site.admin_view(self.revise_shipping), name="orders_order_revise_shipping"),
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
        form = ShippingConfirmationForm(request.POST)
        if not form.is_valid():
            self.message_user(request, "; ".join(error for errors in form.errors.values() for error in errors), level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        return self._run(request, object_id, lambda pk, actor=None: confirm_shipping_and_request_payment(pk, shipping_fee=form.cleaned_data["shipping_fee"], actor=actor), "已確定運費並排入 LINE／Email 付款通知。")

    def start_review(self, request, object_id):
        return self._run(request, object_id, start_shipping_review, "已開始確認運費。")

    def revise_shipping(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        if request.method != "POST":
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        form = ShippingRevisionForm(request.POST)
        if not form.is_valid():
            self.message_user(request, "; ".join(error for errors in form.errors.values() for error in errors), level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        return self._run(request, object_id, lambda pk, actor=None: revise_shipping_and_reissue_payment(pk, shipping_fee=form.cleaned_data["shipping_fee"], actor=actor), "已失效舊付款流程，並以新金額重新排入通知。")

    def resend_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        def operation(pk, actor=None):
            if order.status != Order.Status.AWAITING_PAYMENT or order.is_paid:
                raise ValidationError("只有等待付款中的未付款訂單可以重送付款通知。")
            return retry_or_enqueue_order_notifications(pk, "payment_request", version=order.payment_link_version)
        return self._run(request, object_id, operation, "已安全地重新排入 LINE／Email 付款通知。")

    def confirm_payment(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        if request.method != "POST":
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        form = ManualPaymentConfirmationForm(request.POST, order=order)
        if not form.is_valid():
            self.message_user(request, "請選擇金額一致的台灣 Pay 或銀行轉帳記錄。", level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        payment = form.cleaned_data["payment"]
        return self._run(request, object_id, lambda pk, actor=None: confirm_manual_payment(pk, payment.pk, actor=actor), "已確認付款。")

    def ship_order(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        if request.method != "POST":
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        form = ShippingDispatchForm(request.POST)
        if not form.is_valid():
            self.message_user(request, "; ".join(error for errors in form.errors.values() for error in errors), level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        if order.status != Order.Status.PREPARING:
            self.message_user(request, "只有出貨準備中的訂單可以從管理畫面執行出貨。", level=messages.ERROR)
            return redirect(reverse("admin:orders_order_change", args=[object_id]))
        return self._run(request, object_id, lambda pk, actor=None: mark_shipped(pk, actor=actor, **form.cleaned_data), "已更新為已出貨並排入 LINE／Email 通知。")

    def resend_shipping(self, request, object_id):
        order = get_object_or_404(Order, pk=object_id)
        def operation(pk, actor=None):
            if order.status != Order.Status.SHIPPED:
                raise ValidationError("只有已出貨訂單可以重送出貨通知。")
            return retry_or_enqueue_order_notifications(pk, "order_shipped")
        return self._run(request, object_id, operation, "已安全地重新排入 LINE／Email 出貨通知。")

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
    list_display = (
        "order", "payment_variant_label", "normalized_payment_method", "ecpay_payment_type",
        "actual_installments", "amount", "status", "merchant_trade_no", "provider_reference",
        "paid_at", "created_at",
    )
    list_filter = ("status", "method", "created_at")
    search_fields = ("order__public_number", "merchant_trade_no", "provider_reference")
    readonly_fields = (
        "order", "method", "provider", "amount", "currency", "status", "paid_at", "note",
        "refunded_amount", "refund_status", "refunded_at", "refund_operator",
        "payment_variant_label", "ecpay_payment_type", "normalized_payment_method",
        "actual_installments", "merchant_trade_no", "provider_reference", "provider_event_id", "provider_metadata",
        "created_at", "updated_at", "confirmed_at", "cancelled_at", "refunded_at",
    )
    actions = ("record_remaining_full_refund",)
    fieldsets = (
        ("付款資訊", {"fields": ("order", "method", "provider", "amount", "currency", "status", "paid_at", "note")}),
        ("退款資訊", {"fields": ("refunded_amount", "refund_status", "refund_reason", "refunded_at", "refund_operator")}),
        ("交易識別資訊", {"classes": ("collapse",), "fields": ("payment_variant_label", "ecpay_payment_type", "normalized_payment_method", "actual_installments", "merchant_trade_no", "provider_reference", "provider_event_id")}),
        ("供應商回應與系統資訊", {"classes": ("collapse",), "fields": ("provider_metadata", "created_at", "updated_at", "confirmed_at", "cancelled_at")}),
    )

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    @admin.display(description="ECPay 入口")
    def payment_variant_label(self, obj):
        return {"standard": "一般付款", "installment": "信用卡分期"}.get(obj.payment_variant, "—")

    @admin.display(description="ECPay PaymentType")
    def ecpay_payment_type(self, obj):
        return obj.ecpay_payment_type or "—"

    @admin.display(description="表示用付款方式")
    def normalized_payment_method(self, obj):
        return obj.normalized_payment_method or "—"

    @admin.display(description="分期期數")
    def actual_installments(self, obj):
        return obj.actual_installments or "—"

    @admin.action(description="將剩餘金額登記為全額退款並保留稽核記錄")
    def record_remaining_full_refund(self, request, queryset):
        completed = 0
        for payment in queryset:
            reason = payment.refund_reason.strip()
            increment = (payment.amount or Decimal("0")) - payment.refunded_amount
            if increment <= 0 or not reason:
                self.message_user(request, f"{payment}: 請先填寫退款理由。", level=messages.ERROR)
                continue
            try:
                record_refund(payment.pk, amount=increment, reason=reason, actor=request.user)
            except ValidationError as exc:
                self.message_user(request, f"{payment}: {'; '.join(exc.messages)}", level=messages.ERROR)
            else:
                completed += 1
        self.message_user(request, f"已處理 {completed} 筆退款記錄。")


@admin.register(NotificationOutbox, site=backoffice_site)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = ("order", "event_type", "channel", "status_badge", "attempt_count", "next_attempt_at", "last_error_short", "sent_at")
    list_filter = (OutboxHealthFilter, "channel", "event_type", "status")
    search_fields = ("order__public_number", "dedupe_key", "last_error")
    readonly_fields = ("order", "channel", "event_type", "dedupe_key", "payload", "status", "attempt_count", "max_attempts", "next_attempt_at", "locked_at", "last_error", "response_metadata", "sent_at", "created_at", "updated_at")
    actions = ("retry_jobs",)
    fieldsets = (
        ("通知工作", {"fields": ("order", "event_type", "channel", "status", "attempt_count", "max_attempts", "next_attempt_at", "sent_at")}),
        ("錯誤資訊", {"fields": ("last_error", "locked_at")}),
        ("冪等與原始回應", {"classes": ("collapse",), "fields": ("dedupe_key", "payload", "response_metadata", "created_at", "updated_at")}),
    )

    @admin.action(description="將選取的通知安全地恢復為等待重送")
    def retry_jobs(self, request, queryset):
        count = 0
        with transaction.atomic():
            for job in queryset.select_for_update().filter(status__in=(NotificationOutbox.Status.DEAD, NotificationOutbox.Status.RETRY)):
                job.status = NotificationOutbox.Status.PENDING
                job.next_attempt_at = timezone.now()
                job.locked_at = None
                job.last_error = ""
                job.save(update_fields=("status", "next_attempt_at", "locked_at", "last_error", "updated_at"))
                OrderAuditLog.objects.create(
                    order=job.order,
                    event="notification_requeued",
                    actor=request.user,
                    actor_label=request.user.get_username(),
                    from_status=job.order.status,
                    to_status=job.order.status,
                    metadata={"outbox_id": job.pk, "channel": job.channel, "notification_event": job.event_type},
                )
                count += 1
        self.message_user(request, f"已將 {count} 筆通知設為等待重送。")

    @admin.display(description="狀態", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status-pill status-{}">{}</span>', obj.status, obj.get_status_display())

    @admin.display(description="最後錯誤")
    def last_error_short(self, obj):
        return obj.last_error[:100] if obj.last_error else "—"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)


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

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="安全重試傳送失敗的 LINE 通知")
    def retry_failed(self, request, queryset):
        count = 0
        for notification in queryset.filter(status=LineNotification.Status.FAILED):
            jobs = retry_or_enqueue_order_notifications(
                notification.order_id,
                notification.notification_type,
                channels=("line",),
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
