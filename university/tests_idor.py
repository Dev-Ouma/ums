"""Cross-student object-level authorization acceptance tests."""

from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from university.models import (
    Application,
    ApplicationAttachment,
    Department,
    FeeAccount,
    FeeInvoice,
    FeeReceipt,
    IssuedAdmissionDocument,
    Payment,
    Program,
    StudentRequest,
)


class StudentObjectAuthorizationTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Computing", code="IDOR-CS")
        self.program = Program.objects.create(
            name="BSc Computing", code="IDOR-BCS", department=department)
        self.student_a = User.objects.create_user(
            username="idor.student.a", email="a@example.test",
            password="IdorPass123!", role=Role.STUDENT)
        self.student_b = User.objects.create_user(
            username="idor.student.b", email="b@example.test",
            password="IdorPass123!", role=Role.STUDENT)
        self.profile_a = StudentProfile.objects.create(
            user=self.student_a, roll_no="IDOR/001", program=self.program)
        self.profile_b = StudentProfile.objects.create(
            user=self.student_b, roll_no="IDOR/002", program=self.program)
        self.client = Client()
        self.client.force_login(self.student_a)

        self.application_b = Application.objects.create(
            application_number="IDOR-APP-002", program=self.program,
            first_name="Student", last_name="B", email=self.student_b.email,
            phone="0700000002", date_of_birth=date(2002, 2, 2), gender="OTHER",
            national_id="IDOR-NID-002", student=self.profile_b,
        )
        self.document_b = IssuedAdmissionDocument.objects.create(
            application=self.application_b, student=self.profile_b,
            document_reference="IDOR-DOC-002",
        )
        self.attachment_b = ApplicationAttachment.objects.create(
            application=self.application_b,
            document_type=ApplicationAttachment.DocType.OTHER,
            name="Student B document",
            file=SimpleUploadedFile("student-b.pdf", b"%PDF-1.4 test"),
            file_name="student-b.pdf", file_size=13,
            mime_type="application/pdf", is_visible_to_student=True,
        )
        self.fee_account = FeeAccount.objects.create(
            name="IDOR Test Account", account_identifier="IDOR-ACCOUNT")
        invoice = FeeInvoice.objects.create(
            student=self.profile_b, title="IDOR invoice", amount=Decimal("1000.00"),
            due_date=date(2026, 12, 31))
        self.payment_b = Payment.objects.create(
            invoice=invoice, student=self.profile_b, fee_account=self.fee_account,
            amount=Decimal("100.00"), internal_reference="IDOR-PAY-002",
            reference="IDOR-PROVIDER-002", status=Payment.Status.SUCCESSFUL)
        self.receipt_b = FeeReceipt.objects.create(
            receipt_number="IDOR-REC-002", payment=self.payment_b,
            student=self.profile_b, amount_paid=Decimal("100.00"))
        self.request_b = StudentRequest.objects.create(
            student=self.profile_b, request_type=StudentRequest.Type.DEFERMENT,
            reason="Student B private request")

    @staticmethod
    def assert_denied(response):
        assert response.status_code in (403, 404), response.status_code

    def test_student_id_routes_reject_another_students_academics(self):
        urls = [
            reverse("university:progressive_report_detail", args=[self.profile_b.pk]),
            reverse("university:progressive_report_export", args=[self.profile_b.pk, "csv"]),
            reverse("examinations:transcript_document", args=[self.profile_b.pk, "academic"]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assert_denied(self.client.get(url))

    def test_finance_statement_ignores_a_forged_student_id(self):
        response = self.client.get(
            reverse("university:student_fee_statement"),
            {"student_id": self.profile_b.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.profile_a.roll_no)
        self.assertNotContains(response, self.profile_b.roll_no)

    def test_payment_and_receipt_objects_are_owner_scoped(self):
        urls = [
            reverse("university:student_payment_status", args=[self.payment_b.internal_reference]),
            reverse("university:student_payment_poll", args=[self.payment_b.internal_reference]),
            reverse("university:student_receipt_view", args=[self.receipt_b.receipt_number]),
            reverse("university:student_receipt_pdf", args=[self.receipt_b.receipt_number]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assert_denied(self.client.get(url))

    def test_documents_are_owner_scoped_for_view_and_download(self):
        urls = [
            reverse("university:student_view_admission_document", args=[self.document_b.pk]),
            reverse("university:student_download_admission_document", args=[self.document_b.pk]),
            reverse("university:student_download_attachment", args=[self.attachment_b.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assert_denied(self.client.get(url))

    def test_student_cannot_read_another_students_request(self):
        response = self.client.get(
            reverse("university:admin_student_request_detail", args=[self.request_b.pk]))
        self.assert_denied(response)

    def test_application_status_requires_two_factors_not_only_a_reference(self):
        response = Client().get(reverse("university:admissions_status"), {
            "ref": self.application_b.application_number,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["application"])

        authorized = Client().get(reverse("university:admissions_status"), {
            "ref": self.application_b.application_number,
            "national_id": self.application_b.national_id,
        })
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.context["application"].pk, self.application_b.pk)

        self.assertEqual(
            Client().get(
                reverse("university:pay_application_fee", args=[self.application_b.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            Client().get(
                reverse("university:download_admission_letter", args=[self.application_b.pk])
            ).status_code,
            403,
        )
