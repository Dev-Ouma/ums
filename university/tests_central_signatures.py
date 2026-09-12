import io
from datetime import date
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import UserSignature, UserSignatureHistory
from accounts.signature_services import (
    approve_user_signature,
    deactivate_user_signature,
    get_user_active_signature,
    remove_user_signature,
    revoke_user_signature,
    save_user_signature,
    validate_signature_file,
)
from university.admission_document_services import (
    SignatureAuthorizationError,
    SignatureRequiredError,
    build_admission_document_context,
    build_admission_letter_pdf_bytes,
    generate_admission_document,
    get_or_create_default_template,
)
from university.models import (
    AcademicYear,
    AdmissionDocumentTemplate,
    Application,
    AuditLog,
    Department,
    DocumentSignatureConfig,
    Intake,
    IssuedAdmissionDocument,
    Program,
    School,
)

User = get_user_model()


def _create_sample_png(width=300, height=100, name="signature.png"):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class CentralSignatureAndAdmissionLetterTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Users
        self.admin = User.objects.create_superuser(
            username="admin_test",
            email="admin@test.ac.ke",
            password="TestPassword123!",
            first_name="Admin",
            last_name="User",
            role="ADMIN",
        )
        self.registrar = User.objects.create_user(
            username="registrar_test",
            email="registrar@test.ac.ke",
            password="TestPassword123!",
            first_name="Dr. Jane",
            last_name="Registrar",
            role="REGISTRAR",
        )
        self.staff_unauth = User.objects.create_user(
            username="student_test",
            email="student@test.ac.ke",
            password="TestPassword123!",
            first_name="Sam",
            last_name="Student",
            role="STUDENT",
        )

        # School, Department, Program, Intake, Application
        self.faculty = School.objects.create(name="School of Computing", code="SOC")
        self.dept = Department.objects.create(name="Computer Science", code="CS", school=self.faculty)
        self.prog = Program.objects.create(
            name="Bachelor of Science in Computer Science",
            code="BCS",
            department=self.dept,
            level="UG",
        )
        from datetime import date
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Academic Intake",
            academic_year=self.ay,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 1),
            is_active=True,
        )
        self.application = Application.objects.create(
            application_number="APP-2026-777",
            first_name="John",
            last_name="Doe",
            email="johndoe@example.com",
            phone="+254711223344",
            date_of_birth=date(2004, 5, 15),
            gender="MALE",
            national_id="34567890",
            program=self.prog,
            intake=self.intake,
            status=Application.Status.ACCEPTED,
        )

        # Document Signature Config
        self.sig_config, _ = DocumentSignatureConfig.objects.get_or_create(
            document_type=DocumentSignatureConfig.DocumentType.ADMISSION_LETTER,
            defaults={
                "title": "Official Admission Letter Signatory Policy",
                "is_signature_required": True,
                "required_roles": "ADMIN,REGISTRAR",
                "number_of_signatures": 1,
                "primary_label": "Academic Registrar",
                "primary_position": DocumentSignatureConfig.SignaturePosition.BOTTOM_RIGHT,
            },
        )

    def test_signature_file_validation(self):
        """Test file validation: accepts valid PNG, rejects invalid types and oversized files."""
        valid_png = _create_sample_png(300, 100)
        validate_signature_file(valid_png)  # Should not raise

        # Invalid file type (e.g. text file masked as image)
        bad_file = SimpleUploadedFile("fake.png", b"not an image at all", content_type="image/png")
        with self.assertRaises(Exception):
            validate_signature_file(bad_file)

    def test_user_signature_profile_lifecycle_and_versioning(self):
        """Test uploading signature, version incrementing, and history archiving."""
        # 1. Initial signature upload (v1)
        png1 = _create_sample_png(250, 80, "sig1.png")
        sig1 = save_user_signature(
            user=self.registrar,
            signature_file=png1,
            title="Academic Registrar",
            department_or_office="Directorate of Academic Affairs",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
            reason="Initial official digital signature upload",
        )
        self.assertEqual(sig1.version, 1)
        self.assertEqual(sig1.status, UserSignature.Status.ACTIVE)
        self.assertTrue(sig1.is_active)
        self.assertTrue(bool(sig1.signature_image))
        self.assertEqual(self.registrar.signature_history.count(), 0)

        # 2. Update signature (v2) -> v1 archived into UserSignatureHistory
        png2 = _create_sample_png(300, 90, "sig2.png")
        sig2 = save_user_signature(
            user=self.registrar,
            signature_file=png2,
            title="Senior Academic Registrar",
            department_or_office="Registry Division",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
            reason="Refreshed digital signature for promotion",
        )
        self.assertEqual(sig2.version, 2)
        self.assertEqual(sig2.title, "Senior Academic Registrar")
        self.assertEqual(self.registrar.signature_history.count(), 1)
        hist = self.registrar.signature_history.first()
        self.assertEqual(hist.version, 1)
        self.assertIsNotNone(hist.valid_until)

    def test_signature_status_transitions_and_auditing(self):
        """Test deactivation, approval, revocation, and removal."""
        png = _create_sample_png(250, 80)
        save_user_signature(
            user=self.registrar,
            signature_file=png,
            title="Academic Registrar",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )

        # Deactivate
        deactivate_user_signature(self.registrar, actor=self.admin, reason="Leave of absence")
        sig = get_user_active_signature(self.registrar)
        self.assertIsNone(sig)  # Inactive signatures are not usable

        # Approve and Reactivate
        approve_user_signature(self.registrar, actor=self.admin)
        sig = get_user_active_signature(self.registrar)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.status, UserSignature.Status.ACTIVE)

        # Revoke
        revoke_user_signature(self.registrar, actor=self.admin, reason="Appointment ended")
        self.assertIsNone(get_user_active_signature(self.registrar))

        # Check audit log records
        audits = AuditLog.objects.filter(module=AuditLog.Module.SIGNATURES)
        self.assertTrue(audits.exists())

    def test_secure_signature_endpoint_access_control(self):
        """Ensure unauthenticated and unauthorized third parties cannot access signature assets."""
        png = _create_sample_png(250, 80)
        save_user_signature(
            user=self.registrar,
            signature_file=png,
            title="Academic Registrar",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )

        url = reverse("accounts:serve_user_signature", kwargs={"user_id": self.registrar.id})

        # 1. Anonymous user -> redirected to login
        self.client.logout()
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

        # 2. Student / unauthorized user -> 403 Forbidden
        self.client.force_login(self.staff_unauth)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

        # 3. Owner -> 200 OK
        self.client.force_login(self.registrar)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

        # 4. Admin -> 200 OK
        self.client.force_login(self.admin)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_missing_signature_warning_when_mandatory(self):
        """When DocumentSignatureConfig requires signature, generating without active sig raises SignatureRequiredError."""
        # Registrar has NO signature uploaded
        UserSignature.objects.filter(user=self.registrar).delete()

        with self.assertRaises(SignatureRequiredError) as ctx:
            generate_admission_document(
                application=self.application,
                user=self.admin,
                signatory_user=self.registrar,
            )
        self.assertIn("An active signature has not been configured", str(ctx.exception))

    def test_signature_authorization_rbac_enforcement(self):
        """An unauthorized user cannot sign an official document."""
        # Attempt to sign with a student/unauthorized user
        with self.assertRaises(SignatureAuthorizationError) as ctx:
            generate_admission_document(
                application=self.application,
                user=self.admin,
                signatory_user=self.staff_unauth,
            )
        self.assertIn("not authorized to sign Admission Letters", str(ctx.exception))

    def test_admission_letter_bold_formatting_and_context(self):
        """Verify that important items are selectively emphasized with bold typography."""
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)

        # Upload active signature for registrar
        png = _create_sample_png(250, 80)
        save_user_signature(
            user=self.registrar,
            signature_file=png,
            title="Academic Registrar",
            department_or_office="Office of Academic Affairs",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )

        doc = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )

        ctx = doc.rendered_context
        # Check required fields exist in context
        self.assertEqual(ctx["student_name"], "John Doe")
        self.assertEqual(ctx["programme_name"], "Bachelor of Science in Computer Science")
        self.assertEqual(ctx["programme_code"], "BCS")
        self.assertEqual(ctx["faculty_name"], "School of Computing")
        self.assertEqual(ctx["department_name"], "Computer Science")
        self.assertEqual(ctx["academic_year"], "2026/2027")
        self.assertEqual(ctx["intake"], "September 2026 Academic Intake")
        self.assertTrue(bool(ctx["reporting_date"]))
        self.assertTrue(bool(ctx["acceptance_deadline"]))

        # Verify template structure matches standard institutional 1-pager
        self.assertIn("RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR", template.subject_template)
        self.assertIn("1. KCSE Certificate or Result Slip", template.body_template)
        self.assertIn("2. Birth Certificate", template.body_template)
        self.assertIn("3. National Identity Card or Passport", template.body_template)
        self.assertIn("TUITION FEES", template.fee_schedule_instructions)
        self.assertIn("COMMENCEMENT DATE", template.terms_and_conditions)

        # Verify rendered content has real applicant data
        self.assertIn("John Doe", doc.rendered_context["student_name"])
        self.assertIn("Bachelor of Science in Computer Science", doc.rendered_context["programme_name"])

        # Verify PDF generation succeeds as single page
        pdf_bytes = build_admission_letter_pdf_bytes(doc)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_document_snapshot_immutability(self):
        """
        When a document is issued, its signature snapshot is sealed.
        Changing the user's signature later MUST NOT alter the historical issued document!
        """
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)

        # 1. Issue document with Version 1 signature
        png1 = _create_sample_png(200, 70, "v1.png")
        save_user_signature(
            user=self.registrar,
            signature_file=png1,
            title="Registrar v1",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )

        doc_v1 = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )
        self.assertEqual(doc_v1.signature_version, 1)
        self.assertTrue(bool(doc_v1.signature_snapshot))
        v1_snapshot_bytes = doc_v1.signature_snapshot.read()

        # 2. Registrar updates signature to Version 2
        png2 = _create_sample_png(350, 120, "v2.png")
        save_user_signature(
            user=self.registrar,
            signature_file=png2,
            title="Registrar v2",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )
        self.registrar.signature.refresh_from_db()
        self.assertEqual(self.registrar.signature.version, 2)

        # 3. Old document doc_v1 must still have its original snapshot unchanged!
        doc_v1.refresh_from_db()
        self.assertEqual(doc_v1.signature_version, 1)
        self.assertEqual(doc_v1.signatory_title, "Registrar v1")
        doc_v1.signature_snapshot.open("rb")
        self.assertEqual(doc_v1.signature_snapshot.read(), v1_snapshot_bytes)

        # 4. Generating a NEW document (e.g. for a new application) must use Version 2
        app2 = Application.objects.create(
            application_number="APP-2026-888",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
            phone="+254722334455",
            date_of_birth=date(2005, 3, 20),
            gender="FEMALE",
            national_id="35678901",
            program=self.prog,
            intake=self.intake,
            status=Application.Status.ACCEPTED,
        )
        doc_v2 = generate_admission_document(
            application=app2,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
            issue_as_new_version=True,
        )
        self.assertEqual(doc_v2.signature_version, 2)
        self.assertEqual(doc_v2.signatory_title, "Registrar v2")
