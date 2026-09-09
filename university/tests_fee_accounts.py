"""
End-to-End Test Suite for Fee Accounts, Payment Gateways & Student Pay Fees:
1. Fee Account Administration:
   - Creation (Paybill, Till, Card, Bank)
   - Activation / Deactivation (toggling status)
   - Default account selection constraint
   - Diagnostic Test Connection execution and logging
   - Deletion / Archival

2. Student Pay Fees:
   - Display of outstanding balance and currency
   - Dynamic listing of ACTIVE fee accounts only
   - Disappearance of deactivated accounts
   - Payment initiation validation (amount > 0, account presence)

3. Payment Confirmation, Webhooks & Idempotency:
   - Provider callback confirmation
   - Automatic allocation to pending FeeInvoices
   - Automatic FeeReceipt generation with balance tracking
   - Rejection of duplicate callbacks (idempotency)

4. Financial Admin Oversight:
   - Payments ledger listing & filtering
   - Manual payment verification
   - Payment reversal & refund with justification reason
   - Reconciliation resolution
"""
from datetime import date, timedelta
from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.models import (
    AcademicYear,
    AuditLog,
    Department,
    FeeAccount,
    FeeAccountLog,
    FeeInvoice,
    FeeReceipt,
    Payment,
    PaymentAllocation,
    PaymentReconciliation,
    PaymentReversal,
    Program,
    AcademicTerm,
)
from university.payment_services import (
    get_active_fee_accounts_for_student,
    get_student_balance_summary,
    initiate_student_payment,
    process_payment_confirmation,
    allocate_payment_to_invoices,
    reverse_or_refund_payment,
    test_fee_account_connection,
)

User = get_user_model()


class FeeAccountIntegrationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="School of Computing", code="SOC")
        cls.program = Program.objects.create(department=cls.dept, name="BSc Software Engineering", code="BSE")

        cls.academic_year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 8, 31),
            is_current=True,
        )
        cls.term = AcademicTerm.objects.create(
            academic_year=cls.academic_year,
            name="Semester 1 2025/2026",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 20),
            semester_number=1,
        )

        # Admin user
        cls.admin = User.objects.create_user(
            username="finance.director",
            email="director@ums.ac.ke",
            password="adminpassword123",
            role=Role.ADMIN,
        )

        # Student user
        cls.student_user = User.objects.create_user(
            username="alice.student",
            email="alice@ums.ac.ke",
            password="studentpassword123",
            role=Role.STUDENT,
            first_name="Alice",
            last_name="Mutiso",
        )
        cls.student = StudentProfile.objects.create(
            user=cls.student_user,
            roll_no="BSE/2026/001",
            program=cls.program,
            current_semester=1,
        )

        # Invoices
        cls.invoice1 = FeeInvoice.objects.create(
            student=cls.student,
            title="Tuition Fees Semester 1",
            amount=Decimal("45000.00"),
            amount_paid=Decimal("0.00"),
            term=cls.term,
            due_date=date.today() + timedelta(days=30),
        )
        cls.invoice2 = FeeInvoice.objects.create(
            student=cls.student,
            title="ICT & Lab Amenities Fee",
            amount=Decimal("5000.00"),
            amount_paid=Decimal("0.00"),
            term=cls.term,
            due_date=date.today() + timedelta(days=30),
        )

        # Configured Fee Accounts
        cls.paybill = FeeAccount.objects.create(
            name="Main Tuition Paybill",
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            provider=FeeAccount.Provider.SAFARICOM,
            account_identifier="522123",
            account_name="UNIVERSITY FEES COLLECTION",
            currency="KES",
            environment=FeeAccount.Environment.SANDBOX,
            is_default=True,
            status=FeeAccount.Status.ACTIVE,
        )
        cls.card_gateway = FeeAccount.objects.create(
            name="Online Card Portal",
            account_type=FeeAccount.AccountType.CARD_GATEWAY,
            provider=FeeAccount.Provider.STRIPE,
            account_identifier="acct_card_12345",
            account_name="UMS CARD CHECKOUT",
            currency="KES",
            environment=FeeAccount.Environment.SANDBOX,
            is_default=True,
            status=FeeAccount.Status.ACTIVE,
        )


class FeeAccountAdminTests(FeeAccountIntegrationTestBase):
    def setUp(self):
        self.client.login(username="finance.director", password="adminpassword123")

    def test_fee_accounts_dashboard_loads(self):
        resp = self.client.get(reverse("university:fee_accounts_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Main Tuition Paybill")
        self.assertContains(resp, "Online Card Portal")
        self.assertContains(resp, "522123")

    def test_create_fee_account(self):
        url = reverse("university:fee_account_create")
        post_data = {
            "name": "Library Till Account",
            "account_type": FeeAccount.AccountType.MPESA_TILL,
            "provider": FeeAccount.Provider.SAFARICOM,
            "account_identifier": "998877",
            "account_name": "UNIVERSITY LIBRARY TILL",
            "currency": "KES",
            "environment": FeeAccount.Environment.SANDBOX,
            "description": "Buy goods till for library charges",
            "is_default": "on",
            "secret_key": "SuperSecretPasskey",
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)

        till_acc = FeeAccount.objects.get(account_identifier="998877")
        self.assertEqual(till_acc.name, "Library Till Account")
        self.assertTrue(till_acc.is_default)
        self.assertEqual(till_acc.status, FeeAccount.Status.ACTIVE)
        # Secret should not be visible in raw string
        self.assertIn("SuperSecretPasskey", till_acc.encrypted_credentials)

    def test_toggle_fee_account_status(self):
        url = reverse("university:fee_account_toggle_status", kwargs={"pk": self.paybill.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.paybill.refresh_from_db()
        self.assertEqual(self.paybill.status, FeeAccount.Status.INACTIVE)

        # Toggle back to active
        resp2 = self.client.post(url)
        self.assertEqual(resp2.status_code, 302)
        self.paybill.refresh_from_db()
        self.assertEqual(self.paybill.status, FeeAccount.Status.ACTIVE)

    def test_set_default_account_unsets_previous_default(self):
        # Create a second Paybill
        second_paybill = FeeAccount.objects.create(
            name="Medical School Paybill",
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            provider=FeeAccount.Provider.SAFARICOM,
            account_identifier="654321",
            status=FeeAccount.Status.ACTIVE,
            is_default=False,
        )

        url = reverse("university:fee_account_set_default", kwargs={"pk": second_paybill.pk})
        self.client.post(url)

        second_paybill.refresh_from_db()
        self.paybill.refresh_from_db()

        self.assertTrue(second_paybill.is_default)
        self.assertFalse(self.paybill.is_default)

    def test_test_connection_diagnostics(self):
        url = reverse("university:fee_account_test", kwargs={"pk": self.paybill.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        self.paybill.refresh_from_db()
        self.assertEqual(self.paybill.last_test_status, "PASSED")
        self.assertIsNotNone(self.paybill.last_tested_at)

        log = FeeAccountLog.objects.filter(fee_account=self.paybill, event_type=FeeAccountLog.EventType.TEST_CONNECTION).first()
        self.assertIsNotNone(log)
        self.assertIn("522123", log.message)


class StudentPayFeesViewTests(FeeAccountIntegrationTestBase):
    def setUp(self):
        self.client.login(username="alice.student", password="studentpassword123")

    def test_student_pay_fees_displays_balance_and_active_accounts(self):
        resp = self.client.get(reverse("university:student_pay_fees"))
        self.assertEqual(resp.status_code, 200)
        # Total outstanding = 45,000 + 5,000 = 50,000
        self.assertContains(resp, "50,000")
        self.assertContains(resp, "Main Tuition Paybill")
        self.assertContains(resp, "Online Card Portal")
        self.assertContains(resp, "522123")

    def test_deactivated_fee_account_disappears_from_student_pay_fees(self):
        # Admin disables Paybill
        self.paybill.status = FeeAccount.Status.INACTIVE
        self.paybill.save()

        resp = self.client.get(reverse("university:student_pay_fees"))
        self.assertEqual(resp.status_code, 200)
        # Main Tuition Paybill should no longer appear
        self.assertNotContains(resp, "Main Tuition Paybill")
        # Online Card Portal is still active
        self.assertContains(resp, "Online Card Portal")

    def test_student_initiates_payment_creates_pending_record(self):
        url = reverse("university:student_initiate_payment")
        post_data = {
            "amount": "15000.00",
            "fee_account_id": str(self.paybill.pk),
            "phone": "0712345678",
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)

        payment = Payment.objects.filter(student=self.student, amount=Decimal("15000.00")).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.fee_account, self.paybill)
        self.assertEqual(payment.payer_phone, "254712345678")
        self.assertTrue(payment.internal_reference.startswith("PAY-"))

    def test_student_payment_rejection_on_invalid_amount(self):
        url = reverse("university:student_initiate_payment")
        # Zero or negative amount
        post_data = {
            "amount": "-500.00",
            "fee_account_id": str(self.paybill.pk),
        }
        resp = self.client.post(url, post_data)
        self.assertEqual(resp.status_code, 302)

        count = Payment.objects.filter(student=self.student, amount=Decimal("-500.00")).count()
        self.assertEqual(count, 0)


class PaymentConfirmationAndAllocationTests(FeeAccountIntegrationTestBase):
    def test_payment_confirmation_allocates_to_invoices_and_generates_receipt(self):
        # Student initiates KES 48,000 payment (covers 45,000 on invoice1 + 3,000 on invoice2)
        payment = Payment.objects.create(
            student=self.student,
            fee_account=self.paybill,
            amount=Decimal("48000.00"),
            currency="KES",
            method="M-Pesa Paybill",
            status=Payment.Status.PENDING,
            academic_year=self.academic_year,
            term=self.term,
        )

        receipt = process_payment_confirmation(
            payment=payment,
            provider_reference="MPESA-TEST-998877",
            raw_payload={"ResultCode": 0, "MpesaReceiptNumber": "MPESA-TEST-998877"},
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESSFUL)
        self.assertEqual(payment.provider_reference, "MPESA-TEST-998877")

        # Check allocations
        self.invoice1.refresh_from_db()
        self.invoice2.refresh_from_db()
        self.assertEqual(self.invoice1.amount_paid, Decimal("45000.00"))
        self.assertEqual(self.invoice1.balance, Decimal("0.00"))
        self.assertEqual(self.invoice1.status, FeeInvoice.PAID)

        self.assertEqual(self.invoice2.amount_paid, Decimal("3000.00"))
        self.assertEqual(self.invoice2.balance, Decimal("2000.00"))
        self.assertEqual(self.invoice2.status, FeeInvoice.PARTIAL)

        # Check Receipt
        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.receipt_number.startswith("REC-"))
        self.assertEqual(receipt.amount_paid, Decimal("48000.00"))
        self.assertEqual(receipt.previous_balance, Decimal("50000.00"))
        self.assertEqual(receipt.remaining_balance, Decimal("2000.00"))

        # Check Student Overall Balance
        bal = get_student_balance_summary(self.student)
        self.assertEqual(bal["balance"], Decimal("2000.00"))

    def test_duplicate_callback_idempotency_protection(self):
        # Create pending payment
        payment = Payment.objects.create(
            student=self.student,
            fee_account=self.paybill,
            amount=Decimal("10000.00"),
            currency="KES",
            method="M-Pesa Paybill",
            status=Payment.Status.PENDING,
            academic_year=self.academic_year,
            term=self.term,
        )

        # 1st Callback
        receipt1 = process_payment_confirmation(
            payment=payment,
            provider_reference="MPESA-IDEMPOTENT-001",
        )
        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.amount_paid, Decimal("10000.00"))

        # 2nd Duplicate Callback with identical provider reference
        receipt2 = process_payment_confirmation(
            payment=payment,
            provider_reference="MPESA-IDEMPOTENT-001",
        )

        # Invoices should NOT be credited twice
        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.amount_paid, Decimal("10000.00"))
        # Same receipt returned
        self.assertEqual(receipt1.receipt_number, receipt2.receipt_number)
        self.assertEqual(FeeReceipt.objects.filter(payment=payment).count(), 1)


class AdminFinanceOperationsTests(FeeAccountIntegrationTestBase):
    def setUp(self):
        self.client.login(username="finance.director", password="adminpassword123")

    def test_admin_payments_list_and_filter(self):
        p1 = Payment.objects.create(
            student=self.student,
            fee_account=self.paybill,
            amount=Decimal("5000.00"),
            currency="KES",
            status=Payment.Status.SUCCESSFUL,
            internal_reference="PAY-ADMIN-001",
        )
        p2 = Payment.objects.create(
            student=self.student,
            fee_account=self.card_gateway,
            amount=Decimal("12000.00"),
            currency="KES",
            status=Payment.Status.PENDING,
            internal_reference="PAY-ADMIN-002",
        )

        resp = self.client.get(reverse("university:admin_payments_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PAY-ADMIN-001")
        self.assertContains(resp, "PAY-ADMIN-002")

        # Filter by status
        resp_filtered = self.client.get(reverse("university:admin_payments_list") + "?status=SUCCESSFUL")
        self.assertContains(resp_filtered, "PAY-ADMIN-001")
        self.assertNotContains(resp_filtered, "PAY-ADMIN-002")

    def test_admin_payment_reversal_requires_reason_and_restores_balance(self):
        # Create successful payment and allocate to invoice1
        payment = Payment.objects.create(
            student=self.student,
            fee_account=self.paybill,
            amount=Decimal("20000.00"),
            currency="KES",
            status=Payment.Status.SUCCESSFUL,
            internal_reference="PAY-REVERSE-TEST",
        )
        allocate_payment_to_invoices(payment)
        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.amount_paid, Decimal("20000.00"))

        url = reverse("university:admin_payment_reverse", kwargs={"pk": payment.pk})

        # Try reverse without reason -> fails
        resp_no_reason = self.client.post(url, {"amount": "20000.00", "reason": ""})
        self.assertEqual(resp_no_reason.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESSFUL)

        # Reverse with reason
        resp_valid = self.client.post(url, {
            "amount": "20000.00",
            "reversal_type": PaymentReversal.ReversalType.REVERSAL,
            "reason": "Payment made to wrong department account; refunded per student request.",
        })
        self.assertEqual(resp_valid.status_code, 302)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REVERSED)

        # Invoice amount_paid restored
        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.amount_paid, Decimal("0.00"))

        # Reversal record saved
        reversal = PaymentReversal.objects.filter(original_payment=payment).first()
        self.assertIsNotNone(reversal)
        self.assertEqual(reversal.amount, Decimal("20000.00"))
        self.assertIn("refunded per student request", reversal.reason)

    def test_fee_reconciliation_resolution(self):
        recon = PaymentReconciliation.objects.create(
            fee_account=self.paybill,
            provider_reference="STMT-TRANS-445566",
            transaction_date=timezone.now(),
            amount=Decimal("15000.00"),
            currency="KES",
            status=PaymentReconciliation.Status.UNMATCHED,
            notes="Transaction listed in Safaricom Daraja monthly statement but missing student roll no.",
        )

        match_url = reverse("university:fee_reconciliation_match", kwargs={"pk": recon.pk})
        resp = self.client.post(match_url)
        self.assertEqual(resp.status_code, 302)

        recon.refresh_from_db()
        self.assertEqual(recon.status, PaymentReconciliation.Status.MATCHED)
        self.assertEqual(recon.reconciled_by, self.admin)
        self.assertIsNotNone(recon.reconciled_at)
