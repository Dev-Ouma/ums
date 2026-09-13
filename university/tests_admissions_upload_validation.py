import io
import os
from datetime import date
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from university.models import (
    AcademicYear,
    Application,
    ApplicationAttachment,
    Department,
    Intake,
    Program,
    School,
)

User = get_user_model()


class AdmissionsUploadValidationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.school = School.objects.create(name="School of Science", code="SOS")
        self.dept = Department.objects.create(name="Department of Computing", code="DOC", school=self.school)
        self.program = Program.objects.create(
            name="Bachelor of Science in Software Engineering",
            code="BSE",
            department=self.dept,
            duration_years=4,
        )
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Intake",
            academic_year=self.ay,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 1),
            is_active=True,
        )

        self.upload_url = reverse("university:admissions_upload_document")
        self.remove_url = reverse("university:admissions_remove_document")
        self.apply_url = reverse("university:admissions_apply")

        # Create valid binary contents
        self.valid_pdf_content = b"%PDF-1.4\n%test pdf content stream..."
        self.valid_png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20
        self.valid_jpg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20

    def test_upload_valid_pdf_success(self):
        file = SimpleUploadedFile("kcse_certificate.pdf", self.valid_pdf_content, content_type="application/pdf")
        response = self.client.post(self.upload_url, {
            "document_type": "kcse_document",
            "file": file,
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("document_type"), "kcse_document")
        self.assertEqual(data.get("file_name"), "kcse_certificate.pdf")

        # Verify saved in session
        session = self.client.session
        self.assertIn("draft_application_documents", session)
        draft_docs = session["draft_application_documents"]
        self.assertIn("kcse_document", draft_docs)
        self.assertEqual(draft_docs["kcse_document"]["original_name"], "kcse_certificate.pdf")

        # Verify file exists on disk/storage
        stored_path = draft_docs["kcse_document"]["file_path"]
        self.assertTrue(default_storage.exists(stored_path))

    def test_upload_valid_png_success(self):
        file = SimpleUploadedFile("photo.png", self.valid_png_content, content_type="image/png")
        response = self.client.post(self.upload_url, {
            "document_type": "passport_photo",
            "file": file,
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("document_type"), "passport_photo")

    def test_upload_invalid_extension_rejected(self):
        file = SimpleUploadedFile("malicious.exe", b"MZ\x90\x00\x03\x00\x00\x00", content_type="application/octet-stream")
        response = self.client.post(self.upload_url, {
            "document_type": "id_document",
            "file": file,
        })
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Unsupported file type", data.get("error"))

    def test_upload_oversized_file_rejected(self):
        oversized_content = self.valid_pdf_content + (b"X" * (6 * 1024 * 1024))
        file = SimpleUploadedFile("big_document.pdf", oversized_content, content_type="application/pdf")
        response = self.client.post(self.upload_url, {
            "document_type": "id_document",
            "file": file,
        })
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("exceeds the maximum limit", data.get("error"))

    def test_upload_renamed_fake_file_rejected_by_magic_signature(self):
        fake_pdf_content = b"This is just plain text disguised as a PDF document."
        file = SimpleUploadedFile("fake.pdf", fake_pdf_content, content_type="application/pdf")
        response = self.client.post(self.upload_url, {
            "document_type": "id_document",
            "file": file,
        })
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("format signature is invalid", data.get("error"))

    def test_upload_invalid_document_type_rejected(self):
        file = SimpleUploadedFile("doc.pdf", self.valid_pdf_content, content_type="application/pdf")
        response = self.client.post(self.upload_url, {
            "document_type": "invalid_random_type",
            "file": file,
        })
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Invalid document type", data.get("error"))

    def test_remove_admission_document(self):
        # 1. Upload first
        file = SimpleUploadedFile("id_scan.pdf", self.valid_pdf_content, content_type="application/pdf")
        self.client.post(self.upload_url, {
            "document_type": "id_document",
            "file": file,
        })
        session = self.client.session
        stored_path = session["draft_application_documents"]["id_document"]["file_path"]
        self.assertTrue(default_storage.exists(stored_path))

        # 2. Call remove
        response = self.client.post(self.remove_url, {
            "document_type": "id_document",
        })
        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertNotIn("id_document", session.get("draft_application_documents", {}))
        self.assertFalse(default_storage.exists(stored_path))

    def test_full_application_submission_migrates_draft_documents(self):
        # 1. Upload documents via AJAX
        pdf_file = SimpleUploadedFile("kcse_cert.pdf", self.valid_pdf_content, content_type="application/pdf")
        self.client.post(self.upload_url, {"document_type": "kcse_document", "file": pdf_file})

        id_file = SimpleUploadedFile("national_id.png", self.valid_png_content, content_type="image/png")
        self.client.post(self.upload_url, {"document_type": "id_document", "file": id_file})

        # Check session has both
        session = self.client.session
        self.assertIn("kcse_document", session["draft_application_documents"])
        self.assertIn("id_document", session["draft_application_documents"])

        # 2. Submit application form
        form_data = {
            "program": self.program.pk,
            "first_name": "Amina",
            "last_name": "Ouma",
            "email": "amina.ouma@example.com",
            "phone": "+254712345678",
            "date_of_birth": "2004-05-15",
            "gender": "FEMALE",
            "national_id": "38291042",
            "address": "P.O Box 100, Nairobi",
            "secondary_school": "Alliance Girls High School",
            "kcse_index_number": "12345678/001",
            "kcse_mean_grade": "A-",
            "kcse_year": "2024",
        }
        response = self.client.post(self.apply_url, form_data)
        # Should redirect to fee payment
        self.assertEqual(response.status_code, 302)

        # 3. Verify Application and ApplicationAttachment records created
        application = Application.objects.filter(email="amina.ouma@example.com").first()
        self.assertIsNotNone(application)
        self.assertIn(application.status, [Application.Status.READY_FOR_PAYMENT, Application.Status.SUBMITTED])

        attachments = ApplicationAttachment.objects.filter(application=application)
        self.assertEqual(attachments.count(), 2)

        doc_types = {att.document_type for att in attachments}
        self.assertIn(ApplicationAttachment.DocType.KCSE_CERTIFICATE, doc_types)
        self.assertIn(ApplicationAttachment.DocType.NATIONAL_ID, doc_types)

        # 4. Verify session draft documents were cleaned up
        session = self.client.session
        self.assertNotIn("draft_application_documents", session)
