"""
Module Access Enforcement Middleware.
Intercepts all incoming HTTP requests to ensure disabled or maintenance-mode
modules, submodules, and features cannot be accessed by direct URLs or APIs.
"""

from django.http import JsonResponse
from django.shortcuts import render
from university.module_services import get_request_module_context


class ModuleAccessMiddleware:
    """
    Enforces operational status of modules across all web routes and API endpoints.
    """
    EXEMPT_PREFIXES = (
        "/static/",
        "/media/",
        "/favicon.ico",
        "/accounts/login/",
        "/accounts/logout/",
        "/manage/system/modules/",
        "/django-admin/",
        "/status/",
        "/system-control/status/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Fast bypass for static, auth, and module management itself
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        # Inspect if current route belongs to a managed module
        is_managed, is_active, context = get_request_module_context(request)

        if is_managed and not is_active:
            # Check if superuser requested an explicit diagnostic bypass
            if request.user.is_authenticated and request.user.is_superuser and request.GET.get("admin_bypass") == "1":
                return self.get_response(request)

            status = context.get("status", "DISABLED")
            status_message = context.get("status_message", "")
            module_name = context.get("module_name", "Module")
            submodule_name = context.get("submodule_name", "")

            # Handle JSON / AJAX requests
            is_ajax = (
                request.headers.get("x-requested-with") == "XMLHttpRequest"
                or "application/json" in request.headers.get("Accept", "")
                or "/api/" in path
            )

            if is_ajax:
                http_code = 503 if status == "MAINTENANCE" else 403
                return JsonResponse({
                    "error": "module_inactive",
                    "status": status,
                    "module": module_name,
                    "submodule": submodule_name,
                    "message": status_message or f"{submodule_name or module_name} is currently inactive ({status}).",
                }, status=http_code)

            # Standard browser rendering
            http_code = 503 if status == "MAINTENANCE" else 403
            response = render(
                request,
                "system/module_inactive.html",
                {
                    "status": status,
                    "status_message": status_message,
                    "module_name": module_name,
                    "module_code": context.get("module_code"),
                    "module_icon": context.get("module_icon"),
                    "submodule_name": submodule_name,
                    "submodule_code": context.get("submodule_code"),
                    "submodule_icon": context.get("submodule_icon"),
                },
                status=http_code,
            )
            return response

        return self.get_response(request)
