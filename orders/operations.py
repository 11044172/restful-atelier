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
        raise ValidationError("送料を入力して保存してから実行してください。")
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("キャンセル済み注文では実行できません。")
    if order.is_paid:
        raise ValidationError("入金済み注文では実行できません。")
    if not order.line_customer_id:
        raise ValidationError("LINE購入者が紐付いていません。")
    if not order.line_customer.is_friend or order.line_customer.is_blocked:
        raise ValidationError("購入者へLINE通知できないため、友だち状態を確認してください。")
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
        raise ValidationError("この注文は入金確認できません。")
    if not payment.method or payment.method.code not in {
        PaymentMethod.Method.TAIWAN_PAY,
        PaymentMethod.Method.BANK_TRANSFER,
    }:
        raise ValidationError("Admin確認は台灣 Payまたは銀行振込だけに使用できます。")
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
        raise ValidationError("入金確認前の注文は発送できません。")
    if order.status == Order.Status.CANCELLED:
        raise ValidationError("キャンセル済み注文は発送できません。")
    if not order.carrier.strip():
        raise ValidationError("配送会社を入力して保存してから実行してください。")
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
        raise ValidationError("入金確認前の注文は発送準備中にできません。")
    if order.status in {Order.Status.CANCELLED, Order.Status.SHIPPED, Order.Status.COMPLETED}:
        raise ValidationError("現在の状態から発送準備中へ変更できません。")
    order.status = Order.Status.PREPARING
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def complete_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != Order.Status.SHIPPED:
        raise ValidationError("発送済み注文だけを完了にできます。")
    order.status = Order.Status.COMPLETED
    order.full_clean()
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def cancel_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status in {Order.Status.SHIPPED, Order.Status.COMPLETED}:
        raise ValidationError("発送済み・完了注文は通常のキャンセル操作では取り消せません。")
    if order.is_paid:
        raise ValidationError("入金済み注文は返金確認なしにキャンセルできません。")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=("status", "updated_at"))
    return order
