import hashlib
import json
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import RateLimitBucket


def rate_limit_exceeded(request, scope, limit, window=600):
    identity = getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR", "unknown")
    key_hash = hashlib.sha256(f"{settings.SECRET_KEY}:{identity}".encode()).hexdigest()
    now = timezone.now()
    epoch = int(now.timestamp())
    window_started_at = now - timedelta(seconds=epoch % window, microseconds=now.microsecond)
    with transaction.atomic():
        try:
            bucket, created = RateLimitBucket.objects.select_for_update().get_or_create(
                scope=scope, key_hash=key_hash, window_started_at=window_started_at,
                defaults={"count": 1},
            )
        except IntegrityError:
            bucket = RateLimitBucket.objects.select_for_update().get(scope=scope, key_hash=key_hash, window_started_at=window_started_at)
            created = False
        if created:
            return False
        if bucket.count >= limit:
            return True
        RateLimitBucket.objects.filter(pk=bucket.pk).update(count=F("count") + 1)
    return False


def verify_turnstile(request):
    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        return True
    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return False
    payload = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": getattr(request, "client_ip", None) or ""}).encode()
    try:
        with urllib.request.urlopen("https://challenges.cloudflare.com/turnstile/v0/siteverify", data=payload, timeout=5) as response:
            return bool(json.loads(response.read()).get("success"))
    except Exception:
        return False
