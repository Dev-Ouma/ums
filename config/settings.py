"""
Django settings for the University Management System (UMS).
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY -----------------------------------------------------------------
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes", "on"}
configured_secret_key = os.environ.get("DJANGO_SECRET_KEY", "")
SECRET_KEY = configured_secret_key or (f"dev-{secrets.token_urlsafe(48)}" if DEBUG else "")
configured_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in (configured_hosts or "127.0.0.1,localhost").split(",") if host.strip()]

if not DEBUG:
    if len(SECRET_KEY) < 50 or "replace-with" in SECRET_KEY.lower() or SECRET_KEY.startswith("dev-"):
        raise RuntimeError("DJANGO_SECRET_KEY must be a long, unique production secret")
    if not configured_hosts or "*" in ALLOWED_HOSTS or any(host in {"localhost", "127.0.0.1", "::1"} for host in ALLOWED_HOSTS):
        raise RuntimeError("DJANGO_ALLOWED_HOSTS must explicitly list production hostnames")

# APPLICATIONS -------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local apps
    "accounts",
    "university",
    "cms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "university.security_headers.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "university.control_middleware.SystemControlMiddleware",
    "accounts.activity.LastSeenMiddleware",
    "accounts.identity.PasswordChangeRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # After MessageMiddleware so an idle-timeout logout can flash a message.
    "accounts.identity.SessionIdleTimeoutMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "university.module_middleware.ModuleAccessMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "university.context_processors.theme_and_notifications",
                "cms.context_processors.site",
                "university.control_context.control_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# DATABASE -----------------------------------------------------------------
# SQLite remains the zero-configuration development default. Production must
# explicitly provide a managed/private database and its credentials instead of
# silently booting against a local file.
DATABASE_ENGINE = os.environ.get("DJANGO_DB_ENGINE", "django.db.backends.sqlite3")
if not DEBUG and DATABASE_ENGINE.endswith("sqlite3"):
    raise RuntimeError("Production requires DJANGO_DB_ENGINE and a private database; SQLite is development-only")

if DATABASE_ENGINE.endswith("sqlite3"):
    DATABASES = {"default": {"ENGINE": DATABASE_ENGINE, "NAME": BASE_DIR / "db.sqlite3"}}
else:
    database_name = os.environ.get("DJANGO_DB_NAME", "")
    database_user = os.environ.get("DJANGO_DB_USER", "")
    database_password = os.environ.get("DJANGO_DB_PASSWORD", "")
    database_host = os.environ.get("DJANGO_DB_HOST", "")
    if not all((database_name, database_user, database_password, database_host)):
        raise RuntimeError("DJANGO_DB_NAME, DJANGO_DB_USER, DJANGO_DB_PASSWORD and DJANGO_DB_HOST are required")
    db_options = {}
    if not DEBUG and os.environ.get("DJANGO_DB_SSL_REQUIRE", "true").lower() in {"1", "true", "yes", "on"}:
        db_options["sslmode"] = os.environ.get("DJANGO_DB_SSL_MODE", "verify-full")
    DATABASES = {"default": {
        "ENGINE": DATABASE_ENGINE,
        "NAME": database_name,
        "USER": database_user,
        "PASSWORD": database_password,
        "HOST": database_host,
        "PORT": os.environ.get("DJANGO_DB_PORT", ""),
        "OPTIONS": db_options,
    }}
DATABASE_PRIVATE_NETWORK = os.environ.get("DJANGO_DB_PRIVATE_NETWORK", "false").lower() in {"1", "true", "yes", "on"}

# Bound request bodies to reduce memory/disk exhaustion from oversized uploads.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DJANGO_DATA_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DJANGO_FILE_UPLOAD_MAX_MEMORY_BYTES", str(5 * 1024 * 1024)))

# PASSWORD VALIDATION ------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# INTERNATIONALIZATION -----------------------------------------------------
LANGUAGE_CODE = "en-us"
# UMS operational time follows East Africa Time (EAT), UTC/GMT+3.
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# STATIC & MEDIA -----------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# AUTH ---------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# One authentication path for every user type. The identity backend adds the
# account-status gate on top of Django's credential check, so a disabled,
# suspended, locked or expired account cannot sign in even with a valid password.
AUTHENTICATION_BACKENDS = [
    "accounts.identity.IdentityModelBackend",
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "university:dashboard"
LOGOUT_REDIRECT_URL = "university:home"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# SESSIONS & CSRF ------------------------------------------------------------
# "Remember me" (accounts.views.UMSLoginView) sets the session cookie's expiry
# to this age; unchecked, the session is cleared when the browser closes.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 days
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
MAX_CONCURRENT_SESSIONS = int(os.environ.get("DJANGO_MAX_CONCURRENT_SESSIONS", "5"))

# CSRF_COOKIE_HTTPONLY is intentionally left at its default (False): static/js/ums.js
# reads the csrftoken cookie directly to set the X-CSRFToken header on AJAX requests.
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG

# SECURITY HEADERS ----------------------------------------------------------
# SecurityMiddleware applies these only when the request is served in the
# corresponding production configuration. Local HTTP development remains
# usable while production receives HTTPS enforcement and browser protections.
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Sensitive endpoint throttling is active in production. Use a shared cache
# backend such as Redis when more than one application process is deployed.
SECURITY_RATE_LIMIT_ENABLED = not DEBUG
PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = not DEBUG
PAYMENT_WEBHOOK_SECRET = os.environ.get("UMS_PAYMENT_WEBHOOK_SECRET", "")

# Production rate limits must share state across application workers.
if DEBUG:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "ums-development"}}
else:
    cache_backend = os.environ.get("DJANGO_CACHE_BACKEND", "")
    cache_location = os.environ.get("DJANGO_CACHE_LOCATION", "")
    if not cache_backend or not cache_location:
        raise RuntimeError("DJANGO_CACHE_BACKEND and DJANGO_CACHE_LOCATION are required in production")
    if "locmem" in cache_backend.lower() or "dummy" in cache_backend.lower():
        raise RuntimeError("Production security controls cannot use local-memory or dummy caching")
    CACHES = {"default": {"BACKEND": cache_backend, "LOCATION": cache_location}}

# Keep the policy aligned with the assets currently shipped by the UMS. The
# legacy templates contain inline scripts/styles, so those are allowed until
# the templates are migrated to nonces; framing and plugin execution remain
# denied immediately.
_CSP_DIRECTIVES = [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com",
    "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:",
    "img-src 'self' data: https:",
    "frame-src 'self'",
    "connect-src 'self'",
]
if not DEBUG:
    _CSP_DIRECTIVES.append("upgrade-insecure-requests")
CONTENT_SECURITY_POLICY = "; ".join(_CSP_DIRECTIVES)
CSP_REPORT_ONLY = os.environ.get("DJANGO_CSP_REPORT_ONLY", "false").lower() in {"1", "true", "yes", "on"}
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"

# Central console logging is filtered before emission so credentials,
# authorization material, payment secrets, and email addresses do not leak
# into process logs or a downstream collector.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_sensitive": {"()": "university.logging_filters.SensitiveDataRedactionFilter"}},
    "formatters": {"security": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {
        "console_redacted": {
            "class": "logging.StreamHandler",
            "filters": ["redact_sensitive"],
            "formatter": "security",
        },
    },
    "loggers": {
        "django": {"handlers": ["console_redacted"], "level": "INFO", "propagate": False},
        "security": {"handlers": ["console_redacted"], "level": "INFO", "propagate": False},
        "university": {"handlers": ["console_redacted"], "level": "INFO", "propagate": False},
    },
}

# No CORS middleware is installed: authenticated APIs are same-origin by
# default. If a future integration needs cross-origin access, provide an
# explicit comma-separated allowlist and review credentials separately.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
