import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from university.integrations.mpesa import DarajaClient, get_mpesa_config, is_configured


def _mock_response(payload, status=200):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


class MpesaConfigTests(TestCase):
    def setUp(self):
        from university.settings_services import seed_default_settings
        seed_default_settings()
        cache.clear()

    def test_is_configured_false_when_credentials_missing(self):
        self.assertFalse(is_configured(get_mpesa_config()))

    def test_is_configured_true_when_all_credentials_present(self):
        from university.settings_services import set_setting
        set_setting("mpesa_consumer_key", "key")
        set_setting("mpesa_consumer_secret", "secret")
        set_setting("mpesa_passkey", "passkey")
        self.assertTrue(is_configured(get_mpesa_config()))

    def test_shortcode_defaults_to_the_published_sandbox_value(self):
        config = get_mpesa_config()
        self.assertEqual(config["shortcode"], "174379")


class DarajaClientTests(TestCase):
    def setUp(self):
        from university.settings_services import seed_default_settings, set_setting
        seed_default_settings()
        cache.clear()
        set_setting("mpesa_consumer_key", "test_key")
        set_setting("mpesa_consumer_secret", "test_secret")
        set_setting("mpesa_passkey", "test_passkey")
        set_setting("mpesa_shortcode", "174379")
        set_setting("mpesa_callback_base_url", "https://example.com")

    def test_get_access_token_returns_and_caches_the_token(self):
        client = DarajaClient()
        with patch("urllib.request.urlopen", return_value=_mock_response(
            {"access_token": "abc123", "expires_in": "3599"})) as mock_open:
            token = client.get_access_token()
        self.assertEqual(token, "abc123")
        self.assertEqual(cache.get("mpesa_daraja_access_token"), "abc123")

        # Second call should hit the cache, not the network.
        with patch("urllib.request.urlopen") as mock_open_2:
            token_again = client.get_access_token()
        self.assertEqual(token_again, "abc123")
        mock_open_2.assert_not_called()

    def test_stk_push_success_returns_pending_with_checkout_id(self):
        client = DarajaClient()
        cache.set("mpesa_daraja_access_token", "cached_token")
        with patch("urllib.request.urlopen", return_value=_mock_response({
            "ResponseCode": "0", "CheckoutRequestID": "ws_CO_123",
            "CustomerMessage": "Success. Request accepted for processing",
        })):
            result = client.stk_push(
                phone_number="254708374149", amount=100,
                account_reference="TEST-REF", transaction_desc="Fee Payment",
                callback_path="/api/payments/callback/mpesa/",
            )
        self.assertTrue(result.success)
        self.assertEqual(result.status, "PENDING")
        self.assertEqual(result.provider_reference, "ws_CO_123")

    def test_stk_push_non_zero_response_code_is_a_failure(self):
        client = DarajaClient()
        cache.set("mpesa_daraja_access_token", "cached_token")
        with patch("urllib.request.urlopen", return_value=_mock_response({
            "ResponseCode": "1", "ResponseDescription": "Insufficient funds in short code",
        })):
            result = client.stk_push(
                phone_number="254708374149", amount=100,
                account_reference="TEST-REF", transaction_desc="Fee Payment",
                callback_path="/api/payments/callback/mpesa/",
            )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "FAILED")

    def test_stk_push_network_failure_is_reported_not_raised(self):
        client = DarajaClient()
        cache.set("mpesa_daraja_access_token", "cached_token")
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = client.stk_push(
                phone_number="254708374149", amount=100,
                account_reference="TEST-REF", transaction_desc="Fee Payment",
                callback_path="/api/payments/callback/mpesa/",
            )
        self.assertFalse(result.success)

    def test_test_connection_reports_not_configured_when_credentials_missing(self):
        from university.settings_services import set_setting
        set_setting("mpesa_consumer_key", "")
        client = DarajaClient()
        ok, message, _ = client.test_connection()
        self.assertFalse(ok)
        self.assertIn("not configured", message)

    def test_test_connection_succeeds_with_a_real_token(self):
        client = DarajaClient()
        with patch("urllib.request.urlopen", return_value=_mock_response(
            {"access_token": "xyz", "expires_in": "3599"})):
            ok, message, diagnostics = client.test_connection()
        self.assertTrue(ok)
        self.assertIn("OAuth succeeded", message)


class MpesaProviderAdapterFallbackTests(TestCase):
    """
    The manual-instructions payment flow must still work when Daraja isn't
    configured -- this was the entire behaviour before this integration,
    and must not regress now that a live path exists alongside it.
    """
    def setUp(self):
        from university.settings_services import seed_default_settings
        seed_default_settings()
        cache.clear()

    def test_unconfigured_mpesa_falls_back_to_manual_instructions(self):
        from decimal import Decimal
        from accounts.models import Role, StudentProfile, User
        from university.models import Department, FeeAccount, Payment, Program
        from university.payment_providers.mpesa import MpesaProviderAdapter

        dept = Department.objects.create(name="Mpesa Test Dept", code="MPD")
        program = Program.objects.create(name="BSc Mpesa", code="BSC-MP", department=dept)
        student_user = User.objects.create_user(
            username="mpesa.student", email="mpesa.student@example.com",
            password="pass12345", role=Role.STUDENT)
        student = StudentProfile.objects.create(
            user=student_user, roll_no="STU-MP-1", program=program, current_semester=1)
        fee_account = FeeAccount.objects.create(
            name="Test Paybill", account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            account_identifier="174379", environment=FeeAccount.Environment.SANDBOX)
        payment = Payment.objects.create(
            student=student, fee_account=fee_account, amount=Decimal("1000.00"),
            payer_phone="0712345678")

        adapter = MpesaProviderAdapter(fee_account)
        factory_request = MagicMock()
        factory_request.user = student_user
        result = adapter.initiate_payment(payment, factory_request)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "PENDING")
        self.assertIn("steps", result.instructions)
