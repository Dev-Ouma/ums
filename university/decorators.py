from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from accounts.models import Role


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            active_code = getattr(request.user, "_active_role_code", None)
            if active_code:
                elevated_admin_codes = {"identity_admin", "system_admin", "administrator"}
                faculty_codes = {"faculty", "lecturer", "instructor", "trainer", "hod", "dean", "examiner"}
                active_allowed = any(
                    (role == Role.ADMIN and active_code in elevated_admin_codes)
                    or (role == Role.FACULTY and active_code in faculty_codes)
                    or (role.value.lower() == active_code)
                    for role in roles
                )
            else:
                active_allowed = request.user.role in roles
            if not active_allowed and not request.user.is_superuser:
                messages.error(request, "You don't have access to that area.")
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator


def permission_required(*codes, require_all=False):
    """
    Gate a view on the granular RBAC engine, not just the coarse base role.

    Server-side enforcement is the point: hiding a button is a convenience, this
    is the control. Any one of ``codes`` grants access unless ``require_all``.
    """
    from functools import wraps as _wraps

    def decorator(view):
        @_wraps(view)
        def _wrapped(request, *args, **kwargs):
            from university.permissions_services import has_user_permission
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            checks = [has_user_permission(request.user, code) for code in codes]
            allowed = all(checks) if require_all else any(checks)
            if not allowed:
                messages.error(request, "You don't have permission for that action.")
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator
