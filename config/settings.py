import os
from pathlib import Path

import dj_database_url


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [value.strip() for value in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if value.strip()]
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [value.strip() for value in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if value.strip()]
if os.getenv("RENDER_EXTERNAL_URL") and os.environ["RENDER_EXTERNAL_URL"] not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(os.environ["RENDER_EXTERNAL_URL"])
CANONICAL_ORIGIN = os.getenv("CANONICAL_ORIGIN", os.getenv("RENDER_EXTERNAL_URL", "https://restfull-xhex.onrender.com")).rstrip("/")
CANONICAL_REDIRECT_HOSTS = [value.strip().lower() for value in os.getenv("CANONICAL_REDIRECT_HOSTS", "").split(",") if value.strip()]
SEO_INDEX_ENABLED = env_bool("SEO_INDEX_ENABLED", False)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "core",
    "catalog",
    "orders",
    "inquiries",
    "content",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.CanonicalHostMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "core.context_processors.site_context",
            "orders.context_processors.cart_context",
        ],
    },
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hant"
TIME_ZONE = "Asia/Taipei"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static", BASE_DIR / "src" / "styles", BASE_DIR / "public"]
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

if os.getenv("AWS_STORAGE_BUCKET_NAME"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": os.getenv("AWS_ACCESS_KEY_ID"),
            "secret_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
            "bucket_name": os.getenv("AWS_STORAGE_BUCKET_NAME"),
            "endpoint_url": os.getenv("AWS_S3_ENDPOINT_URL"),
            "region_name": os.getenv("AWS_S3_REGION_NAME", "auto"),
            "custom_domain": os.getenv("AWS_S3_CUSTOM_DOMAIN") or None,
            "querystring_auth": env_bool("AWS_QUERYSTRING_AUTH", False),
            "file_overwrite": False,
        },
    }

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend" if os.getenv("EMAIL_HOST") else "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Rfull <rfullshop@gmail.com>")
ORDER_NOTIFICATION_EMAIL = os.getenv("ORDER_NOTIFICATION_EMAIL", "rfullshop@gmail.com")

TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
CONTACT_RATE_LIMIT = int(os.getenv("CONTACT_RATE_LIMIT", "5"))
CHECKOUT_RATE_LIMIT = int(os.getenv("CHECKOUT_RATE_LIMIT", "5"))
MAX_IMAGE_UPLOAD_MB = int(os.getenv("MAX_IMAGE_UPLOAD_MB", "10"))

LINE_LOGIN_CHANNEL_ID = os.getenv("LINE_LOGIN_CHANNEL_ID", "")
LINE_LOGIN_CHANNEL_SECRET = os.getenv("LINE_LOGIN_CHANNEL_SECRET", "")
LINE_LOGIN_CALLBACK_URL = os.getenv("LINE_LOGIN_CALLBACK_URL", "")
LINE_MESSAGING_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_MESSAGING_CHANNEL_ACCESS_TOKEN", "")
LINE_MESSAGING_CHANNEL_SECRET = os.getenv("LINE_MESSAGING_CHANNEL_SECRET", "")
LINE_OFFICIAL_ACCOUNT_BASIC_ID = os.getenv("LINE_OFFICIAL_ACCOUNT_BASIC_ID", "")
LINE_API_TIMEOUT = float(os.getenv("LINE_API_TIMEOUT", "5"))
LINE_FRIENDSHIP_MAX_AGE = int(os.getenv("LINE_FRIENDSHIP_MAX_AGE", "900"))
PAYMENT_LINK_MAX_AGE = int(os.getenv("PAYMENT_LINK_MAX_AGE", "604800"))



SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    if SECRET_KEY == "unsafe-development-key":
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DEBUG=False")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
