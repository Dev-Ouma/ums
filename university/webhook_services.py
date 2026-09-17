"""
Outbound webhook dispatch and delivery.

WebhookDispatcher is connected to the domain events in university/events.py
the same way any other receiver is (see event_receivers.py) -- it doesn't
know or care what fired the event, only that one happened. Firing the
event just queues a WebhookDelivery row per subscribed, active endpoint;
the actual HTTP POST happens in deliver_pending_webhooks(), called from
control_services.tick() (the same background-job hook backup_tick() and
apply_audit_retention() already use) so delivery is retried automatically
on the next tick if a POST fails, with no new infrastructure needed.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from django.utils import timezone

from university.integrations.base import BaseIntegrationAdapter
from university.webhook_models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

#: Maps a webhook-subscribable event kind (WebhookEndpoint.EVENT_KIND_CHOICES)
#: to a JSON-shaping function for that event's dispatch_event() kwargs.
EVENT_PAYLOAD_BUILDERS = {}


def register_event_payload(event_kind):
    """Decorator: register how to turn a signal's kwargs into a JSON payload."""
    def decorator(fn):
        EVENT_PAYLOAD_BUILDERS[event_kind] = fn
        return fn
    return decorator


@register_event_payload("account_status_changed")
def _account_status_changed_payload(user, old_status, new_status, actor=None, **kwargs):
    return {
        "user_id": user.pk, "username": user.username,
        "old_status": old_status, "new_status": new_status,
        "actor_id": getattr(actor, "pk", None),
    }


@register_event_payload("exam_marks_published")
def _exam_marks_published_payload(exam, published_by=None, **kwargs):
    return {
        "exam_id": exam.pk, "exam_name": exam.name,
        "course_id": exam.course_id, "course_code": getattr(exam.course, "code", None),
        "published_by_id": getattr(published_by, "pk", None),
    }


@register_event_payload("fee_payment_confirmed")
def _fee_payment_confirmed_payload(payment, fee_account=None, **kwargs):
    return {
        "payment_id": payment.pk, "amount": str(payment.amount),
        "currency": payment.currency, "student_id": payment.student_id,
        "fee_account_id": getattr(fee_account, "pk", None),
    }


def queue_webhook_deliveries(event_kind, **event_kwargs):
    """
    Create a WebhookDelivery for every active endpoint subscribed to
    event_kind. Called by WebhookDispatcher's signal receivers -- never
    call directly from business logic, that would defeat the point of
    routing everything through events.py.
    """
    endpoints = WebhookEndpoint.objects.filter(is_active=True)
    subscribed = [e for e in endpoints if e.subscribes_to(event_kind)]
    if not subscribed:
        return []

    builder = EVENT_PAYLOAD_BUILDERS.get(event_kind)
    payload_data = builder(**event_kwargs) if builder else {}
    payload = {
        "event": event_kind,
        "occurred_at": timezone.now().isoformat(),
        "data": payload_data,
    }

    deliveries = []
    for endpoint in subscribed:
        deliveries.append(WebhookDelivery.objects.create(
            endpoint=endpoint, event_kind=event_kind, payload=payload,
        ))
    return deliveries


def _sign_payload(secret, body_bytes):
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


class WebhookDeliveryAdapter(BaseIntegrationAdapter):
    integration_name = "Webhooks"

    def deliver(self, delivery):
        body = json.dumps(delivery.payload).encode("utf-8")
        signature = _sign_payload(delivery.endpoint.secret, body)
        req = urllib.request.Request(
            delivery.endpoint.url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-UMS-Signature": signature,
                "X-UMS-Event": delivery.event_kind,
            },
        )

        def _post():
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.getcode(), response.read().decode("utf-8", errors="replace")

        try:
            status_code, body_text = self.call_with_audit(
                _post, action=f"deliver {delivery.event_kind} to {delivery.endpoint.name}")
            delivery.response_status = status_code
            delivery.response_body = body_text[:2000]
            delivery.status = WebhookDelivery.Status.SUCCESS
            delivery.error_message = ""
        except urllib.error.HTTPError as exc:
            delivery.response_status = exc.code
            delivery.response_body = (exc.read().decode("utf-8", errors="replace") if exc.fp else "")[:2000]
            delivery.error_message = str(exc)
            self._mark_retry_or_failed(delivery)
        except Exception as exc:
            delivery.error_message = str(exc)
            self._mark_retry_or_failed(delivery)

        delivery.attempt_count += 1
        delivery.last_attempted_at = timezone.now()
        delivery.save()

    def _mark_retry_or_failed(self, delivery):
        if delivery.attempt_count + 1 >= delivery.max_attempts:
            delivery.status = WebhookDelivery.Status.FAILED
        else:
            delivery.status = WebhookDelivery.Status.RETRYING
            # Exponential backoff: 1 min, 2 min, 4 min, 8 min, ...
            backoff_minutes = 2 ** delivery.attempt_count
            delivery.next_attempt_at = timezone.now() + timezone.timedelta(minutes=backoff_minutes)


def deliver_pending_webhooks(limit=50):
    """
    Send every WebhookDelivery that's due. Called from control_services.tick(),
    so it runs on the same cadence as the rest of the system's background jobs
    -- a failed delivery gets retried automatically on a later tick, with
    exponential backoff, up to max_attempts.
    """
    due = WebhookDelivery.objects.filter(
        status__in=[WebhookDelivery.Status.PENDING, WebhookDelivery.Status.RETRYING],
        next_attempt_at__lte=timezone.now(),
    ).select_related("endpoint")[:limit]

    adapter = WebhookDeliveryAdapter()
    delivered = 0
    for delivery in due:
        adapter.deliver(delivery)
        delivered += 1
    return delivered
