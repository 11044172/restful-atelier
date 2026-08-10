from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User

from .admin_site import backoffice_site
from .models import SiteSettings


backoffice_site.register(User, UserAdmin)
backoffice_site.register(Group, GroupAdmin)


@admin.register(SiteSettings, site=backoffice_site)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("品牌資訊", {"fields": ("brand_name", "public_name", "english_name", "meta_description")}),
        ("聯絡方式", {"fields": ("phone_primary", "phone_secondary", "general_email", "design_email", "media_email", "business_email", "order_notification_email")}),
        ("SNS / LINE", {"fields": ("facebook_url", "instagram_url", "line_official_url", "line_add_friend_url", "line_service_label", "line_service_hours", "line_after_hours_note")}),
        ("銀行與 Taiwan Pay", {"fields": ("bank_name", "bank_code", "bank_account_number", "bank_account_name", "taiwan_pay_qr")}),
        ("結帳與訂單", {"fields": ("checkout_enabled",)}),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
