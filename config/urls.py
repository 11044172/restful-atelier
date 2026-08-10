from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from content.sitemaps import sitemaps
from core.admin_site import backoffice_site
from core import views as core_views
from orders import views as order_views


urlpatterns = [
    path("admin/", backoffice_site.urls),
    path("healthz/", core_views.healthz, name="healthz"),
    path("auth/line/login/", order_views.line_login_start, name="line_login_start"),
    path("auth/line/callback/", order_views.line_login_callback, name="line_login_callback"),
    path("webhooks/line/messaging/", order_views.line_messaging_webhook, name="line_messaging_webhook"),
    path("pay/<str:token>/", order_views.payment, name="payment"),
    path("robots.txt", core_views.robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("", include("core.urls")),
    path("", include("content.urls")),
    path("", include("inquiries.urls")),
    path("shop/", include("catalog.urls")),
    path("shop/", include("orders.urls")),
]

handler404 = "core.views.custom_404"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
