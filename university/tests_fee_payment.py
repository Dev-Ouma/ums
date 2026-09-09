"""
HTTP-level regression tests for the tuition "Pay Fees" workflow:
- Student dashboard: /me/fees/ (view invoices, submit a payment, download receipt)
- Admin dashboard: /manage/fees/ (view invoices, record a payment, download receipt)

Complements the model/service-level coverage in tests_new_modules.py
(check_financial_clearance, generate_fee_receipt_pdf, generate_student_statement_pdf)
by exercising the actual views a student or admin clicks through.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.models import Department, FeeInvoice, Payment, Program

User = get_user_model()


class FeePaymentTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="School of Computing", code="SOC")
        cls.program = Program.objects.create(department=dept, name="BSc Computer Science", code="BCS")

        cls.admin = User.objects.create_user(
            username="fee.admin", email="feeadmin@ums.ac.ke", password="password123", role=Role.ADMIN
        )

        cls.student_user = User.objects.create_user(
            username="fee.student", email="feestudent@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        cls.student = StudentProfile.objects.create(
            user=cls.student_user, roll_no="BCS/0200/2026", program=cls.program, current_semester=1
        )

        cls.other_user = User.objects.create_user(
            username="other.student", email="other@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        cls.other_student = StudentProfile.objects.create(
            user=cls.other_user, roll_no="BCS/0201/2026", program=cls.program, current_semester=1
        )

        cls.invoice = FeeInvoice.objects.create(
            student=cls.student, title="Semester Tuition Fee", amount=Decimal("10000.00"),
            amount_paid=Decimal("0.00"), due_date=date.today() + timedelta(days=30),
        )


class StudentPayFeesViewTests(FeePaymentTestBase):
    def setUp(self):
        self.client.login(username="fee.student", password="password123")

    def test_student_fees_page_loads(self):
        resp = self.client.get(reverse("university:student_fees"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Semester Tuition Fee")

    def test_partial_payment_updates_invoice_and_creates_payment(self):
        resp = self.client.post(reverse("university:student_fees"), {
            "invoice_id": self.invoice.id,
            "amount": "4000",
            "method": "M-Pesa Paybill",
            "reference": "QK89XY3410",
        })
        self.assertRedirects(resp, reverse("university:student_fees"))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("4000.00"))
        self.assertEqual(self.invoice.balance, Decimal("6000.00"))
        self.assertEqual(self.invoice.status, FeeInvoice.PARTIAL)

        pmt = Payment.objects.get(invoice=self.invoice)
        self.assertEqual(pmt.amount, Decimal("4000.00"))
        self.assertEqual(pmt.method, "M-Pesa Paybill")
        self.assertEqual(pmt.reference, "QK89XY3410")

    def test_full_payment_marks_invoice_paid(self):
        self.client.post(reverse("university:student_fees"), {
            "invoice_id": self.invoice.id,
            "amount": "10000",
            "method": "Bank Deposit",
            "reference": "BNK-000111",
        })
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.PAID)
        self.assertEqual(self.invoice.balance, Decimal("0.00"))

    def test_overpayment_is_capped_to_balance(self):
        self.client.post(reverse("university:student_fees"), {
            "invoice_id": self.invoice.id,
            "amount": "999999",
            "method": "Card / Online",
            "reference": "CARD-000222",
        })
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("10000.00"))
        self.assertEqual(self.invoice.balance, Decimal("0.00"))

    def test_invalid_amount_records_no_payment(self):
        resp = self.client.post(reverse("university:student_fees"), {
            "invoice_id": self.invoice.id,
            "amount": "0",
            "method": "M-Pesa Paybill",
            "reference": "BAD-REF",
        })
        self.assertRedirects(resp, reverse("university:student_fees"))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertFalse(Payment.objects.filter(invoice=self.invoice).exists())

    def test_student_cannot_pay_another_students_invoice(self):
        other_invoice = FeeInvoice.objects.create(
            student=self.other_student, title="Semester Tuition Fee", amount=Decimal("5000.00"),
            due_date=date.today() + timedelta(days=30),
        )
        resp = self.client.post(reverse("university:student_fees"), {
            "invoice_id": other_invoice.id,
            "amount": "5000",
            "method": "M-Pesa Paybill",
            "reference": "SNEAKY-REF",
        })
        self.assertEqual(resp.status_code, 404)
        other_invoice.refresh_from_db()
        self.assertEqual(other_invoice.amount_paid, Decimal("0.00"))

    def test_student_can_download_own_receipt(self):
        self.client.post(reverse("university:student_fees"), {
            "invoice_id": self.invoice.id,
            "amount": "4000",
            "method": "M-Pesa Paybill",
            "reference": "QK89XY3410",
        })
        pmt = Payment.objects.get(invoice=self.invoice)
        resp = self.client.get(reverse("university:fee_receipt_pdf", args=[pmt.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_student_cannot_download_other_students_receipt(self):
        other_invoice = FeeInvoice.objects.create(
            student=self.other_student, title="Semester Tuition Fee", amount=Decimal("5000.00"),
            amount_paid=Decimal("5000.00"), due_date=date.today() + timedelta(days=30),
        )
        other_pmt = Payment.objects.create(
            invoice=other_invoice, amount=Decimal("5000.00"), method="M-Pesa", reference="OTH-REF"
        )
        resp = self.client.get(reverse("university:fee_receipt_pdf", args=[other_pmt.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_statement_of_account_page_loads(self):
        resp = self.client.get(reverse("university:student_fee_statement"))
        self.assertEqual(resp.status_code, 200)

    def test_statement_of_account_pdf_downloads(self):
        resp = self.client.get(reverse("university:student_fee_statement_pdf"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class AdminRecordPaymentViewTests(FeePaymentTestBase):
    def setUp(self):
        self.client.login(username="fee.admin", password="password123")

    def test_admin_fees_page_loads(self):
        resp = self.client.get(reverse("university:admin_fees"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Semester Tuition Fee")

    def test_admin_records_front_desk_payment(self):
        resp = self.client.post(reverse("university:record_payment", args=[self.invoice.pk]), {
            "amount": "6000",
        })
        self.assertRedirects(resp, reverse("university:admin_fees"))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("6000.00"))
        self.assertEqual(self.invoice.status, FeeInvoice.PARTIAL)

        pmt = Payment.objects.get(invoice=self.invoice)
        self.assertEqual(pmt.amount, Decimal("6000.00"))
        self.assertEqual(pmt.method, "Front-desk")

    def test_admin_overpayment_is_capped_to_balance(self):
        self.client.post(reverse("university:record_payment", args=[self.invoice.pk]), {
            "amount": "50000",
        })
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("10000.00"))
        self.assertEqual(self.invoice.status, FeeInvoice.PAID)

    def test_admin_can_download_any_receipt(self):
        self.client.post(reverse("university:record_payment", args=[self.invoice.pk]), {"amount": "10000"})
        pmt = Payment.objects.get(invoice=self.invoice)
        resp = self.client.get(reverse("university:fee_receipt_pdf", args=[pmt.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_student_cannot_access_admin_record_payment(self):
        self.client.logout()
        self.client.login(username="fee.student", password="password123")
        resp = self.client.post(reverse("university:record_payment", args=[self.invoice.pk]), {"amount": "1000"})
        self.assertEqual(resp.status_code, 403)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))


class DirectC2BPaybillTests(FeePaymentTestBase):
    def setUp(self):
        from university.models import FeeAccount
        self.fee_account = FeeAccount.objects.create(
            name="University Main Paybill",
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            account_identifier="522123",
            environment=FeeAccount.Environment.SANDBOX,
            status=FeeAccount.Status.ACTIVE,
            is_default=True,
            configuration={"account_ref_format": "STUDENT_REG_NO"},
        )

    def test_mpesa_validation_endpoint(self):
        resp = self.client.post(
            reverse("university:mpesa_validation"),
            data='{"BusinessShortCode": "522123", "BillRefNumber": "BCS/0200/2026", "TransAmount": "5000"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ResultCode"), 0)

    def test_direct_c2b_paybill_matched_student(self):
        import json
        c2b_payload = {
            "TransactionType": "Pay Bill",
            "TransID": "RKT9911743",
            "TransTime": "20260909193000",
            "TransAmount": "4000.00",
            "BusinessShortCode": "522123",
            "BillRefNumber": "BCS/0200/2026",
            "MSISDN": "254712345678",
            "FirstName": "Jane",
            "LastName": "Doe",
        }
        resp = self.client.post(
            reverse("university:mpesa_callback"),
            data=json.dumps(c2b_payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ResultCode"), 0)

        # Verify payment was created and confirmed
        pmt = Payment.objects.filter(provider_reference="RKT9911743").first()
        self.assertIsNotNone(pmt)
        self.assertEqual(pmt.student, self.student)
        self.assertEqual(pmt.amount, Decimal("4000.00"))
        self.assertEqual(pmt.status, Payment.Status.SUCCESSFUL)

        # Verify invoice allocation
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("4000.00"))

        # Verify official fee receipt generated
        from university.models import FeeReceipt
        receipt = FeeReceipt.objects.filter(payment=pmt).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.amount_paid, Decimal("4000.00"))

    def test_direct_c2b_paybill_unmatched_logged_to_reconciliation(self):
        import json
        from university.models import PaymentReconciliation
        c2b_payload = {
            "TransactionType": "Pay Bill",
            "TransID": "RKTUNKNOWN01",
            "TransTime": "20260909193000",
            "TransAmount": "7500.00",
            "BusinessShortCode": "522123",
            "BillRefNumber": "NONEXISTENT/9999",
            "MSISDN": "254799999999",
            "FirstName": "Stranger",
            "LastName": "Person",
        }
        resp = self.client.post(
            reverse("university:mpesa_callback"),
            data=json.dumps(c2b_payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # Verify recorded in PaymentReconciliation
        recon = PaymentReconciliation.objects.filter(provider_reference="RKTUNKNOWN01").first()
        self.assertIsNotNone(recon)
        self.assertEqual(recon.amount, Decimal("7500.00"))
        self.assertEqual(recon.status, PaymentReconciliation.Status.UNMATCHED)

