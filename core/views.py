from django.db import connection
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.db.migrations.executor import MigrationExecutor


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


def readiness(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        pending = cache.get("readiness:pending-migration-count")
        if pending is None:
            executor = MigrationExecutor(connection)
            pending = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
            cache.set("readiness:pending-migration-count", pending, 30)
    except Exception:
        response = JsonResponse({"status": "unavailable"}, status=503)
        response["Cache-Control"] = "no-store"
        return response
    status = "ready" if not pending else "not_ready"
    response = JsonResponse({"status": status}, status=200 if not pending else 503)
    response["Cache-Control"] = "no-store"
    return response


def robots_txt(request):
    from django.conf import settings

    if settings.SEO_INDEX_ENABLED:
        body = f"User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /shop/cart/\nDisallow: /shop/checkout/\nSitemap: {settings.CANONICAL_ORIGIN}/sitemap.xml\n"
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain")


def custom_404(request, exception):
    return render(request, "core/404.html", status=404)
