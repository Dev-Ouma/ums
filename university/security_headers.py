"""Application-owned response headers not provided by Django core."""

from django.conf import settings


class SecurityHeadersMiddleware:
    """Attach the UMS CSP and privacy policy to every application response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = getattr(settings, "CONTENT_SECURITY_POLICY", "")
        if csp:
            if getattr(response, "_ums_allow_same_origin_frame", False):
                csp = csp.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
            header = "Content-Security-Policy-Report-Only" if getattr(settings, "CSP_REPORT_ONLY", False) else "Content-Security-Policy"
            response[header] = csp
        response["Permissions-Policy"] = getattr(
            settings, "PERMISSIONS_POLICY", "camera=(), microphone=(), geolocation=(), payment=()")
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        response["X-Permitted-Cross-Domain-Policies"] = "none"
        if request.path.startswith("/accounts/password/"):
            # Reset URLs contain a short-lived bearer token. Never send that
            # token in a Referer header to another page or external service.
            response["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/accounts/") or getattr(request.user, "is_authenticated", False):
            # Prevent sensitive UMS pages and account responses being replayed
            # from a shared browser/proxy cache after logout or role changes.
            response["Cache-Control"] = "no-store, max-age=0"
            response["Pragma"] = "no-cache"
        return response
