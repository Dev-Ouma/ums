from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from university.security_testing_services import run_automated_checks


User = get_user_model()


class SecurityTestingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("testing-admin", password="password123", role=Role.ADMIN)

    def test_security_testing_dashboard_requires_admin_and_lists_manual_gates(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:security_testing_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dependency vulnerability scan")
        self.assertContains(response, "Independent penetration test")
        self.assertContains(response, "Evidence required")

    def test_available_automated_checks_run_without_claiming_external_scanners(self):
        results = {result["key"]: result for result in run_automated_checks()}
        self.assertEqual(results["configuration"]["status"], "PASS")
        self.assertEqual(results["secrets"]["status"], "PASS")
        self.assertEqual(results["dependency"]["status"], "EVIDENCE REQUIRED")

    def test_security_preflight_command_runs(self):
        call_command("security_preflight")
