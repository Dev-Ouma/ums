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
    user_roles = set()
    is_hod = False
    is_dean = False
    is_exam_officer = False
    is_registrar = False
    is_finance_officer = False
    is_admissions_officer = False
    is_identity_admin = False
    is_auditor = False
    is_vc_or_dvc = False

    if user is not None and user.is_authenticated:
        theme = user.theme
        from .control_services import active_messages
        from .models import MessageDelivery, StaffRoleAssignment
        read_ids = set(MessageDelivery.objects.filter(recipient=user, read_at__isnull=False).values_list('message_id', flat=True))
        unread = sum(n.pk not in read_ids for n in active_messages(user))

        user_roles = set(StaffRoleAssignment.objects.filter(user=user, is_active=True).values_list('role__code', flat=True))
        is_hod = 'hod' in user_roles or user.username == 'hod'
        is_dean = 'dean' in user_roles or user.username == 'dean'
        is_exam_officer = 'exam_officer' in user_roles or user.username == 'examofficer'
        is_registrar = 'academic_registrar' in user_roles or user.username == 'registrar'
        is_finance_officer = 'finance_officer' in user_roles or user.username == 'finance'
        is_admissions_officer = 'admissions_officer' in user_roles or user.username == 'admissions'
        is_identity_admin = 'identity_admin' in user_roles or user.username in ('ictdirector', 'admin', 'superadmin') or user.is_superuser
        is_auditor = 'auditor' in user_roles or user.username == 'auditor'
        is_vc_or_dvc = bool(user_roles & {'vc', 'dvcaa'}) or user.username in ('vc', 'dvcaa')

    from university.module_services import (
        get_cached_module_registry,
        is_module_active,
        is_submodule_active,
        is_feature_active,
    )
    registry = get_cached_module_registry()

    from university.institution_domain_services import get_institution_settings
    inst_settings = get_institution_settings()

    return {
        "theme": theme,
        "notice_count": unread,
        "ROLE_THEMES": ROLE_THEMES,
        "Role": Role,
        "user_roles": user_roles,
        "is_hod": is_hod,
        "is_dean": is_dean,
        "is_exam_officer": is_exam_officer,
        "is_registrar": is_registrar,
        "is_finance_officer": is_finance_officer,
        "is_admissions_officer": is_admissions_officer,
        "is_identity_admin": is_identity_admin,
        "is_auditor": is_auditor,
        "is_vc_or_dvc": is_vc_or_dvc,
        "brand_name": inst_settings.get("institution_name", "University Management System"),
        "brand_short": inst_settings.get("institution_short_name", "UMS"),
        "institution_settings": inst_settings,
        "primary_domain": inst_settings.get("primary_domain", "ums.ac.ke"),
        "staff_email_domain": inst_settings.get("staff_email_domain", "ums.ac.ke"),
        "student_email_domain": inst_settings.get("student_email_domain", "student.ums.ac.ke"),
        "asset_version": _asset_version() if settings.DEBUG else _STATIC_ASSET_VERSION,
        "active_modules": registry["active_module_codes"],
        "active_submodules": registry["active_submodule_codes"],
        "active_features": registry["active_feature_codes"],
        "is_module_active": is_module_active,
        "is_submodule_active": is_submodule_active,
        "is_feature_active": is_feature_active,
    }
