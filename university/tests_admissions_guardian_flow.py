import re
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.models import (
    AcademicYear,
    Application,
    ApplicationAttachment,
    ApplicationFeePayment,
    Department,
    Intake,
    Program,
    School,
)
from university.admissions_services import matriculate_applicant
from university.admissions_views import application_access_token

User = get_user_model()


class AdmissionsGuardianAndApplicantJourneyTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="127.0.0.1")
        self.school = School.objects.create(name="School of Health Sciences", code="SHS")
        self.dept = Department.objects.create(name="Department of Nursing", code="DON", school=self.school)
        self.program = Program.objects.create(
            name="Bachelor of Science in Nursing",
            code="BSN",
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

        self.admin = User.objects.create_user(
            username="admissions.officer",
            email="admissions@ums.ac.ke",
            password="AdminPassword123!",
            role=Role.ADMIN,
        )

        self.valid_pdf_content = b"%PDF-1.4\n%test pdf content..."

    def test_applicant_registration_and_otp_verification_flow(self):
        # 1. Register
        register_url = reverse("university:applicant_register")
        resp = self.client.post(register_url, {
            "first_name": "Diana",
            "last_name": "Chebet",
            "email": "diana.chebet@example.com",
            "phone": "+254711223344",
            "password": "SecurePass123!",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("verify-otp", resp.url)

        # Check user created with APPLICANT role
        applicant_user = User.objects.filter(email="diana.chebet@example.com").first()
        self.assertIsNotNone(applicant_user)
        self.assertEqual(applicant_user.role, Role.APPLICANT)

        # 2. Retrieve OTP from session
        session = self.client.session
        otp_code = session["applicant_pending_verification"]["otp_code"]
        self.assertTrue(len(otp_code) == 6)

        # 3. Verify OTP
        verify_url = reverse("university:applicant_verify_otp")
        verify_resp = self.client.post(verify_url, {
            "email": "diana.chebet@example.com",
            "otp_code": otp_code,
        })
        self.assertEqual(verify_resp.status_code, 302)
        self.assertIn("dashboard", verify_resp.url)

        # Check user is logged in
        self.assertEqual(int(self.client.session["_auth_user_id"]), applicant_user.id)

    def test_application_submission_with_complete_guardian_details(self):
        # Create & login applicant user
        user = User.objects.create_user(
            username="applicant.kelvin",
            email="kelvin.ochieng@example.com",
            password="Password123!",
            role=Role.APPLICANT,
        )
        self.client.force_login(user)

        # Pre-upload draft KCSE certificate
        upload_url = reverse("university:admissions_upload_document")
        file = SimpleUploadedFile("kcse.pdf", self.valid_pdf_content, content_type="application/pdf")
        self.client.post(upload_url, {"document_type": "kcse_document", "file": file})

        apply_url = reverse("university:admissions_apply")
        form_data = {
            "program": self.program.pk,
            "first_name": "Kelvin",
            "last_name": "Ochieng",
            "email": "kelvin.ochieng@example.com",
            "phone": "+254712345678",
            "date_of_birth": "2005-04-12",
            "gender": "MALE",
            "national_id": "39485721",
            "address": "P.O Box 101, Nairobi",
            # Guardian Details
            "guardian_name": "George Ochieng",
            "guardian_relationship": "Father",
            "guardian_phone": "+254722112233",
            "guardian_alternative_phone": "+254733445566",
            "guardian_email": "george.ochieng@example.com",
            "guardian_address": "P.O Box 101, Nairobi",
            "guardian_country": "Kenya",
            "guardian_occupation": "Civil Engineer",
            "guardian_employer": "National Highways Authority",
            "is_guardian_emergency_contact": "on",
            # Academic details
            "secondary_school": "Maseno School",
            "kcse_index_number": "11223344/005",
            "kcse_mean_grade": "A-",
            "kcse_year": "2024",
        }

        resp = self.client.post(apply_url, form_data)
        self.assertEqual(resp.status_code, 302)

        # Verify application created with complete guardian details
        app = Application.objects.filter(email="kelvin.ochieng@example.com").first()
        self.assertIsNotNone(app)
        self.assertEqual(app.applicant_user, user)
        self.assertEqual(app.guardian_name, "George Ochieng")
        self.assertEqual(app.guardian_relationship, "Father")
        self.assertEqual(app.guardian_phone, "+254722112233")
        self.assertEqual(app.guardian_occupation, "Civil Engineer")
        self.assertTrue(app.is_guardian_emergency_contact)
        self.assertEqual(app.status, Application.Status.READY_FOR_PAYMENT)

    def test_guardian_validation_errors(self):
        apply_url = reverse("university:admissions_apply")
        invalid_data = {
            "program": self.program.pk,
            "first_name": "Kelvin",
            "last_name": "Ochieng",
            "email": "kelvin.ochieng@example.com",
            "phone": "+254712345678",
            "date_of_birth": "2005-04-12",
            "gender": "MALE",
            "national_id": "39485721",
            "guardian_name": "",  # Missing guardian name
            "guardian_phone": "",  # Missing guardian phone
        }
        resp = self.client.post(apply_url, invalid_data)
        self.assertEqual(resp.status_code, 200)
        # Should stay on form with errors
        self.assertFalse(Application.objects.filter(email="kelvin.ochieng@example.com").exists())

    def test_authenticated_payment_and_explicit_submission_flow(self):
        # 1. Create applicant & application
        applicant = User.objects.create_user(
            username="app.mercy",
            email="mercy.njeri@example.com",
            password="Password123!",
            role=Role.APPLICANT,
        )
        self.client.force_login(applicant)

        application = Application.objects.create(
            application_number="APP-2026-9001",
            applicant_user=applicant,
            intake=self.intake,
            program=self.program,
            first_name="Mercy",
            last_name="Njeri",
            email="mercy.njeri@example.com",
            phone="+254799887766",
            date_of_birth=date(2004, 3, 10),
            gender="FEMALE",
            national_id="38495021",
            guardian_name="Jane Njeri",
            guardian_relationship="Mother",
            guardian_phone="+254711009988",
            status=Application.Status.READY_FOR_PAYMENT,
        )

        # 2. Pay Application Fee
        pay_url = reverse("university:pay_application_fee", args=[application.pk])
        pay_resp = self.client.post(pay_url, {
            "method": "MPESA",
            "reference": "QW998877XX",
        })
        self.assertEqual(pay_resp.status_code, 302)

        # Verify Payment and Receipt
        application.refresh_from_db()
        self.assertTrue(application.fee_paid)
        self.assertEqual(application.status, Application.Status.READY_FOR_SUBMISSION)

        payment = ApplicationFeePayment.objects.filter(application=application).first()
        self.assertIsNotNone(payment)
        self.assertTrue(payment.receipt_number.startswith("PAY-2026-"))
        self.assertEqual(payment.amount, Decimal("1000.00"))

        # 3. Duplicate Payment is blocked
        dup_resp = self.client.post(pay_url, {
            "method": "CARD",
            "reference": "NEWREF1234",
        })
        self.assertEqual(dup_resp.status_code, 302)
        self.assertEqual(ApplicationFeePayment.objects.filter(application=application).count(), 1)

        # 4. Explicit Final Submission
        submit_url = reverse("university:submit_application", args=[application.pk])
        sub_resp = self.client.post(submit_url)
        self.assertEqual(sub_resp.status_code, 302)

        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)

    def test_idor_protection_for_payment_and_submission(self):
        # Applicant A
        user_a = User.objects.create_user(username="user.a", email="a@example.com", password="x", role=Role.APPLICANT)
        app_a = Application.objects.create(
            application_number="APP-2026-000A",
            applicant_user=user_a,
            intake=self.intake,
            program=self.program,
            first_name="User",
            last_name="A",
            email="a@example.com",
            phone="+254711111111",
            date_of_birth=date(2004, 1, 1),
            gender="MALE",
            national_id="11111111",
            status=Application.Status.READY_FOR_PAYMENT,
        )

        # Applicant B logs in and tries to pay/submit for Applicant A without access token
        user_b = User.objects.create_user(username="user.b", email="b@example.com", password="x", role=Role.APPLICANT)
        self.client.force_login(user_b)

        pay_url = reverse("university:pay_application_fee", args=[app_a.pk])
        pay_resp = self.client.post(pay_url, {"method": "MPESA", "reference": "MALICIOUS123"})
        # Should be denied / redirected to login
        self.assertEqual(pay_resp.status_code, 302)
        self.assertIn("login", pay_resp.url)
        self.assertFalse(app_a.fee_paid)

    def test_matriculation_syncs_guardian_details_to_student_profile(self):
        application = Application.objects.create(
            application_number="APP-2026-MATRIC1",
            intake=self.intake,
            program=self.program,
            first_name="Grace",
            last_name="Mutua",
            email="grace.mutua@example.com",
            phone="+254722334455",
            date_of_birth=date(2004, 8, 20),
            gender="FEMALE",
            national_id="38291032",
            address="Machakos, Kenya",
            guardian_name="Samuel Mutua",
            guardian_relationship="Father",
            guardian_phone="+254733112233",
            guardian_email="samuel.mutua@example.com",
            guardian_address="P.O Box 55, Machakos",
            status=Application.Status.ACCEPTED,
        )

        student, user, _ = matriculate_applicant(application, created_by=self.admin)
        self.assertEqual(student.guardian_name, "Samuel Mutua")
        self.assertEqual(student.guardian_relationship, "Father")
        self.assertEqual(student.guardian_phone, "+254733112233")
        self.assertEqual(student.guardian_email, "samuel.mutua@example.com")
        self.assertEqual(student.guardian_address, "P.O Box 55, Machakos")

    def test_admin_admission_detail_view_renders_guardian_info(self):
        self.client.force_login(self.admin)
        application = Application.objects.create(
            application_number="APP-2026-ADMINDETAIL",
            intake=self.intake,
            program=self.program,
            first_name="Brian",
            last_name="Kiprono",
            email="brian.kiprono@example.com",
            phone="+254722998877",
            date_of_birth=date(2005, 2, 14),
            gender="MALE",
            national_id="39281726",
            guardian_name="Ezekiel Kiprono",
            guardian_relationship="Father",
            guardian_phone="+254711554433",
            status=Application.Status.SUBMITTED,
        )

        detail_url = reverse("university:admin_admission_detail", args=[application.pk])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ezekiel Kiprono")
        self.assertContains(resp, "+254711554433")
        self.assertContains(resp, "Parent / Guardian &amp; Emergency Contact")
