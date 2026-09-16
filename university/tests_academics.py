"""Comprehensive tests for Student & Admin Academics modules.
Verifies Unit Registration, Provisional Transcripts, and Academic Transcripts.
"""
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.models import (
    AcademicTerm, AcademicYear, Course, Department, DocumentReleaseControl,
    Enrollment, Program, SemesterRegistration
)

User = get_user_model()


class AcademicsModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="School of Computing", code="SOC", color="#6C5CE7")
        cls.program = Program.objects.create(name="BSc Computer Science", department=cls.dept, level="UG")
        cls.academic_year = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=330),
            status=AcademicYear.Status.PUBLISHED,
            is_current=True,
        )
        cls.term = AcademicTerm.objects.create(
            academic_year=cls.academic_year,
            name="2026/2027 Academic Year",
            semester_number=1,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() + timedelta(days=60),
            registration_start_date=date.today() - timedelta(days=10),
            registration_end_date=date.today() + timedelta(days=30),
            status=AcademicYear.Status.CURRENT,
            is_current=True
        )

        # Create Courses
        cls.course1 = Course.objects.create(
            code="CSC101", title="Introduction to Computer Science",
            department=cls.dept, program=cls.program, credits=4, semester_no=1
        )
        cls.course2 = Course.objects.create(
            code="CSC102", title="Discrete Structures",
            department=cls.dept, program=cls.program, credits=4, semester_no=1
        )
        cls.course3 = Course.objects.create(
            code="CSC103", title="Computer Systems & Architecture",
            department=cls.dept, program=cls.program, credits=4, semester_no=1
        )
        cls.course_heavy = Course.objects.create(
            code="CSC999", title="Major Capstone Project",
            department=cls.dept, program=cls.program, credits=20, semester_no=1
        )

        # Users & Profiles
        cls.student_user = User.objects.create_user(
            username="test.student", email="student@university.ac.ke",
            password="password123", role=Role.STUDENT, first_name="Aarav", last_name="Sharma"
        )
        cls.student = StudentProfile.objects.create(
            user=cls.student_user, roll_no="SC/001/2026",
            program=cls.program, current_semester=1
        )

        cls.student_user2 = User.objects.create_user(
            username="test.student2", email="student2@university.ac.ke",
            password="password123", role=Role.STUDENT, first_name="Jane", last_name="Doe"
        )
        cls.student2 = StudentProfile.objects.create(
            user=cls.student_user2, roll_no="SC/002/2026",
            program=cls.program, current_semester=1
        )

        cls.admin_user = User.objects.create_user(
            username="admin.academic", email="registrar@university.ac.ke",
            password="password123", role=Role.ADMIN, first_name="Academic", last_name="Registrar"
        )

    def complete_semester_registration(self):
        self.client.force_login(self.student_user)
        self.client.post(reverse("university:student_semester_registration"))

    def test_student_register_units_view_and_add_unit(self):
        """Student can view the registration page and add an available course unit."""
        self.complete_semester_registration()
        url = reverse("university:student_register_units")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Register Units")
        self.assertContains(res, "CSC101")

        # Add course 1
        post_res = self.client.post(url, {"action": "add_unit", "course_id": self.course1.pk})
        self.assertRedirects(post_res, url)

        # Verify registration and enrollment created
        reg = SemesterRegistration.objects.get(student=self.student, term=self.term)
        self.assertEqual(reg.status, SemesterRegistration.REGISTERED)
        self.assertEqual(reg.total_credits, 4)
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.course1, status=Enrollment.DRAFT).exists())

    def test_student_cannot_add_duplicate_or_exceed_credits(self):
        """Student cannot add duplicate units and cannot exceed 24 total credits."""
        self.complete_semester_registration()
        url = reverse("university:student_register_units")

        # Add course 1
        self.client.post(url, {"action": "add_unit", "course_id": self.course1.pk})

        # Try duplicate
        dup_res = self.client.post(url, {"action": "add_unit", "course_id": self.course1.pk})
        self.assertRedirects(dup_res, url)
        self.assertEqual(Enrollment.objects.filter(student=self.student, course=self.course1).count(), 1)

        # Try adding course that exceeds 24 credits (4 + 20 = 24 is ok, but add another = 28 fails)
        self.client.post(url, {"action": "add_unit", "course_id": self.course_heavy.pk})
        reg = SemesterRegistration.objects.get(student=self.student, term=self.term)
        self.assertEqual(reg.total_credits, 24)

        # Trying to add CSC102 (4 credits) should be rejected
        overflow_res = self.client.post(url, {"action": "add_unit", "course_id": self.course2.pk})
        self.assertRedirects(overflow_res, url)
        reg.refresh_from_db()
        self.assertEqual(reg.total_credits, 24)
        self.assertFalse(Enrollment.objects.filter(student=self.student, course=self.course2).exists())

    def test_student_cannot_post_a_course_outside_their_programme_or_department(self):
        other_dept = Department.objects.create(name="School of Law", code="SOL-REG")
        unrelated = Course.objects.create(
            code="LAW101", title="Foundations of Law", department=other_dept, credits=3,
        )
        self.complete_semester_registration()
        response = self.client.post(reverse("university:student_register_units"), {
            "action": "add_unit", "course_id": unrelated.pk,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Enrollment.objects.filter(student=self.student, course=unrelated).exists())

    def test_student_drop_unit(self):
        """Student can drop a previously added course unit while in draft."""
        self.complete_semester_registration()
        url = reverse("university:student_register_units")
        self.client.post(url, {"action": "add_unit", "course_id": self.course1.pk})

        enr = Enrollment.objects.get(student=self.student, course=self.course1)
        drop_res = self.client.post(url, {"action": "drop_unit", "enrollment_id": enr.pk})
        self.assertRedirects(drop_res, url)

        reg = SemesterRegistration.objects.get(student=self.student, term=self.term)
        self.assertEqual(reg.total_credits, 0)
        enr.refresh_from_db()
        self.assertEqual(enr.status, Enrollment.DROPPED)

    def test_student_submit_registration(self):
        """Student can submit draft registration for formal administrative review."""
        self.complete_semester_registration()
        url = reverse("university:student_register_units")
        self.client.post(url, {"action": "add_unit", "course_id": self.course1.pk})
        self.client.post(url, {"action": "add_unit", "course_id": self.course2.pk})

        submit_res = self.client.post(url, {"action": "submit_registration"})
        self.assertRedirects(submit_res, url)

        reg = SemesterRegistration.objects.get(student=self.student, term=self.term)
        self.assertEqual(reg.status, SemesterRegistration.APPROVED)
        self.assertIsNotNone(reg.submitted_at)
        # Child enrollments updated
        self.assertEqual(
            Enrollment.objects.filter(student=self.student, status=Enrollment.ACTIVE).count(), 2
        )

    def test_student_transcripts_submodules_view(self):
        """Student can view their own Provisional and Academic Transcripts."""
        self.client.force_login(self.student_user)
        prov_url = reverse("university:student_provisional_transcript")
        res_prov = self.client.get(prov_url)
        self.assertEqual(res_prov.status_code, 200)

        acad_url = reverse("university:student_academic_transcript")
        res_acad = self.client.get(acad_url)
        self.assertEqual(res_acad.status_code, 200)

    def test_admin_unit_registration_management(self):
        """Admin can monitor registrations and filter the list."""
        reg = SemesterRegistration.objects.create(
            student=self.student, term=self.term, semester_no=1,
            status=SemesterRegistration.APPROVED, total_credits=8
        )
        Enrollment.objects.create(
            student=self.student, course=self.course1, term=self.term,
            registration=reg, status=Enrollment.ACTIVE
        )

        self.client.force_login(self.admin_user)
        admin_url = reverse("university:admin_unit_registrations")
        res = self.client.get(admin_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.student.roll_no)

    def test_admin_unit_registration_pending_metric_counts_drafts_not_dead_submitted_status(self):
        """
        Registrations are auto-approved on student submission (see
        submit_registration) — nothing ever reaches SUBMITTED, so the
        'pending' KPI must reflect DRAFT (not-yet-submitted) registrations,
        never the unreachable SUBMITTED status.
        """
        SemesterRegistration.objects.create(
            student=self.student, term=self.term, semester_no=1,
            status=SemesterRegistration.DRAFT, total_credits=0,
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("university:admin_unit_registrations"))
        self.assertEqual(res.context["pending_count"], 1)

    def test_admin_unit_registration_detail_actions(self):
        """Admin can review, add units, and approve/reject individual registrations."""
        reg = SemesterRegistration.objects.create(
            student=self.student, term=self.term, semester_no=1,
            status=SemesterRegistration.APPROVED, total_credits=4
        )
        Enrollment.objects.create(
            student=self.student, course=self.course1, term=self.term,
            registration=reg, status=Enrollment.ACTIVE
        )

        self.client.force_login(self.admin_user)
        detail_url = reverse("university:admin_unit_registration_detail", args=[reg.pk])
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, 200)

        # Admin adds course 2
        self.client.post(detail_url, {"action": "add_unit", "course_id": self.course2.pk})
        reg.refresh_from_db()
        self.assertEqual(reg.total_credits, 8)
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.course2).exists())

        # Admin approves with remarks
        self.client.post(detail_url, {
            "action": "approve", "admin_remarks": "Approved by Academic Registrar."
        })
        reg.refresh_from_db()
        self.assertEqual(reg.status, SemesterRegistration.APPROVED)
        self.assertEqual(reg.admin_remarks, "Approved by Academic Registrar.")

    def test_admin_transcripts_directories(self):
        """Admin can access Provisional and Academic Transcripts directories."""
        self.client.force_login(self.admin_user)

        prov_url = reverse("university:admin_provisional_transcripts")
        res_prov = self.client.get(prov_url)
        self.assertEqual(res_prov.status_code, 200)
        self.assertContains(res_prov, self.student.roll_no)

        acad_url = reverse("university:admin_academic_transcripts")
        res_acad = self.client.get(acad_url)
        self.assertEqual(res_acad.status_code, 200)
        self.assertContains(res_acad, self.student.roll_no)

    def test_admin_supplementary_list_view(self):
        """Admin can access the supplementary list directory."""
        self.client.force_login(self.admin_user)
        url = reverse("university:admin_supplementary_list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_admin_exam_nominal_rolls_view(self):
        """Admin can access the nominal rolls directory."""
        self.client.force_login(self.admin_user)
        url = reverse("university:admin_exam_nominal_rolls")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_permissions_student_cannot_access_admin_academics(self):
        """Regular student cannot access admin registration management."""
        self.client.force_login(self.student_user)
        res = self.client.get(reverse("university:admin_unit_registrations"))
        self.assertEqual(res.status_code, 403)


class StudentTransferDecisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="School of Business", code="SOB", color="#00b894")
        cls.program_a = Program.objects.create(name="BCom", code="BCOM-T", department=dept, level="UG")
        cls.program_b = Program.objects.create(name="BBA", code="BBA-T", department=dept, level="UG")
        student_user = User.objects.create_user(
            username="transfer.student", email="transfer@university.ac.ke",
            password="password123", role=Role.STUDENT,
        )
        cls.student = StudentProfile.objects.create(
            user=student_user, roll_no="SB/001/2026", program=cls.program_a, current_semester=1
        )
        cls.admin_user = User.objects.create_user(
            username="admin.transfer", email="admin.transfer@university.ac.ke",
            password="password123", role=Role.ADMIN,
        )

    def _make_request(self):
        from university.models import StudentTransferRequest
        return StudentTransferRequest.objects.create(
            student=self.student, from_program=self.program_a, to_program=self.program_b,
            reason="Better fit for career goals",
        )

    def test_approval_updates_program_and_logs_audit(self):
        from university.models import StudentTransferRequest, AuditLog
        req = self._make_request()
        self.client.force_login(self.admin_user)
        url = reverse("university:admin_student_transfer_decision", args=[req.pk])
        res = self.client.post(url, {"decision": StudentTransferRequest.Status.APPROVED, "review_comments": "Approved"})
        self.assertRedirects(res, reverse("university:admin_student_transfers"))

        req.refresh_from_db()
        self.assertEqual(req.status, StudentTransferRequest.Status.APPROVED)
        self.student.refresh_from_db()
        self.assertEqual(self.student.program_id, self.program_b.id)
        self.assertTrue(AuditLog.objects.filter(entity="StudentTransferRequest", entity_id=req.id).exists())

    def test_cannot_approve_same_transfer_twice(self):
        from university.models import StudentTransferRequest
        req = self._make_request()
        self.client.force_login(self.admin_user)
        url = reverse("university:admin_student_transfer_decision", args=[req.pk])
        self.client.post(url, {"decision": StudentTransferRequest.Status.APPROVED})
        second = self.client.post(url, {"decision": StudentTransferRequest.Status.APPROVED})
        self.assertEqual(second.status_code, 404)


class SenateApprovalDocumentGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from university.models import DocumentReleaseControl
        dept = Department.objects.create(name="School of Law", code="SOL", color="#0984e3")
        program = Program.objects.create(name="LLB", code="LLB-T", department=dept, level="UG")
        cls.academic_year = AcademicYear.objects.create(
            name="2026/2027", start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=330),
            status=AcademicYear.Status.PUBLISHED, is_current=True,
        )
        cls.term = AcademicTerm.objects.create(
            academic_year=cls.academic_year, name="Law Term 1", semester_number=1,
            start_date=date.today() - timedelta(days=10), end_date=date.today() + timedelta(days=60),
            status=AcademicYear.Status.CURRENT, is_current=True,
        )
        student_user = User.objects.create_user(
            username="senate.student", email="senate@university.ac.ke",
            password="password123", role=Role.STUDENT,
        )
        cls.student = StudentProfile.objects.create(user=student_user, roll_no="SL/001/2026", program=program)
        cls.control = DocumentReleaseControl.objects.create(
            term=cls.term, document_type=DocumentReleaseControl.DocumentType.RESULTS_STATEMENT,
            is_open=True, require_senate_approval=True,
        )

    def test_blocked_until_senate_approved(self):
        from university.document_access_services import check_document_access
        allowed, reason, control, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.RESULTS_STATEMENT, term=self.term
        )
        self.assertFalse(allowed)
        self.assertIn("Senate", reason)

    def test_allowed_after_senate_approval_recorded(self):
        from university.document_access_services import check_document_access
        self.term.senate_approved_at = timezone.now()
        self.term.save(update_fields=["senate_approved_at"])
        allowed, reason, control, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.RESULTS_STATEMENT, term=self.term
        )
        self.assertTrue(allowed)

    def test_admin_can_toggle_senate_approval_via_semester_action(self):
        admin_user = User.objects.create_user(
            username="registrar.senate", email="registrar.senate@university.ac.ke",
            password="password123", role=Role.ADMIN,
        )
        self.client.force_login(admin_user)
        url = reverse("university:semester_action", args=[self.term.pk, "senate_approve"])
        res = self.client.post(url)
        self.assertEqual(res.status_code, 302)
        self.term.refresh_from_db()
        self.assertIsNotNone(self.term.senate_approved_at)
        self.assertEqual(self.term.senate_approved_by_id, admin_user.id)

        revoke_url = reverse("university:semester_action", args=[self.term.pk, "senate_unapprove"])
        self.client.post(revoke_url)
        self.term.refresh_from_db()
        self.assertIsNone(self.term.senate_approved_at)
