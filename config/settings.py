"""
Django settings for the University Management System (UMS).
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY -----------------------------------------------------------------
SECRET_KEY = "django-insecure-ums-demo-key-change-me-in-production-000000000000"
DEBUG = True
ALLOWED_HOSTS = ["*"]

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
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# PASSWORD VALIDATION ------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# INTERNATIONALIZATION -----------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
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

# CSRF_COOKIE_HTTPONLY is intentionally left at its default (False): static/js/ums.js
# reads the csrftoken cookie directly to set the X-CSRFToken header on AJAX requests.
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
