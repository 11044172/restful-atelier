from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Product, ProductCategory


def shop(request):
    categories = ProductCategory.objects.filter(is_active=True).prefetch_related("products")
    products = Product.objects.published().select_related("category").prefetch_related("images")
    return render(request, "catalog/shop.html", {"categories": categories, "products": products, "featured_products": products[:4], "shop_page": True})


def category_detail(request, slug):
    category = get_object_or_404(ProductCategory, slug=slug, is_active=True)
    products = Product.objects.published().filter(category=category).prefetch_related("images")
    subcategory = request.GET.get("subcategory", "").strip()
    if subcategory:
        products = products.filter(subcategory=subcategory)
    ordering = request.GET.get("sort", "featured")
    order_map = {"featured": ("sort_order",), "latest": ("-created_at",), "price-asc": ("price",), "price-desc": ("-price",)}
    if ordering not in order_map:
        raise Http404
    products = products.order_by(*order_map[ordering])
    return render(request, "catalog/category_detail.html", {"category": category, "products": products, "selected_subcategory": subcategory, "selected_sort": ordering, "shop_page": True})


def product_detail(request, slug):
    product = get_object_or_404(Product.objects.published().select_related("category").prefetch_related("images", "specifications"), slug=slug)
    related = Product.objects.published().exclude(pk=product.pk).select_related("category").prefetch_related("images").order_by(models_case_category(product.category_id), "sort_order")[:4]
    return render(request, "catalog/product_detail.html", {"product": product, "related": related, "shop_page": True})


def models_case_category(category_id):
    from django.db.models import Case, IntegerField, Value, When
    return Case(When(category_id=category_id, then=Value(0)), default=Value(1), output_field=IntegerField())


def search(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.published().select_related("category").prefetch_related("images", "specifications")
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(short_description__icontains=query) | Q(description__icontains=query)
            | Q(category__name__icontains=query) | Q(category__english_name__icontains=query)
            | Q(specifications__label__icontains=query) | Q(specifications__value__icontains=query)
        ).distinct()
    return render(request, "catalog/search.html", {"query": query, "products": products, "shop_page": True})


def favorites(request):
    products = Product.objects.published().select_related("category").prefetch_related("images")
    return render(request, "catalog/favorites.html", {"products": products, "shop_page": True})
