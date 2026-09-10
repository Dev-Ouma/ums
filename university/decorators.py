from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("accounts:login")
            if request.user.role not in roles and not request.user.is_superuser:
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
                return redirect("accounts:login")
            checks = [has_user_permission(request.user, code) for code in codes]
            allowed = all(checks) if require_all else any(checks)
            if not allowed:
                messages.error(request, "You don't have permission for that action.")
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator
