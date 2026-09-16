"""
Internal domain events.

Cross-cutting side effects (e.g. "email someone when X happens") have
historically been wired by having the module where X happens directly
import and call into whichever module handles the side effect --
identity_services.py importing email_services, financial_services.py
importing sms_services, and so on. That's fine for one or two call sites,
but it means every module that might care about "a student was admitted"
has to be individually known and imported by whatever code admits a
student, and the audit found several such tangles already.

This module defines a small set of named Signal instances for the domain
events that matter today. A module that *causes* one of these events calls
dispatch_event(); modules that *react* to one connect a receiver in
event_receivers.py. Neither side needs to know about the other's existence.

This is also the seam a future outbound-webhook dispatcher or a public
API's change-feed would attach to: it would just be another receiver
connected to these same signals, with no change to the business logic that
fires them. Building that dispatcher is out of scope for this pass -- this
module only establishes the pattern.
"""
import logging

from django.dispatch import Signal

from university.audit_services import log_activity
from university.models import AuditLog

logger = logging.getLogger(__name__)


# Each signal's expected kwargs are documented here rather than via Django's
# now-removed providing_args, since that's the only place left to say it.

#: kwargs: student (StudentProfile), application (Application or None)
student_admitted = Signal()

#: kwargs: exam (Exam), published_by (User or None)
exam_marks_published = Signal()

#: kwargs: payment (Payment), fee_account (FeeAccount)
fee_payment_confirmed = Signal()

#: kwargs: user (User), old_status (str), new_status (str), actor (User or None)
account_status_changed = Signal()


def dispatch_event(signal, sender=None, request=None, **kwargs):
    """
    Fire a domain event. Uses Signal.send_robust() (not .send()) so a
    broken receiver can't take down the request that triggered the event --
    each receiver's exception is caught individually by Django and returned
    alongside its response rather than propagating. Any receiver failure is
    still logged to the audit trail rather than silently swallowed.
    """
    responses = signal.send_robust(sender=sender, **kwargs)
    for receiver, response in responses:
        if isinstance(response, Exception):
            receiver_name = getattr(receiver, "__name__", repr(receiver))
            logger.error("Event receiver %s failed handling %s: %s",
                        receiver_name, signal, response)
            log_activity(
                request=request, user=None, action=AuditLog.Action.UPDATE,
                module=AuditLog.Module.CONFIG, entity="Domain Event",
                description=f"Receiver '{receiver_name}' failed handling event: {response}",
            )
    return responses
