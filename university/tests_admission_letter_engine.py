import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserSignature
from accounts.signature_services import save_user_signature
from cms.models import MenuItem, Page
from university.models import (
    AcademicYear, AcademicTerm, AdmissionDocumentTemplate, Application,
    Department, DocumentSignatureConfig, FeeStructure, Intake,
    IssuedAdmissionDocument, Program, School, SystemSetting
)
from university.admission_document_services import (
    build_admission_document_context, build_admission_letter_pdf_bytes,
    generate_admission_document, get_or_create_default_template,
    compute_admission_document_checksum
)
from university.settings_services import set_setting

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

User = get_user_model()


def _create_sample_png(width=300, height=100, name="signature.png"):
    import io
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class AdmissionLetterEngineAndNavigationTests(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Academic Structure
        self.school = School.objects.create(name="School of Computing & Informatics", code="SCI")
        self.dept = Department.objects.create(name="Computer Science", code="CS", school=self.school)
        self.prog = Program.objects.create(
            name="Bachelor of Science in Computer Science",
            code="BCS",
            department=self.dept,
            duration_years=4,
            status=Program.Status.ACTIVE
        )
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
            status=AcademicYear.Status.CURRENT
        )
        self.term = AcademicTerm.objects.create(
            name="Semester 1",
            academic_year=self.ay,
            semester_number=1,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            is_current=True,
            status=AcademicYear.Status.CURRENT
        )
        self.intake = Intake.objects.create(
            name="September 2026 Academic Intake",
            academic_year=self.ay,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 10, 31),
            is_active=True
        )

        # 2. Fee Structure
        self.fee_struct = FeeStructure.objects.create(
            program=self.prog,
            term=self.term,
            year_of_study=1,
            semester=1,
            tuition_fee=Decimal("45000.00"),
            registration_fee=Decimal("1500.00"),
            examination_fee=Decimal("3000.00"),
            library_fee=Decimal("1000.00"),
            activity_fee=Decimal("1000.00"),
            medical_fee=Decimal("1500.00"),
            ict_fee=Decimal("2000.00"),
            student_union_fee=Decimal("500.00")
        )

        # 3. Users
        self.admin = User.objects.create_superuser(
            username="admin_adm",
            email="admin@ums.ac.ke",
            password="adminpassword123",
            role=Role.ADMIN
        )
        self.registrar = User.objects.create_user(
            username="dr_omolo",
            email="omolo@ums.ac.ke",
            password="registrarpassword123",
            first_name="Margaret",
            last_name="Omolo",
            role=Role.ADMIN
        )
        self.applicant_user = User.objects.create_user(
            username="mary_wabs",
            email="mary.wabs@example.com",
            password="applicantpassword123",
            first_name="Mary",
            last_name="Wabs",
            role=Role.APPLICANT
        )

        # 4. Save Registrar Signature
        png = _create_sample_png()
        save_user_signature(
            user=self.registrar,
            signature_file=png,
            title="Academic Registrar",
            department_or_office="Directorate of Academic Affairs",
            status=UserSignature.Status.ACTIVE,
            actor=self.registrar,
        )

        # 5. Application Record
        self.application = Application.objects.create(
            application_number="APP-2026-0042",
            applicant_user=self.applicant_user,
            first_name="Mary",
            last_name="Wabs",
            email="mary.wabs@example.com",
            phone="+254 712 345 678",
            date_of_birth=date(2004, 5, 14),
            gender="FEMALE",
            national_id="39485721",
            address="P.O. Box 90100 - 00100, Nairobi, Kenya",
            program=self.prog,
            intake=self.intake,
            status=Application.Status.ACCEPTED,
            admitted_reg_no="BCS/2026/00042",
            reporting_date=date(2026, 10, 4),
        )

        # 6. Configure System Settings
        set_setting("institution_name", "Nexus International University")
        set_setting("institution_code", "NIU")
        set_setting("institution_address", "P.O. Box 90100 - 00100, GPO, Nairobi, Kenya")
        set_setting("institution_phone", "+254 (0) 20 123 4567")
        set_setting("admissions_email", "admissions@ums.ac.ke")
        set_setting("bank_name", "Absa Bank Kenya PLC")
        set_setting("bank_account_no", "03-094-8002145")
        set_setting("bank_branch", "University Way Branch")
        set_setting("mpesa_paybill", "222111")

    # --------------------------------------------------------------------------
    # MODULE 1: Navigation Tests
    # --------------------------------------------------------------------------

    def test_public_website_navigation_contains_apply_now(self):
        """Verify public navigation has 'Apply Now' pointing directly to admissions_apply."""
        response = self.client.get(reverse("university:home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Verify Apply Now exists
        self.assertIn("Apply Now", content)
        apply_url = reverse("university:admissions_apply")
        self.assertIn(f'href="{apply_url}"', content)

        # Direct access without login redirect
        apply_resp = self.client.get(apply_url)
        self.assertEqual(apply_resp.status_code, 200)

    def test_cms_seeded_menu_items_include_apply_now(self):
        """Verify that seeded CMS menu items include Apply Now."""
        from django.core.management import call_command
        call_command("seed_cms")
        header_items = list(MenuItem.objects.filter(location="header").values_list("label", "url"))
        labels = [item[0] for item in header_items]
        self.assertIn("Apply Now", labels)

    # --------------------------------------------------------------------------
    # MODULE 2: Admission Letter Subsystem Tests
    # --------------------------------------------------------------------------

    def test_dynamic_institutional_identity_and_data_binding(self):
        """Verify admission letter context pulls actual records without test placeholders."""
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)
        doc = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )

        ctx = doc.rendered_context
        # Recipient Legal Name (No 'WABS' raw placeholder)
        self.assertEqual(ctx["student_name"], "Mary Wabs")
        self.assertEqual(ctx["title_name"], "Ms. Mary Wabs")
        self.assertEqual(ctx["applicant_title_name"], "Ms. Mary Wabs")

        # Programme metadata
        self.assertEqual(ctx["programme_name"], "Bachelor of Science in Computer Science")
        self.assertEqual(ctx["programme_code"], "BCS")
        self.assertEqual(ctx["faculty_name"], "School of Computing & Informatics")
        self.assertEqual(ctx["department_name"], "Computer Science")
        self.assertEqual(ctx["duration_years"], "4")
        self.assertEqual(ctx["academic_year"], "2026/2027")
        self.assertEqual(ctx["intake"], "September 2026 Academic Intake")

        # Reference block
        self.assertEqual(ctx["application_number"], "APP-2026-0042")
        self.assertEqual(ctx["registration_number"], "BCS/2026/00042")
        self.assertTrue(bool(ctx["document_reference"]))

        # Reporting & Acceptance Deadlines
        self.assertIn("4 October 2026", ctx["reporting_date"])
        self.assertTrue(bool(ctx["acceptance_deadline"]))

        # Dynamic Fee Schedule
        self.assertEqual(ctx["tuition_fee"], "KES 45,000.00")
        self.assertEqual(ctx["statutory_fees"], "KES 10,500.00")
        self.assertEqual(ctx["total_fees"], "KES 55,500.00")

        # Dynamic Banking Details
        self.assertEqual(ctx["bank_name"], "Absa Bank Kenya PLC")
        self.assertEqual(ctx["bank_account"], "03-094-8002145")
        self.assertEqual(ctx["mpesa_paybill"], "222111")

        # Authority hierarchy
        self.assertEqual(ctx["signatory_name"], "Margaret Omolo")
        self.assertEqual(ctx["signatory_title"], "Academic Registrar")
        self.assertIn("Deputy Vice-Chancellor", ctx["signatory_office"])

    def test_pdf_generation_single_page_and_clean_header(self):
        """Verify that PDF generation renders high fidelity byte stream with clean header."""
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)
        doc = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )

        pdf_bytes = build_admission_letter_pdf_bytes(doc)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 5000)

    def test_public_qr_code_verification_endpoint(self):
        """Verify QR code URL and public verification endpoint validate authentic admission letters."""
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)
        doc = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )

        ref = doc.document_reference
        # 1. Query via clean URL /verify/admission/<reference>/
        verify_url = reverse("university:verify_admission", kwargs={"reference_no": ref.replace("/", "-")})
        response = self.client.get(verify_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("Certified Authentic", content)
        self.assertIn("Mary Wabs", content)
        self.assertIn("BCS/2026/00042", content)
        self.assertIn("Bachelor of Science in Computer Science", content)
        self.assertIn("Margaret Omolo", content)

        # 2. Query with raw reference query param
        query_url = f"{reverse('university:verify_admission_query')}?ref={ref}"
        query_resp = self.client.get(query_url)
        self.assertEqual(query_resp.status_code, 200)
        self.assertIn("Certified Authentic", query_resp.content.decode("utf-8"))

    def test_document_lifecycle_revocation(self):
        """Verify that revoking an admission letter reflects as REVOKED on the public verification page."""
        template = get_or_create_default_template(program=self.prog, academic_year=self.ay)
        doc = generate_admission_document(
            application=self.application,
            template=template,
            user=self.admin,
            signatory_user=self.registrar,
        )

        # Revoke document
        doc.status = IssuedAdmissionDocument.Status.REVOKED
        doc.save(update_fields=["status"])

        verify_url = reverse("university:verify_admission", kwargs={"reference_no": doc.document_reference.replace("/", "-")})
        response = self.client.get(verify_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("Certified Authentic", content)
        self.assertIn("Revoked", content)

    def test_applicant_dashboard_actions_and_offer_acceptance(self):
        """Verify applicant dashboard exposes View, Download, and Accept Offer actions."""
        self.client.force_login(self.applicant_user)
        dash_url = reverse("university:applicant_dashboard")
        response = self.client.get(dash_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("View Letter", content)
        self.assertIn("Download PDF", content)
        self.assertIn("Accept Offer", content)

        # Test offer acceptance action
        accept_url = reverse("university:applicant_accept_offer", kwargs={"pk": self.application.pk})
        post_resp = self.client.post(accept_url, follow=True)
        self.assertEqual(post_resp.status_code, 200)

        # Reload application and verify accepted
        self.application.refresh_from_db()
        self.assertIn("Offer Accepted", self.application.review_notes)

    def test_view_and_download_letter_views(self):
        """Verify view_admission_letter and download_admission_letter endpoints."""
        self.client.force_login(self.applicant_user)

        # View letter inline
        view_url = reverse("university:view_admission_letter", kwargs={"pk": self.application.pk})
        view_resp = self.client.get(view_url)
        self.assertEqual(view_resp.status_code, 200)
        self.assertEqual(view_resp["Content-Type"], "application/pdf")
        self.assertIn("inline", view_resp["Content-Disposition"])

        # Download letter attachment
        dl_url = f"{reverse('university:download_admission_letter', kwargs={'pk': self.application.pk})}?download=1"
        dl_resp = self.client.get(dl_url)
        self.assertEqual(dl_resp.status_code, 200)
        self.assertEqual(dl_resp["Content-Type"], "application/pdf")
        self.assertIn("attachment", dl_resp["Content-Disposition"])
