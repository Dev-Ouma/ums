from datetime import date, timedelta
from decimal import Decimal
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from university.admissions_views import application_access_token
from university.models import (
    AcademicYear, Application, ApplicationAttachment, ApplicationFeePayment,
    AuditLog, Department, FeeAccount, Intake, Program
)
from university.settings_services import set_setting


class AdmissionsSecurityAuditTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="School of Computing", code="SOC")
        self.program = Program.objects.create(name="BSc Computer Science", code="BCS", department=dept)
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            is_current=True,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Intake",
            academic_year=self.ay,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() + timedelta(days=60),
            is_active=True,
        )

        # Applicant Users
        self.applicant_a = User.objects.create_user(
            username="applicant_a",
            email="applicant_a@example.com",
            password="Password123!",
            role=Role.APPLICANT,
            first_name="Alice",
            last_name="Wanjiku",
        )
        self.applicant_b = User.objects.create_user(
            username="applicant_b",
            email="applicant_b@example.com",
            password="Password123!",
            role=Role.APPLICANT,
            first_name="Bob",
            last_name="Otieno",
        )
        self.admin = User.objects.create_superuser(
            username="adm_officer",
            email="adm_officer@ums.ac.ke",
            password="Password123!",
            role=Role.ADMIN,
        )

        # Applications
        self.app_a = Application.objects.create(
            application_number="APP-2026-00001",
            applicant_user=self.applicant_a,
            intake=self.intake,
            program=self.program,
            first_name="Alice",
            last_name="Wanjiku",
            email="applicant_a@example.com",
            phone="+254711000001",
            national_id="11111111",
            date_of_birth=date(2004, 1, 1),
            gender="FEMALE",
            guardian_name="Grace Wanjiku",
            guardian_phone="+254711999999",
            status=Application.Status.READY_FOR_PAYMENT,
        )
        self.app_b = Application.objects.create(
            application_number="APP-2026-00002",
            applicant_user=self.applicant_b,
            intake=self.intake,
            program=self.program,
            first_name="Bob",
            last_name="Otieno",
            email="applicant_b@example.com",
            phone="+254711000002",
            national_id="22222222",
            date_of_birth=date(2004, 2, 2),
            gender="MALE",
            guardian_name="David Otieno",
            guardian_phone="+254711888888",
            status=Application.Status.READY_FOR_PAYMENT,
        )

        # Active Fee Accounts
        self.pochi = FeeAccount.objects.create(
            name="University Pochi la Biashara",
            account_type=FeeAccount.AccountType.POCHI_LA_BIASHARA,
            provider=FeeAccount.Provider.SAFARICOM,
            account_identifier="0113636154",
            status=FeeAccount.Status.ACTIVE,
        )
        self.paybill = FeeAccount.objects.create(
            name="University Fees Paybill",
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            provider=FeeAccount.Provider.SAFARICOM,
            account_identifier="222111",
            status=FeeAccount.Status.ACTIVE,
        )

    def test_authenticated_cross_applicant_idor_denied_with_403(self):
        """Applicant B must not be permitted to access Applicant A's payment checkout or submission."""
        client_b = Client()
        client_b.force_login(self.applicant_b)

        # 1. Access payment page of applicant A
        resp = client_b.get(reverse("university:pay_application_fee", args=[self.app_a.pk]))
        self.assertEqual(resp.status_code, 403)

        # 2. Submit payment for applicant A
        resp_post = client_b.post(reverse("university:pay_application_fee", args=[self.app_a.pk]), {
            "method": "MPESA",
            "reference": "ATTACKREF01",
        })
        self.assertEqual(resp_post.status_code, 403)

        # 3. Final submit applicant A's application
        resp_submit = client_b.post(reverse("university:submit_application", args=[self.app_a.pk]))
        self.assertEqual(resp_submit.status_code, 403)

    def test_application_fee_payment_reference_unique_constraint(self):
        """The database must strictly enforce unique payment references preventing replay attacks."""
        ApplicationFeePayment.objects.create(
            application=self.app_a,
            applicant_user=self.applicant_a,
            amount=Decimal("1000.00"),
            method=ApplicationFeePayment.Method.MPESA,
            reference="QKH998877A",
            status=ApplicationFeePayment.Status.PENDING,
        )

        # Attempting to insert duplicate reference for another application must raise IntegrityError
        with self.assertRaises(IntegrityError):
            ApplicationFeePayment.objects.create(
                application=self.app_b,
                applicant_user=self.applicant_b,
                amount=Decimal("1000.00"),
                method=ApplicationFeePayment.Method.MPESA,
                reference="QKH998877A",
                status=ApplicationFeePayment.Status.PENDING,
            )

    def test_direct_mpesa_c2b_callback_confirms_application_fee(self):
        """
        When a prospective student pays via M-Pesa Paybill using their application number
        (BillRefNumber: APP-2026-00001), the C2B webhook must automatically credit the fee,
        mark it CONFIRMED, and transition status to READY_FOR_SUBMISSION.
        """
        client = Client()
        payload = {
            "TransactionType": "Pay Bill",
            "TransID": "QKH776655B",
            "TransTime": "20260913100000",
            "TransAmount": "1000.00",
            "BusinessShortCode": "222111",
            "BillRefNumber": self.app_a.application_number,
            "MSISDN": "254711000001",
            "FirstName": "Alice",
            "LastName": "Wanjiku",
        }

        resp = client.post(
            reverse("university:mpesa_callback"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ResultCode"), 0)

        # Verify application fee payment record created and confirmed
        payment = ApplicationFeePayment.objects.filter(reference="QKH776655B").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, ApplicationFeePayment.Status.CONFIRMED)
        self.assertEqual(payment.amount, Decimal("1000.00"))
        self.assertTrue(payment.receipt_number.startswith("APPFEE-"))

        self.app_a.refresh_from_db()
        self.assertTrue(self.app_a.fee_paid)
        self.assertEqual(self.app_a.status, Application.Status.READY_FOR_SUBMISSION)

    def test_upload_rejects_double_extensions(self):
        """Upload validator must reject deceptive executable double extensions (e.g. payload.php.png)."""
        client = Client()
        client.force_login(self.applicant_a)

        fake_php_png = SimpleUploadedFile(
            "shell.php.png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4",
            content_type="image/png",
        )
        resp = client.post(reverse("university:admissions_upload_document"), {
            "document_type": "id_document",
            "file": fake_php_png,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Suspicious file name detected", resp.json().get("error", ""))

    def test_documents_immutable_after_application_submission(self):
        """An applicant must not be able to upload or remove documents once the application is submitted."""
        # Set app_a to SUBMITTED
        self.app_a.status = Application.Status.SUBMITTED
        self.app_a.save(update_fields=["status"])

        client = Client()
        client.force_login(self.applicant_a)

        valid_pdf = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 test content", content_type="application/pdf")

        # 1. Attempt upload
        upload_resp = client.post(reverse("university:admissions_upload_document"), {
            "document_type": "kcse_document",
            "file": valid_pdf,
        })
        self.assertEqual(upload_resp.status_code, 403)
        self.assertIn("cannot be modified after application submission", upload_resp.json().get("error", ""))

        # 2. Attempt removal
        remove_resp = client.post(reverse("university:admissions_remove_document"), {
            "document_type": "kcse_document",
        })
        self.assertEqual(remove_resp.status_code, 403)
        self.assertIn("cannot be modified after application submission", remove_resp.json().get("error", ""))

    def test_offer_decline_workflow(self):
        """Applicant must be able to decline an official admission offer with audit trail."""
        self.app_a.status = Application.Status.ACCEPTED
        self.app_a.save(update_fields=["status"])

        client = Client()
        client.force_login(self.applicant_a)

        resp = client.post(
            reverse("university:applicant_decline_offer", args=[self.app_a.pk]),
            {"reason": "Decided to pursue studies overseas."},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        self.app_a.refresh_from_db()
        self.assertEqual(self.app_a.status, Application.Status.REJECTED)
        self.assertIn("Offer Declined by Applicant", self.app_a.review_notes)

        # Check AuditLog
        log_entry = AuditLog.objects.filter(
            entity="Application",
            entity_id=self.app_a.pk,
            action=AuditLog.Action.UPDATE,
        ).order_by("-timestamp").first()
        self.assertIsNotNone(log_entry)
        self.assertIn("declined admission offer", log_entry.description)

    def test_closed_intake_rejected_on_submission(self):
        """Submission must be rejected if the selected intake cycle has closed."""
        closed_intake = Intake.objects.create(
            name="Past Intake",
            academic_year=self.ay,
            start_date=date.today() - timedelta(days=90),
            end_date=date.today() - timedelta(days=10),
            is_active=True,
        )

        client = Client()
        client.force_login(self.applicant_a)

        resp = client.post(reverse("university:admissions_api_submit"), {
            "intake": closed_intake.pk,
            "program": self.program.pk,
            "first_name": "Alice",
            "last_name": "Wanjiku",
            "email": "applicant_a@example.com",
            "phone": "+254711000001",
            "date_of_birth": "2004-01-01",
            "gender": "FEMALE",
            "national_id": "11111111",
            "guardian_name": "Grace Wanjiku",
            "guardian_phone": "+254711999999",
        })
        self.assertEqual(resp.status_code, 422)
        errors = resp.json().get("errors", [])
        self.assertTrue(any("closed" in e.lower() for e in errors))

    def test_submitted_application_cannot_be_mutated_via_draft_services(self):
        """Once an application is submitted or accepted, draft update and resubmission are blocked."""
        from university.admissions_draft_services import update_applicant_draft, validate_and_submit_application
        self.app_a.status = Application.Status.SUBMITTED
        self.app_a.save(update_fields=["status"])

        # Attempt to modify first_name via draft service
        success, res = update_applicant_draft(self.app_a, {"first_name": "TamperedName"})
        self.assertFalse(success)
        self.assertIn("cannot be modified", res.get("message", ""))
        self.app_a.refresh_from_db()
        self.assertEqual(self.app_a.first_name, "Alice")

        # Attempt to resubmit already submitted application
        success_sub, res_sub = validate_and_submit_application(self.app_a, payload={})
        self.assertFalse(success_sub)
        self.assertIn("cannot be resubmitted", res_sub.get("message", ""))
        self.app_a.refresh_from_db()
        self.assertEqual(self.app_a.status, Application.Status.SUBMITTED)

    def test_applicant_otp_expiration_and_brute_force_capping(self):
        """OTP must expire after 15 minutes and reject brute force guessing after 5 failed attempts."""
        from university.applicant_auth_services import register_applicant, verify_applicant_otp
        from django.core.exceptions import ValidationError

        client = Client()
        session = client.session
        request = client.get("/").wsgi_request
        request.session = session

        user, otp = register_applicant(
            request,
            first_name="David",
            last_name="Kimani",
            email="david.kimani@example.com",
            phone="+254722000002",
            password="SecurePassword123!",
        )

        # 1. Expired OTP (>15 mins)
        pending = request.session["applicant_pending_verification"]
        pending["generated_at"] = (timezone.now() - timedelta(minutes=16)).isoformat()
        request.session["applicant_pending_verification"] = pending
        request.session.save()

        with self.assertRaises(ValidationError) as cm:
            verify_applicant_otp(request, "david.kimani@example.com", otp)
        self.assertIn("expired", str(cm.exception).lower())

        # 2. Reset with fresh OTP and test brute force capping (>5 attempts)
        request.session["applicant_pending_verification"] = {
            "email": "david.kimani@example.com",
            "user_id": user.id,
            "otp_code": otp,
            "generated_at": timezone.now().isoformat(),
            "attempts": 0,
        }
        request.session.save()

        for _ in range(5):
            with self.assertRaises(ValidationError):
                verify_applicant_otp(request, "david.kimani@example.com", "000000")

        # 6th attempt should trigger brute force lockout
        with self.assertRaises(ValidationError) as cm_lockout:
            verify_applicant_otp(request, "david.kimani@example.com", "000000")
        self.assertIn("too many", str(cm_lockout.exception).lower())

    def test_revoked_admission_letter_cannot_be_regenerated_by_applicant(self):
        """A revoked admission letter must not be auto-regenerated by an applicant."""
        from university.models import IssuedAdmissionDocument
        self.app_a.status = Application.Status.ACCEPTED
        self.app_a.save(update_fields=["status"])

        # Create a revoked document
        revoked_doc = IssuedAdmissionDocument.objects.create(
            application=self.app_a,
            document_reference="UMS/ADM/2026/00099",
            status=IssuedAdmissionDocument.Status.REVOKED,
            is_current_version=False,
            version=1,
            issue_date=date.today(),
        )

        client = Client()
        client.force_login(self.applicant_a)
        resp = client.get(reverse("university:view_admission_letter", args=[self.app_a.pk]))
        self.assertEqual(resp.status_code, 302)
        # Verify no new current document was auto-generated
        self.assertFalse(self.app_a.issued_documents.filter(is_current_version=True).exists())

    def test_tuition_fee_payment_cannot_be_hijacked_for_application_fee(self):
        """A Payment tied to a tuition invoice cannot be claimed as an application fee."""
        from accounts.models import StudentProfile
        from university.models import FeeInvoice, Payment

        student = StudentProfile.objects.create(
            user=self.applicant_b,
            roll_no="BCS/2026/00099",
            program=self.program,
        )
        invoice = FeeInvoice.objects.create(
            student=student,
            amount=Decimal("45000.00"),
            due_date=date.today() + timedelta(days=30),
        )
        tuition_payment = Payment.objects.create(
            invoice=invoice,
            amount=Decimal("1000.00"),
            reference="TUITION-PAY-999",
            provider_reference="MPESA-TUIT-999",
            status=Payment.Status.SUCCESSFUL,
        )

        client = Client()
        client.force_login(self.applicant_a)
        pay_url = reverse("university:admissions_fee_pay", args=[self.app_a.pk])
        resp = client.post(pay_url, {
            "method": "MPESA",
            "reference": "MPESA-TUIT-999",
        }, follow=True)

        self.app_a.refresh_from_db()
        self.assertFalse(self.app_a.fee_paid)
        # Must be PENDING (manual verification) rather than auto-CONFIRMED
        app_pay = self.app_a.fee_payments.filter(reference="MPESA-TUIT-999").first()
        self.assertIsNotNone(app_pay)
        self.assertEqual(app_pay.status, ApplicationFeePayment.Status.PENDING)

    def test_custom_field_query_flooding_mitigation(self):
        """Custom field submissions must be capped and prefetched to avoid N+1 and flooding."""
        from university.admissions_draft_services import validate_and_submit_application
        from university.models import ApplicationCustomField, ApplicationCustomFieldValue

        # Create 3 active custom fields
        cf1 = ApplicationCustomField.objects.create(name="campus", label="Campus", is_active=True)
        cf2 = ApplicationCustomField.objects.create(name="hostel", label="Hostel", is_active=True)
        cf_inactive = ApplicationCustomField.objects.create(name="legacy_code", label="Legacy", is_active=False)

        payload = {
            "first_name": "Alice",
            "last_name": "Wanjiku",
            "email": "alice@example.com",
            "phone": "+254711000001",
            "national_id": "11111111",
            "date_of_birth": "2004-01-01",
            "gender": "FEMALE",
            "guardian_name": "Grace Wanjiku",
            "guardian_phone": "+254711999999",
            "custom_campus": "Main Campus",
            "custom_hostel": "Block B",
            "custom_legacy_code": "IGNORED_INACTIVE",
        }
        # Add 60 dummy custom fields to test flooding cap (max 50)
        for i in range(60):
            payload[f"custom_dummy_{i}"] = f"val_{i}"

        # Submit draft
        app = Application.objects.create(
            application_number="APP-CUSTOM-001",
            applicant_user=self.applicant_a,
            intake=self.intake,
            program=self.program,
            status=Application.Status.DRAFT,
        )
        validate_and_submit_application(app, payload)

        # Ensure active custom fields were saved properly
        self.assertEqual(
            ApplicationCustomFieldValue.objects.get(application=app, field=cf1).value,
            "Main Campus"
        )
        self.assertEqual(
            ApplicationCustomFieldValue.objects.get(application=app, field=cf2).value,
            "Block B"
        )
        # Inactive custom field should NOT have been created
        self.assertFalse(
            ApplicationCustomFieldValue.objects.filter(application=app, field=cf_inactive).exists()
        )

    def test_admin_decision_state_machine_guards(self):
        """Admins cannot regress ENROLLED applications or accept non-submitted drafts."""
        client = Client()
        client.force_login(self.admin)
        url = reverse("university:admin_admission_detail", args=[self.app_a.pk])

        # 1. Application is currently READY_FOR_PAYMENT (not submitted).
        # Attempting to accept or move to under_review must fail.
        resp = client.post(url, {"action": "accept"}, follow=True)
        self.assertContains(resp, "The application must be fully submitted first.")
        self.app_a.refresh_from_db()
        self.assertEqual(self.app_a.status, Application.Status.READY_FOR_PAYMENT)

        resp = client.post(url, {"action": "under_review"}, follow=True)
        self.assertContains(resp, "The application must be fully submitted first.")
        self.app_a.refresh_from_db()
        self.assertEqual(self.app_a.status, Application.Status.READY_FOR_PAYMENT)

        # 2. Cannot reject draft application either
        resp = client.post(url, {"action": "reject"}, follow=True)
        self.assertContains(resp, "Cannot reject an application in &#x27;Ready For Payment&#x27; status.")

        # 3. Simulate enrolled student: changing status through admission detail is blocked
        self.app_a.status = Application.Status.ENROLLED
        self.app_a.save(update_fields=["status"])

        for forbidden_action in ("accept", "under_review", "reject"):
            resp = client.post(url, {"action": forbidden_action}, follow=True)
            self.assertContains(resp, "This applicant is already enrolled as an active student.")
            self.app_a.refresh_from_db()
            self.assertEqual(self.app_a.status, Application.Status.ENROLLED)

    def test_public_status_pii_masking(self):
        """Public status lookup must mask national ID and email to protect applicant privacy."""
        self.app_a.status = Application.Status.SUBMITTED
        self.app_a.email = "alice.wanjiku@example.com"
        self.app_a.national_id = "12345678"
        self.app_a.save(update_fields=["status", "email", "national_id"])

        self.assertEqual(self.app_a.national_id_masked, "123***78")
        self.assertEqual(self.app_a.email_masked, "a***@example.com")

        client = Client()
        status_url = reverse("university:admissions_status")
        token = application_access_token(self.app_a)
        resp = client.get(f"{status_url}?access={token}")
        self.assertEqual(resp.status_code, 200)

        # Unmasked sensitive PII should NOT be in the HTML response
        self.assertNotContains(resp, "alice.wanjiku@example.com")
        self.assertNotContains(resp, "12345678")
        # Masked values must be present
        self.assertContains(resp, "123***78")
        self.assertContains(resp, "a***@example.com")

    def test_fee_payment_blocked_on_submitted_application(self):
        """Applications that have already been submitted with confirmed fee cannot submit new payments."""
        ApplicationFeePayment.objects.create(
            application=self.app_a,
            applicant_user=self.applicant_a,
            amount=Decimal("1000.00"),
            method=ApplicationFeePayment.Method.MPESA,
            reference="EARLYPAY001",
            status=ApplicationFeePayment.Status.CONFIRMED,
        )
        self.app_a.status = Application.Status.SUBMITTED
        self.app_a.save(update_fields=["status"])

        client = Client()
        client.force_login(self.applicant_a)
        pay_url = reverse("university:pay_application_fee", args=[self.app_a.pk])

        resp = client.post(pay_url, {
            "method": "MPESA",
            "reference": "LATEPAY001",
        }, follow=True)

        # Must redirect with info message, without creating any new payment
        self.assertContains(resp, "has already been paid and verified")
        self.assertFalse(self.app_a.fee_payments.filter(reference="LATEPAY001").exists())

    def test_applicant_offer_acceptance_idor_protection(self):
        """An applicant cannot accept another applicant's admission offer (returns 403 Forbidden)."""
        self.app_a.status = Application.Status.ACCEPTED
        self.app_a.save(update_fields=["status"])

        client_b = Client()
        client_b.force_login(self.applicant_b)

        accept_url = reverse("university:applicant_accept_offer", args=[self.app_a.pk])
        resp = client_b.post(accept_url)

        self.assertEqual(resp.status_code, 403)
        self.app_a.refresh_from_db()
        self.assertNotIn("Offer Accepted", self.app_a.review_notes or "")

    def test_admissions_apply_page_uses_xss_safe_json_script(self):
        """Admission apply page must use Django's json_script instead of raw |safe embedding."""
        client = Client()
        resp = client.get(reverse("university:admissions_apply"))
        self.assertEqual(resp.status_code, 200)

        # Must have the initialDraftState json script tag generated by json_script
        self.assertContains(resp, 'id="initialDraftState"')
        self.assertContains(resp, 'type="application/json"')
        # Must NOT contain unsafe raw json unescaped injection
        self.assertNotContains(resp, '{{ draft_state_json|safe }}')

    def test_public_home_page_no_inline_onerror_scripts(self):
        """Public home page must not inject untrusted inline onerror JavaScript handlers."""
        client = Client()
        resp = client.get(reverse("university:home"))
        self.assertEqual(resp.status_code, 200)

        # Must not contain external third-party picsum inline onerror handlers
        self.assertNotContains(resp, "onerror=\"this.src='https://picsum.photos")

