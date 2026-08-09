import base64
import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LineCustomer, LineWebhookEvent


def valid_signature(raw_body, signature):
    if not signature or not settings.LINE_MESSAGING_CHANNEL_SECRET:
        return False
    expected = base64.b64encode(
        hmac.new(settings.LINE_MESSAGING_CHANNEL_SECRET.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature)


def parse_payload(raw_body):
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON") from exc
    if not isinstance(payload.get("events", []), list):
        raise ValueError("Invalid events")
    return payload


@transaction.atomic
def process_event(event):
    event_type = event.get("type", "")
    event_id = event.get("webhookEventId")
    if not event_id:
        event_id = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    _, created = LineWebhookEvent.objects.get_or_create(webhook_event_id=event_id, defaults={"event_type": event_type[:40]})
    if not created:
        return False
    if event_type not in {"follow", "unfollow"}:
        return True
    line_user_id = event.get("source", {}).get("userId")
    if not line_user_id:
        return True
    now = timezone.now()
    customer, _ = LineCustomer.objects.get_or_create(
        line_user_id=line_user_id,
        defaults={"display_name": "LINE顧客"},
    )
    if event_type == "follow":
        customer.is_friend = True
        customer.is_blocked = False
        customer.followed_at = now
    else:
        customer.is_friend = False
        customer.is_blocked = True
        customer.unfollowed_at = now
    customer.save()
    return True
