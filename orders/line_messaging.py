import json
import logging
import uuid
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import LineNotification, Order
from .payment_links import make_cancel_url, make_payment_url


logger = logging.getLogger(__name__)
PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineMessagingError(Exception):
    def __init__(self, message, *, http_status=None, retryable=False, response_metadata=None):
        super().__init__(message)
        self.http_status = http_status
        self.retryable = retryable
        self.response_metadata = response_metadata or {}


def money(value):
    return f"NT${value:,.0f}"


def _row(label, value, *, emphasis=False):
    return {
        "type": "box", "layout": "baseline", "margin": "md",
        "contents": [
            {"type": "text", "text": label, "size": "sm", "color": "#777777", "flex": 4},
            {"type": "text", "text": value, "size": "sm", "color": "#222222", "align": "end", "weight": "bold" if emphasis else "regular", "flex": 6, "wrap": True},
        ],
    }


def _flex_message(*, alt_text, title, rows, note=None, buttons=None):
    body = [
        {"type": "text", "text": "Rfull", "size": "xs", "color": "#8A8178"},
        {"type": "text", "text": title, "size": "xl", "weight": "bold", "margin": "md", "color": "#222222"},
        {"type": "separator", "margin": "xl", "color": "#E7E2DC"},
        {"type": "box", "layout": "vertical", "margin": "lg", "contents": rows},
    ]
    if note:
        body.append({"type": "text", "text": note, "size": "sm", "color": "#666666", "wrap": True, "margin": "xl"})
    bubble = {"type": "bubble", "styles": {"body": {"backgroundColor": "#FAF9F7"}}, "body": {"type": "box", "layout": "vertical", "paddingAll": "24px", "contents": body}}
    if buttons:
        contents = []
        for index, button in enumerate(buttons):
            contents.append({"type": "button", "height": "sm", "style": "primary" if index == 0 else "secondary", "color": "#4C5148", "action": {"type": "uri", "label": button[0], "uri": button[1]}})
        bubble["footer"] = {"type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "20px", "contents": contents}
    return {"type": "flex", "altText": alt_text, "contents": bubble}


def build_order_received(order):
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 已收到，正在確認運費。",
        title="感謝您的訂購",
        rows=[_row("訂單編號", order.public_number), _row("商品金額", money(order.subtotal), emphasis=True)],
        note="我們已收到您的訂單，正在確認配送方式與運費。確認完成後，會透過 LINE 傳送最終金額及付款連結。",
    )


def build_payment_request(order):
    rows = [_row("訂單編號", order.public_number)]
    for item in order.items.all():
        rows.append(_row(f"{item.product_name_snapshot} × {item.quantity}", money(item.line_total)))
    rows.extend([
        _row("商品小計", money(order.subtotal)),
        _row("運費", money(order.shipping_fee)),
        _row("合計金額", money(order.final_total), emphasis=True),
    ])
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 運費已確認，付款金額 {money(order.final_total)}。",
        title="運費已確認",
        rows=rows,
        note="請確認最終內容後選擇付款，或在未付款時取消訂單。安全連結有期限。",
        buttons=(("前往付款", make_payment_url(order)), ("取消訂單", make_cancel_url(order))),
    )


def build_payment_confirmed(order):
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 已確認收到付款。",
        title="付款已確認",
        rows=[_row("訂單編號", order.public_number), _row("付款金額", money(order.final_total), emphasis=True)],
        note="我們將開始準備出貨，預計於付款確認後 3 個工作日內寄出。出貨完成後會再次透過 LINE 通知您。",
    )


def build_shipping_notification(order):
    rows = [_row("訂單編號", order.public_number), _row("物流公司", order.carrier)]
    if order.tracking_number:
        rows.append(_row("追蹤編號", order.tracking_number, emphasis=True))
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 已出貨。",
        title="您的商品已出貨",
        rows=rows,
        note="感謝您的耐心等候。",
        buttons=(("查看配送狀態", order.tracking_url),) if order.tracking_url else None,
    )


def build_cancelled(order):
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 已取消。",
        title="訂單已取消",
        rows=[_row("訂單編號", order.public_number), _row("商品小計", money(order.subtotal))],
        note="此訂單未付款並已取消；已保留的一般商品庫存已安全還原。如有疑問請聯絡店家。",
    )


BUILDERS = {
    LineNotification.Type.ORDER_RECEIVED: build_order_received,
    LineNotification.Type.PAYMENT_REQUEST: build_payment_request,
    LineNotification.Type.PAYMENT_CONFIRMED: build_payment_confirmed,
    LineNotification.Type.ORDER_SHIPPED: build_shipping_notification,
    LineNotification.Type.ORDER_CANCELLED: build_cancelled,
}


def push_message(*, line_user_id, message, retry_key):
    payload = json.dumps({"to": line_user_id, "messages": [message]}, ensure_ascii=False).encode("utf-8")
    request = Request(
        PUSH_URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN}",
            "X-Line-Retry-Key": str(retry_key),
        },
    )
    try:
        with urlopen(request, timeout=settings.LINE_API_TIMEOUT) as response:
            if response.status != 200:
                raise LineMessagingError("LINE Messaging API returned an unexpected response.", http_status=response.status)
    except HTTPError as exc:
        metadata = {"accepted_request_id": exc.headers.get("x-line-accepted-request-id", ""), "request_id": exc.headers.get("x-line-request-id", "")}
        if exc.code == 409:
            return metadata
        raise LineMessagingError(
            "LINE Messaging API rejected the notification.",
            http_status=exc.code,
            retryable=exc.code == 429 or 500 <= exc.code < 600,
            response_metadata=metadata,
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise LineMessagingError("LINE Messaging API timed out or was unavailable.", retryable=True) from exc


def _default_dedupe_key(order, notification_type):
    if notification_type == LineNotification.Type.PAYMENT_REQUEST:
        return f"payment-link-v{order.payment_link_version}"
    return "initial"


def send_order_notification(order_id, notification_type, *, force=False):
    order = Order.objects.select_related("line_customer").get(pk=order_id)
    if not order.line_customer_id:
        return None
    key = f"manual-{uuid.uuid4()}" if force else _default_dedupe_key(order, notification_type)
    try:
        with transaction.atomic():
            notification, created = LineNotification.objects.get_or_create(
                order=order, line_customer=order.line_customer,
                notification_type=notification_type, dedupe_key=key,
            )
    except IntegrityError:
        notification = LineNotification.objects.get(order=order, notification_type=notification_type, dedupe_key=key)
        created = False
    if not created:
        if notification.status == LineNotification.Status.FAILED:
            return retry_notification(notification.pk)
        return notification
    if not settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN:
        error = LineMessagingError("LINE Messaging API is not configured.")
    elif not order.line_customer.is_friend or order.line_customer.is_blocked:
        error = LineMessagingError("LINE notification is unavailable because the account is not a reachable friend.")
    else:
        error = None
        try:
            push_message(
                line_user_id=order.line_customer.line_user_id,
                message=BUILDERS[notification_type](order), retry_key=notification.api_retry_key,
            )
        except LineMessagingError as exc:
            error = exc
        except Exception:
            logger.exception("Unexpected LINE notification failure for order %s", order.public_number)
            error = LineMessagingError("Unexpected LINE notification failure.")
    now = timezone.now()
    if error:
        notification.status = LineNotification.Status.FAILED
        notification.failed_at = now
        notification.http_status = error.http_status
        notification.error_message = str(error)[:255]
        notification.save(update_fields=("status", "failed_at", "http_status", "error_message", "updated_at"))
    else:
        notification.status = LineNotification.Status.SENT
        notification.sent_at = now
        notification.error_message = ""
        notification.save(update_fields=("status", "sent_at", "error_message", "updated_at"))
    return notification


def retry_notification(notification_id):
    notification = LineNotification.objects.select_related("order__line_customer").get(pk=notification_id)
    if notification.status != LineNotification.Status.FAILED:
        return notification
    if notification.created_at < timezone.now() - timedelta(hours=23):
        notification.error_message = "已超過 LINE retry key 的安全重試期限，請由管理人員判斷是否以新的手動通知重新傳送。"
        notification.save(update_fields=("error_message", "updated_at"))
        return notification
    notification.retry_count += 1
    notification.status = LineNotification.Status.PENDING
    notification.save(update_fields=("retry_count", "status", "updated_at"))
    try:
        push_message(
            line_user_id=notification.line_customer.line_user_id,
            message=BUILDERS[notification.notification_type](notification.order),
            retry_key=notification.api_retry_key,
        )
    except LineMessagingError as exc:
        notification.status = LineNotification.Status.FAILED
        notification.failed_at = timezone.now()
        notification.http_status = exc.http_status
        notification.error_message = str(exc)[:255]
    else:
        notification.status = LineNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.failed_at = None
        notification.http_status = 200
        notification.error_message = ""
    notification.save()
    return notification


def schedule_order_received(order_id):
    from .notifications import enqueue_order_notifications
    return enqueue_order_notifications(order_id, LineNotification.Type.ORDER_RECEIVED, channels=("line",))


def schedule_payment_request(order_id, *, force=False):
    from .notifications import enqueue_order_notifications
    return enqueue_order_notifications(order_id, LineNotification.Type.PAYMENT_REQUEST, channels=("line",), force=force)


def schedule_payment_confirmed(order_id):
    from .notifications import enqueue_order_notifications
    return enqueue_order_notifications(order_id, LineNotification.Type.PAYMENT_CONFIRMED, channels=("line",))


def schedule_shipping_notification(order_id, *, force=False):
    from .notifications import enqueue_order_notifications
    return enqueue_order_notifications(order_id, LineNotification.Type.ORDER_SHIPPED, channels=("line",), force=force)
