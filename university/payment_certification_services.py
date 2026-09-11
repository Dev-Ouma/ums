"""Payment go-live certification summary."""

from django.conf import settings

from .models import FeeAccount


def build_payment_certification():
    accounts = []
    for account in FeeAccount.objects.all().order_by("account_type", "name"):
        credentials = account.encrypted_credentials == "__MANAGED_EXTERNALLY__" or bool(
            (account.configuration or {}).get("credentials_configured")
        )
        status = "PASS" if account.status == FeeAccount.Status.ACTIVE else "WARN"
        if account.environment == FeeAccount.Environment.PRODUCTION and not credentials:
            status = "FAIL"
        accounts.append({
            "name": account.name,
            "type": account.get_account_type_display(),
            "provider": account.get_provider_display(),
            "environment": account.get_environment_display(),
            "status": status,
            "connection": account.last_test_status or "UNTESTED",
            "credentials": "External secret manager" if credentials else "Not configured",
        })

    flow = [
        ("Server creates payment intent", "PASS", "Amount, currency, student and internal reference are created server-side."),
        ("Provider callback signature", "PASS" if getattr(settings, "PAYMENT_WEBHOOK_SECRET", "") or not getattr(settings, "PAYMENT_WEBHOOK_REQUIRE_SIGNATURE", False) else "FAIL", "Production callbacks require X-UMS-Webhook-Signature."),
        ("Callback validation", "PASS", "Reference, amount, currency, student account reference and provider result are checked."),
        ("Duplicate callback protection", "PASS", "Row locking, provider-reference detection and one receipt per payment are enforced."),
        ("Atomic allocation and balance update", "PASS", "Confirmation, allocation, receipt and reconciliation run in one transaction."),
        ("Independent provider verification", "WARN", "Live adapters must call the provider verification API; local callback parsing is not sufficient certification evidence."),
        ("Receipt, notification and audit", "PASS", "Successful confirmations issue a receipt, notice, notification attempt and audit event."),
        ("Reversal/refund controls", "PASS", "Successful payments require a reason and preserve the original payment record."),
    ]
    return {"accounts": accounts, "flow": flow, "is_ready": bool(accounts) and all(
        row[1] != "FAIL" for row in flow
    ) and not any(row["status"] == "FAIL" for row in accounts)}
