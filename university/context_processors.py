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
    """Expose the active UI theme, notifications + module availability to every template."""
    user = getattr(request, "user", None)
    theme = None
    unread = 0
    if user is not None and user.is_authenticated:
        theme = user.theme
        from .control_services import active_messages
        from .models import MessageDelivery
        read_ids = set(MessageDelivery.objects.filter(recipient=user, read_at__isnull=False).values_list('message_id', flat=True))
        unread = sum(n.pk not in read_ids for n in active_messages(user))

    from university.module_services import (
        get_cached_module_registry,
        is_module_active,
        is_submodule_active,
        is_feature_active,
    )
    registry = get_cached_module_registry()

    return {
        "theme": theme,
        "notice_count": unread,
        "ROLE_THEMES": ROLE_THEMES,
        "Role": Role,
        "brand_name": "University Management System",
        "brand_short": "UMS",
        "asset_version": _asset_version() if settings.DEBUG else _STATIC_ASSET_VERSION,
        "active_modules": registry["active_module_codes"],
        "active_submodules": registry["active_submodule_codes"],
        "active_features": registry["active_feature_codes"],
        "is_module_active": is_module_active,
        "is_submodule_active": is_submodule_active,
        "is_feature_active": is_feature_active,
    }
