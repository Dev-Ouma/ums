"""
Shared foundation for every external-service integration in UMS (payments,
SMS, email, and future providers like LMS/Zoom -- see
``university/integration_views.py`` for the standing wishlist).

Before this module existed, each integration was built independently with
its own config source and its own ad-hoc error handling: payments read
config off the ``FeeAccount`` model, email read it from ``SystemSetting``,
SMS read it from Django ``settings``/env vars. A future integration had no
obvious pattern to follow. This module is that pattern: one result type,
one config-loading convention (``SystemSetting``, matching email's already
-- admin-editable, secrets masked), and one retry/audit wrapper so every
outbound call to a third party is logged the same way regardless of which
provider it's talking to.
"""
import logging

from university.audit_services import log_activity
from university.models import AuditLog

logger = logging.getLogger(__name__)


class IntegrationResult:
    """
    Generic outcome of a call to an external provider.

    ``PaymentResult`` (university/payment_providers/base.py) predates this
    class and carries payment-specific fields (checkout_url, instructions);
    it is kept as-is and not replaced by this class, to avoid touching the
    already-working payment adapter subclasses. New integrations (SMS and
    anything added after it) use this directly.
    """
    def __init__(self, success, status="", message="", provider_reference="", raw_response=None):
        self.success = success
        self.status = status
        self.message = message
        self.provider_reference = provider_reference
        self.raw_response = raw_response or {}

    def __repr__(self):
        return f"IntegrationResult(success={self.success!r}, status={self.status!r})"


class BaseIntegrationAdapter:
    """
    Base class for an external-service adapter.

    Subclasses implement whatever provider-specific methods they need
    (``send``, ``verify``, ``test_connection`` -- there's no fixed
    interface here, unlike the payment adapters which have one because
    every payment provider needs the exact same four operations). What
    every integration *does* share is ``call_with_audit``: wrap the actual
    provider call in it so failures are retried a bounded number of times
    and the outcome -- success or final failure -- is always written to the
    audit trail, instead of each module hand-rolling its own try/except
    and deciding independently whether/how to log it.
    """
    #: Human-readable name used in audit log entries, e.g. "SMS (Africa's Talking)".
    integration_name = "Integration"

    def call_with_audit(self, fn, *, action, entity="", retries=1, user=None, request=None):
        """
        Run ``fn()`` (a zero-arg callable), retrying on exception up to
        ``retries`` additional times, and log the outcome via
        audit_services.log_activity every time -- success or the final
        failure after retries are exhausted. Returns whatever ``fn()``
        returns on success; re-raises the last exception if every attempt
        fails (callers that want a soft failure should catch that and
        convert it to an IntegrationResult themselves).
        """
        attempts = max(1, retries + 1)
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                result = fn()
                log_activity(
                    request=request, user=user, action=AuditLog.Action.UPDATE,
                    module=AuditLog.Module.CONFIG, entity=self.integration_name,
                    description=f"{self.integration_name}: '{action}' succeeded"
                                f"{f' (attempt {attempt}/{attempts})' if attempt > 1 else ''}.",
                )
                return result
            except Exception as exc:
                last_exc = exc
                logger.warning("%s: '%s' failed on attempt %d/%d: %s",
                               self.integration_name, action, attempt, attempts, exc)
        log_activity(
            request=request, user=user, action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.CONFIG, entity=self.integration_name,
            description=f"{self.integration_name}: '{action}' failed after {attempts} attempt(s): {last_exc}",
        )
        raise last_exc
