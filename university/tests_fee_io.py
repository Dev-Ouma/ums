"""Regression coverage for fee report generation — export_fee_pdf previously
raised NameError (total_billed/total_paid/total_balance referenced but never
computed) because no test exercised it."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Role, StudentProfile, User
from university import fee_io
from university.models import Department, FeeInvoice, Program


class FeeIOTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="Computing", code="CMP")
        program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        user = User.objects.create_user("stud1", password="x", role=Role.STUDENT)
        student = StudentProfile.objects.create(user=user, roll_no="R1", program=program)
        cls.invoices = [
            FeeInvoice.objects.create(
                student=student, title="Tuition", amount=Decimal("45000.00"),
                amount_paid=Decimal("20000.00"), issued_on=date.today(),
                due_date=date.today() + timedelta(days=30)),
            FeeInvoice.objects.create(
                student=student, title="Library Fee", amount=Decimal("1000.00"),
                amount_paid=Decimal("1000.00"), issued_on=date.today(),
                due_date=date.today() + timedelta(days=30)),
        ]
        cls.queryset = FeeInvoice.objects.filter(student=student)

    def test_export_fee_pdf_does_not_raise(self):
        data = fee_io.export_fee_pdf(self.queryset)
        self.assertGreater(len(data), 500)

    def test_export_fee_pdf_handles_empty_queryset(self):
        data = fee_io.export_fee_pdf(FeeInvoice.objects.none())
        self.assertGreater(len(data), 500)

    def test_export_fee_excel_does_not_raise(self):
        data = fee_io.export_fee_excel(self.queryset)
        self.assertGreater(len(data), 500)

    def test_export_fee_csv_does_not_raise(self):
        data = fee_io.export_fee_csv(self.queryset)
        self.assertIn(b"Tuition", data)
