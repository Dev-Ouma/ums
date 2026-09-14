from .models import StaffRoleAssignment


class ActiveRoleMiddleware:
    """Bind the validated session role to this request's user object."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            code = request.session.get("active_role")
            if code and StaffRoleAssignment.objects.filter(user=user, role__code=code, is_active=True).exists():
                user._active_role_code = code
            else:
                user._active_role_code = None
                if code:
                    request.session.pop("active_role", None)
        return self.get_response(request)
