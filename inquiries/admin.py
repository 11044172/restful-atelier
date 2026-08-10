from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from core.admin_site import backoffice_site

from .models import Inquiry, InquiryCategory


@admin.register(InquiryCategory, site=backoffice_site)
class InquiryCategoryAdmin(admin.ModelAdmin):
    list_display = ("display_name", "recipient_email", "active", "sort_order")
    list_editable = ("recipient_email", "active", "sort_order")
    prepopulated_fields = {"slug": ("display_name",)}


@admin.register(Inquiry, site=backoffice_site)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "email", "phone", "status_badge", "created_at", "handled_at")
    list_filter = ("status", "category", "newsletter_opt_in", "created_at")
    search_fields = ("name", "email", "phone", "message", "project_location")
    readonly_fields = ("created_at", "privacy_agreed", "newsletter_opt_in")
    date_hierarchy = "created_at"
    list_per_page = 25

    @admin.display(description="ステータス", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="status-pill status-{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    def save_model(self, request, obj, form, change):
        if obj.status == Inquiry.Status.COMPLETED and not obj.handled_at:
            obj.handled_at = timezone.now()
        super().save_model(request, obj, form, change)
