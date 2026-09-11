import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from university.models import AuditLog
from accounts.models import StudentProfile


class PrivacyControlsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="privacy.student", email="privacy@example.edu",
            password="A-secure-test-password-123!", first_name="Privacy", last_name="Student",
        )

    def test_privacy_notice_is_public_and_links_to_rights(self):
        response = self.client.get(reverse("university:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy &amp; Data Protection")
        self.assertContains(response, reverse("accounts:profile"))

    def test_personal_export_requires_authentication(self):
        response = self.client.get(reverse("accounts:personal_data_export"))
        self.assertEqual(response.status_code, 302)

    def test_personal_export_excludes_credentials_and_audits_access(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:personal_data_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="ums-personal-data.json"')
        payload = json.loads(response.content)
        body = response.content.decode()
        self.assertEqual(payload["account"]["username"], self.user.username)
        self.assertNotIn(self.user.password, body)
        self.assertNotIn("password_hash", body)
        self.assertNotIn("token_hash", body)
        self.assertNotIn("session_key", body)
        self.assertTrue(AuditLog.objects.filter(
            user=self.user, action=AuditLog.Action.EXPORT, entity="PersonalData"
        ).exists())

    def test_personal_export_includes_parent_guardian_details(self):
        profile = StudentProfile.objects.create(
            user=self.user,
            roll_no="PRIV-001",
            guardian_name="Parent Example",
            guardian_relationship="Parent",
            guardian_phone="+254700000001",
            guardian_email="parent@example.edu",
            guardian_address="Nairobi",
        )
        self.client.force_login(self.user)
        payload = json.loads(self.client.get(reverse("accounts:personal_data_export")).content)
        guardian = payload["student_profile"]
        self.assertEqual(guardian["guardian_name"], "Parent Example")
        self.assertEqual(guardian["guardian_relationship"], "Parent")
        self.assertEqual(guardian["guardian_phone"], "+254700000001")
        self.assertEqual(guardian["guardian_email"], "parent@example.edu")
        self.assertEqual(guardian["guardian_address"], "Nairobi")
