from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("works/", views.project_list, name="project_list"),
    path("works/<slug:slug>/", views.project_detail, name="project_detail"),
    path("publications/", views.publication_list, name="publication_list"),
    path("publications/<slug:slug>/", views.publication_detail, name="publication_detail"),
    path("policies/<slug:slug>/", views.policy, name="policy"),
]
