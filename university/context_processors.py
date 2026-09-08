from django.conf import settings

from accounts.models import ROLE_THEMES, Role

# Cache-busting token for the hand-written CSS/JS. In DEBUG it tracks the files'
# modification times so an edit is picked up on the next reload instead of
# needing a hard refresh; in production it is computed once at import time.
_ASSET_FILES = ("css/ums.css", "css/public.css", "js/ums.js")


def _asset_version():
    stamp = 0
    for name in _ASSET_FILES:
        for base in settings.STATICFILES_DIRS:
            path = base / name
            if path.exists():
                stamp = max(stamp, int(path.stat().st_mtime))
                break
    return stamp


_STATIC_ASSET_VERSION = _asset_version()


def theme_and_notifications(request):
    """Expose the active UI theme + a few global bits to every template."""
    user = getattr(request, "user", None)
    theme = None
    unread = 0
    if user is not None and user.is_authenticated:
        theme = user.theme
        from .models import Notice
        aud = ["ALL", user.role]
        unread = Notice.objects.filter(audience__in=aud).count()
    return {
        "theme": theme,
        "notice_count": unread,
        "ROLE_THEMES": ROLE_THEMES,
        "Role": Role,
        "brand_name": "University Management System",
        "brand_short": "UMS",
        "asset_version": _asset_version() if settings.DEBUG else _STATIC_ASSET_VERSION,
    }
