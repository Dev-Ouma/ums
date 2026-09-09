from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, StudentProfile, Role
from university.models import (
    Department, Program, Course, AcademicTerm, Exam, Result,
    FeeInvoice, DocumentReleaseControl, AuditLog
)
from university.document_access_services import check_document_access
from university.transcript_io import build_transcript_context, export_transcript_pdf


class DocumentSecurityAndControlTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="ENG", name="Engineering")
        self.program = Program.objects.create(code="BSC-ENG", name="B.Sc Engineering", department=self.dept)
        
        self.student_user = User.objects.create_user(
            username="test.student", email="student@example.com", password="password123",
            first_name="Alice", last_name="Smith", role=Role.STUDENT
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user, roll_no="STU/2026/001", program=self.program, current_semester=1
        )

        self.staff_user = User.objects.create_user(
            username="test.registrar", email="registrar@example.com", password="password123",
            first_name="Reg", last_name="Istrar", role=Role.ADMIN, is_staff=True
        )

        self.term = AcademicTerm.objects.create(
            name="Fall 2026", start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=90), is_current=True
        )

        self.course = Course.objects.create(
            code="ENG101", title="Engineering Foundations", department=self.dept, program=self.program, credits=4
        )
        self.exam = Exam.objects.create(
            course=self.course, term=self.term, name="Final Exam", kind=Exam.Kind.FINAL,
            weight=100, status=Exam.Status.PUBLISHED
        )
        Result.objects.create(
            exam=self.exam, student=self.student, attendance="PRESENT", marks_obtained=82.5
        )

    def test_default_access_allowed_when_no_controls_configured(self):
        allowed, reason, control, is_bypass = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL, term=self.term
        )
        self.assertTrue(allowed)
        self.assertFalse(is_bypass)

    def test_master_switch_locks_document_for_students(self):
        DocumentReleaseControl.objects.create(
            document_type=DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL,
            term=self.term,
            is_open=False,
            notes="Transcripts are temporarily locked for system moderation."
        )

        allowed, reason, control, is_bypass = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL, term=self.term, user=self.student_user
        )
        self.assertFalse(allowed)
        self.assertIn("temporarily locked", reason)

        # Staff can still bypass
        staff_allowed, _, _, staff_bypass = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL, term=self.term, user=self.staff_user
        )
        self.assertTrue(staff_allowed)
        self.assertTrue(staff_bypass)

    def test_schedule_window_opening_and_locking(self):
        now = timezone.now()
        # Future opening date
        control = DocumentReleaseControl.objects.create(
            document_type=DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL,
            open_date=now + timedelta(days=5),
            is_open=True
        )
        allowed, reason, _, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL, user=self.student_user
        )
        self.assertFalse(allowed)
        self.assertIn("will open on", reason)

        # Active window
        control.open_date = now - timedelta(days=2)
        control.lock_date = now + timedelta(days=5)
        control.save()
        allowed, _, _, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL, user=self.student_user
        )
        self.assertTrue(allowed)

        # Expired window
        control.lock_date = now - timedelta(hours=1)
        control.save()
        allowed, reason, _, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_OFFICIAL, user=self.student_user
        )
        self.assertFalse(allowed)
        self.assertIn("closed on", reason)

    def test_financial_clearance_gate(self):
        DocumentReleaseControl.objects.create(
            document_type=DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL,
            require_financial_clearance=True,
            max_allowed_fee_balance=Decimal("5000.00"),
            is_open=True
        )

        # Create outstanding invoice of 20,000 KES
        FeeInvoice.objects.create(
            student=self.student,
            term=self.term,
            title="Tuition Fees",
            amount=Decimal("20000.00"),
            amount_paid=Decimal("0.00"),
            issued_on=timezone.now().date(),
            due_date=timezone.now().date() + timedelta(days=30),
        )

        allowed, reason, _, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL, user=self.student_user
        )
        self.assertFalse(allowed)
        self.assertIn("outstanding fee balance", reason)

        # Make payment reducing balance below 5,000 threshold
        invoice = FeeInvoice.objects.get(student=self.student)
        invoice.amount_paid = Decimal("16000.00")  # Balance = 4,000 <= 5,000
        invoice.save()

        allowed, reason, _, _ = check_document_access(
            self.student, DocumentReleaseControl.DocumentType.TRANSCRIPT_PROVISIONAL, user=self.student_user
        )
        self.assertTrue(allowed)

    def test_pdf_with_qr_code_and_public_verification_portal(self):
        ctx = build_transcript_context(self.student)
        pdf_bytes = export_transcript_pdf(
            self.student, ctx, kind="provisional",
            verify_url=f"/verify/document/{ctx['reference_no']}/",
            tracking_info="Requester: audit.test"
        )
        self.assertGreater(len(pdf_bytes), 1000)

        # Test public verification view with valid reference
        client = Client()
        url = reverse("university:verify_document", kwargs={"reference_no": ctx["reference_no"]})
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_verified"])
        self.assertEqual(response.context["status"], "VALID")
        self.assertContains(response, self.student.user.display_name)
        self.assertContains(response, ctx["digest"])

        # Test verification with manipulated reference
        fake_url = reverse("university:verify_document", kwargs={"reference_no": "UMS/TR/2026/FAKE-12345"})
        fake_response = client.get(fake_url)
        self.assertEqual(fake_response.status_code, 200)
        self.assertFalse(fake_response.context["is_verified"])
        self.assertEqual(fake_response.context["status"], "NOT_FOUND")
        self.assertContains(fake_response, "Record Not Found")

    def test_admin_document_controls_dashboard(self):
        client = Client()
        controls_url = reverse("university:admin_document_controls")

        # Anonymous or student user blocked
        res_anon = client.get(controls_url)
        self.assertEqual(res_anon.status_code, 302)

        client.force_login(self.student_user)
        res_student = client.get(controls_url)
        self.assertEqual(res_student.status_code, 403)

        # Admin can access and save
        client.force_login(self.staff_user)
        res_admin = client.get(controls_url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Document Release")

        # POST updates
        post_data = {
            "action": "save_controls",
            "is_open_exam_card": "on",
            "open_date_exam_card": "2026-10-01T08:00",
            "lock_date_exam_card": "2026-10-31T17:00",
            "require_fee_exam_card": "on",
            "max_fee_exam_card": "2500.00",
            "notes_exam_card": "Exam cards open for October finals.",
        }
        post_res = client.post(controls_url, post_data)
        self.assertEqual(post_res.status_code, 302)

        # Verify DB updated
        exam_ctrl = DocumentReleaseControl.objects.get(
            document_type=DocumentReleaseControl.DocumentType.EXAM_CARD,
            term=None
        )
        self.assertTrue(exam_ctrl.is_open)
        self.assertTrue(exam_ctrl.require_financial_clearance)
        self.assertEqual(exam_ctrl.max_allowed_fee_balance, Decimal("2500.00"))
        self.assertEqual(exam_ctrl.notes, "Exam cards open for October finals.")

