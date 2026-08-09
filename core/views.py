from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render


def home(request):
    from catalog.models import Product
    from content.models import InteriorProject, Publication

    return render(request, "core/home.html", {
        "products": Product.objects.published().select_related("category").prefetch_related("images")[:4],
        "featured_project": InteriorProject.objects.filter(published=True).order_by("sort_order", "-year").first(),
        "publications": Publication.objects.filter(published=True).order_by("sort_order", "-published_date")[:4],
    })


def about(request):
    return render(request, "core/about.html")


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def robots_txt(request):
    from django.conf import settings

    if settings.SEO_INDEX_ENABLED:
        body = f"User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /shop/cart/\nDisallow: /shop/checkout/\nSitemap: {settings.CANONICAL_ORIGIN}/sitemap.xml\n"
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain")


def custom_404(request, exception):
    return render(request, "core/404.html", status=404)
