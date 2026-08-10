from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from urllib.parse import urlsplit

from content.models import PolicyPage
from core.models import SiteSettings
from core.line_config import line_settings_configured
from orders.models import PaymentMethod
from orders.payment_providers import get_provider


class Command(BaseCommand):
    help = "檢查正式上線所需的設定。"

    def handle(self, *args, **options):
        site = SiteSettings.load()
        failures = []
        warnings = []
        if settings.DEBUG:
            failures.append("DEBUG 目前為啟用狀態")
        canonical = urlsplit(settings.CANONICAL_ORIGIN)
        if canonical.scheme != "https" or not canonical.hostname or canonical.path not in {"", "/"}:
            failures.append("CANONICAL_ORIGIN 必須是不含路徑的 HTTPS origin")
        elif canonical.hostname not in settings.ALLOWED_HOSTS:
            failures.append("CANONICAL_ORIGIN 的 host 未列入 ALLOWED_HOSTS")
        if settings.CANONICAL_ORIGIN not in settings.CSRF_TRUSTED_ORIGINS:
            failures.append("CANONICAL_ORIGIN 未列入 CSRF_TRUSTED_ORIGINS")
        line_settings = {
            "LINE_LOGIN_CHANNEL_ID": settings.LINE_LOGIN_CHANNEL_ID,
            "LINE_LOGIN_CHANNEL_SECRET": settings.LINE_LOGIN_CHANNEL_SECRET,
            "LINE_LOGIN_CALLBACK_URL": settings.LINE_LOGIN_CALLBACK_URL,
            "LINE_MESSAGING_CHANNEL_ACCESS_TOKEN": settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN,
            "LINE_MESSAGING_CHANNEL_SECRET": settings.LINE_MESSAGING_CHANNEL_SECRET,
            "LINE_OFFICIAL_ACCOUNT_BASIC_ID": settings.LINE_OFFICIAL_ACCOUNT_BASIC_ID,
        }
        for name, value in line_settings.items():
            if not value:
                failures.append(f"{name} 尚未設定")
        if not site.line_url:
            failures.append("網站設定中尚未填寫 LINE 加好友網址")
        if settings.LINE_LOGIN_CALLBACK_URL and not settings.LINE_LOGIN_CALLBACK_URL.startswith("https://"):
            failures.append("LINE_LOGIN_CALLBACK_URL 不是 HTTPS 網址")
        expected_callback = f"{settings.CANONICAL_ORIGIN}/auth/line/callback/"
        if settings.LINE_LOGIN_CALLBACK_URL and settings.LINE_LOGIN_CALLBACK_URL != expected_callback:
            failures.append(f"LINE_LOGIN_CALLBACK_URL 必須與 {expected_callback} 一致")
        if not settings.CANONICAL_ORIGIN.startswith("https://"):
            failures.append("CANONICAL_ORIGIN 不是 HTTPS 網址")
        configured_payment_methods = []
        for method in PaymentMethod.objects.filter(enabled=True):
            if method.code == PaymentMethod.Method.BANK_TRANSFER:
                configured = all((site.bank_name, site.bank_code, site.bank_account_number, site.bank_account_name))
            elif method.code == PaymentMethod.Method.TAIWAN_PAY:
                configured = bool(method.qr_image or site.taiwan_pay_qr)
            else:
                configured = bool(method.provider and get_provider(method.provider) is not None)
            if configured:
                configured_payment_methods.append(method.code)
            else:
                failures.append(f"已啟用的付款方式設定不完整：{method.display_name}")
        if not configured_payment_methods:
            failures.append("沒有可用且設定完整的付款方式")
        if not (site.order_notification_email or settings.ORDER_NOTIFICATION_EMAIL):
            failures.append("訂單通知 Email 尚未設定")
        if not settings.SEO_INDEX_ENABLED:
            warnings.append("SEO_INDEX_ENABLED 目前為停用狀態")
        if not settings.STORAGES["default"]["BACKEND"].startswith("storages.backends.s3"):
            failures.append("S3/R2 媒體儲存尚未設定")
        else:
            storage_options = settings.STORAGES["default"].get("OPTIONS", {})
            if not storage_options.get("custom_domain") and not storage_options.get("querystring_auth"):
                failures.append("R2 媒體沒有公開網域，也未啟用簽署網址，瀏覽器將無法讀取圖片")
        required = {"privacy-policy", "shopping-guide", "payment-methods", "shipping-policy", "preorder-information", "returns-policy"}
        published = set(PolicyPage.objects.filter(slug__in=required, published=True).exclude(body="").values_list("slug", flat=True))
        for slug in sorted(required - published):
            failures.append(f"必要的政策頁面尚未公開或未填寫內文：{slug}")
        if not site.checkout_enabled:
            warnings.append("checkout_enabled 目前為停用狀態")
        elif not line_settings_configured():
            failures.append("LINE 設定不完整，無法啟用 checkout_enabled")
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"WARN: {warning}"))
        if failures:
            for failure in failures:
                self.stdout.write(self.style.ERROR(f"NG: {failure}"))
            raise CommandError(f"正式上線前仍有 {len(failures)} 項未完成設定。")
        self.stdout.write(self.style.SUCCESS("已完成正式上線必要設定的檢查。"))
