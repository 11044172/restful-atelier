from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from urllib.parse import urlsplit

from content.models import PolicyPage
from core.models import SiteSettings
from core.line_config import line_settings_configured
from orders.models import PaymentMethod
from orders.payment_providers import get_provider


class Command(BaseCommand):
    help = "本番公開に必要な設定を確認します。"

    def handle(self, *args, **options):
        site = SiteSettings.load()
        failures = []
        warnings = []
        if settings.DEBUG:
            failures.append("DEBUG が有効です")
        canonical = urlsplit(settings.CANONICAL_ORIGIN)
        if canonical.scheme != "https" or not canonical.hostname or canonical.path not in {"", "/"}:
            failures.append("CANONICAL_ORIGIN はパスを含まないHTTPS originで設定してください")
        elif canonical.hostname not in settings.ALLOWED_HOSTS:
            failures.append("CANONICAL_ORIGINのhostがALLOWED_HOSTSに含まれていません")
        if settings.CANONICAL_ORIGIN not in settings.CSRF_TRUSTED_ORIGINS:
            failures.append("CANONICAL_ORIGINがCSRF_TRUSTED_ORIGINSに含まれていません")
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
                failures.append(f"{name} が未設定です")
        if not site.line_url:
            failures.append("Site SettingsのLINE友だち追加URLが未設定です")
        if settings.LINE_LOGIN_CALLBACK_URL and not settings.LINE_LOGIN_CALLBACK_URL.startswith("https://"):
            failures.append("LINE_LOGIN_CALLBACK_URL がHTTPSではありません")
        expected_callback = f"{settings.CANONICAL_ORIGIN}/auth/line/callback/"
        if settings.LINE_LOGIN_CALLBACK_URL and settings.LINE_LOGIN_CALLBACK_URL != expected_callback:
            failures.append(f"LINE_LOGIN_CALLBACK_URL は {expected_callback} と一致させてください")
        if not settings.CANONICAL_ORIGIN.startswith("https://"):
            failures.append("CANONICAL_ORIGIN がHTTPSではありません")
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
                failures.append(f"有効なPayment Methodの設定が不完全です: {method.display_name}")
        if not configured_payment_methods:
            failures.append("利用可能かつ設定済みのPayment Methodがありません")
        if not (site.order_notification_email or settings.ORDER_NOTIFICATION_EMAIL):
            failures.append("注文通知 Email が未設定です")
        if not settings.SEO_INDEX_ENABLED:
            warnings.append("SEO_INDEX_ENABLED が無効です")
        if not settings.STORAGES["default"]["BACKEND"].startswith("storages.backends.s3"):
            failures.append("S3/R2 メディアストレージが未設定です")
        required = {"privacy-policy", "shopping-guide", "payment-methods", "shipping-policy", "preorder-information", "returns-policy"}
        published = set(PolicyPage.objects.filter(slug__in=required, published=True).exclude(body="").values_list("slug", flat=True))
        for slug in sorted(required - published):
            failures.append(f"必須ポリシーが未公開または本文未入力です: {slug}")
        if not site.checkout_enabled:
            warnings.append("checkout_enabled が無効です")
        elif not line_settings_configured():
            failures.append("LINE設定が不完全なためcheckout_enabledを有効にできません")
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"WARN: {warning}"))
        if failures:
            for failure in failures:
                self.stdout.write(self.style.ERROR(f"NG: {failure}"))
            raise CommandError(f"本番公開準備に {len(failures)} 件の未完了項目があります。")
        self.stdout.write(self.style.SUCCESS("本番公開の必須設定を確認しました。"))
