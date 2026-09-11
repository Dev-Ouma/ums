from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from university.database_security_services import build_database_security_checks


class DatabaseSecurityTests(TestCase):
    @override_settings(DEBUG=True)
    def test_development_database_is_explicitly_flagged(self):
        checks = {check["label"]: check for check in build_database_security_checks()}
        self.assertEqual(checks["Production database backend"]["status"], "WARN")
        self.assertEqual(checks["Database credentials"]["status"], "WARN")

    def test_passwords_are_not_plaintext_and_audit_controls_are_reported(self):
        get_user_model().objects.create_user(username="db-check", password="A-test-password-123!")
        checks = {check["label"]: check for check in build_database_security_checks()}
        self.assertEqual(checks["Password storage"]["status"], "PASS")
        self.assertEqual(checks["Audit record protection"]["status"], "PASS")

    @override_settings(DEBUG=False, DATABASE_PRIVATE_NETWORK=True)
    def test_sqlite_is_a_production_blocker(self):
        checks = {check["label"]: check for check in build_database_security_checks()}
        self.assertEqual(checks["Production database backend"]["status"], "FAIL")
        self.assertEqual(checks["Database encryption in transit"]["status"], "FAIL")
