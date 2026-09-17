from django.utils import timezone
from rest_framework import authentication, exceptions

from university.api.models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticates via `Authorization: Api-Key <key>`. An API key
    authenticates as the user it was issued to -- it's not a separate
    principal with its own permissions, so every existing
    has_user_permission()/has_scoped_permission() check downstream works
    unchanged for API requests exactly as it does for browser requests.
    """
    keyword = "Api-Key"

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).decode("utf-8", errors="ignore")
        if not auth_header or not auth_header.startswith(f"{self.keyword} "):
            return None

        raw_key = auth_header[len(self.keyword) + 1:].strip()
        if not raw_key:
            raise exceptions.AuthenticationFailed("No API key provided.")

        key_hash = ApiKey.hash_key(raw_key)
        api_key = ApiKey.objects.filter(key_hash=key_hash, is_active=True).select_related("user").first()
        if not api_key:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")
        if not api_key.user.is_active:
            raise exceptions.AuthenticationFailed("This account is not active.")

        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at"])

        return (api_key.user, api_key)

    def authenticate_header(self, request):
        # Declaring this makes DRF return 401 (with a WWW-Authenticate
        # header) for a missing/invalid/revoked key instead of a bare 403 --
        # 401 means "you're not authenticated," 403 means "you are, but
        # can't do this," and a bad API key is the former.
        return self.keyword
