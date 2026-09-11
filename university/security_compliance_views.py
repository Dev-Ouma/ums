from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from university.permissions_services import has_user_permission
from university.security_compliance_services import build_security_compliance_summary


def _security_admin_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not (
            request.user.is_admin_role
            or request.user.is_superuser
            or has_user_permission(request.user, "security_compliance.view")
            or has_user_permission(request.user, "golive.view")
        ):
            messages.error(request, "Access restricted. Security & Compliance privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapped


@login_required
@_security_admin_required
def security_compliance_dashboard(request):
    return render(request, "system/security_compliance/dashboard.html", {
        "summary": build_security_compliance_summary(),
    })
