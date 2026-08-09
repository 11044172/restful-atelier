from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.shop, name="shop"),
    path("category/<slug:slug>/", views.category_detail, name="category"),
    path("products/<slug:slug>/", views.product_detail, name="product"),
    path("search/", views.search, name="search"),
    path("favorites/", views.favorites, name="favorites"),
]
