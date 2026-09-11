"""Small, dependency-free abuse controls for sensitive browser endpoints."""

import hashlib
import logging
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


logger = logging.getLogger("security")


def _client_address(request):
    """Use the socket peer; forwarded headers are untrusted until a proxy is configured."""
    return request.META.get("REMOTE_ADDR", "unknown")


def rate_limit(scope, limit, window_seconds):
    """Throttle POST abuse using a shared Django cache in production.

    ``SECURITY_RATE_LIMIT_ENABLED`` defaults to production-only so local
    development and the test suite remain deterministic. Deployments should
    point Django's cache at Redis or another shared backend when horizontally
    scaled.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if (request.method != "POST"
                    or not getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", not settings.DEBUG)):
                return view(request, *args, **kwargs)

            identity = f"{scope}:{_client_address(request)}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            key = f"ums:rate:{scope}:{digest}"
            try:
                if cache.add(key, 1, timeout=window_seconds):
                    attempts = 1
                else:
                    attempts = cache.incr(key)
            except Exception:
                # Failing open would remove brute-force protection exactly when
                # the shared cache is unhealthy. Fail closed for enabled
                # security controls and let monitoring alert on the outage.
                logger.exception("Rate-limit backend unavailable: scope=%s", scope)
                return HttpResponse(
                    "Security protection is temporarily unavailable. Please try again later.",
                    status=503,
                    content_type="text/plain; charset=utf-8",
                )

            if attempts > limit:
                logger.warning(
                    "Rate limit exceeded: scope=%s client=%s path=%s",
                    scope, digest, request.path,
                )
                response = HttpResponse(
                    "Too many requests. Please try again later.", status=429,
                    content_type="text/plain; charset=utf-8")
                response["Retry-After"] = str(window_seconds)
                return response
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
