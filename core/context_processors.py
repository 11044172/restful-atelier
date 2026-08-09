from django.conf import settings

from catalog.models import ProductCategory
from .models import SiteSettings


def site_context(request):
    from content.models import PolicyPage

    site_settings = SiteSettings.load()
    return {
        "site_settings": site_settings,
        "nav_categories": ProductCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
        "policy_pages": PolicyPage.objects.filter(published=True).order_by("sort_order", "title"),
        "canonical_origin": settings.CANONICAL_ORIGIN,
        "seo_index_enabled": settings.SEO_INDEX_ENABLED,
    }
