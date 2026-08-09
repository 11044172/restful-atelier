from django.contrib import admin
from django.utils import timezone

from .models import Inquiry, InquiryCategory


@admin.register(InquiryCategory)
class InquiryCategoryAdmin(admin.ModelAdmin):
    list_display = ("display_name", "recipient_email", "active", "sort_order")
    list_editable = ("recipient_email", "active", "sort_order")
    prepopulated_fields = {"slug": ("display_name",)}


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "email", "phone", "status", "created_at", "handled_at")
    list_filter = ("status", "category", "newsletter_opt_in", "created_at")
    search_fields = ("name", "email", "phone", "message", "project_location")
    readonly_fields = ("created_at", "privacy_agreed", "newsletter_opt_in")
    date_hierarchy = "created_at"

    def save_model(self, request, obj, form, change):
        if obj.status == Inquiry.Status.COMPLETED and not obj.handled_at:
            obj.handled_at = timezone.now()
        super().save_model(request, obj, form, change)
