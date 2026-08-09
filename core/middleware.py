from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DEBUG and request.get_host().split(":", 1)[0].lower() == "www.restfull.com":
            return HttpResponsePermanentRedirect(f"https://restfull.com{request.get_full_path()}")
        return self.get_response(request)
