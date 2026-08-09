from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from catalog.models import Product, ProductCategory
from .models import InteriorProject, PolicyPage, Publication


class StaticSitemap(Sitemap):
    priority = 0.7
    changefreq = "monthly"

    def items(self):
        return ["core:home", "content:project_list", "content:publication_list", "core:about", "inquiries:contact", "catalog:shop", "catalog:search"]

    def location(self, item):
        return reverse(item)


class ProductSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Product.objects.published()

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return ProductCategory.objects.filter(is_active=True)


class ProjectSitemap(Sitemap):
    changefreq = "monthly"

    def items(self):
        return InteriorProject.objects.filter(published=True)

    def lastmod(self, obj):
        return obj.updated_at


class PublicationSitemap(Sitemap):
    changefreq = "monthly"

    def items(self):
        return Publication.objects.filter(published=True)

    def lastmod(self, obj):
        return obj.updated_at


class PolicySitemap(Sitemap):
    changefreq = "monthly"

    def items(self):
        return PolicyPage.objects.filter(published=True)

    def lastmod(self, obj):
        return obj.updated_at


sitemaps = {
    "static": StaticSitemap,
    "products": ProductSitemap,
    "categories": CategorySitemap,
    "projects": ProjectSitemap,
    "publications": PublicationSitemap,
    "policies": PolicySitemap,
}
