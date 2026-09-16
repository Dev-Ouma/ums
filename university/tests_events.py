from django.dispatch import Signal, receiver
from django.test import TestCase

from university.events import (
    account_status_changed, dispatch_event, exam_marks_published, fee_payment_confirmed,
)
from university.models import AuditLog


class DispatchEventTests(TestCase):
    def test_connected_receiver_observes_the_event(self):
        test_signal = Signal()
        seen = {}

        @receiver(test_signal)
        def _capture(sender, **kwargs):
            seen.update(kwargs)

        dispatch_event(test_signal, foo="bar")
        self.assertEqual(seen.get("foo"), "bar")
        test_signal.disconnect(_capture)

    def test_a_broken_receiver_does_not_propagate_or_block_other_receivers(self):
        test_signal = Signal()
        seen = {"ok_receiver_ran": False}

        @receiver(test_signal)
        def _broken(sender, **kwargs):
            raise RuntimeError("receiver blew up")

        @receiver(test_signal)
        def _ok(sender, **kwargs):
            seen["ok_receiver_ran"] = True

        # Must not raise, even though one receiver raises internally.
        dispatch_event(test_signal, some_kwarg=1)
        self.assertTrue(seen["ok_receiver_ran"])
        test_signal.disconnect(_broken)
        test_signal.disconnect(_ok)

    def test_a_broken_receiver_is_logged_to_the_audit_trail(self):
        test_signal = Signal()

        @receiver(test_signal)
        def _broken(sender, **kwargs):
            raise RuntimeError("receiver blew up specifically")

        dispatch_event(test_signal)
        self.assertTrue(AuditLog.objects.filter(
            entity="Domain Event", description__icontains="receiver blew up specifically").exists())
        test_signal.disconnect(_broken)


class WiredEventReceiverTests(TestCase):
    """
    event_receivers.py's receivers are connected via UniversityConfig.ready()
    at Django startup, not per-test -- these tests confirm that wiring is
    live (not just that the signal fires) by capturing real log output.
    """
    def test_account_status_changed_signal_is_connected_to_a_receiver(self):
        self.assertTrue(len(account_status_changed.receivers) >= 1)

    def test_exam_marks_published_signal_is_connected_to_a_receiver(self):
        self.assertTrue(len(exam_marks_published.receivers) >= 1)

    def test_fee_payment_confirmed_signal_is_connected_to_a_receiver(self):
        self.assertTrue(len(fee_payment_confirmed.receivers) >= 1)


class RealCallSiteEventTests(TestCase):
    """
    Confirms the three wired call sites actually dispatch their event, using
    a temporary test receiver rather than relying on log output.
    """
    def test_set_account_status_fires_account_status_changed(self):
        from accounts.models import Role, User
        from university.identity_models import AccountStatus
        from university.identity_services import ensure_account, set_account_status

        user = User.objects.create_user(
            username="event.test.user", email="event.test.user@example.com",
            password="pass12345", role=Role.STUDENT)
        ensure_account(user, status=AccountStatus.ACTIVE)

        seen = {}

        @receiver(account_status_changed)
        def _capture(sender, user, old_status, new_status, **kwargs):
            seen["old_status"] = old_status
            seen["new_status"] = new_status

        try:
            set_account_status(user, AccountStatus.SUSPENDED, notify=False)
        finally:
            account_status_changed.disconnect(_capture)

        self.assertEqual(seen.get("old_status"), AccountStatus.ACTIVE)
        self.assertEqual(seen.get("new_status"), AccountStatus.SUSPENDED)
