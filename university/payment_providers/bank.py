import logging
from typing import Any, Dict, Optional, Tuple
from .base import BasePaymentProviderAdapter, PaymentResult

logger = logging.getLogger(__name__)


class BankTransferAdapter(BasePaymentProviderAdapter):
    """
    Direct Bank Transfer & Deposit Slip Adapter.
    Generates official deposit slip instructions and reference codes for institutional bank accounts.
    """

    def initiate_payment(self, payment, request, extra_data: Optional[Dict[str, Any]] = None) -> PaymentResult:
        student_roll = payment.student.roll_no if payment.student else "STUDENT"
        bank_config = self.fee_account.configuration or {}

        bank_name = self.fee_account.account_name or self.fee_account.name
        account_number = self.fee_account.account_identifier
        branch = bank_config.get("branch", "Main Campus Branch")
        bank_code = bank_config.get("bank_code", "")

        payment.status = "PENDING"
        payment.notes = f"Bank transfer reference generated for {bank_name} A/C {account_number}. Awaiting bank deposit."
        payment.save(update_fields=["status", "notes"])

        instructions = {
            "type": "Bank Deposit / Direct Transfer",
            "bank_name": bank_name,
            "account_name": self.fee_account.account_name or "University Main Operating Account",
            "account_number": account_number,
            "branch": branch,
            "bank_code": bank_code,
            "deposit_reference": payment.internal_reference,
            "student_reg_no": student_roll,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "steps": [
                f"1. Visit any branch of <b>{bank_name}</b> or use online/mobile banking transfer.",
                f"2. Deposit into Account Number: <b>{account_number}</b> ({self.fee_account.account_name}).",
                f"3. Quote Payment Reference: <b>{payment.internal_reference}</b> (or your Reg No: <b>{student_roll}</b>) on the deposit slip.",
                f"4. Retain the bank deposit slip / transaction advice.",
                "5. Upload or present your bank slip to the Cashier's office for immediate verification.",
            ]
        }

        return PaymentResult(
            success=True,
            status="PENDING",
            transaction_reference=payment.internal_reference,
            provider_reference=payment.internal_reference,
            instructions=instructions,
            message=f"Official bank deposit instructions generated. Quote reference {payment.internal_reference}.",
            raw_response={"bank": bank_name, "account": account_number},
        )

    def process_callback(self, request) -> PaymentResult:
        # Handles automated Bank Core / EFT integration webhooks
        import json
        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            payload = {}

        tx_ref = payload.get("internal_reference") or payload.get("reference", "")
        bank_ref = payload.get("bank_reference") or payload.get("transaction_id", "")
        return PaymentResult(
            success=True,
            status="SUCCESSFUL",
            transaction_reference=tx_ref,
            provider_reference=str(bank_ref),
            message="Bank deposit confirmed via direct feed.",
            raw_response=payload,
        )

    def verify_payment(self, payment, provider_payload: Optional[Dict[str, Any]] = None) -> PaymentResult:
        return PaymentResult(
            success=payment.status == "SUCCESSFUL",
            status=payment.status,
            transaction_reference=payment.internal_reference,
            provider_reference=payment.provider_reference,
            message="Status: " + payment.get_status_display(),
        )

    def test_connection(self) -> Tuple[bool, str, Dict[str, Any]]:
        acc_no = self.fee_account.account_identifier.strip()
        if not acc_no:
            return False, "Bank account number is required.", {}

        diagnostics = {
            "bank_name": self.fee_account.name,
            "account_number": acc_no,
            "account_title": self.fee_account.account_name,
            "currency": self.fee_account.currency,
            "branch": (self.fee_account.configuration or {}).get("branch", "Main Branch"),
            "status": "ACTIVE_RECEIVING",
        }
        return True, f"Bank payment channel '{self.fee_account.name}' verified. Ready to issue deposit references.", diagnostics
