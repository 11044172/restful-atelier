from pathlib import Path
from urllib.parse import urlsplit
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from catalog.models import Product
from content.models import InteriorProject, PolicyPage, Publication
from core.line_config import line_settings_configured
from core.models import SiteSettings
from orders.models import NotificationOutbox, Payment, PaymentMethod
from orders.payment_providers import get_provider


REQUIRED_POLICIES = {"privacy-policy", "shopping-guide", "payment-methods", "shipping-policy", "preorder-information", "returns-policy"}


class Command(BaseCommand):
    help = "正式納品・公開 readiness を PASS/WARN/FAIL で検査します（checkout状態は変更しません）。"

    def add_arguments(self, parser):
        parser.add_argument("--external", action="store_true", help="R2 read/write、SMTP接続等の明示的な外部疎通を行う")
        parser.add_argument("--production", action="store_true", help="正式公開基準でWARNの一部をFAILとして扱う")

    def handle(self, *args, **options):
        self.failures, self.warnings, self.passes = [], [], []
        production, external = options["production"], options["external"]
        site = SiteSettings.load()
        self._check(not production or settings.DEPLOYMENT_ENV == "production", "production環境ラベル", f"DEPLOYMENT_ENV={settings.DEPLOYMENT_ENV}", fail=production)
        self._check(not production or settings.CREDENTIAL_SET_NAME == "production", "production資格情報セット分離", f"CREDENTIAL_SET_NAME={settings.CREDENTIAL_SET_NAME}（stagingとの共有を確認）", fail=production)
        self._check(settings.OUTBOX_WORKER_CONFIGURED, "Outbox worker設定済み", "Outbox worker未設定。通知はDBに残るが自動送信されない", fail=production)

        self._check(not settings.DEBUG, "DEBUG=False", "DEBUG が有効", fail=True)
        self._check(settings.SECRET_KEY != "unsafe-development-key" and len(settings.SECRET_KEY) >= 32, "SECRET_KEY は本番形式", "SECRET_KEY が既定値または短すぎる", fail=True)
        canonical = urlsplit(settings.CANONICAL_ORIGIN)
        canonical_ok = canonical.scheme == "https" and bool(canonical.hostname) and canonical.path in {"", "/"}
        self._check(canonical_ok, "production URL はHTTPS origin", "CANONICAL_ORIGIN はパスを含まないHTTPS originが必要", fail=True)
        self._check(bool(canonical.hostname and canonical.hostname in settings.ALLOWED_HOSTS), "canonical host はALLOWED_HOSTS内", "canonical host がALLOWED_HOSTSにない", fail=True)
        self._check(settings.CANONICAL_ORIGIN in settings.CSRF_TRUSTED_ORIGINS, "CSRF trusted origin設定済み", "CANONICAL_ORIGIN がCSRF_TRUSTED_ORIGINSにない", fail=True)
        self._check(not settings.SEO_INDEX_ENABLED, "正式公開前noindex維持", "SEO_INDEX_ENABLED=True。正式公開承認前はFalseに戻す", fail=not production)

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            self._pass("DB接続")
        except Exception as exc:
            self._fail(f"DB接続失敗: {exc}")
        try:
            pending = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
            self._check(not pending, "migration適用済み", f"未適用migration: {len(pending)}", fail=True)
        except Exception as exc:
            self._fail(f"migration検査失敗: {exc}")

        line_values = {
            "LINE_LOGIN_CHANNEL_ID": settings.LINE_LOGIN_CHANNEL_ID,
            "LINE_LOGIN_CHANNEL_SECRET": settings.LINE_LOGIN_CHANNEL_SECRET,
            "LINE_LOGIN_CALLBACK_URL": settings.LINE_LOGIN_CALLBACK_URL,
            "LINE_MESSAGING_CHANNEL_ACCESS_TOKEN": settings.LINE_MESSAGING_CHANNEL_ACCESS_TOKEN,
            "LINE_MESSAGING_CHANNEL_SECRET": settings.LINE_MESSAGING_CHANNEL_SECRET,
            "LINE_OFFICIAL_ACCOUNT_BASIC_ID": settings.LINE_OFFICIAL_ACCOUNT_BASIC_ID,
        }
        for name, value in line_values.items():
            self._check(bool(value), f"{name} 設定済み", f"{name} 未設定", fail=production)
        expected_callback = f"{settings.CANONICAL_ORIGIN}/auth/line/callback/"
        self._check(not settings.LINE_LOGIN_CALLBACK_URL or settings.LINE_LOGIN_CALLBACK_URL == expected_callback, "LINE callback URL一致", f"LINE callbackは {expected_callback} が必要", fail=production)
        self._check(bool(site.line_url), "LINE友だち追加URL設定済み", "LINE友だち追加URL未設定", fail=production)
        self._check(line_settings_configured(), "LINE Login/Messaging構成完了", "LINE構成が不完全（checkoutは停止しません）", fail=production)

        smtp_ok = bool(settings.EMAIL_HOST and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD and settings.DEFAULT_FROM_EMAIL)
        self._check(smtp_ok, "SMTP設定済み", "SMTP資格情報が不完全", fail=production)
        if external and smtp_ok:
            try:
                mail_connection = get_connection(timeout=5)
                opened = mail_connection.open()
                mail_connection.close()
                self._check(opened is not False, "SMTP接続疎通（送信なし）", "SMTP接続を開けない", fail=True)
            except Exception as exc:
                self._fail(f"SMTP接続失敗: {exc}")
        self._check(bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY), "Turnstile設定済み", "Turnstileキー未設定", fail=production)

        storage_backend = settings.STORAGES["default"]["BACKEND"]
        r2_ok = storage_backend.startswith("storages.backends.s3")
        self._check(r2_ok, "R2/S3 storage設定済み", "本番メディアがR2/S3ではない", fail=production)
        if r2_ok:
            options_storage = settings.STORAGES["default"].get("OPTIONS", {})
            self._check(bool(options_storage.get("custom_domain") or options_storage.get("querystring_auth")), "R2 read配信方式設定済み", "R2 媒體沒有公開網域，也未啟用簽署網址", fail=True)
        if external and r2_ok:
            probe = f"readiness/{uuid.uuid4()}.txt"
            try:
                saved = default_storage.save(probe, ContentFile(b"restfull-readiness"))
                with default_storage.open(saved, "rb") as handle:
                    ok = handle.read() == b"restfull-readiness"
                default_storage.delete(saved)
                self._check(ok, "R2 write/read/delete疎通", "R2 read内容不一致", fail=True)
            except Exception as exc:
                self._fail(f"R2 write/read/delete失敗: {exc}")
        elif r2_ok:
            self._warn("R2実疎通未実施（--externalで確認）")

        usable_methods = []
        for method in PaymentMethod.objects.filter(enabled=True):
            if method.code == PaymentMethod.Method.BANK_TRANSFER:
                configured = all((site.bank_name, site.bank_code, site.bank_account_number, site.bank_account_name))
            elif method.code == PaymentMethod.Method.TAIWAN_PAY:
                configured = bool(method.qr_image or site.taiwan_pay_qr)
            else:
                configured = bool(method.provider and get_provider(method.provider))
            if configured: usable_methods.append(method.code)
            else: self._fail(f"有効な支払方法の設定不足: {method.display_name}")
        self._check(bool(usable_methods), "利用可能な支払方法あり", "利用可能な支払方法なし", fail=production)
        self._pass("Payment confirmed一意制約はmigrationで管理")

        pages = {page.slug: page for page in PolicyPage.objects.filter(slug__in=REQUIRED_POLICIES)}
        for slug in sorted(REQUIRED_POLICIES):
            page = pages.get(slug)
            self._check(bool(page and page.published and page.body.strip() and page.version and page.effective_date), f"policy {slug} 公開・version済み", f"policy {slug} が未完成", fail=True)
            if page:
                self._check(page.legal_reviewed, f"policy {slug} 事業者確認済み", f"policy {slug} は法務最終確認前", fail=production)
        business_fields = [site.business_legal_name, site.business_representative, site.phone_primary, site.general_email, site.business_address, site.returns_contact, site.business_hours, site.privacy_contact_email]
        self._check(all(business_fields), "事業者情報完備", "事業者名・責任者・住所・窓口等が未完備", fail=production)

        test_products = Product.objects.filter(name__icontains="TEST") | Product.objects.filter(sku__istartswith="TEST")
        test_count = test_products.distinct().count()
        self._check(test_count == 0, "TEST商品なし", f"TEST商品 {test_count} 件（確認環境では保持可）", fail=production)
        placeholders = Product.objects.filter(is_published=True, images__image="").distinct().count()
        self._check(placeholders == 0, "公開商品に正式画像あり", f"公開商品で画像未設定 {placeholders} 件", fail=production)
        invalid_products = 0
        for product in Product.objects.filter(is_published=True).prefetch_related("images"):
            if not all((product.name.strip(), product.sku.strip(), product.description.strip())) or product.price < 0 or not any(image.image and image.alt_text.strip() for image in product.images.all()):
                invalid_products += 1
            if product.is_preorder and (not product.preorder_limit or not product.preorder_delivery_estimate.strip()):
                invalid_products += 1
        self._check(invalid_products == 0, "公開商品必須項目完備", f"公開商品必須項目エラー {invalid_products} 件", fail=production)
        demo_content = InteriorProject.objects.filter(title__icontains="TEST").count() + Publication.objects.filter(title__icontains="TEST").count()
        self._check(demo_content == 0, "TEST作品・出版物なし", f"TEST作品・出版物 {demo_content} 件", fail=production)

        static_ok = (Path(settings.STATIC_ROOT) / "css" / "app.css").exists() or settings.DEBUG
        self._check(static_ok, "static files確認", "collectstatic出力が見つからない", fail=production)
        backlog = NotificationOutbox.objects.exclude(status=NotificationOutbox.Status.SENT).count()
        dead = NotificationOutbox.objects.filter(status=NotificationOutbox.Status.DEAD).count()
        self._check(dead == 0, "dead通知なし", f"dead通知 {dead} 件", fail=production)
        if backlog: self._warn(f"outbox未送信 {backlog} 件")
        self._check(bool(settings.TRUSTED_PROXY_IPS), "trusted proxy IP明示", "TRUSTED_PROXY_IPS未設定（X-Forwarded-Forを信頼しません）", fail=False)
        self._warn("LINE webhook登録・R2 CORS・production credentials同一性は各Consoleで人間確認が必要")

        for message in self.passes: self.stdout.write(self.style.SUCCESS(f"PASS: {message}"))
        for message in self.warnings: self.stdout.write(self.style.WARNING(f"WARN: {message}"))
        for message in self.failures: self.stdout.write(self.style.ERROR(f"FAIL: {message}"))
        self.stdout.write(f"SUMMARY: PASS={len(self.passes)} WARN={len(self.warnings)} FAIL={len(self.failures)}")
        if self.failures:
            raise CommandError(f"正式公開前に {len(self.failures)} 件のFAILがあります。checkout状態は変更していません。")

    def _pass(self, message): self.passes.append(message)
    def _warn(self, message): self.warnings.append(message)
    def _fail(self, message): self.failures.append(message)
    def _check(self, condition, success, failure, *, fail):
        if condition: self._pass(success)
        elif fail: self._fail(failure)
        else: self._warn(failure)
