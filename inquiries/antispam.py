import json
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache


def rate_limit_exceeded(request, scope, limit, window=600):
    identity = request.META.get("REMOTE_ADDR", "unknown")
    key = f"rate:{scope}:{identity}"
    current = cache.get(key, 0)
    if current >= limit:
        return True
    if current == 0:
        cache.set(key, 1, window)
    else:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, window)
    return False


def verify_turnstile(request):
    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        return True
    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return False
    payload = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": request.META.get("REMOTE_ADDR", "")}).encode()
    try:
        with urllib.request.urlopen("https://challenges.cloudflare.com/turnstile/v0/siteverify", data=payload, timeout=5) as response:
            return bool(json.loads(response.read()).get("success"))
    except Exception:
        return False
