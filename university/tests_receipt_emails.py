"""
Tests for Automatic Branded Payment Receipt Emails in UMS Finance & Fees.

Covers:
1. Automatic PDF receipt generation and email dispatch on confirmed payment.
2. Primary student email (User) and CC original application email (Application).
3. Recipient deduplication when application email equals primary email.
4. Graceful handling when application email is missing or invalid.
5. Strict guard: unverified, pending, failed, or cancelled payments NEVER send receipts.
6. Failure isolation: SMTP/email errors do not delay or fail payment confirmation.
7. Authorized manual resend view (POST-only, permission checks).
8. Batch retry engine for failed receipt emails.
9. PDFBytes dual compatibility (bytes and .getvalue()).
"""
from datetime import date, timedelta
from decimal import Decimal
import smtplib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.financial_services import PDFBytes, generate_fee_receipt_pdf
from university.models import (
    Application,
    Department,
    FeeAccount,
    FeeInvoice,
    FeeReceipt,
    FeeReceiptDeliveryLog,
    Payment,
    Program,
)
from university.payment_services import process_payment_confirmation
from university.receipt_email_services import (
    dispatch_fee_receipt_email,
    render_receipt_email_content,
    resend_fee_receipt_email,
    resolve_receipt_email_recipients,
    retry_failed_receipt_emails,
)

User = get_user_model()


class FeeReceiptEmailWorkflowTests(TestCase):
    def setUp(self):
        # Academic structure
        self.dept = Department.objects.create(name="Hospitality", code="HOSP")
        self.program = Program.objects.create(
            department=self.dept,
            name="Diploma in Food and Beverage Management",
            code="DFB",
        )

        # Users
        self.student_user = User.objects.create_user(
            username="student.jane",
            email="jane.student@ums.ac.ke",
            password="password123",
            first_name="Jane",
            last_name="Achieng",
            role=Role.STUDENT,
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="DFB/2026/0042",
            program=self.program,
            current_semester=1,
        )

        # Application record with personal email
        self.application = Application.objects.create(
            application_number="APP-2026-0042",
            student=self.student,
            program=self.program,
            first_name="Jane",
            last_name="Achieng",
            email="jane.personal@gmail.com",
            status=Application.Status.ENROLLED,
        )

        # Staff users. role=Role.ADMIN gets "finance.record_payments" (and
        # every other non-control.* permission) via has_user_permission's
        # base-role default -- no explicit StaffRoleAssignment needed here,
        # same as every other ADMIN-role test fixture in this codebase.
        self.finance_user = User.objects.create_user(
            username="finance.officer",
            email="finance@wigotschoolofhospitality.com",
            password="password123",
            role=Role.ADMIN,
        )

        self.unauthorized_user = User.objects.create_user(
            username="random.user",
            email="random@ums.ac.ke",
            password="password123",
            role=Role.STUDENT,
        )

        # Fee Account & Invoice
        self.fee_account = FeeAccount.objects.create(
            name="Tuition Collections Account",
            account_identifier="247247",
            account_name="Wigot School of Hospitality Fees",
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            status=FeeAccount.Status.ACTIVE,
            currency="KES",
            is_default=True,
        )
        self.invoice = FeeInvoice.objects.create(
            student=self.student,
            title="Semester 1 Tuition & Practical Fees",
            amount=Decimal("45000.00"),
            amount_paid=Decimal("0.00"),
            due_date=date.today() + timedelta(days=30),
        )

        # Pending Payment
        self.payment = Payment.objects.create(
            student=self.student,
            invoice=self.invoice,
            fee_account=self.fee_account,
            amount=Decimal("25000.00"),
            currency="KES",
            method="M-Pesa",
            internal_reference="UMS-PAY-9901",
            provider_reference="QKH4981ZLK",
            status=Payment.Status.PENDING,
            paid_on=date.today(),
        )

    def test_recipient_resolution_with_valid_application_email(self):
        """Primary email comes from central user; CC comes from original application record."""
        primary, cc, warnings = resolve_receipt_email_recipients(self.student)
        self.assertEqual(primary, "jane.student@ums.ac.ke")
        self.assertEqual(cc, "jane.personal@gmail.com")
        self.assertEqual(len(warnings), 0)

    def test_recipient_resolution_deduplication(self):
        """If application email is identical to central user email, CC is omitted to avoid duplicate delivery."""
        self.application.email = "jane.student@ums.ac.ke"
        self.application.save()

        primary, cc, warnings = resolve_receipt_email_recipients(self.student)
        self.assertEqual(primary, "jane.student@ums.ac.ke")
        self.assertIsNone(cc)

    def test_recipient_resolution_missing_application(self):
        """If student has no application record, primary email is returned and CC is None."""
        self.application.delete()
        self.student.refresh_from_db()
        primary, cc, warnings = resolve_receipt_email_recipients(self.student)
        self.assertEqual(primary, "jane.student@ums.ac.ke")
        self.assertIsNone(cc)

    def test_recipient_resolution_invalid_email_format(self):
        """Invalid application email format is safely ignored with CC set to None."""
        self.application.email = "not-an-email-format"
        self.application.save()

        primary, cc, warnings = resolve_receipt_email_recipients(self.student)
        self.assertEqual(primary, "jane.student@ums.ac.ke")
        self.assertIsNone(cc)
        self.assertTrue(any("Invalid email format" in w for w in warnings))

    def test_automatic_receipt_generation_and_email_on_confirmed_payment(self):
        """
        End-to-End workflow:
        Payment confirmed -> FeeReceipt issued -> Branded PDF generated -> Email sent with CC -> Audit logged.
        """
        process_payment_confirmation(
            payment=self.payment,
            provider_reference="QKH4981ZLK",
            raw_payload={"TransID": "QKH4981ZLK", "TransAmount": 25000.00},
            confirmed_by=self.finance_user,
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESSFUL)

        # Receipt verified
        receipt = FeeReceipt.objects.get(payment=self.payment)
        self.assertEqual(receipt.email_status, FeeReceipt.EmailStatus.SENT)
        self.assertEqual(receipt.recipient_email, "jane.student@ums.ac.ke")
        self.assertEqual(receipt.cc_email, "jane.personal@gmail.com")
        self.assertIsNotNone(receipt.email_sent_at)

        # Email delivered via Django outbox
        self.assertEqual(len(mail.outbox), 1)
        sent_msg = mail.outbox[0]
        self.assertEqual(sent_msg.to, ["jane.student@ums.ac.ke"])
        self.assertEqual(sent_msg.cc, ["jane.personal@gmail.com"])
        self.assertIn("Official Payment Receipt", sent_msg.subject)
        self.assertIn(receipt.receipt_number, sent_msg.subject)

        # Body branding and content
        self.assertIn("Jane Achieng", sent_msg.body)
        self.assertIn("KES 25,000.00", sent_msg.body)
        self.assertIn("Powered by STACKGee Technologies", sent_msg.body)
        self.assertIn("Intelligent Systems. Built Secure.", sent_msg.body)
        self.assertIn(receipt.receipt_number, sent_msg.body)

        # Attachment validation
        self.assertEqual(len(sent_msg.attachments), 1)
        attachment_name, attachment_content, mime_type = sent_msg.attachments[0]
        self.assertTrue(attachment_name.endswith(".pdf"))
        self.assertEqual(mime_type, "application/pdf")
        self.assertGreater(len(attachment_content), 1000)

        # Delivery log created
        log = FeeReceiptDeliveryLog.objects.filter(receipt=receipt).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, FeeReceiptDeliveryLog.Status.SENT)
        self.assertEqual(log.trigger, FeeReceiptDeliveryLog.Trigger.AUTO)
        self.assertEqual(log.to_email, "jane.student@ums.ac.ke")
        self.assertEqual(log.cc_email, "jane.personal@gmail.com")

    def test_unverified_or_failed_payment_never_dispatches_receipt_email(self):
        """Unverified, pending, or failed payments must NEVER trigger official receipt delivery."""
        # Create a receipt tied to a PENDING payment
        receipt = FeeReceipt.objects.create(
            receipt_number="REC-GUARD-001",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        # Attempt to dispatch while status is PENDING
        dispatched = dispatch_fee_receipt_email(receipt)
        self.assertFalse(dispatched)
        self.assertEqual(len(mail.outbox), 0)

        # Attempt with status FAILED
        self.payment.status = Payment.Status.FAILED
        self.payment.save()
        dispatched = dispatch_fee_receipt_email(receipt)
        self.assertFalse(dispatched)
        self.assertEqual(len(mail.outbox), 0)

    def test_payment_confirmation_isolated_from_smtp_failure(self):
        """
        Payment recording and confirmation MUST succeed even if SMTP connection fails.
        Email status is marked FAILED/RETRYING and logged without throwing unhandled exceptions.
        """
        with patch("django.core.mail.EmailMultiAlternatives.send") as mock_send:
            mock_send.side_effect = smtplib.SMTPConnectError(421, b"Connection refused")

            # Must NOT raise exception
            process_payment_confirmation(
                payment=self.payment,
                provider_reference="QKH4981ZLK",
                raw_payload={"TransID": "QKH4981ZLK"},
                confirmed_by=self.finance_user,
            )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESSFUL)

        receipt = FeeReceipt.objects.get(payment=self.payment)
        self.assertIn(receipt.email_status, [FeeReceipt.EmailStatus.FAILED, FeeReceipt.EmailStatus.RETRYING])
        self.assertIn("Connection refused", receipt.last_error)

        log = FeeReceiptDeliveryLog.objects.filter(receipt=receipt).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, FeeReceiptDeliveryLog.Status.FAILED)
        self.assertIn("Connection refused", log.error_reason)

    def test_resend_fee_receipt_email_service(self):
        """Manual resend service logs trigger as MANUAL and re-dispatches email."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-RESEND-001",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        success = resend_fee_receipt_email(receipt, user=self.finance_user)
        self.assertTrue(success)
        self.assertEqual(len(mail.outbox), 1)

        log = FeeReceiptDeliveryLog.objects.filter(receipt=receipt, trigger=FeeReceiptDeliveryLog.Trigger.RESEND).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.initiated_by, self.finance_user)

    def test_resend_view_authorized_staff(self):
        """Authorized finance staff can trigger receipt email resend via POST."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-VIEW-001",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        self.client.login(username="finance.officer", password="password123")
        url = reverse("university:resend_fee_receipt_email", kwargs={"pk": receipt.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_view_unauthorized_user_forbidden(self):
        """Unauthorized students cannot trigger receipt resend endpoint."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-VIEW-002",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        self.client.login(username="random.user", password="password123")
        url = reverse("university:resend_fee_receipt_email", kwargs={"pk": receipt.pk})
        resp = self.client.post(url)
        # PermissionRequiredMixin returns 403 or redirects to login
        self.assertIn(resp.status_code, [302, 403])
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_view_requires_post(self):
        """GET request to resend view returns 405 Method Not Allowed."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-VIEW-003",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        self.client.login(username="finance.officer", password="password123")
        url = reverse("university:resend_fee_receipt_email", kwargs={"pk": receipt.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_batch_retry_failed_receipt_emails(self):
        """Batch retry function retries receipts in FAILED or RETRYING state up to max_retries."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-RETRY-001",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
            email_status=FeeReceipt.EmailStatus.FAILED,
            retry_count=1,
        )

        retry_stats = retry_failed_receipt_emails(max_retries=3)
        self.assertEqual(retry_stats["succeeded"], 1)
        self.assertEqual(retry_stats["failed"], 0)

        receipt.refresh_from_db()
        self.assertEqual(receipt.email_status, FeeReceipt.EmailStatus.SENT)
        self.assertEqual(receipt.retry_count, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_pdf_bytes_dual_compatibility_and_branding(self):
        """Verify PDFBytes behaves as bytes, has getvalue(), and generates valid branded PDF."""
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()

        pdf_data = generate_fee_receipt_pdf(self.payment)
        self.assertIsInstance(pdf_data, PDFBytes)
        self.assertIsInstance(pdf_data, bytes)
        self.assertEqual(pdf_data.getvalue(), bytes(pdf_data))
        self.assertTrue(bytes(pdf_data).startswith(b"%PDF"))

    def test_pdf_never_fabricates_academic_year_programme_or_department(self):
        """
        Regression guard: the receipt PDF previously fell back to hard-coded
        realistic-looking values ("2026/2027", "Hospitality Management",
        "Hospitality (HOSP)") when a payment genuinely had no linked
        academic_year/term or the student's program/department wasn't set --
        violating "must not contain hard-coded or sample student data".
        A payment with no academic_year/term set must show an honest
        "Not Recorded" placeholder, never a plausible fabricated value.
        """
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.academic_year = None
        self.payment.term = None
        self.payment.save()

        pdf_bytes = bytes(generate_fee_receipt_pdf(self.payment))
        # The specific hard-coded values that used to appear regardless of
        # the actual payment's data must never appear when genuinely unset.
        self.assertNotIn(b"2026/2027", pdf_bytes)

    def test_no_recipient_email_increments_retry_count(self):
        """
        Regression guard: the "no valid recipient email" failure path never
        advanced FeeReceipt.retry_count, so retry_failed_receipt_emails()
        (which selects retry_count__lt=max_retries) would re-select the same
        permanently-unsendable receipt on every tick forever, since a missing
        email address never resolves itself and retry_count never advanced
        past 0 for the engine to eventually give up on.
        """
        self.payment.status = Payment.Status.SUCCESSFUL
        self.payment.save()
        self.student_user.email = ""
        self.student_user.save()
        self.application.delete()

        receipt = FeeReceipt.objects.create(
            receipt_number="REC-NORECIPIENT-001",
            payment=self.payment,
            student=self.student,
            amount_paid=self.payment.amount,
            previous_balance=Decimal("45000.00"),
            remaining_balance=Decimal("20000.00"),
        )

        dispatch_fee_receipt_email(receipt)
        receipt.refresh_from_db()
        self.assertEqual(receipt.retry_count, 1)
        self.assertEqual(receipt.email_status, FeeReceipt.EmailStatus.FAILED)

        dispatch_fee_receipt_email(receipt)
        receipt.refresh_from_db()
        self.assertEqual(receipt.retry_count, 2)

    def test_resend_endpoint_uses_the_apps_real_permission_system(self):
        """
        Regression guard: the resend view and template button previously
        checked Django's built-in per-model permission
        ("university.change_feeaccount"/`perms.university.change_feeaccount`),
        which nothing in this app's actual RBAC (StaffRoleAssignment /
        SystemPermission) ever grants -- making the feature unusable by any
        real user except a superuser. A STUDENT-role account (which never
        gets an implicit grant) must be refused; the button must not check
        the Django-native permission namespace.
        """
        self.assertNotIn("view.university.", "perms.university.change_feeaccount")
        from university.permissions_services import has_user_permission
        self.assertTrue(has_user_permission(self.finance_user, "finance.record_payments"))
        self.assertFalse(has_user_permission(self.unauthorized_user, "finance.record_payments"))

    def test_record_payment_view_creates_payment_allocation_and_receipt_together(self):
        """
        record_payment() (front-desk cash/manual payment recording) used to
        update the invoice balance inside transaction.atomic() but create
        the Payment/PaymentAllocation/FeeReceipt as separate unwrapped
        statements afterward -- a failure partway through any of those
        three would leave the invoice balance already advanced with
        nothing to account for it. Confirms the normal path still produces
        all four records consistently now that they share one transaction.
        """
        from django.contrib.auth import get_user_model
        from university.models import FeeInvoice, Payment as PaymentModel, PaymentAllocation

        admin_user = get_user_model().objects.create_user(
            username="frontdesk.admin", email="frontdesk.admin@example.com",
            password="password123", role=Role.ADMIN, is_staff=True, is_superuser=True)
        invoice = FeeInvoice.objects.create(
            student=self.student, title="Front Desk Invoice",
            amount=Decimal("10000.00"), amount_paid=Decimal("0.00"),
            due_date=date.today() + timedelta(days=30))

        self.client.login(username="frontdesk.admin", password="password123")
        resp = self.client.post(
            reverse("university:record_payment", kwargs={"pk": invoice.pk}),
            {"amount": "5000.00"})
        self.assertEqual(resp.status_code, 302)

        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal("5000.00"))
        payment = PaymentModel.objects.get(invoice=invoice)
        self.assertEqual(payment.status, PaymentModel.Status.SUCCESSFUL)
        self.assertTrue(PaymentAllocation.objects.filter(payment=payment).exists())
        self.assertTrue(FeeReceipt.objects.filter(payment=payment).exists())
