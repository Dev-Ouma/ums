from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from university.golive_models import GoLiveCategory, GoLiveIssue, GoLiveReadiness, IssueSeverity, ReadinessStatus
from university.golive_services import compute_readiness_summary


User = get_user_model()


class GoLiveGateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="gate-admin",
            email="gate-admin@example.test",
            password="password123",
            role=Role.ADMIN,
        )

    def _sign_off_all(self):
        for category in GoLiveCategory.values:
            GoLiveReadiness.objects.update_or_create(
                category=category,
                defaults={
                    "status": ReadinessStatus.PASS_,
                    "owner": self.admin,
                    "signed_off_by": self.admin,
                    "signed_off_at": timezone.now(),
                },
            )

    def test_gate_is_green_only_when_all_areas_are_signed_off_and_issue_free(self):
        self._sign_off_all()

        summary = compute_readiness_summary()

        self.assertEqual(summary["gate_status"], "GREEN")
        self.assertTrue(summary["is_ready"])

    def test_gate_is_amber_for_non_blocking_open_issue(self):
        self._sign_off_all()
        GoLiveIssue.objects.create(
            category=GoLiveCategory.INTEGRATIONS,
            severity=IssueSeverity.MEDIUM,
            description="Provider sandbox certificate is still pending production renewal.",
            owner=self.admin,
            resolution="Renewal is scheduled and tracked by the integration owner.",
            evidence_url="https://example.test/change/42",
        )

        summary = compute_readiness_summary()

        self.assertEqual(summary["gate_status"], "AMBER")
        self.assertEqual(summary["warning_open"], 1)

    def test_gate_is_red_for_unresolved_critical_issue(self):
        self._sign_off_all()
        GoLiveIssue.objects.create(
            category=GoLiveCategory.SECURITY,
            severity=IssueSeverity.CRITICAL,
            description="Authentication bypass remains unresolved.",
            owner=self.admin,
        )

        summary = compute_readiness_summary()

        self.assertEqual(summary["gate_status"], "RED")
        self.assertIn("1 open CRITICAL issue(s)", summary["gate_reasons"])

    def test_unsigned_pass_area_is_not_green(self):
        self._sign_off_all()
        row = GoLiveReadiness.objects.get(category=GoLiveCategory.SECURITY)
        row.signed_off_by = None
        row.signed_off_at = None
        row.save(update_fields=["signed_off_by", "signed_off_at"])

        summary = compute_readiness_summary()

        self.assertEqual(summary["gate_status"], "AMBER")
        self.assertFalse(summary["is_ready"])

    def test_sequence_contains_ordered_certification_and_approval_step(self):
        summary = compute_readiness_summary()

        self.assertEqual(len(summary["sequence"]), 22)
        self.assertEqual(summary["sequence"][0]["order"], 1)
        self.assertEqual(summary["sequence"][0]["name"], "Infrastructure Hardening")
        self.assertEqual(summary["sequence"][-1]["name"], "GO-LIVE APPROVAL")
        self.assertEqual(summary["sequence"][-1]["status"], ReadinessStatus.WARN)
