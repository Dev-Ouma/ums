"""SMS provider adapter -- config sourced from SystemSetting, matching email_services."""
import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings as django_settings
from django.utils import timezone

from university.integrations.base import BaseIntegrationAdapter, IntegrationResult
from university.settings_services import get_setting

logger = logging.getLogger(__name__)


def get_sms_config():
    """
    Resolve the live SMS configuration from SystemSetting.

    Django settings/env vars (AFRICASTALKING_USERNAME etc.) are used only as
    the seeded default for each SystemSetting row (see settings_services.py),
    so an install that already relies on them keeps working unchanged, but
    the live value is now admin-editable the same way email config is.
    """
    return {
        "provider": get_setting("sms_provider", "AFRICASTALKING") or "AFRICASTALKING",
        "username": get_setting("sms_username", "") or getattr(django_settings, "AFRICASTALKING_USERNAME", "") or "",
        "api_key": get_setting("sms_api_key", "") or getattr(django_settings, "AFRICASTALKING_API_KEY", "") or "",
        "sender_id": get_setting("sms_sender_id", "UMS") or "UMS",
        "sandbox": bool(get_setting("sms_backend_debug", True)),
    }


class SmsProviderAdapter(BaseIntegrationAdapter):
    integration_name = "SMS"

    def __init__(self, config=None):
        self.config = config or get_sms_config()

    def send(self, formatted_phone, message, sender_id=None, user=None, request=None):
        """
        Dispatch a single SMS. Returns an IntegrationResult. Falls back to
        sandbox/log mode when credentials are absent or sandbox mode is on
        -- same behaviour send_sms() always had, just sourced from
        SystemSetting instead of Django settings now.
        """
        cfg = self.config
        sender = sender_id or cfg["sender_id"]

        if not cfg["api_key"] or not cfg["username"] or cfg["sandbox"]:
            logger.info(f"[SMS SANDBOX DISPATCH] To: {formatted_phone} | From: {sender} | Msg: {message}")
            return IntegrationResult(
                success=True, status="SENT_SANDBOX",
                raw_response={"recipient": formatted_phone, "sender": sender,
                             "message": message, "dispatched_at": timezone.now().isoformat()},
            )

        def _dispatch():
            url = "https://api.africastalking.com/version1/messaging"
            data = urllib.parse.urlencode({
                "username": cfg["username"], "to": formatted_phone,
                "message": message, "from": sender,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"apikey": cfg["api_key"], "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            raw = self.call_with_audit(
                _dispatch, action=f"send SMS to {formatted_phone}", user=user, request=request)
            return IntegrationResult(success=True, status="DELIVERED", raw_response=raw)
        except Exception as exc:
            return IntegrationResult(success=False, status="FAILED", message=str(exc))
