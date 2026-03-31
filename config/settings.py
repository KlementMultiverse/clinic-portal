import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if os.environ.get("DEBUG", "False").lower() != "true":
        raise ValueError("SECRET_KEY must be set in production")
    SECRET_KEY = "django-insecure-dev-only-key"

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
if not ALLOWED_HOSTS or ALLOWED_HOSTS == [""]:
    # In dev: allow all .localhost subdomains for dynamic tenant creation
    ALLOWED_HOSTS = [".localhost", "localhost", "127.0.0.1", ".ngrok-free.app", ".ngrok.io"]

SHARED_APPS = [
    "django_tenants",
    "apps.tenants",
    "apps.users",
    "tenant_users.permissions",
    "tenant_users.tenants",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "tenant_users.permissions",
    "apps.dashboard",
    "apps.workflows",
    "apps.documents",
    "apps.search",
]

INSTALLED_APPS = list(SHARED_APPS) + [
    app for app in TENANT_APPS if app not in SHARED_APPS
]

TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"
AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    "tenant_users.permissions.backend.UserBackend",
]

DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ.get("DB_NAME", "clinic_portal"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.users.middleware.PasswordResetMiddleware",
    "tenant_users.tenants.middleware.TenantAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
PUBLIC_SCHEMA_URLCONF = "config.urls_public"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

# Redis cache with tenant-aware keys
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        "KEY_FUNCTION": "django_tenants.cache.make_key",
        "REVERSE_KEY_FUNCTION": "django_tenants.cache.reverse_key",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN", None)
CSRF_COOKIE_DOMAIN = os.environ.get("CSRF_COOKIE_DOMAIN", None)
CSRF_TRUSTED_ORIGINS = [
    "http://portal.localhost:8000",
    "http://clinic1.localhost:8000",
    "http://clinic2.localhost:8000",
    "http://localhost:8000",
]
# Allow ngrok for live demo
NGROK_URL = os.environ.get("NGROK_URL", "")
if NGROK_URL:
    CSRF_TRUSTED_ORIGINS.append(NGROK_URL)

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# AWS Configuration
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_S3_BUCKET", "clinic-portal-docs")
AWS_S3_REGION_NAME = os.environ.get("AWS_REGION", "us-east-1")
LAMBDA_SUMMARIZE_ARN = os.environ.get("LAMBDA_SUMMARIZE_ARN", "")

# Security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Tenant Users settings
TENANT_USERS_DOMAIN = os.environ.get("TENANT_USERS_DOMAIN", "localhost")

# Login URL for @login_required decorator redirect
LOGIN_URL = "/login/"
