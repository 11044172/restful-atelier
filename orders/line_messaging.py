import json
import logging
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import LineNotification, Order
from .payment_links import make_payment_url


logger = logging.getLogger(__name__)
PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineMessagingError(Exception):
    def __init__(self, message, *, http_status=None):
        super().__init__(message)
        self.http_status = http_status


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


def _flex_message(*, alt_text, title, rows, note=None, button=None):
    body = [
        {"type": "text", "text": "Rfull", "size": "xs", "color": "#8A8178"},
        {"type": "text", "text": title, "size": "xl", "weight": "bold", "margin": "md", "color": "#222222"},
        {"type": "separator", "margin": "xl", "color": "#E7E2DC"},
        {"type": "box", "layout": "vertical", "margin": "lg", "contents": rows},
    ]
    if note:
        body.append({"type": "text", "text": note, "size": "sm", "color": "#666666", "wrap": True, "margin": "xl"})
    bubble = {"type": "bubble", "styles": {"body": {"backgroundColor": "#FAF9F7"}}, "body": {"type": "box", "layout": "vertical", "paddingAll": "24px", "contents": body}}
    if button:
        bubble["footer"] = {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [{"type": "button", "height": "sm", "style": "primary", "color": "#4C5148", "action": {"type": "uri", "label": button[0], "uri": button[1]}}]}
    return {"type": "flex", "altText": alt_text, "contents": bubble}


def build_order_received(order):
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 已收到，正在確認運費。",
        title="感謝您的訂購",
        rows=[_row("訂單編號", order.public_number), _row("商品金額", money(order.subtotal), emphasis=True)],
        note="我們已收到您的訂單，正在確認配送方式與運費。確認完成後，會透過 LINE 傳送最終金額及付款連結。",
    )


def build_payment_request(order):
    return _flex_message(
        alt_text=f"訂單 {order.public_number} 運費已確認，付款金額 {money(order.final_total)}。",
        title="運費已確認",
        rows=[
            _row("訂單編號", order.public_number), _row("商品金額", money(order.subtotal)),
            _row("運費", money(order.shipping_fee)), _row("付款金額", money(order.final_total), emphasis=True),
        ],
        note="請由下方安全連結前往 Rfull 付款頁面。",
        button=("前往付款", make_payment_url(order)),
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
        button=("查看配送狀態", order.tracking_url) if order.tracking_url else None,
    )


BUILDERS = {
    LineNotification.Type.ORDER_RECEIVED: build_order_received,
    LineNotification.Type.PAYMENT_REQUEST: build_payment_request,
    LineNotification.Type.PAYMENT_CONFIRMED: build_payment_confirmed,
    LineNotification.Type.ORDER_SHIPPED: build_shipping_notification,
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
        raise LineMessagingError("LINE Messaging API rejected the notification.", http_status=exc.code) from exc
    except (URLError, TimeoutError) as exc:
        raise LineMessagingError("LINE Messaging API timed out or was unavailable.") from exc


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
    return send_order_notification(order_id, LineNotification.Type.ORDER_RECEIVED)


def schedule_payment_request(order_id, *, force=False):
    return send_order_notification(order_id, LineNotification.Type.PAYMENT_REQUEST, force=force)


def schedule_payment_confirmed(order_id):
    return send_order_notification(order_id, LineNotification.Type.PAYMENT_CONFIRMED)


def schedule_shipping_notification(order_id, *, force=False):
    return send_order_notification(order_id, LineNotification.Type.ORDER_SHIPPED, force=force)
