"""
Outbound webhook delivery: lets an external system subscribe to UMS domain
events (university/events.py) over HTTP instead of needing direct database
or code access. Built on top of the internal event bus added earlier --
a WebhookDispatcher (webhook_services.py) is just another receiver
connected to the same signals every other receiver uses.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class WebhookEndpoint(models.Model):
    """An external URL subscribed to one or more UMS domain events."""

    #: Must match a key in university.webhook_services.EVENT_KIND_SIGNALS.
    EVENT_KIND_CHOICES = [
        ("account_status_changed", "Account Status Changed"),
        ("exam_marks_published", "Exam Marks Published"),
        ("fee_payment_confirmed", "Fee Payment Confirmed"),
    ]

    name = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    event_kinds = models.JSONField(default=list, help_text="List of subscribed event kind keys.")
    secret = models.CharField(max_length=64, blank=True, default="",
                             help_text="Used to HMAC-sign delivered payloads (X-UMS-Signature header).")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.url})"

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def subscribes_to(self, event_kind):
        return event_kind in (self.event_kinds or [])


class WebhookDelivery(models.Model):
    """One attempted (or pending) delivery of one event to one endpoint."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Delivered"
        FAILED = "FAILED", "Failed (retries exhausted)"
        RETRYING = "RETRYING", "Retrying"

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event_kind = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_kind} -> {self.endpoint.name} ({self.status})"
