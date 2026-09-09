from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from university.models import AcademicYear, Application, ApplicationFeePayment, Department, Intake, Program
from university.settings_services import get_setting, seed_default_settings, set_setting


class ApplicationFeeTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="Computing", code="CMP")
        cls.program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        cls.academic_year = AcademicYear.objects.create(
            name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31))
        cls.intake = Intake.objects.create(
            name="Test Intake", academic_year=cls.academic_year,
            start_date=timezone.now().date(), end_date=timezone.now().date() + timedelta(days=90),
            is_active=True)
        cls.admin = User.objects.create_user("admin1", password="x", role=Role.ADMIN)
        cls.application = Application.objects.create(
            application_number="APP-TEST-0001", intake=cls.intake, program=cls.program,
            first_name="Jane", last_name="Doe", email="jane@example.com", phone="0700000000",
            date_of_birth=date(2004, 1, 1), gender="FEMALE", national_id="12345678",
        )


class ApplicationFeeModelTests(ApplicationFeeTestBase):
    def test_fee_default_setting_is_1000(self):
        seed_default_settings()
        self.assertEqual(get_setting("application_fee_default"), Decimal("1000.00"))

    def test_fee_setting_is_configurable(self):
        set_setting("application_fee_default", Decimal("1200.00"))
        self.assertEqual(get_setting("application_fee_default"), Decimal("1200.00"))

    def test_fee_paid_false_by_default(self):
        self.assertFalse(self.application.fee_paid)

    def test_fee_paid_true_after_confirmed_payment(self):
        ApplicationFeePayment.objects.create(
            application=self.application, amount=Decimal("1000.00"), method=ApplicationFeePayment.Method.MPESA,
            reference="TXN123", status=ApplicationFeePayment.Status.CONFIRMED)
        self.assertTrue(self.application.fee_paid)

    def test_fee_paid_false_when_only_pending(self):
        ApplicationFeePayment.objects.create(
            application=self.application, amount=Decimal("1000.00"),
            reference="TXN123", status=ApplicationFeePayment.Status.PENDING)
        self.assertFalse(self.application.fee_paid)


class ApplicationFeeViewTests(ApplicationFeeTestBase):
    def test_apply_redirects_to_fee_payment(self):
        resp = self.client.post(reverse("university:admissions_apply"), {
            "program": self.program.pk, "first_name": "New", "last_name": "Applicant",
            "email": "new@example.com", "phone": "0711111111", "date_of_birth": "2005-05-05",
            "gender": "MALE", "national_id": "99999999", "address": "Nairobi",
        })
        new_app = Application.objects.get(email="new@example.com")
        self.assertRedirects(resp, reverse("university:pay_application_fee", args=[new_app.pk]))

    def test_pay_fee_page_loads(self):
        resp = self.client.get(reverse("university:pay_application_fee", args=[self.application.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1,000.00")

    def test_pay_fee_submission_confirms_payment(self):
        resp = self.client.post(reverse("university:pay_application_fee", args=[self.application.pk]), {
            "method": "MPESA", "reference": "QWE123RTY",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.application.refresh_from_db()
        self.assertTrue(self.application.fee_paid)
        payment = self.application.fee_payments.first()
        self.assertEqual(payment.status, ApplicationFeePayment.Status.CONFIRMED)
        self.assertEqual(payment.reference, "QWE123RTY")

    def test_pay_fee_requires_reference(self):
        resp = self.client.post(reverse("university:pay_application_fee", args=[self.application.pk]), {
            "method": "MPESA", "reference": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.application.refresh_from_db()
        self.assertFalse(self.application.fee_paid)

    def test_admin_cannot_advance_unpaid_application(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("university:admin_admission_detail", args=[self.application.pk]), {
            "action": "under_review",
        }, follow=True)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.SUBMITTED)

    def test_admin_can_advance_paid_application(self):
        ApplicationFeePayment.objects.create(
            application=self.application, amount=Decimal("1000.00"),
            reference="TXN999", status=ApplicationFeePayment.Status.CONFIRMED)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("university:admin_admission_detail", args=[self.application.pk]), {
            "action": "under_review",
        }, follow=True)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.UNDER_REVIEW)
