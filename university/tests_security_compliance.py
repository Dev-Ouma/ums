from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from university.golive_models import GoLiveCategory, IssueSeverity, IssueStatus, ReadinessStatus
from university.models import GoLiveIssue, GoLiveReadiness
from university.security_compliance_services import build_security_compliance_summary


User = get_user_model()


class SecurityComplianceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="security-admin",
            email="security-admin@example.test",
            password="password123",
            role=Role.ADMIN,
        )
        self.student = User.objects.create_user(
            username="security-student",
            email="security-student@example.test",
            password="password123",
            role=Role.STUDENT,
        )

    def test_security_compliance_dashboard_requires_admin_access(self):
        url = reverse("university:security_compliance_dashboard")
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("university:dashboard"))

    def test_security_compliance_dashboard_renders_security_domains(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:security_compliance_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Security &amp; Compliance")
        self.assertContains(response, "Financial &amp; Payment Security")
        self.assertContains(response, "Incident Response")
        self.assertContains(response, "GO-LIVE: NOT APPROVED")

    def test_summary_exposes_explicit_session_and_cookie_controls(self):
        summary = build_security_compliance_summary()
        labels = {check["label"] for check in summary["runtime_checks"]}
        self.assertIn("Session cookie: Secure", labels)
        self.assertIn("Session cookie: HttpOnly", labels)
        self.assertIn("Session cookie: SameSite", labels)
        self.assertIn("Idle session timeout", labels)
        self.assertIn("Session invalidation controls", labels)
        self.assertIn("Concurrent session management", labels)
        self.assertIn("Production database backend", labels)
        self.assertIn("Password storage", labels)
        self.assertIn("Audit record protection", labels)

    @override_settings(DEBUG=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=31536000)
    def test_summary_blocks_approval_for_open_critical_or_unsigned_pass(self):
        for category in GoLiveCategory.values:
            GoLiveReadiness.objects.update_or_create(
                category=category,
                defaults={
                    "status": ReadinessStatus.PASS_,
                    "signed_off_by": self.admin,
                },
            )

        summary = build_security_compliance_summary()
        self.assertFalse(summary["is_approved"])
        self.assertIn("passed domain(s) still need authorized sign-off", " ".join(summary["not_ready_reasons"]))

        for row in GoLiveReadiness.objects.all():
            row.signed_off_at = row.updated_at
            row.save(update_fields=["signed_off_at"])

        GoLiveIssue.objects.create(
            category=GoLiveCategory.SECURITY,
            severity=IssueSeverity.CRITICAL,
            status=IssueStatus.OPEN,
            description="Unprotected admin interface exposed to the Internet.",
            created_by=self.admin,
        )
        summary = build_security_compliance_summary()
        self.assertFalse(summary["is_approved"])
        self.assertEqual(summary["blocking_issues"], 1)

    @override_settings(DEBUG=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=31536000)
    def test_summary_stays_blocked_when_production_database_is_unsafe(self):
        for category in GoLiveCategory.values:
            row, _ = GoLiveReadiness.objects.update_or_create(
                category=category,
                defaults={
                    "status": ReadinessStatus.PASS_,
                    "signed_off_by": self.admin,
                },
            )
            row.signed_off_at = row.updated_at
            row.save(update_fields=["signed_off_at"])

        summary = build_security_compliance_summary()
        self.assertFalse(summary["is_approved"])
        self.assertIn("live security configuration check(s) failed", " ".join(summary["not_ready_reasons"]))

    @override_settings(SECURE_CONTENT_TYPE_NOSNIFF=True, SECURE_REFERRER_POLICY="strict-origin-when-cross-origin", X_FRAME_OPTIONS="DENY")
    def test_security_headers_are_present_on_protected_route(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("university:security_compliance_dashboard"),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["Permissions-Policy"], "camera=(), microphone=(), geolocation=(), payment=()")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(response["X-Permitted-Cross-Domain-Policies"], "none")
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_login_rate_limit_returns_retry_after(self):
        from django.core.cache import cache
        cache.clear()
        url = reverse("accounts:login")
        for _ in range(5):
            self.client.post(url, {"username": "unknown", "password": "wrong"})
        response = self.client.post(url, {"username": "unknown", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_login_rate_limit_fails_closed_when_cache_is_unavailable(self):
        from unittest.mock import patch

        with patch("university.security_decorators.cache.add", side_effect=RuntimeError("cache unavailable")):
            response = self.client.post(reverse("accounts:login"), {"username": "unknown", "password": "wrong"})

        self.assertEqual(response.status_code, 503)

    def test_direct_upload_validator_rejects_mismatched_signature(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from university.upload_security import validate_uploaded_file

        upload = SimpleUploadedFile("statement.pdf", b"not a pdf", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_uploaded_file(upload, extensions={".pdf"}, mime_types={"application/pdf"})
