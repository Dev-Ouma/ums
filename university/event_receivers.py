"""
Receivers for the domain events defined in university/events.py.

Connected from UniversityConfig.ready() (university/apps.py). This is the
decoupling point: the module that fires an event (identity_services.py,
examination_services.py, payment_services.py) never imports this file or
knows it exists.

The three events wired this pass already have their real side effects
(email/SMS notification, in-app notices) running inline at their call
sites -- those are left in place rather than moved, to avoid re-plumbing
working, tested code as part of establishing this pattern. The receivers
below are additive: a second, independent place events can be observed
from, proving the wiring works end-to-end. Future side effects (an
outbound webhook dispatcher, a new notification channel, a change-feed for
a public API) are natural additions here without touching the modules that
fire the events at all.
"""
import logging

from django.dispatch import receiver

from university.events import (
    account_status_changed, exam_marks_published, fee_payment_confirmed,
)

logger = logging.getLogger(__name__)


@receiver(account_status_changed)
def log_account_status_change(sender, user, old_status, new_status, actor=None, **kwargs):
    logger.info("Account status changed: %s went from %s to %s (actor=%s)",
               getattr(user, "username", user), old_status, new_status, actor)


@receiver(exam_marks_published)
def log_exam_marks_published(sender, exam, published_by=None, **kwargs):
    logger.info("Exam marks published: %s (course=%s) by %s",
               getattr(exam, "name", exam), getattr(exam, "course", None), published_by)


@receiver(fee_payment_confirmed)
def log_fee_payment_confirmed(sender, payment, fee_account=None, **kwargs):
    logger.info("Fee payment confirmed: %s amount=%s account=%s",
               getattr(payment, "internal_reference", payment),
               getattr(payment, "amount", None), fee_account)
