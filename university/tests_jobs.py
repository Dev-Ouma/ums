from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from university.control_models import ControlHeartbeat


User = get_user_model()


class BackgroundJobsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("jobs-admin", password="password123", role=Role.ADMIN)

    def test_job_dashboard_lists_all_required_processes(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:job_dashboard"))
        self.assertEqual(response.status_code, 200)
        for label in ("Scheduled backups", "Email notifications", "SMS notifications", "Payment verification", "Webhook processing", "Integration synchronization", "Report generation", "Payment reconciliation", "Retention cleanup", "Maintenance scheduling"):
            self.assertContains(response, label)

    def test_job_runner_updates_server_heartbeat_without_browser(self):
        call_command("run_background_jobs")
        heartbeat = ControlHeartbeat.objects.get(key="job_runner")
        self.assertIsNotNone(heartbeat.last_success_at)
