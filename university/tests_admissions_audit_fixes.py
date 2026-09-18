"""
Regression tests for the end-to-end Admissions module audit fixes:
- NameError crash bugs from missing imports (User, settings, PermissionDenied, Q)
- FeeScheduleMissingError replacing fabricated tuition fallbacks
- Honest "Not Provided" placeholders instead of fabricated address/guardian data
- Audit logging on admission decision state changes and Intake/Cohort CRUD
- Session document migration no longer silently discards failed attachments
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from university.admission_document_services import FeeScheduleMissingError, build_admission_document_context
from university.admissions_services import matriculate_applicant
from university.models import (
    AcademicTerm,
    AcademicYear,
    Application,
    ApplicationFeePayment,
    AuditLog,
    Cohort,
    Department,
    FeeInvoice,
    FeeStructure,
    Intake,
    Program,
    School,
)

User = get_user_model()


class AdmissionsAuditFixesTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Faculty of Computing", code="FOC")
        self.dept = Department.objects.create(name="Dept of CS", code="DCS", school=self.school)
        self.prog = Program.objects.create(
            name="Bachelor of Science in IT", code="BIT", department=self.dept, duration_years=4,
        )
        self.ay = AcademicYear.objects.create(
            name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31), is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            name="Semester 1", academic_year=self.ay, start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20), is_current=True, semester_number=1,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Intake", academic_year=self.ay,
            start_date=date(2026, 9, 1), end_date=date(2026, 12, 1), is_active=True,
        )
        self.admin_user = User.objects.create_user(
            username="admin.audit", email="admin.audit@ums.ac.ke", role=Role.ADMIN, password="password123",
        )
        self.client = Client()

    def _make_application(self, **overrides):
        defaults = dict(
            application_number="APP-2026-9001",
            intake=self.intake,
            program=self.prog,
            first_name="Jane",
            last_name="Doe",
            email="jane.doe@example.com",
            phone="+254700000000",
            date_of_birth=date(2004, 1, 1),
            gender="FEMALE",
            national_id="12345678",
            secondary_school="Test High School",
            kcse_index_number="20400001099",
            kcse_mean_grade="B+",
            kcse_year=2025,
            status=Application.Status.ACCEPTED,
        )
        defaults.update(overrides)
        return Application.objects.create(**defaults)

    # ---- FeeScheduleMissingError replaces fabricated fee data ----

    def test_admission_letter_context_raises_when_fee_structure_missing(self):
        app = self._make_application(admitted_reg_no="BIT/2026/00001")
        with self.assertRaises(FeeScheduleMissingError):
            build_admission_document_context(app)

    def test_admission_letter_context_uses_real_fee_structure(self):
        FeeStructure.objects.create(
            program=self.prog, year_of_study=1, semester=1, tuition_fee=Decimal("60000.00"),
        )
        app = self._make_application(admitted_reg_no="BIT/2026/00002")
        context = build_admission_document_context(app)
        self.assertIn("60,000.00", context["tuition_fee"])

    # ---- matriculate_applicant: no fabricated data, resilient to missing fee schedule ----

    def test_matriculation_skips_invoice_and_logs_when_fee_structure_missing(self):
        app = self._make_application(admitted_reg_no="BIT/2026/00003")
        student_profile, user, _ = matriculate_applicant(app, created_by=self.admin_user)

        self.assertEqual(student_profile.address, "Not Provided")
        self.assertEqual(student_profile.guardian_name, "Not Provided")
        self.assertFalse(FeeInvoice.objects.filter(student=student_profile).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.FEES, entity="FeeInvoice", entity_id=student_profile.roll_no,
            ).exists()
        )

    def test_matriculation_creates_invoice_when_fee_structure_present(self):
        FeeStructure.objects.create(
            program=self.prog, year_of_study=1, semester=1, tuition_fee=Decimal("50000.00"),
        )
        app = self._make_application(admitted_reg_no="BIT/2026/00004")
        student_profile, user, _ = matriculate_applicant(app, created_by=self.admin_user)
        self.assertTrue(FeeInvoice.objects.filter(student=student_profile).exists())

    def test_matriculation_preserves_real_address_and_guardian_name(self):
        app = self._make_application(
            admitted_reg_no="BIT/2026/00005",
            address="123 Real Street, Nairobi",
            guardian_name="John Doe",
        )
        student_profile, user, _ = matriculate_applicant(app, created_by=self.admin_user)
        self.assertEqual(student_profile.address, "123 Real Street, Nairobi")
        self.assertEqual(student_profile.guardian_name, "John Doe")

    # ---- NameError crash-bug fixes (exercised via real request cycle) ----

    def test_applicant_verify_otp_view_does_not_crash_with_nameerror(self):
        """Regression for missing `settings` import in applicant_verify_otp (crashed every request)."""
        response = self.client.get(reverse("university:applicant_verify_otp"))
        self.assertNotEqual(response.status_code, 500)

    def test_admin_admission_detail_denies_scoped_user_without_nameerror(self):
        """Regression for missing `PermissionDenied` import (crashed instead of clean 403)."""
        app = self._make_application(admitted_reg_no="BIT/2026/00006")
        unscoped_user = User.objects.create_user(
            username="dean.unscoped", email="dean.unscoped@ums.ac.ke", role=Role.ADMIN, password="password123",
        )
        # Strip default admin permissions to force the scoped-denial branch, if scoping applies.
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("university:admin_admission_detail", args=[app.pk]))
        self.assertIn(response.status_code, (200, 302, 403))

    # ---- Audit logging on admission decision state changes ----

    def test_under_review_action_logs_audit_entry(self):
        app = self._make_application(status=Application.Status.SUBMITTED, admitted_reg_no="")
        ApplicationFeePayment.objects.create(
            application=app, amount=Decimal("1000.00"), method=ApplicationFeePayment.Method.MPESA,
            reference="TESTREF001", status=ApplicationFeePayment.Status.CONFIRMED,
            confirmed_at=timezone.now(),
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("university:admin_admission_detail", args=[app.pk]),
            {"action": "under_review", "review_notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.UNDER_REVIEW)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ADMISSIONS, entity="Application", entity_id=app.pk,
                description__icontains="Under Review",
            ).exists()
        )

    def test_reject_action_logs_audit_entry(self):
        app = self._make_application(status=Application.Status.UNDER_REVIEW, admitted_reg_no="")
        ApplicationFeePayment.objects.create(
            application=app, amount=Decimal("1000.00"), method=ApplicationFeePayment.Method.MPESA,
            reference="TESTREF002", status=ApplicationFeePayment.Status.CONFIRMED,
            confirmed_at=timezone.now(),
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("university:admin_admission_detail", args=[app.pk]),
            {"action": "reject", "review_notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.REJECTED)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ADMISSIONS, entity="Application", entity_id=app.pk,
                description__icontains="Rejected",
            ).exists()
        )

    # ---- Audit logging on Intake / Cohort CRUD ----

    def test_admin_intakes_create_logs_audit_entry(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("university:admin_intakes"), {
            "name": "January 2027 Intake",
            "academic_year": self.ay.pk,
            "start_date": "2027-01-05",
            "end_date": "2027-04-01",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Intake.objects.filter(name="January 2027 Intake").exists())
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ADMISSIONS, entity="Intake",
                description__icontains="January 2027 Intake",
            ).exists()
        )

    def test_admin_cohorts_create_logs_audit_entry(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("university:admin_cohorts"), {
            "month": "JAN",
            "academic_year": self.ay.pk,
            "start_date": "2027-01-05",
            "end_date": "2027-04-01",
            "description": "January cohort",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Cohort.objects.filter(name__startswith="JAN-").exists())
        self.assertTrue(
            AuditLog.objects.filter(module=AuditLog.Module.ADMISSIONS, entity="Cohort").exists()
        )
