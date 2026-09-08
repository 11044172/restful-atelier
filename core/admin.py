from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.utils.html import format_html

from .admin_site import backoffice_site
from .models import PrivacyRequest, SiteSettings


backoffice_site.register(User, UserAdmin)
backoffice_site.register(Group, GroupAdmin)


@admin.register(SiteSettings, site=backoffice_site)
class SiteSettingsAdmin(admin.ModelAdmin):
    readonly_fields = (
        "brand_logo_preview",
        "shop_logo_preview",
        "home_hero_preview",
        "shop_hero_preview",
        "shop_story_preview",
        "about_image_preview",
        "og_image_preview",
    )
    fieldsets = (
        ("品牌資訊", {"fields": ("brand_name", "public_name", "english_name", "header_tagline", "brand_logo_preview", "brand_logo", "brand_logo_alt", "shop_logo_preview", "shop_logo")}),
        ("首頁", {"fields": ("home_hero_preview", "home_hero_image", "home_hero_alt", "home_hero_position", "home_hero_eyebrow", "home_hero_title", "home_hero_title_accent", "home_hero_description", "home_shop_cta_label", "home_works_cta_label", "home_featured_title", "home_featured_aside", "home_brand_quote", "home_brand_message", "home_brand_cta_label")}),
        ("室內設計", {"fields": ("works_intro_title", "works_intro_statement", "works_consultation_text", "works_consultation_cta")}),
        ("出版品", {"fields": ("publications_intro_text", "publications_headline", "publications_note_quote", "publications_note_text")}),
        ("購物首頁", {"fields": ("shop_hero_preview", "shop_hero_image", "shop_hero_alt", "shop_hero_position", "shop_hero_eyebrow", "shop_hero_title", "shop_hero_description", "shop_featured_title", "shop_featured_aside", "shop_story_preview", "shop_story_image", "shop_story_alt", "shop_story_position", "shop_story_title", "shop_story_quote", "shop_story_text")}),
        ("關於", {"fields": ("about_image_preview", "about_image", "about_image_alt", "about_image_position", "about_title", "about_intro", "about_timeline_heading", "about_timeline_text")}),
        ("聯絡頁", {"fields": ("contact_title", "contact_lead")}),
        ("頁尾", {"fields": ("footer_message", "footer_copyright")}),
        ("聯絡方式", {"fields": ("phone_primary", "phone_secondary", "general_email", "design_email", "media_email", "business_email", "order_notification_email")}),
        ("商家資訊", {"fields": ("business_legal_name", "business_representative", "business_address", "returns_contact", "business_hours", "privacy_contact_email")}),
        ("SNS / LINE", {"fields": ("facebook_url", "instagram_url", "line_official_url", "line_add_friend_url", "line_service_label", "line_service_hours", "line_after_hours_note")}),
        ("銀行與 Taiwan Pay", {"fields": ("bank_name", "bank_code", "bank_account_number", "bank_account_name", "taiwan_pay_qr")}),
        ("結帳・個資", {"fields": ("checkout_enabled", "customer_data_retention_days")}),
        ("SEO", {"fields": ("meta_description", "og_image_preview", "default_og_image")}),
    )

    @staticmethod
    def _image_preview(obj, field_name):
        image = getattr(obj, field_name, None) if obj else None
        if not image:
            return "未設定（公開頁面將使用目前的預設圖片／顯示）"
        return format_html(
            '<img src="{}" style="width:240px;max-height:160px;object-fit:cover;border:1px solid #ddd" alt="">',
            image.url,
        )

    @admin.display(description="目前網站標誌")
    def brand_logo_preview(self, obj):
        return self._image_preview(obj, "brand_logo")

    @admin.display(description="目前購物網站標誌")
    def shop_logo_preview(self, obj):
        return self._image_preview(obj, "shop_logo")

    @admin.display(description="目前首頁 Hero")
    def home_hero_preview(self, obj):
        return self._image_preview(obj, "home_hero_image")

    @admin.display(description="目前購物首頁 Hero")
    def shop_hero_preview(self, obj):
        return self._image_preview(obj, "shop_hero_image")

    @admin.display(description="目前季節文章圖片")
    def shop_story_preview(self, obj):
        return self._image_preview(obj, "shop_story_image")

    @admin.display(description="目前關於頁圖片")
    def about_image_preview(self, obj):
        return self._image_preview(obj, "about_image")

    @admin.display(description="目前 OGP 圖片")
    def og_image_preview(self, obj):
        return self._image_preview(obj, "default_og_image")

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrivacyRequest, site=backoffice_site)
class PrivacyRequestAdmin(admin.ModelAdmin):
    list_display = ("email", "request_type", "order_reference", "status", "created_at", "completed_at")
    list_filter = ("request_type", "status", "created_at")
    search_fields = ("email", "order_reference")
    readonly_fields = ("created_at", "updated_at")
