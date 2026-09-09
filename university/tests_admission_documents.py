import io
import os
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.admission_document_services import (
    build_admission_document_context,
    build_admission_letter_pdf_bytes,
    build_dynamic_fields_catalog,
    generate_admission_document,
    get_or_create_default_template,
    get_student_admission_documents,
    render_template_text,
    resend_admission_document,
    revoke_admission_document,
)
from university.models import (
    AcademicTerm,
    AcademicYear,
    AdmissionDocumentTemplate,
    Application,
    ApplicationAttachment,
    ApplicationCustomField,
    ApplicationCustomFieldValue,
    AuditLog,
    Department,
    DocumentDeliveryLog,
    School,
    FeeStructure,
    Intake,
    IssuedAdmissionDocument,
    Program,
)

User = get_user_model()


class AdmissionDocumentManagementTests(TestCase):
    def setUp(self):
        # 1. School, Department, Program
        self.school = School.objects.create(name="Faculty of Computing & Information Technology", code="FCIT")
        self.dept = Department.objects.create(name="Department of Computer Science", code="DCS", school=self.school)
        self.prog = Program.objects.create(
            name="Bachelor of Science in Computer Science",
            code="BCS",
            department=self.dept,
            duration_years=4,
        )

        # 2. Academic Year and Term
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            name="Semester 1",
            academic_year=self.ay,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            is_current=True,
            semester_number=1,
        )

        # 3. Intake
        self.intake = Intake.objects.create(
            name="September 2026 Intake",
            academic_year=self.ay,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 1),
            is_active=True,
        )

        # 4. Fee Structure
        self.fee_struct = FeeStructure.objects.create(
            program=self.prog,
            year_of_study=1,
            semester=1,
            tuition_fee=Decimal("45000.00"),
        )

        # 5. Admin user
        self.admin_user = User.objects.create_user(
            username="admin.test",
            email="admin.test@ums.ac.ke",
            role=Role.ADMIN,
            password="password123",
        )

        # 6. Student A user & profile
        self.student_a_user = User.objects.create_user(
            username="stu.aarav",
            email="aarav@ums.ac.ke",
            first_name="Aarav",
            last_name="Sharma",
            role=Role.STUDENT,
            password="password123",
        )
        self.student_a = StudentProfile.objects.create(
            user=self.student_a_user,
            roll_no="BCS/2026/00143",
            program=self.prog,
            current_semester=1,
            date_of_birth=date(2004, 5, 12),
        )

        # 7. Student B user & profile (for security isolation checks)
        self.student_b_user = User.objects.create_user(
            username="stu.beatrice",
            email="beatrice@ums.ac.ke",
            first_name="Beatrice",
            last_name="Wanjiku",
            role=Role.STUDENT,
            password="password123",
        )
        self.student_b = StudentProfile.objects.create(
            user=self.student_b_user,
            roll_no="BCS/2026/00144",
            program=self.prog,
            current_semester=1,
            date_of_birth=date(2005, 3, 20),
        )

        # 8. Application for Student A
        self.app_a = Application.objects.create(
            application_number="APP-2026-0143",
            intake=self.intake,
            program=self.prog,
            first_name="Aarav",
            last_name="Sharma",
            email="aarav@ums.ac.ke",
            phone="+254712345678",
            date_of_birth=date(2004, 5, 12),
            gender="MALE",
            national_id="38472910",
            address="P.O. Box 90100 - 00100, Nairobi",
            secondary_school="Nairobi School",
            kcse_index_number="20400001014",
            kcse_mean_grade="A-",
            kcse_year=2025,
            status=Application.Status.ENROLLED,
            admitted_reg_no="BCS/2026/00143",
            student=self.student_a,
        )

        self.client = Client()

    def test_dynamic_fields_catalog_and_rendering(self):
        """Test token dictionary catalog and placeholder interpolation."""
        catalog = build_dynamic_fields_catalog()
        self.assertIn("Student", catalog)
        self.assertIn("Programme", catalog)
        self.assertIn("Finance", catalog)

        # Test custom fields dynamically appearing in catalog
        cf = ApplicationCustomField.objects.create(
            name="campus_location",
            label="Preferred Campus",
            field_type=ApplicationCustomField.FieldType.TEXT,
            is_active=True,
        )
        catalog_updated = build_dynamic_fields_catalog()
        self.assertIn("Custom Application Fields", catalog_updated)

        # Set custom field value on Application A
        ApplicationCustomFieldValue.objects.create(
            application=self.app_a,
            field=cf,
            value="Main Campus (Westlands)",
        )

        context = build_admission_document_context(self.app_a)
        self.assertEqual(context["student_name"], "Aarav Sharma")
        self.assertEqual(context["programme_code"], "BCS")
        self.assertEqual(context["academic_year"], "2026/2027")
        self.assertEqual(context["campus_location"], "Main Campus (Westlands)")

        # Test template string substitution
        tmpl_str = "Dear {{student_name}}, welcome to {{programme_name}} at {{campus_location}}."
        rendered = render_template_text(tmpl_str, context)
        self.assertEqual(rendered, "Dear Aarav Sharma, welcome to Bachelor of Science in Computer Science at Main Campus (Westlands).")

    def test_application_attachment_upload_and_relationship(self):
        """Verify attachments stay permanently linked across Applicant -> Application -> Student."""
        dummy_slip = SimpleUploadedFile("kcse_slip.pdf", b"%PDF-1.4 dummy result slip content", content_type="application/pdf")
        dummy_id = SimpleUploadedFile("national_id.pdf", b"%PDF-1.4 dummy national ID card", content_type="application/pdf")

        att1 = ApplicationAttachment.objects.create(
            application=self.app_a,
            document_type=ApplicationAttachment.DocType.KCSE_CERTIFICATE,
            name="KCSE Result Slip",
            file=dummy_slip,
            file_name="kcse_slip.pdf",
            file_size=len(dummy_slip),
            mime_type="application/pdf",
        )
        att2 = ApplicationAttachment.objects.create(
            application=self.app_a,
            document_type=ApplicationAttachment.DocType.NATIONAL_ID,
            name="National ID Card",
            file=dummy_id,
            file_name="national_id.pdf",
            file_size=len(dummy_id),
            mime_type="application/pdf",
        )

        self.assertEqual(self.app_a.attachments.count(), 2)

        # Check retrieval through StudentProfile helper
        student_docs = get_student_admission_documents(self.student_a)
        self.assertEqual(len(student_docs["attachments"]), 2)
        self.assertIn(att1, student_docs["attachments"])
        self.assertIn(att2, student_docs["attachments"])

    def test_admission_letter_generation_and_versioning(self):
        """Verify v1 generation, v2 regeneration with change reasons, and superseded status."""
        # Version 1 generation
        doc_v1 = generate_admission_document(
            application=self.app_a,
            user=self.admin_user,
            reason="Initial official issuance",
        )
        self.assertEqual(doc_v1.version, 1)
        self.assertEqual(doc_v1.status, IssuedAdmissionDocument.Status.CURRENT)
        self.assertTrue(doc_v1.is_current_version)
        self.assertTrue(doc_v1.pdf_file)
        self.assertEqual(doc_v1.rendered_context["student_name"], "Aarav Sharma")

        # Version 2 regeneration
        new_reporting = date(2026, 9, 21)
        doc_v2 = generate_admission_document(
            application=self.app_a,
            user=self.admin_user,
            custom_overrides={"reporting_date": "Monday, 21 September 2026"},
            reason="Reporting date shifted for faculty orientation",
            issue_as_new_version=True,
        )

        doc_v1.refresh_from_db()
        self.assertEqual(doc_v1.status, IssuedAdmissionDocument.Status.SUPERSEDED)
        self.assertFalse(doc_v1.is_current_version)

        self.assertEqual(doc_v2.version, 2)
        self.assertEqual(doc_v2.status, IssuedAdmissionDocument.Status.CURRENT)
        self.assertTrue(doc_v2.is_current_version)
        self.assertIn("V2", doc_v2.document_reference)
        self.assertEqual(doc_v2.change_reason, "Reporting date shifted for faculty orientation")

        # Check student query returns v2 as current and v1 in historical
        docs = get_student_admission_documents(self.student_a)
        self.assertEqual(docs["admission_letter"], doc_v2)
        self.assertIn(doc_v1, docs["historical_letters"])

    def test_admin_resend_and_delivery_logging(self):
        """Verify document delivery logging and audit recording."""
        doc = generate_admission_document(application=self.app_a, user=self.admin_user)
        log_entry = resend_admission_document(
            document=doc,
            delivery_method=DocumentDeliveryLog.Method.PORTAL_NOTICE,
            recipient="aarav@ums.ac.ke",
            subject="Your Admission Letter is Ready",
            message="Please log in to your portal.",
            user=self.admin_user,
        )
        self.assertEqual(log_entry.status, DocumentDeliveryLog.Status.DELIVERED)
        self.assertEqual(doc.delivery_logs.count(), 1)

        # AuditLog should contain RESEND_DOCUMENT
        audit = AuditLog.objects.filter(action=AuditLog.Action.RESEND_DOCUMENT, entity_id=doc.id).first()
        self.assertIsNotNone(audit)

    def test_student_portal_security_and_isolation(self):
        """Verify Student B cannot access Student A's admission documents or attachments."""
        doc_a = generate_admission_document(application=self.app_a, user=self.admin_user)
        dummy_file = SimpleUploadedFile("cert_a.pdf", b"%PDF-1.4 dummy", content_type="application/pdf")
        att_a = ApplicationAttachment.objects.create(
            application=self.app_a,
            document_type=ApplicationAttachment.DocType.KCSE_CERTIFICATE,
            name="Aarav KCSE Slip",
            file=dummy_file,
        )

        # 1. Log in as Student A: Should successfully access own document and attachment
        self.client.force_login(self.student_a_user)
        res = self.client.get(reverse("university:student_admission_documents"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, doc_a.document_reference)
        self.assertContains(res, "Aarav KCSE Slip")

        res_view = self.client.get(reverse("university:student_view_admission_document", args=[doc_a.id]))
        self.assertEqual(res_view.status_code, 200)

        # 2. Log in as Student B: Must receive 403 Forbidden attempting to access Student A's files
        self.client.force_login(self.student_b_user)
        res_view_forbidden = self.client.get(reverse("university:student_view_admission_document", args=[doc_a.id]))
        self.assertEqual(res_view_forbidden.status_code, 403)

        res_dl_forbidden = self.client.get(reverse("university:student_download_admission_document", args=[doc_a.id]))
        self.assertEqual(res_dl_forbidden.status_code, 403)

        res_att_forbidden = self.client.get(reverse("university:student_download_attachment", args=[att_a.id]))
        self.assertEqual(res_att_forbidden.status_code, 403)

    def test_attachment_verification_workflow(self):
        """Test admin verifying and rejecting an applicant's submitted KCSE certificate."""
        dummy_file = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 test certificate", content_type="application/pdf")
        att = ApplicationAttachment.objects.create(
            application=self.app_a,
            document_type=ApplicationAttachment.DocType.KCSE_CERTIFICATE,
            name="KCSE Result Slip",
            file=dummy_file,
            verification_status=ApplicationAttachment.VerificationStatus.PENDING,
        )

        self.client.force_login(self.admin_user)
        post_url = reverse("university:admin_verify_attachment", args=[att.id])
        res = self.client.post(post_url, {
            "verification_status": "VERIFIED",
            "verification_notes": "KNEC portal confirmation matched Grade A-.",
        })
        self.assertEqual(res.status_code, 302)

        att.refresh_from_db()
        self.assertEqual(att.verification_status, ApplicationAttachment.VerificationStatus.VERIFIED)
        self.assertEqual(att.verified_by, self.admin_user)
        self.assertIn("KNEC portal confirmation", att.verification_notes)

        # AuditLog entry created
        audit = AuditLog.objects.filter(action=AuditLog.Action.VERIFY_DOCUMENT, entity_id=att.id).first()
        self.assertIsNotNone(audit)

    def test_admin_template_management_and_live_preview(self):
        """Test creating, editing, and previewing custom templates."""
        self.client.force_login(self.admin_user)

        # 1. Create template
        create_url = reverse("university:admin_template_editor")
        res = self.client.post(create_url, {
            "name": "Custom Postgraduate Offer Letter",
            "document_type": "PROVISIONAL_OFFER",
            "header_title": "BOARD OF POSTGRADUATE STUDIES",
            "salutation_template": "Dear {{student_name}},",
            "subject_template": "PROVISIONAL OFFER: {{programme_name}}",
            "body_template": "Congratulations {{student_name}}, your admission has been recommended.",
            "signatory_name": "Prof. David Kiplagat",
            "signatory_title": "Dean, Graduate School",
            "is_active": "on",
        })
        self.assertEqual(res.status_code, 302)

        tmpl = AdmissionDocumentTemplate.objects.filter(name="Custom Postgraduate Offer Letter").first()
        self.assertIsNotNone(tmpl)
        self.assertEqual(tmpl.document_type, "PROVISIONAL_OFFER")
        self.assertEqual(tmpl.version, 1)

        # 2. Preview template
        preview_url = reverse("university:admin_template_preview", args=[tmpl.id])
        res_prev = self.client.get(preview_url)
        self.assertEqual(res_prev.status_code, 200)
        self.assertEqual(res_prev["Content-Type"], "application/pdf")
