from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .line_messaging import schedule_payment_request, schedule_shipping_notification
from .models import Order, Payment, PaymentMethod


@transaction.atomic
def confirm_shipping_and_request_payment(order_id):
    order = (
        Order.objects.select_related("line_customer")
        .select_for_update(of=("self",))
        .get(pk=order_id)
    )
    if order.shipping_fee is None:
        raise ValidationError("請先填寫運費並儲存，再執行此操作。")
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("已取消的訂單無法執行此操作。")
    if order.is_paid:
        raise ValidationError("已付款的訂單無法執行此操作。")
    if not order.line_customer_id:
        raise ValidationError("此訂單尚未綁定 LINE 購買者。")
    if not order.line_customer.is_friend or order.line_customer.is_blocked:
        raise ValidationError("目前無法傳送 LINE 通知，請確認購買者的好友狀態。")
    requested_total = order.subtotal + order.shipping_fee
    if (
        order.status == Order.Status.AWAITING_PAYMENT
        and order.payment_link_version > 0
        and order.payment_request_total == requested_total
    ):
        return order
    order.payment_link_version += 1
    order.payment_request_total = requested_total
    order.status = Order.Status.AWAITING_PAYMENT
    order.full_clean()
    order.save(update_fields=("shipping_fee", "final_total", "payment_link_version", "payment_request_total", "status", "updated_at"))
    transaction.on_commit(lambda: schedule_payment_request(order.pk))
    return order


@transaction.atomic
def confirm_manual_payment(order_id, payment_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    payment = Payment.objects.select_for_update().select_related("method").get(pk=payment_id, order=order)
    if order.status == Order.Status.CANCELLED or order.final_total is None:
        raise ValidationError("此訂單無法確認付款。")
    if not payment.method or payment.method.code not in {
        PaymentMethod.Method.TAIWAN_PAY,
        PaymentMethod.Method.BANK_TRANSFER,
    }:
        raise ValidationError("後台手動確認僅適用於台灣 Pay 或銀行轉帳。")
    if payment.status == Payment.Status.CONFIRMED:
        return payment
    payment.amount = order.final_total
    payment.currency = "TWD"
    payment.status = Payment.Status.CONFIRMED
    payment.paid_at = timezone.now()
    payment.full_clean()
    payment.save()
    return payment


@transaction.atomic
def mark_shipped(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.is_paid:
        raise ValidationError("尚未確認付款的訂單不能出貨。")
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("已取消的訂單不能出貨。")
    if not order.carrier.strip():
        raise ValidationError("請先填寫物流公司並儲存，再執行此操作。")
    if order.status == Order.Status.SHIPPED:
        return order
    order.status = Order.Status.SHIPPED
    order.shipped_at = timezone.now()
    order.full_clean()
    order.save(update_fields=("status", "shipped_at", "updated_at"))
    transaction.on_commit(lambda: schedule_shipping_notification(order.pk))
    return order


@transaction.atomic
def mark_preparing(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.is_paid:
        raise ValidationError("尚未確認付款的訂單不能更新為出貨準備中。")
    if order.status in {Order.Status.CANCELLED, Order.Status.SHIPPED, Order.Status.COMPLETED}:
        raise ValidationError("目前訂單狀態無法更新為出貨準備中。")
    order.status = Order.Status.PREPARING
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def complete_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != Order.Status.SHIPPED:
        raise ValidationError("只有已出貨的訂單可以標記為已完成。")
    order.status = Order.Status.COMPLETED
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def cancel_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status in {Order.Status.SHIPPED, Order.Status.COMPLETED}:
        raise ValidationError("已出貨或已完成的訂單無法使用一般取消操作。")
    if order.is_paid:
        raise ValidationError("已付款訂單必須先確認退款，不能直接取消。")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=("status", "updated_at"))
    return order
