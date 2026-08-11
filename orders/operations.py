from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Order, OrderAuditLog, Payment, PaymentMethod
from .notifications import enqueue_order_notifications


ALLOWED_TRANSITIONS = {
    Order.Status.RECEIVED: {Order.Status.SHIPPING_REVIEW, Order.Status.CANCELLED},
    Order.Status.SHIPPING_REVIEW: {Order.Status.AWAITING_PAYMENT, Order.Status.CANCELLED},
    Order.Status.AWAITING_PAYMENT: {Order.Status.PAID, Order.Status.CANCELLED},
    Order.Status.PAID: {Order.Status.PREPARING, Order.Status.SHIPPED, Order.Status.REFUND_PENDING},
    Order.Status.PREPARING: {Order.Status.SHIPPED, Order.Status.REFUND_PENDING},
    Order.Status.SHIPPED: {Order.Status.COMPLETED, Order.Status.REFUND_PENDING},
    Order.Status.COMPLETED: {Order.Status.REFUND_PENDING},
    Order.Status.REFUND_PENDING: {Order.Status.REFUNDED},
    Order.Status.REFUNDED: set(),
    Order.Status.CANCELLED: set(),
}


def record_audit(order, event, *, actor=None, actor_label="system", from_status="", changes=None, metadata=None):
    return OrderAuditLog.objects.create(
        order=order,
        event=event,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        actor_label=(getattr(actor, "get_username", lambda: "")() if getattr(actor, "is_authenticated", False) else actor_label),
        from_status=from_status,
        to_status=order.status,
        changes=changes or {},
        metadata=metadata or {},
    )


def transition(order, target, *, event, actor=None, actor_label="system", changes=None):
    previous = order.status
    if previous == target:
        return False
    if target not in ALLOWED_TRANSITIONS.get(previous, set()):
        raise ValidationError(f"不允許從「{order.get_status_display()}」變更為此狀態。")
    order.status = target
    record_audit(order, event, actor=actor, actor_label=actor_label, from_status=previous, changes=changes)
    return True


@transaction.atomic
def confirm_shipping_and_request_payment(order_id, *, actor=None):
    order = Order.objects.select_related("line_customer").select_for_update(of=("self",)).get(pk=order_id)
    if order.shipping_fee is None:
        raise ValidationError("請先填寫運費並儲存，再執行此操作。")
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("已取消的訂單無法執行此操作。")
    if order.is_paid:
        raise ValidationError("已付款的訂單無法執行此操作。")
    requested_total = order.subtotal + order.shipping_fee
    if order.status == Order.Status.AWAITING_PAYMENT and order.payment_link_version > 0 and order.payment_request_total == requested_total:
        return order
    previous = order.status
    order.payment_link_version += 1
    order.cancel_link_version += 1
    order.payment_request_total = requested_total
    transition(order, Order.Status.AWAITING_PAYMENT, event="shipping_confirmed", actor=actor, changes={"shipping_fee": str(order.shipping_fee), "final_total": str(requested_total)})
    order.full_clean()
    order.save(update_fields=("shipping_fee", "final_total", "payment_link_version", "cancel_link_version", "payment_request_total", "status", "updated_at"))
    if previous == Order.Status.AWAITING_PAYMENT:
        record_audit(order, "payment_link_reissued", actor=actor, from_status=previous, changes={"version": order.payment_link_version})
    transaction.on_commit(lambda: enqueue_order_notifications(order.pk, "payment_request", version=order.payment_link_version))
    return order


@transaction.atomic
def confirm_manual_payment(order_id, payment_id, *, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    payment = Payment.objects.select_for_update().select_related("method").get(pk=payment_id, order=order)
    if order.status == Order.Status.CANCELLED or order.final_total is None:
        raise ValidationError("此訂單無法確認付款。")
    if not payment.method or payment.method.code not in {PaymentMethod.Method.TAIWAN_PAY, PaymentMethod.Method.BANK_TRANSFER}:
        raise ValidationError("後台手動確認僅適用於台灣 Pay 或銀行轉帳。")
    if payment.status == Payment.Status.CONFIRMED:
        return payment
    if Payment.objects.filter(order=order, status=Payment.Status.CONFIRMED).exclude(pk=payment.pk).exists():
        raise ValidationError("此訂單已有確認付款記錄。")
    payment.amount = order.final_total
    payment.currency = "TWD"
    payment.status = Payment.Status.CONFIRMED
    payment.paid_at = payment.confirmed_at = timezone.now()
    payment.full_clean()
    try:
        payment.save()
    except IntegrityError as exc:
        raise ValidationError("此訂單已有確認付款記錄。") from exc
    order.refresh_from_db()
    record_audit(order, "payment_confirmed", actor=actor, from_status=Order.Status.AWAITING_PAYMENT, changes={"payment_id": payment.pk, "amount": str(payment.amount), "method": payment.method.code})
    return payment


@transaction.atomic
def mark_shipped(order_id, *, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.is_paid:
        raise ValidationError("尚未確認付款的訂單不能出貨。")
    if not order.carrier.strip():
        raise ValidationError("請先填寫物流公司並儲存，再執行此操作。")
    if order.status == Order.Status.SHIPPED:
        return order
    transition(order, Order.Status.SHIPPED, event="order_shipped", actor=actor, changes={"carrier": order.carrier, "tracking_number": order.tracking_number})
    order.shipped_at = timezone.now()
    order.full_clean()
    order.save(update_fields=("status", "shipped_at", "updated_at"))
    transaction.on_commit(lambda: enqueue_order_notifications(order.pk, "order_shipped"))
    return order


@transaction.atomic
def mark_preparing(order_id, *, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.is_paid:
        raise ValidationError("尚未確認付款的訂單不能更新為出貨準備中。")
    transition(order, Order.Status.PREPARING, event="preparing", actor=actor)
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def complete_order(order_id, *, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    transition(order, Order.Status.COMPLETED, event="completed", actor=actor)
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def cancel_order(order_id, *, actor=None, actor_label="system"):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.Status.CANCELLED:
        return order
    if order.is_paid or order.status not in {Order.Status.RECEIVED, Order.Status.SHIPPING_REVIEW, Order.Status.AWAITING_PAYMENT}:
        raise ValidationError("此訂單已付款或進入出貨流程，無法在線取消。請聯絡店家。")
    transition(order, Order.Status.CANCELLED, event="cancelled", actor=actor, actor_label=actor_label)
    order.cancelled_at = timezone.now()
    order.save(update_fields=("status", "cancelled_at", "updated_at"))
    transaction.on_commit(lambda: enqueue_order_notifications(order.pk, "order_cancelled"))
    return order


@transaction.atomic
def record_refund(payment_id, *, amount, reason, actor=None):
    payment = Payment.objects.select_for_update().select_related("order").get(pk=payment_id)
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if payment.status != Payment.Status.CONFIRMED:
        raise ValidationError("只有已確認付款可登記退款。")
    if amount <= 0 or payment.amount is None or payment.refunded_amount + amount > payment.amount:
        raise ValidationError("退款金額必須大於0，且累計不得超過實收金額。")
    payment.refunded_amount += amount
    payment.refund_reason = reason
    payment.refunded_at = timezone.now()
    payment.refund_operator = actor if getattr(actor, "is_authenticated", False) else None
    full = payment.refunded_amount == payment.amount
    payment.refund_status = "full" if full else "partial"
    payment.status = Payment.Status.REFUNDED if full else Payment.Status.CONFIRMED
    payment.save()
    if full:
        if order.status != Order.Status.REFUND_PENDING:
            transition(order, Order.Status.REFUND_PENDING, event="refund_started", actor=actor, changes={"payment_id": payment.pk})
            order.save(update_fields=("status", "updated_at"))
        transition(order, Order.Status.REFUNDED, event="refund_completed", actor=actor, changes={"amount": str(payment.refunded_amount), "reason": reason})
        order.save(update_fields=("status", "updated_at"))
    else:
        record_audit(order, "partial_refund", actor=actor, from_status=order.status, changes={"amount": str(amount), "total_refunded": str(payment.refunded_amount), "reason": reason})
    return payment
