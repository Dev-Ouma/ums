"""API key model for the public API (university/api/). Keys authenticate a
request as a specific user, the same way a session cookie does for the
browser app -- scoped to whatever that user is already allowed to see."""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class ApiKey(models.Model):
    """
    A long-lived credential for programmatic access, following the same
    hash-and-never-store-plaintext pattern used for password reset tokens
    (identity_services.py's _hash_token). The raw key is shown exactly once,
    at creation.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=120, help_text="What this key is for, e.g. 'Mobile app'.")
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    key_prefix = models.CharField(max_length=8, help_text="First few characters, shown in listings for identification.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...) for {self.user}"

    @staticmethod
    def hash_key(raw_key):
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def generate(cls, user, name):
        """Create a new key, returning (instance, raw_key). raw_key is never stored."""
        raw_key = "ums_" + secrets.token_urlsafe(32)
        instance = cls.objects.create(
            user=user, name=name, key_hash=cls.hash_key(raw_key), key_prefix=raw_key[:12])
        return instance, raw_key

    def revoke(self):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at"])
