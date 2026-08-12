import ipaddress
import logging
import time

from django.conf import settings
from django.http import HttpResponsePermanentRedirect, JsonResponse

logger = logging.getLogger("restfull.requests")


class ClientIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote = request.META.get("REMOTE_ADDR", "")
        trusted = remote in settings.TRUSTED_PROXY_IPS
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "") if trusted else ""
        candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
        client_ip = remote
        for candidate in reversed(candidates):
            if candidate in settings.TRUSTED_PROXY_IPS:
                continue
            try:
                client_ip = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            break
        try:
            request.client_ip = str(ipaddress.ip_address(client_ip))
        except ValueError:
            request.client_ip = None
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if request.path.startswith(("/pay/", "/shop/cancel/")):
            # Keep signed payment/cancellation URLs out of cross-origin
            # referrers, while still allowing Django's HTTPS CSRF fallback to
            # validate same-origin form submissions in WebViews that omit the
            # Origin header (including LINE's in-app browser).
            response["Referrer-Policy"] = "same-origin"
        response.setdefault(
            "Content-Security-Policy-Report-Only",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self' https://challenges.cloudflare.com; "
            "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; connect-src 'self' https://challenges.cloudflare.com; "
            "frame-src https://challenges.cloudflare.com",
        )
        return response


class AdminLoginRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.rstrip("/") == "/admin/login":
            from inquiries.antispam import rate_limit_exceeded
            if rate_limit_exceeded(request, "admin-login", settings.ADMIN_LOGIN_RATE_LIMIT, window=900):
                return JsonResponse({"detail": "登入嘗試過多，請於15分鐘後再試。"}, status=429)
        return self.get_response(request)


class RequestObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        if response.status_code >= 500 or duration_ms >= settings.SLOW_REQUEST_MS:
            logger.warning(
                "http_request",
                extra={"method": request.method, "path": request.path, "status": response.status_code, "duration_ms": duration_ms},
            )
        return response


class CanonicalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower()
        if not settings.DEBUG and host in settings.CANONICAL_REDIRECT_HOSTS:
            return HttpResponsePermanentRedirect(f"{settings.CANONICAL_ORIGIN}{request.get_full_path()}")
        return self.get_response(request)
