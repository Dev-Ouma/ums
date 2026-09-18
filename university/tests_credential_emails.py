"""
Regression tests for branded HTML credential/account-lifecycle emails.

Prior to this fix, notify_account_created/notify_password_reset/
notify_password_changed sent plain-text-only messages via send_system_email,
even though send_system_email has always supported an html_body alternative
(used elsewhere for branded payment receipts). These tests confirm the
account-creation, password-reset, and password-changed emails now carry a
branded HTML alternative alongside the plain-text body, and that the
end-to-end admin "Create User" flow actually dispatches one.
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from university.email_services import (
    notify_account_created,
    notify_password_changed,
    notify_password_reset,
)
from university.identity_models import UserType

User = get_user_model()


class BrandedCredentialEmailContentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="branding.test", email="branding.test@ums.ac.ke",
            password="password123", role=Role.STUDENT,
            first_name="Jane", last_name="Doe",
        )

    def test_account_created_with_activation_link_has_branded_html_alternative(self):
        ok, _ = notify_account_created(
            self.user, self.user.username, activation_url="https://ums.local/activate/abc123",
        )
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertTrue(message.alternatives, "Expected an HTML alternative body")
        html, mime_type = message.alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("linear-gradient", html)  # branded header
        self.assertIn("Set Your Password", html)
        self.assertIn("https://ums.local/activate/abc123", html)
        self.assertIn(self.user.username, html)

    def test_account_created_with_temporary_password_never_puts_password_in_email(self):
        ok, _ = notify_account_created(
            self.user, self.user.username, temporary_password="SomeGeneratedSecret123!",
        )
        self.assertTrue(ok)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertNotIn("SomeGeneratedSecret123!", message.body)
        self.assertNotIn("SomeGeneratedSecret123!", html)

    def test_password_reset_email_has_branded_html_alternative(self):
        ok, _ = notify_password_reset(self.user, "https://ums.local/reset/xyz789", expiry_minutes=30)
        self.assertTrue(ok)
        message = mail.outbox[0]
        self.assertTrue(message.alternatives)
        html = message.alternatives[0][0]
        self.assertIn("Reset Your Password", html)
        self.assertIn("https://ums.local/reset/xyz789", html)

    def test_password_changed_email_has_branded_html_alternative(self):
        ok, _ = notify_password_changed(self.user)
        self.assertTrue(ok)
        message = mail.outbox[0]
        self.assertTrue(message.alternatives)
        html = message.alternatives[0][0]
        self.assertIn("Password Changed", html)
        self.assertIn(self.user.username, html)


class UserCreateEndToEndEmailTests(TestCase):
    """
    Exercises the actual admin "Create User" view end to end, confirming a
    real account gets created and a branded activation email is dispatched.
    """
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin.create", email="admin.create@ums.ac.ke",
            password="password123", role=Role.ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_creating_a_user_sends_a_branded_activation_email(self):
        response = self.client.post(reverse("university:user_create"), {
            "user_type": UserType.STAFF,
            "first_name": "New",
            "last_name": "Lecturer",
            "email": "new.lecturer@ums.ac.ke",
            "username": "new.lecturer",
            "role": Role.FACULTY,
            "password_mode": "LINK",
            "status": "PENDING",
            "send_notification": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="new.lecturer").exists())

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("new.lecturer@ums.ac.ke", message.to)
        self.assertTrue(message.alternatives, "Expected a branded HTML alternative on the welcome email")
        html = message.alternatives[0][0]
        self.assertIn("Your Account Is Ready", html)
        self.assertIn("Set Your Password", html)
