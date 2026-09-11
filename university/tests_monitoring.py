import logging

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from university.logging_filters import redact_log_value


User = get_user_model()


class MonitoringDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="monitor-admin", password="password123", role=Role.ADMIN
        )

    def test_monitoring_dashboard_renders_required_coverage(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:monitoring_dashboard"))
        self.assertEqual(response.status_code, 200)
        for label in ("Authentication failures", "Server errors", "Database errors", "Payment failures", "Webhook failures", "Backup failures", "Suspicious activity"):
            self.assertContains(response, label)

    def test_log_redaction_masks_secrets_and_email(self):
        message = redact_log_value("password=plain-secret token=abc123 user@example.com")
        self.assertNotIn("plain-secret", message)
        self.assertNotIn("abc123", message)
        self.assertNotIn("user@example.com", message)
        self.assertIn("[REDACTED]", message)
        self.assertIn("[EMAIL]", message)
