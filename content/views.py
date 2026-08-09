from django.shortcuts import get_object_or_404, render

from .models import InteriorProject, PolicyPage, Publication


def project_list(request):
    projects = InteriorProject.objects.filter(published=True).prefetch_related("images")
    project_type = request.GET.get("type", "").strip()
    if project_type:
        projects = projects.filter(project_type=project_type)
    types = InteriorProject.objects.filter(published=True).exclude(project_type="").values_list("project_type", flat=True).distinct()
    return render(request, "content/project_list.html", {"projects": projects, "project_types": types, "selected_type": project_type})


def project_detail(request, slug):
    project = get_object_or_404(InteriorProject.objects.filter(published=True).prefetch_related("images"), slug=slug)
    ordered = list(InteriorProject.objects.filter(published=True).order_by("sort_order", "-year"))
    index = ordered.index(project)
    previous = ordered[(index - 1) % len(ordered)] if len(ordered) > 1 else project
    next_project = ordered[(index + 1) % len(ordered)] if len(ordered) > 1 else project
    return render(request, "content/project_detail.html", {"project": project, "previous_project": previous, "next_project": next_project})


def publication_list(request):
    return render(request, "content/publication_list.html", {"publications": Publication.objects.filter(published=True)})


def publication_detail(request, slug):
    publication = get_object_or_404(Publication, slug=slug, published=True)
    return render(request, "content/publication_detail.html", {"publication": publication})


def policy(request, slug):
    page = get_object_or_404(PolicyPage, slug=slug, published=True)
    return render(request, "content/policy.html", {"page": page})
