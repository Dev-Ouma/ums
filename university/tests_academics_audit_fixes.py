"""
Regression tests for the end-to-end Academics module audit fixes:
- Orphaned exam/course results no longer fabricate a "3 credits" fallback in CGPA
- Missing audit logging on unit registration decisions, supplementary exam
  decisions, graduation ceremony creation, and logbook review
- Staff certificate lookups no longer silently fall back to an arbitrary
  student's official Degree/Clearance certificate when unidentified
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import FacultyProfile, Role, StudentProfile
from university.attachment_services import review_logbook_entry
from university.graduation_services import audit_graduation_eligibility
from university.models import (
    AcademicTerm,
    AttachmentLogbookEntry,
    AttachmentPlacement,
    AuditLog,
    Course,
    Department,
    Exam,
    GraduationCeremony,
    Program,
    Result,
    SemesterRegistration,
    SupplementaryExamRegistration,
)

User = get_user_model()


class GraduationCreditFabricationTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        self.term = AcademicTerm.objects.create(
            name="Sem 1", start_date=date(2026, 1, 1), end_date=date(2026, 5, 1), is_current=True,
        )
        user = User.objects.create_user(username="stu1", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=user, roll_no="BCS/001/2026", program=self.prog)

        self.course = Course.objects.create(code="CSC101", title="Intro", department=self.dept, credits=4)
        self.exam_with_course = Exam.objects.create(
            course=self.course, term=self.term, name="CSC101 Final", status=Exam.Status.PUBLISHED, max_marks=100,
        )

    def test_orphaned_result_excluded_from_cgpa_not_fabricated(self):
        """
        The database schema enforces Exam.course and Result.exam as NOT NULL
        (on_delete=CASCADE), so a genuinely orphaned course link can't be
        constructed through the ORM in a healthy database -- but the audit
        function defensively guarded against it anyway, and that defensive
        branch used to fabricate a "3 credits" fallback. Simulate the
        orphaned-course case at the object level (bypassing the DB
        constraint) to verify the fixed code path no longer fabricates data.
        """
        real_result = Result.objects.create(exam=self.exam_with_course, student=self.student, marks_obtained=85)
        orphaned_result = SimpleNamespace(
            pk="orphan-1", grade="B",
            exam=SimpleNamespace(course=None),
        )

        fake_results = [real_result, orphaned_result]
        with mock.patch(
            "university.graduation_services.Result.objects.filter"
        ) as mock_filter:
            fake_queryset = mock.MagicMock()
            fake_queryset.exists.return_value = True
            fake_queryset.__iter__.return_value = iter(fake_results)
            mock_filter.return_value.select_related.return_value = fake_queryset
            audit = audit_graduation_eligibility(self.student)

        # Only the real, linked-course result should contribute credits.
        self.assertEqual(audit["credits_earned"], 4)
        # The orphaned result must block eligibility with a clear issue,
        # not silently get counted using a fabricated 3-credit weight.
        self.assertFalse(audit["eligible"])
        self.assertTrue(any("missing course/credit data" in issue for issue in audit["issues"]))

    def test_clean_results_still_compute_normally(self):
        Result.objects.create(exam=self.exam_with_course, student=self.student, marks_obtained=85)
        audit = audit_graduation_eligibility(self.student)
        self.assertEqual(audit["credits_earned"], 4)
        self.assertNotIn("missing course/credit data", " ".join(audit["issues"]))


class AcademicsAuditLoggingTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        self.term = AcademicTerm.objects.create(
            name="Sem 1", start_date=date(2026, 1, 1), end_date=date(2026, 5, 1), is_current=True,
        )
        self.admin_user = User.objects.create_user(
            username="admin.academics", email="admin.academics@ums.ac.ke", role=Role.ADMIN, password="password123",
        )
        student_user = User.objects.create_user(username="stu2", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=student_user, roll_no="BCS/002/2026", program=self.prog)
        self.course = Course.objects.create(code="CSC102", title="Data Structures", department=self.dept, credits=4)
        self.client = Client()

    def test_unit_registration_approve_logs_audit_entry(self):
        reg = SemesterRegistration.objects.create(
            student=self.student, term=self.term, semester_no=1,
            academic_year="2026/2027", status=SemesterRegistration.SUBMITTED,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("university:admin_unit_registration_detail", args=[reg.pk]),
            {"action": "approve", "admin_remarks": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ACADEMICS, entity="SemesterRegistration", entity_id=reg.pk,
                description__icontains="Approved",
            ).exists()
        )

    def test_unit_registration_reject_logs_audit_entry(self):
        reg = SemesterRegistration.objects.create(
            student=self.student, term=self.term, semester_no=1,
            academic_year="2026/2027", status=SemesterRegistration.SUBMITTED,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("university:admin_unit_registration_detail", args=[reg.pk]),
            {"action": "reject", "admin_remarks": "Incomplete"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ACADEMICS, entity="SemesterRegistration", entity_id=reg.pk,
                description__icontains="Rejected",
            ).exists()
        )

    def test_supplementary_exam_approve_logs_audit_entry(self):
        supp = SupplementaryExamRegistration.objects.create(
            student=self.student, course=self.course, term=self.term,
            status=SupplementaryExamRegistration.Status.PENDING,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("university:admin_supplementary_decision", args=[supp.pk]), {"action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ACADEMICS, entity="SupplementaryExamRegistration", entity_id=supp.pk,
                description__icontains="Approved",
            ).exists()
        )

    def test_ceremony_create_logs_audit_entry_and_validates_date(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("university:admin_ceremony_create"), {
            "title": "16th Congregation",
            "academic_year": "2026/2027",
            "ceremony_date": "2027-12-01",
            "venue": "Main Pavilion",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GraduationCeremony.objects.filter(title="16th Congregation").exists())
        self.assertTrue(
            AuditLog.objects.filter(module=AuditLog.Module.ACADEMICS, entity="GraduationCeremony").exists()
        )

    def test_ceremony_create_rejects_invalid_date_instead_of_crashing(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("university:admin_ceremony_create"), {
            "title": "Bad Ceremony",
            "academic_year": "2026/2027",
            "ceremony_date": "not-a-date",
        })
        self.assertNotEqual(response.status_code, 500)
        self.assertFalse(GraduationCeremony.objects.filter(title="Bad Ceremony").exists())

    def test_logbook_review_logs_audit_entry(self):
        faculty_user = User.objects.create_user(username="fac1", password="x", role=Role.FACULTY)
        faculty_profile = FacultyProfile.objects.create(user=faculty_user, department=self.dept)
        placement = AttachmentPlacement.objects.create(
            student=self.student, term=self.term, company_name="Acme Corp",
            company_supervisor_name="Jane", company_supervisor_phone="0700000000",
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 1),
            academic_supervisor=faculty_profile,
        )
        entry = AttachmentLogbookEntry.objects.create(
            attachment=placement, week_number=1,
            date_from=date(2026, 1, 1), date_to=date(2026, 1, 7),
            activities_summary="Onboarding", skills_acquired="Git",
        )
        self.client.force_login(faculty_user)
        response = self.client.post(
            reverse("university:faculty_attachment_review_log", args=[entry.pk]),
            {"feedback": "Good work"},
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertTrue(entry.faculty_supervisor_reviewed)
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.ACADEMICS, entity="AttachmentLogbookEntry", entity_id=entry.pk,
            ).exists()
        )


class GraduationCertificateFallbackTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        self.admin_user = User.objects.create_user(
            username="admin.cert", email="admin.cert@ums.ac.ke", role=Role.ADMIN, password="password123",
        )
        self.client = Client()

    def test_unidentified_staff_request_404s_instead_of_leaking_arbitrary_certificate(self):
        # Another student's GraduationApplication exists in the system, but the
        # staff user issues the request with no app_id/student_id/roll_no and
        # has no student_profile of their own.
        student_user = User.objects.create_user(username="stu3", password="x", role=Role.STUDENT)
        student = StudentProfile.objects.create(user=student_user, roll_no="BCS/003/2026", program=self.prog)
        from university.models import GraduationApplication
        GraduationApplication.objects.create(
            student=student, status=GraduationApplication.Status.GRADUATED,
            final_cgpa=Decimal("3.50"), classification=GraduationApplication.Classification.SECOND_UPPER,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("university:student_degree_certificate_pdf"))
        self.assertEqual(response.status_code, 404)
