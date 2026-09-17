"""
Safaricom Daraja API client (OAuth token + Lipa Na M-Pesa Online / STK Push).

Config comes from SystemSetting (mpesa_* keys, settings_services.py) --
same pattern as SMS and email in this package, admin-editable from the
Setups UI, secrets masked. If mpesa_consumer_key/mpesa_consumer_secret
aren't configured, is_configured() returns False and callers should fall
back to the manual-instructions flow (MpesaProviderAdapter.initiate_payment
already does this).
"""
import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from django.core.cache import cache

from university.integrations.base import BaseIntegrationAdapter, IntegrationResult
from university.settings_services import get_setting

logger = logging.getLogger(__name__)

_TOKEN_CACHE_KEY = "mpesa_daraja_access_token"


def get_mpesa_config():
    return {
        "environment": (get_setting("mpesa_environment", "SANDBOX") or "SANDBOX").upper(),
        "consumer_key": get_setting("mpesa_consumer_key", "") or "",
        "consumer_secret": get_setting("mpesa_consumer_secret", "") or "",
        "shortcode": get_setting("mpesa_shortcode", "174379") or "174379",
        "passkey": get_setting("mpesa_passkey", "") or "",
        "callback_base_url": get_setting("mpesa_callback_base_url", "") or "",
    }


def is_configured(config=None):
    cfg = config or get_mpesa_config()
    return bool(cfg["consumer_key"] and cfg["consumer_secret"] and cfg["passkey"])


class DarajaClient(BaseIntegrationAdapter):
    integration_name = "M-Pesa Daraja"

    def __init__(self, config=None):
        self.config = config or get_mpesa_config()

    @property
    def base_url(self):
        return ("https://sandbox.safaricom.co.ke" if self.config["environment"] == "SANDBOX"
                else "https://api.safaricom.co.ke")

    def _request(self, path, data=None, headers=None, method="POST"):
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.error("Daraja %s returned %s: %s", path, exc.code, error_body)
            raise

    def get_access_token(self, force_refresh=False):
        """
        OAuth token via Basic auth on the consumer key/secret. Cached for
        slightly under its stated lifetime (Safaricom issues ~3600s tokens)
        so a burst of STK pushes doesn't re-authenticate every time.
        """
        if not force_refresh:
            cached = cache.get(_TOKEN_CACHE_KEY)
            if cached:
                return cached

        credentials = f"{self.config['consumer_key']}:{self.config['consumer_secret']}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

        def _fetch():
            return self._request(
                "/oauth/v1/generate?grant_type=client_credentials",
                data=None, method="GET",
                headers={"Authorization": f"Basic {encoded}"},
            )

        response = self.call_with_audit(_fetch, action="fetch OAuth access token")
        token = response.get("access_token")
        expires_in = int(response.get("expires_in", 3599))
        if token:
            cache.set(_TOKEN_CACHE_KEY, token, timeout=max(60, expires_in - 60))
        return token

    def _password_and_timestamp(self):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        raw = f"{self.config['shortcode']}{self.config['passkey']}{timestamp}"
        password = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        return password, timestamp

    def stk_push(self, *, phone_number, amount, account_reference, transaction_desc, callback_path):
        """
        Initiate Lipa Na M-Pesa Online (STK Push): sends a payment prompt
        to the customer's phone. Returns an IntegrationResult; on success,
        raw_response contains CheckoutRequestID/MerchantRequestID -- the
        actual payment outcome arrives later via the callback URL, handled
        by fee_payment_views.mpesa_callback (unchanged by this client).
        """
        token = self.get_access_token()
        if not token:
            return IntegrationResult(success=False, status="FAILED",
                                     message="Could not obtain a Daraja access token.")

        password, timestamp = self._password_and_timestamp()
        callback_base = self.config["callback_base_url"].rstrip("/")
        callback_url = f"{callback_base}{callback_path}" if callback_base else callback_path

        payload = {
            "BusinessShortCode": self.config["shortcode"],
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone_number,
            "PartyB": self.config["shortcode"],
            "PhoneNumber": phone_number,
            "CallBackURL": callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": transaction_desc[:13] or "Fee Payment",
        }

        def _push():
            return self._request(
                "/mpesa/stkpush/v1/processrequest", data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )

        try:
            response = self.call_with_audit(_push, action=f"STK push to {phone_number}")
        except Exception as exc:
            return IntegrationResult(success=False, status="FAILED", message=str(exc))

        if response.get("ResponseCode") == "0":
            return IntegrationResult(
                success=True, status="PENDING",
                provider_reference=response.get("CheckoutRequestID", ""),
                message=response.get("CustomerMessage", "STK push sent."),
                raw_response=response,
            )
        return IntegrationResult(
            success=False, status="FAILED",
            message=response.get("errorMessage") or response.get("ResponseDescription", "STK push failed."),
            raw_response=response,
        )

    def test_connection(self):
        """Just confirms OAuth works -- doesn't push a real STK prompt."""
        if not is_configured(self.config):
            return False, "M-Pesa is not configured (consumer key/secret/passkey missing).", {}
        try:
            token = self.get_access_token(force_refresh=True)
        except Exception as exc:
            return False, f"Daraja OAuth failed: {exc}", {}
        if not token:
            return False, "Daraja OAuth returned no access token.", {}
        return True, f"Daraja OAuth succeeded ({self.config['environment']}).", {
            "environment": self.config["environment"], "shortcode": self.config["shortcode"],
        }
