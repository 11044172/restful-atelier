import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import SiteSettings
from .models import LineNotification, NotificationOutbox, Order, OrderAuditLog
from .payment_links import make_cancel_url, make_payment_url


logger = logging.getLogger(__name__)
EVENT_TO_LINE = {
    "order_received": LineNotification.Type.ORDER_RECEIVED,
    "payment_request": LineNotification.Type.PAYMENT_REQUEST,
    "payment_confirmed": LineNotification.Type.PAYMENT_CONFIRMED,
    "order_shipped": LineNotification.Type.ORDER_SHIPPED,
    "order_cancelled": LineNotification.Type.ORDER_CANCELLED,
}


def _event_dedupe(order, event_type, version=None):
    if event_type == "payment_request":
        return f"order:{order.pk}:payment:v{version or order.payment_link_version}"
    return f"order:{order.pk}:{event_type}"


def enqueue_order_notifications(order_id, event_type, *, version=None, channels=("line", "email"), force=False):
    order = Order.objects.get(pk=order_id)
    created = []
    base = _event_dedupe(order, event_type, version)
    for channel in channels:
        key = f"{base}:{uuid.uuid4()}" if force else base
        job, was_created = NotificationOutbox.objects.get_or_create(
            channel=channel,
            event_type=event_type,
            dedupe_key=key,
            defaults={"order": order, "payload": {"version": version, "force": force}},
        )
        if was_created:
            created.append(job)
            OrderAuditLog.objects.create(
                order=order,
                event="notification_queued",
                actor_label="system",
                from_status=order.status,
                to_status=order.status,
                metadata={"outbox_id": job.pk, "channel": channel, "notification_event": event_type, "forced": force},
            )
    return created


def _email_body(order, event_type):
    lines = ["靜院居家 / Rfull", "", f"訂單編號：{order.public_number}"]
    for item in order.items.all():
        lines.append(f"- {item.product_name_snapshot} × {item.quantity}　NT${item.line_total:,.0f}")
    lines.append(f"商品小計：NT${order.subtotal:,.0f}")
    subjects = {
        "order_received": "訂單已受理（運費確認中）",
        "payment_request": "運費與最終金額已確認",
        "payment_confirmed": "付款確認",
        "order_shipped": "商品已出貨",
        "order_cancelled": "訂單已取消",
    }
    if order.shipping_fee is not None:
        lines.extend([f"運費：NT${order.shipping_fee:,.0f}", f"合計：NT${order.final_total:,.0f}"])
    if event_type == "order_received":
        lines.append("目前僅為訂單受理，工作人員確認配送方法與運費後，會再通知最終金額。")
    elif event_type == "payment_request":
        lines.extend(["", f"付款確認：{make_payment_url(order)}", f"未付款取消：{make_cancel_url(order)}", "連結有期限。付款前請再次確認商品、運費及各項政策。"])
    elif event_type == "payment_confirmed":
        lines.append("已確認收到款項，將進入出貨準備。")
    elif event_type == "order_shipped":
        lines.append(f"物流公司：{order.carrier}")
        if order.tracking_number: lines.append(f"追蹤編號：{order.tracking_number}")
        if order.tracking_url: lines.append(f"配送查詢：{order.tracking_url}")
    elif event_type == "order_cancelled":
        lines.append("未付款訂單已取消；一般商品保留庫存已還原。")
    lines.extend(["", "本信由系統寄送。如需協助，請透過網站所列正式客服方式聯絡。"])
    return f"[Rfull] {subjects[event_type]} {order.public_number}", "\n".join(lines)


def send_order_email_event(order, event_type):
    subject, body = _email_body(order, event_type)
    recipients = [order.email]
    if event_type == "order_received":
        site = SiteSettings.load()
        for address in [site.order_notification_email or settings.ORDER_NOTIFICATION_EMAIL, *settings.ORDER_NOTIFICATION_EMAILS]:
            if address and address not in recipients:
                recipients.append(address)
        admin_url = f"{settings.CANONICAL_ORIGIN}{reverse('admin:orders_order_change', args=[order.pk])}"
        body += f"\n\n管理員訂單頁面：{admin_url}"
    return send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)


def deliver_outbox(job):
    order = Order.objects.prefetch_related("items").select_related("line_customer").get(pk=job.order_id)
    if job.channel == NotificationOutbox.Channel.LINE:
        from .line_messaging import send_order_notification
        line_type = EVENT_TO_LINE[job.event_type]
        notification = send_order_notification(order.pk, line_type, force=bool(job.payload.get("force")))
        if notification is None:
            raise RuntimeError("訂單沒有可用的 LINE 顧客資料。")
        if notification.status != LineNotification.Status.SENT:
            error = RuntimeError(notification.error_message or "LINE 傳送失敗。")
            error.retryable = notification.http_status in {429, 500, 502, 503, 504} or notification.http_status is None
            raise error
        return {"line_notification_id": notification.pk, "http_status": notification.http_status or 200}
    if job.channel == NotificationOutbox.Channel.EMAIL:
        send_order_email_event(order, job.event_type)
        return {"recipient": order.email}
    raise RuntimeError("不支援的通知管道。")


def process_next_outbox():
    now = timezone.now()
    with transaction.atomic():
        job = (
            NotificationOutbox.objects.select_for_update(skip_locked=True)
            .filter(status__in=(NotificationOutbox.Status.PENDING, NotificationOutbox.Status.RETRY), next_attempt_at__lte=now)
            .order_by("next_attempt_at", "pk")
            .first()
        )
        if not job:
            return None
        job.status = NotificationOutbox.Status.PROCESSING
        job.locked_at = now
        job.attempt_count += 1
        job.save(update_fields=("status", "locked_at", "attempt_count", "updated_at"))
    try:
        metadata = deliver_outbox(job)
    except Exception as exc:
        logger.exception("notification_delivery_failed", extra={"order": job.order_id, "channel": job.channel, "event": job.event_type})
        retryable = getattr(exc, "retryable", True)
        if retryable and job.attempt_count < job.max_attempts:
            delay = min(3600, 60 * (2 ** (job.attempt_count - 1)))
            job.status = NotificationOutbox.Status.RETRY
            job.next_attempt_at = timezone.now() + timedelta(seconds=delay)
        else:
            job.status = NotificationOutbox.Status.DEAD
        job.last_error = str(exc)[:2000]
        job.locked_at = None
        job.save(update_fields=("status", "next_attempt_at", "last_error", "locked_at", "updated_at"))
        OrderAuditLog.objects.create(
            order_id=job.order_id,
            event="notification_retry" if job.status == NotificationOutbox.Status.RETRY else "notification_dead",
            actor_label="outbox-worker",
            metadata={"outbox_id": job.pk, "channel": job.channel, "notification_event": job.event_type, "attempt": job.attempt_count, "error": job.last_error},
        )
    else:
        job.status = NotificationOutbox.Status.SENT
        job.sent_at = timezone.now()
        job.locked_at = None
        job.last_error = ""
        job.response_metadata = metadata or {}
        job.save(update_fields=("status", "sent_at", "locked_at", "last_error", "response_metadata", "updated_at"))
        OrderAuditLog.objects.create(
            order_id=job.order_id,
            event="notification_sent",
            actor_label="outbox-worker",
            metadata={"outbox_id": job.pk, "channel": job.channel, "notification_event": job.event_type, "attempt": job.attempt_count},
        )
    return job
