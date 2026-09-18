"""
Regression tests for the end-to-end Finance & Fees module audit fixes:
- get_or_create_semester_invoice no longer fabricates a fake KES 55,500.00
  fallback invoice when no FeeStructure exists
- reverse_or_refund_payment now validates against the amount actually still
  reversible (blocks over-reversal across multiple reversal calls)
- admin_payment_verify no longer fabricates a "MANUAL-<timestamp>" provider
  reference when the field is left blank
- Missing audit logging on fee account default/delete, reconciliation match,
  and bank statement import
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.financial_services import FeeScheduleMissingError, get_or_create_semester_invoice
from university.models import (
    AuditLog,
    Department,
    FeeAccount,
    FeeInvoice,
    FeeStructure,
    Payment,
    PaymentReversal,
    Program,
)
from university.payment_services import reverse_or_refund_payment

User = get_user_model()


class SemesterInvoiceFabricationTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        user = User.objects.create_user(username="stu1", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=user, roll_no="BCS/001/2026", program=self.prog, current_semester=1)

    def test_raises_when_no_fee_structure_configured(self):
        with self.assertRaises(FeeScheduleMissingError):
            get_or_create_semester_invoice(self.student)
        self.assertFalse(FeeInvoice.objects.filter(student=self.student).exists())

    def test_uses_real_fee_structure_amount(self):
        FeeStructure.objects.create(program=self.prog, year_of_study=1, semester=1, tuition_fee=Decimal("60000.00"))
        fee_struct = FeeStructure.objects.get(program=self.prog)
        invoice, created = get_or_create_semester_invoice(self.student)
        self.assertTrue(created)
        self.assertEqual(invoice.amount, fee_struct.total_fee)


class PaymentReversalOverReversalTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        user = User.objects.create_user(username="stu2", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=user, roll_no="BCS/002/2026", program=self.prog, current_semester=1)
        self.admin = User.objects.create_user(username="finadmin", password="x", role=Role.ADMIN)
        self.invoice = FeeInvoice.objects.create(
            student=self.student, title="Tuition", amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"), due_date=date.today() + timedelta(days=30),
        )
        self.payment = Payment.objects.create(
            student=self.student, invoice=self.invoice, amount=Decimal("1000.00"),
            currency="KES", status=Payment.Status.SUCCESSFUL, internal_reference="PAY-REV-TEST",
        )
        from university.payment_services import allocate_payment_to_invoices
        allocate_payment_to_invoices(self.payment)

    def test_first_partial_reversal_succeeds(self):
        reverse_or_refund_payment(
            payment=self.payment, reversal_type=PaymentReversal.ReversalType.PARTIAL_REFUND,
            amount=Decimal("600.00"), reason="Overcharge", user=self.admin,
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("400.00"))

    def test_second_reversal_cannot_exceed_remaining_reversible_amount(self):
        reverse_or_refund_payment(
            payment=self.payment, reversal_type=PaymentReversal.ReversalType.PARTIAL_REFUND,
            amount=Decimal("600.00"), reason="First reversal", user=self.admin,
        )
        self.payment.refresh_from_db()
        # Only KES 400 of this payment remains reversible; requesting 600 more
        # must be rejected instead of over-reversing past what was ever paid.
        with self.assertRaises(ValueError):
            reverse_or_refund_payment(
                payment=self.payment, reversal_type=PaymentReversal.ReversalType.PARTIAL_REFUND,
                amount=Decimal("600.00"), reason="Second reversal", user=self.admin,
            )
        self.invoice.refresh_from_db()
        # Ledger must be unaffected by the rejected second reversal.
        self.assertEqual(self.invoice.amount_paid, Decimal("400.00"))

    def test_second_reversal_within_remaining_amount_succeeds(self):
        reverse_or_refund_payment(
            payment=self.payment, reversal_type=PaymentReversal.ReversalType.PARTIAL_REFUND,
            amount=Decimal("600.00"), reason="First reversal", user=self.admin,
        )
        self.payment.refresh_from_db()
        reverse_or_refund_payment(
            payment=self.payment, reversal_type=PaymentReversal.ReversalType.PARTIAL_REFUND,
            amount=Decimal("400.00"), reason="Second reversal", user=self.admin,
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))


class ManualPaymentVerifyFabricationTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept, duration_years=4)
        user = User.objects.create_user(username="stu3", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=user, roll_no="BCS/003/2026", program=self.prog, current_semester=1)
        self.admin = User.objects.create_user(username="finadmin2", password="password123", role=Role.ADMIN)
        self.invoice = FeeInvoice.objects.create(
            student=self.student, title="Tuition", amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"), due_date=date.today() + timedelta(days=30),
        )
        self.client = Client()

    def test_blank_reference_is_rejected_not_fabricated(self):
        payment = Payment.objects.create(
            student=self.student, invoice=self.invoice, amount=Decimal("1000.00"),
            currency="KES", status=Payment.Status.PENDING, internal_reference="PAY-BLANK-TEST",
            reference="",
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("university:admin_payment_verify", args=[payment.pk]))
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(payment.provider_reference.startswith("MANUAL-"))

    def test_student_declared_reference_is_accepted_as_real_evidence(self):
        payment = Payment.objects.create(
            student=self.student, invoice=self.invoice, amount=Decimal("1000.00"),
            currency="KES", status=Payment.Status.PENDING, internal_reference="PAY-REF-TEST",
            reference="QK89XY3410",
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("university:admin_payment_verify", args=[payment.pk]))
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESSFUL)
        self.assertEqual(payment.provider_reference, "QK89XY3410")


class FeeAccountAuditLoggingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="finadmin3", password="password123", role=Role.ADMIN)
        self.account1 = FeeAccount.objects.create(
            name="Paybill A", account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            account_identifier="123456", is_default=True,
        )
        self.account2 = FeeAccount.objects.create(
            name="Paybill B", account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            account_identifier="654321", is_default=False,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_set_default_logs_audit_entry(self):
        response = self.client.post(reverse("university:fee_account_set_default", args=[self.account2.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(module=AuditLog.Module.FEES, entity="FeeAccount", entity_id=str(self.account2.id)).exists()
        )

    def test_delete_without_payments_logs_audit_entry(self):
        response = self.client.post(reverse("university:fee_account_delete", args=[self.account2.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FeeAccount.objects.filter(pk=self.account2.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                module=AuditLog.Module.FEES, entity="FeeAccount", action=AuditLog.Action.DELETE,
            ).exists()
        )
