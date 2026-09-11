from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role


User = get_user_model()


class IntegrationFeatureStateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="integration-admin",
            email="integration-admin@example.test",
            password="password123",
            role=Role.ADMIN,
        )
        self.student = User.objects.create_user(
            username="integration-student",
            email="integration-student@example.test",
            password="password123",
            role=Role.STUDENT,
        )

    def test_integration_feature_is_contextual_and_not_a_hash_link(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:integration_feature", args=["lms"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LMS Integration")
        self.assertContains(response, "Integration Pending")
        self.assertContains(response, "student and staff identity")
        self.assertContains(response, reverse("university:security_compliance_dashboard"))

    def test_integration_feature_requires_security_admin_access(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("university:integration_feature", args=["lms"]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("university:dashboard"))
