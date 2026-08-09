from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower()
        if not settings.DEBUG and host in settings.CANONICAL_REDIRECT_HOSTS:
            return HttpResponsePermanentRedirect(f"{settings.CANONICAL_ORIGIN}{request.get_full_path()}")
        return self.get_response(request)
