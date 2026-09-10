import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile, User
from cms.models import SiteSettings
from university.models import (
    AcademicTerm,
    AcademicYear,
    AuditLog,
    FeeAccount,
    FeeInvoice,
    FeeReceipt,
    Payment,
    PaymentAllocation,
    Program,
    SystemSetting,
)
from university.settings_services import set_setting, get_setting
from university.document_design import get_branding


class AuditConsolidationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="audit_admin",
            email="admin@niu.edu",
            password="adminpassword123",
            role=Role.ADMIN,
        )
        self.student_user = User.objects.create_user(
            username="audit_student",
            email="student@niu.edu",
            password="studentpassword123",
            role=Role.STUDENT,
        )
        from university.models import Department
        self.dept = Department.objects.create(name="Computing", code="COMP")
        self.program = Program.objects.create(name="Computer Science", code="CS", department=self.dept)
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="CS/2026/001",
            program=self.program,
        )
        self.academic_year = AcademicYear.objects.create(
            name="2026/2027",
            code="AY2026",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=365),
            is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.academic_year,
            name="Semester 1",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=120),
        )
        self.fee_account = FeeAccount.objects.create(
            name="Main Tuition Bank Account",
            account_type=FeeAccount.AccountType.BANK_ACCOUNT,
            provider=FeeAccount.Provider.EQUITY,
            account_identifier="01100223344",
            account_name="Equity Bank",
            status=FeeAccount.Status.ACTIVE,
            is_default=True,
        )
        self.invoice = FeeInvoice.objects.create(
            student=self.student,
            term=self.term,
            title="Semester 1 Tuition",
            amount=Decimal("50000.00"),
            amount_paid=Decimal("0.00"),
            due_date=timezone.now().date() + timezone.timedelta(days=30),
        )

    def test_record_payment_creates_unified_ledger_and_receipt(self):
        """Front-desk payment recording must create an allocated payment, FeeReceipt, and AuditLog."""
        self.client.force_login(self.admin_user)
        url = reverse("university:record_payment", args=[self.invoice.pk])
        resp = self.client.post(url, {"amount": "20000.00"})
        self.assertRedirects(resp, reverse("university:admin_fees"))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("20000.00"))
        self.assertEqual(self.invoice.balance, Decimal("30000.00"))

        pmt = Payment.objects.filter(invoice=self.invoice).first()
        self.assertIsNotNone(pmt)
        self.assertEqual(pmt.amount, Decimal("20000.00"))
        self.assertEqual(pmt.status, Payment.Status.SUCCESSFUL)
        self.assertEqual(pmt.method, "Front-desk")

        alloc = PaymentAllocation.objects.filter(payment=pmt, invoice=self.invoice).first()
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc.amount, Decimal("20000.00"))

        receipt = FeeReceipt.objects.filter(payment=pmt).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.amount_paid, Decimal("20000.00"))
        self.assertEqual(receipt.remaining_balance, Decimal("30000.00"))

        audit = AuditLog.objects.filter(module=AuditLog.Module.FEES, entity="Payment").first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, AuditLog.Action.CREATE)

    def test_student_fees_direct_payment_creates_receipt_and_allocation(self):
        """Direct payment submission from student fees page creates full allocation and receipt."""
        self.client.force_login(self.student_user)
        url = reverse("university:student_fees")
        resp = self.client.post(url, {
            "invoice_id": self.invoice.pk,
            "amount": "15000.00",
            "method": "M-Pesa",
            "reference": "TESTMPESA01",
        })
        self.assertRedirects(resp, reverse("university:student_fees"))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("15000.00"))

        pmt = Payment.objects.filter(reference="TESTMPESA01").first()
        self.assertIsNotNone(pmt)
        self.assertEqual(pmt.amount, Decimal("15000.00"))
        self.assertEqual(pmt.status, Payment.Status.SUCCESSFUL)

        alloc = PaymentAllocation.objects.filter(payment=pmt, invoice=self.invoice).first()
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc.amount, Decimal("15000.00"))

        receipt = FeeReceipt.objects.filter(payment=pmt).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.amount_paid, Decimal("15000.00"))

    def test_examination_terms_setup_redirects_to_calendar(self):
        """Legacy examination term setup must redirect to the authoritative Academic Calendar."""
        self.client.force_login(self.admin_user)
        url = reverse("examinations:terms")
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("university:admin_academic_calendar"))

    def test_system_settings_synchronizes_with_site_settings(self):
        """Updating institution_name in SystemSetting must synchronize with SiteSettings in CMS."""
        cms_site = SiteSettings.load()
        cms_site.site_name = "Original Name"
        cms_site.save()

        set_setting("institution_name", "Synchronized Apex University", user=self.admin_user)

        cms_site.refresh_from_db()
        self.assertEqual(cms_site.site_name, "Synchronized Apex University")

        branding = get_branding()
        self.assertEqual(branding["site_name"], "Synchronized Apex University")
