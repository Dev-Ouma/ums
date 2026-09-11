import json
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.models import FeeAccount, FeeInvoice, Payment
from university.payment_certification_services import build_payment_certification
from university.payment_services import process_payment_confirmation


class PaymentSecurityTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="payment-admin", password="Admin-payment-123!", role=Role.ADMIN)
        self.user = get_user_model().objects.create_user(
            username="payment-student", password="Student-payment-123!", role=Role.STUDENT,
            phone="0712345678")
        self.student = StudentProfile.objects.create(user=self.user, roll_no="PAY/001")
        self.account = FeeAccount.objects.create(
            name="Card Test", account_type=FeeAccount.AccountType.CARD_GATEWAY,
            provider=FeeAccount.Provider.STRIPE, account_identifier="merchant-1",
            environment=FeeAccount.Environment.SANDBOX, status=FeeAccount.Status.ACTIVE)
        self.invoice = FeeInvoice.objects.create(
            student=self.student, title="Tuition", amount=Decimal("1000.00"),
            due_date=date.today() + timedelta(days=30))

    def test_failed_card_callback_never_settles_payment(self):
        payment = Payment.objects.create(
            student=self.student, fee_account=self.account, amount=Decimal("100.00"),
            currency="KES", status=Payment.Status.PROCESSING)
        response = self.client.post(reverse("university:card_callback"), data=json.dumps({
            "internal_reference": payment.internal_reference,
            "provider_reference": "card-failed-1", "status": "FAILED",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertFalse(payment.allocations.exists())

    def test_callback_amount_tampering_is_rejected(self):
        payment = Payment.objects.create(
            student=self.student, fee_account=self.account, amount=Decimal("100.00"),
            currency="KES", status=Payment.Status.PROCESSING)
        response = self.client.post(reverse("university:card_callback"), data=json.dumps({
            "internal_reference": payment.internal_reference,
            "provider_reference": "card-tampered-1", "status": "SUCCESSFUL",
            "amount": "999.00", "currency": "KES",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        self.assertNotEqual(payment.status, Payment.Status.SUCCESSFUL)

    @override_settings(PAYMENT_WEBHOOK_REQUIRE_SIGNATURE=True, PAYMENT_WEBHOOK_SECRET="test-webhook-secret")
    def test_production_webhooks_require_signature(self):
        response = self.client.post(
            reverse("university:card_callback"), data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_certification_contains_payment_flow_controls(self):
        certification = build_payment_certification()
        labels = {row[0] for row in certification["flow"]}
        self.assertIn("Callback validation", labels)
        self.assertIn("Duplicate callback protection", labels)
        self.assertIn("Independent provider verification", labels)

    def test_callback_audit_storage_excludes_personal_provider_fields(self):
        payment = Payment.objects.create(
            student=self.student, fee_account=self.account, amount=Decimal("100.00"),
            currency="KES", status=Payment.Status.PROCESSING,
        )
        process_payment_confirmation(
            payment=payment,
            provider_reference="provider-audit-1",
            raw_payload={
                "TransID": "provider-audit-1",
                "TransAmount": "100.00",
                "BillRefNumber": "PAY/001",
                "MSISDN": "254712345678",
                "FirstName": "Private",
            },
        )

        payment.refresh_from_db()
        self.assertEqual(payment.raw_callback_payload["TransID"], "provider-audit-1")
        self.assertNotIn("MSISDN", payment.raw_callback_payload)
        self.assertNotIn("FirstName", payment.raw_callback_payload)
