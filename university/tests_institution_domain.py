from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile, FacultyProfile
from university.models import (
    AuditLog,
    DomainMigrationRecord,
    SystemSetting,
)
from university.identity_models import (
    InstitutionalEmail,
    UserAccount,
    UserType,
)
from university.institution_domain_services import (
    clean_domain,
    derive_subdomains,
    execute_domain_migration,
    generate_staff_email,
    generate_student_email,
    get_institution_settings,
    get_primary_domain,
    get_staff_email_domain,
    get_student_email_domain,
    is_institutional_email,
    preview_domain_migration,
    validate_staff_email,
    validate_student_email,
)
from university.settings_services import set_setting

User = get_user_model()


class InstitutionDomainArchitectureTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="sysadmin",
            email="admin@ums.ac.ke",
            password="AdminPassword123!",
            role=Role.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            username="m.wabs",
            first_name="Michael",
            last_name="Wabs",
            email="m.wabs@ums.ac.ke",
            password="Password123!",
            role=Role.FACULTY,
        )
        self.student_user = User.objects.create_user(
            username="cs012026",
            first_name="Amina",
            last_name="Ouma",
            email="cs012026@students.ums.ac.ke",
            password="Password123!",
            role=Role.STUDENT,
        )

        # Baseline institutional email records
        self.staff_inst_email = InstitutionalEmail.objects.create(
            user=self.staff_user,
            address="m.wabs@ums.ac.ke",
            kind=UserType.STAFF,
            status=InstitutionalEmail.Status.ACTIVE,
            is_primary=True,
        )
        self.student_inst_email = InstitutionalEmail.objects.create(
            user=self.student_user,
            address="cs012026@students.ums.ac.ke",
            kind=UserType.STUDENT,
            status=InstitutionalEmail.Status.ACTIVE,
            is_primary=True,
        )

    def test_domain_derivation_and_normalization(self):
        """Test cleaning and automatic derivation of subdomains from root domain."""
        self.assertEqual(clean_domain("https://UMS.AC.KE/"), "ums.ac.ke")
        self.assertEqual(clean_domain("@newuniversity.ac.ke"), "newuniversity.ac.ke")

        derived = derive_subdomains("newuniversity.ac.ke", student_prefix="students")
        self.assertEqual(derived["primary_domain"], "newuniversity.ac.ke")
        self.assertEqual(derived["staff_domain"], "newuniversity.ac.ke")
        self.assertEqual(derived["student_domain"], "students.newuniversity.ac.ke")

        derived_custom = derive_subdomains("globaltech.edu", student_prefix="learn")
        self.assertEqual(derived_custom["student_domain"], "learn.globaltech.edu")

    def test_dynamic_email_generation(self):
        """Test staff and student email generation using active configurable domains."""
        staff_email = generate_staff_email(first_name="Michael", last_name="Wabs", username="m.wabs")
        self.assertEqual(staff_email, "michael.wabs@ums.ac.ke")

        student_email = generate_student_email(reg_number="CS/01/2026")
        self.assertEqual(student_email, "cs012026@students.ums.ac.ke")

    def test_server_side_email_validation(self):
        """Test strict domain validation for staff and student addresses."""
        valid, _ = validate_staff_email("lecturer@ums.ac.ke")
        self.assertTrue(valid)

        invalid, err = validate_staff_email("lecturer@gmail.com")
        self.assertFalse(invalid)
        self.assertIn("Staff email must belong to the official domain", err)

        valid_student, _ = validate_student_email("cs012026@students.ums.ac.ke", reg_no="CS/01/2026")
        self.assertTrue(valid_student)

        invalid_student_domain, err = validate_student_email("cs012026@ums.ac.ke")
        self.assertFalse(invalid_student_domain)

        self.assertTrue(is_institutional_email("dean@ums.ac.ke"))
        self.assertTrue(is_institutional_email("cs012026@students.ums.ac.ke"))
        self.assertFalse(is_institutional_email("outsider@external.com"))

    def test_preview_domain_migration_calculation(self):
        """Test dry-run migration preview calculations and impact analysis."""
        preview = preview_domain_migration(
            new_primary_domain="newuniversity.ac.ke",
            new_staff_domain="newuniversity.ac.ke",
            new_student_domain="students.newuniversity.ac.ke",
            new_institution_name="New University of Science",
            new_short_name="NUST",
        )

        self.assertTrue(preview["is_domain_changing"])
        self.assertGreaterEqual(preview["staff_affected_count"], 1)
        self.assertGreaterEqual(preview["student_affected_count"], 1)
        self.assertEqual(preview["target"]["primary_domain"], "newuniversity.ac.ke")
        self.assertEqual(preview["target"]["staff_email_domain"], "newuniversity.ac.ke")
        self.assertEqual(preview["target"]["student_email_domain"], "students.newuniversity.ac.ke")

    def test_execute_domain_migration_with_alias_preservation(self):
        """Test full migration execution: settings update, email rotation, alias archiving, and audit trail."""
        new_settings = {
            "institution_name": "Apex National University",
            "institution_short_name": "ANU",
            "primary_domain": "apex.ac.ke",
            "staff_email_domain": "apex.ac.ke",
            "student_email_domain": "students.apex.ac.ke",
            "student_email_subdomain_prefix": "students",
        }

        record = execute_domain_migration(
            new_settings_data=new_settings,
            initiated_by=self.admin,
            migration_policy=DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES,
        )

        self.assertEqual(record.status, DomainMigrationRecord.Status.COMPLETED)
        self.assertEqual(record.new_primary_domain, "apex.ac.ke")
        self.assertEqual(record.new_institution_name, "Apex National University")

        # Verify active settings updated
        current = get_institution_settings()
        self.assertEqual(current["primary_domain"], "apex.ac.ke")
        self.assertEqual(current["staff_email_domain"], "apex.ac.ke")
        self.assertEqual(current["student_email_domain"], "students.apex.ac.ke")

        # Verify staff email rotation and alias archiving
        old_staff_email = InstitutionalEmail.objects.get(id=self.staff_inst_email.id)
        self.assertFalse(old_staff_email.is_primary)
        self.assertEqual(old_staff_email.status, InstitutionalEmail.Status.ARCHIVED)
        self.assertIsNotNone(old_staff_email.archived_at)

        new_staff_email = InstitutionalEmail.objects.filter(
            user=self.staff_user, is_primary=True
        ).first()
        self.assertIsNotNone(new_staff_email)
        self.assertEqual(new_staff_email.address, "m.wabs@apex.ac.ke")
        self.assertEqual(new_staff_email.status, InstitutionalEmail.Status.ACTIVE)

        # Verify student email rotation and alias archiving
        old_student_email = InstitutionalEmail.objects.get(id=self.student_inst_email.id)
        self.assertFalse(old_student_email.is_primary)
        self.assertEqual(old_student_email.status, InstitutionalEmail.Status.ARCHIVED)

        new_student_email = InstitutionalEmail.objects.filter(
            user=self.student_user, is_primary=True
        ).first()
        self.assertIsNotNone(new_student_email)
        self.assertEqual(new_student_email.address, "cs012026@students.apex.ac.ke")

        # Verify AuditLog created
        audit = AuditLog.objects.filter(entity="InstitutionalDomainSettings").order_by("-id").first()
        self.assertIsNotNone(audit)
        self.assertIn("Institutional domain migrated", audit.description)

    def test_admin_settings_ui_view_and_preview_api(self):
        """Test admin dashboard access and AJAX preview endpoint."""
        self.client.force_login(self.admin)

        # 1. View settings page
        resp = self.client.get(reverse("university:admin_institution_settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "University Identity & Domain Architecture")
        self.assertContains(resp, "Primary University Root Domain")

        # 2. AJAX Preview API
        preview_resp = self.client.get(
            reverse("university:api_preview_domain_migration"),
            {"primary_domain": "moderntech.ac.ke", "student_email_subdomain_prefix": "learn"},
        )
        self.assertEqual(preview_resp.status_code, 200)
        data = preview_resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["preview"]["target"]["primary_domain"], "moderntech.ac.ke")
        self.assertEqual(data["preview"]["target"]["student_email_domain"], "learn.moderntech.ac.ke")

        # 3. POST Migration execution view
        post_resp = self.client.post(
            reverse("university:execute_domain_migration"),
            {
                "institution_name": "Modern Tech University",
                "institution_short_name": "MTU",
                "primary_domain": "moderntech.ac.ke",
                "staff_email_domain": "moderntech.ac.ke",
                "email_student_domain": "learn.moderntech.ac.ke",
                "student_email_subdomain_prefix": "learn",
                "migration_policy": DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES,
            },
            follow=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertEqual(get_primary_domain(), "moderntech.ac.ke")
        self.assertEqual(get_student_email_domain(), "learn.moderntech.ac.ke")

    def test_rbac_security_blocks_non_admin(self):
        """Test that regular students and unauthenticated users cannot alter domain settings."""
        # Unauthenticated
        resp = self.client.get(reverse("university:admin_institution_settings"))
        self.assertEqual(resp.status_code, 302)

        # Student user
        self.client.force_login(self.student_user)
        resp_student = self.client.get(reverse("university:admin_institution_settings"))
        self.assertEqual(resp_student.status_code, 302)
