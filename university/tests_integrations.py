from unittest.mock import patch

from django.test import TestCase

from university.integrations.base import BaseIntegrationAdapter, IntegrationResult
from university.models import AuditLog


class CallWithAuditTests(TestCase):
    def test_success_is_logged_once(self):
        adapter = BaseIntegrationAdapter()
        adapter.integration_name = "Test Integration"
        result = adapter.call_with_audit(lambda: "ok", action="do the thing")
        self.assertEqual(result, "ok")
        self.assertTrue(AuditLog.objects.filter(
            entity="Test Integration", description__icontains="succeeded").exists())

    def test_failure_retries_then_logs_final_failure(self):
        adapter = BaseIntegrationAdapter()
        adapter.integration_name = "Test Integration"
        calls = {"count": 0}

        def always_fails():
            calls["count"] += 1
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            adapter.call_with_audit(always_fails, action="do the thing", retries=2)

        self.assertEqual(calls["count"], 3)  # 1 initial + 2 retries
        self.assertTrue(AuditLog.objects.filter(
            entity="Test Integration", description__icontains="failed after 3 attempt").exists())

    def test_retries_can_succeed_on_a_later_attempt(self):
        adapter = BaseIntegrationAdapter()
        adapter.integration_name = "Test Integration"
        calls = {"count": 0}

        def fails_once_then_succeeds():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
            return "ok"

        result = adapter.call_with_audit(fails_once_then_succeeds, action="do the thing", retries=2)
        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)


class SmsProviderAdapterTests(TestCase):
    def test_sandbox_mode_returns_success_without_network_call(self):
        from university.integrations.sms import SmsProviderAdapter
        from university.settings_services import seed_default_settings, set_setting
        seed_default_settings()
        set_setting("sms_backend_debug", True)

        adapter = SmsProviderAdapter()
        result = adapter.send("+254712345678", "Test message")
        self.assertTrue(result.success)
        self.assertEqual(result.status, "SENT_SANDBOX")
        self.assertEqual(result.raw_response["recipient"], "+254712345678")

    def test_config_is_read_from_system_setting(self):
        from university.integrations.sms import get_sms_config
        from university.settings_services import seed_default_settings, set_setting
        seed_default_settings()
        set_setting("sms_sender_id", "MYUNI")
        set_setting("sms_provider", "AFRICASTALKING")

        config = get_sms_config()
        self.assertEqual(config["sender_id"], "MYUNI")
        self.assertEqual(config["provider"], "AFRICASTALKING")

    def test_send_sms_wrapper_preserves_original_sandbox_return_shape(self):
        from university.sms_services import send_sms
        from university.settings_services import seed_default_settings, set_setting
        seed_default_settings()
        set_setting("sms_backend_debug", True)

        result = send_sms("0712345678", "Hello")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "SENT_SANDBOX")
        self.assertEqual(result["recipient"], "+254712345678")

    def test_live_mode_failure_is_reported_without_raising(self):
        from university.integrations.sms import SmsProviderAdapter
        from university.settings_services import seed_default_settings, set_setting
        seed_default_settings()
        set_setting("sms_backend_debug", False)
        set_setting("sms_username", "liveuser")
        set_setting("sms_api_key", "livekey")

        adapter = SmsProviderAdapter()
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = adapter.send("+254712345678", "Test message")
        self.assertFalse(result.success)
        self.assertIn("network down", result.message)


class IntegrationRegistryTests(TestCase):
    def test_unknown_kind_raises_key_error(self):
        from university.integrations.registry import get_integration
        with self.assertRaises(KeyError):
            get_integration("not_a_real_integration")

    def test_sms_kind_returns_an_adapter(self):
        from university.integrations.registry import get_integration
        from university.integrations.sms import SmsProviderAdapter
        adapter = get_integration("sms")
        self.assertIsInstance(adapter, SmsProviderAdapter)
