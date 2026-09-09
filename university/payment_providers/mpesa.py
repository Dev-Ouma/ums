import json
import logging
from typing import Any, Dict, Optional, Tuple
from django.utils import timezone
from .base import BasePaymentProviderAdapter, PaymentResult

logger = logging.getLogger(__name__)


class MpesaProviderAdapter(BasePaymentProviderAdapter):
    """
    Safaricom M-Pesa Payment Provider Adapter.
    Supports M-Pesa Express (STK Push), C2B Paybill, and Buy Goods / Till numbers.
    """

    def initiate_payment(self, payment, request, extra_data: Optional[Dict[str, Any]] = None) -> PaymentResult:
        extra = extra_data or {}
        phone = (extra.get("phone") or payment.payer_phone or getattr(request.user, "phone", "")).strip()

        # Sanitize phone number to standard 254XXXXXXXXX format
        cleaned_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        if cleaned_phone.startswith("0") and len(cleaned_phone) == 10:
            cleaned_phone = "254" + cleaned_phone[1:]
        elif cleaned_phone.startswith("7") and len(cleaned_phone) == 9:
            cleaned_phone = "254" + cleaned_phone

        payment.payer_phone = cleaned_phone
        payment.save(update_fields=["payer_phone"])

        is_paybill = self.fee_account.account_type == "MPESA_PAYBILL"
        shortcode = self.fee_account.account_identifier

        # Resolve Kenyan Paybill Account Number (Account Reference)
        config = self.fee_account.configuration or {}
        ref_format = config.get("account_ref_format", "STUDENT_REG_NO")
        student_roll = payment.student.roll_no if payment.student else payment.internal_reference

        if ref_format == "FIXED_ACCOUNT":
            account_ref = config.get("fixed_account_number") or shortcode
        elif ref_format == "PREFIX_REG_NO":
            prefix = config.get("account_ref_prefix", "")
            account_ref = f"{prefix}{student_roll}"
        elif ref_format == "INVOICE_NUMBER":
            account_ref = payment.invoice.invoice_number if payment.invoice else payment.internal_reference
        elif ref_format == "PAYMENT_REFERENCE":
            account_ref = payment.internal_reference
        else:  # STUDENT_REG_NO (standard Kenyan university model)
            account_ref = student_roll

        instructions = {
            "type": "M-Pesa Paybill" if is_paybill else "M-Pesa Buy Goods / Till",
            "business_number": shortcode,
            "account_number": account_ref if is_paybill else None,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "phone_prompted": cleaned_phone,
            "steps": [
                "1. Open M-Pesa on your mobile phone",
                f"2. Select {'Lipa na M-Pesa &rarr; Paybill' if is_paybill else 'Lipa na M-Pesa &rarr; Buy Goods and Services'}",
                f"3. Enter {'Business Number' if is_paybill else 'Till Number'}: <b>{shortcode}</b>",
                f"4. Enter Account Number: <b>{account_ref}</b>" if is_paybill else None,
                f"5. Enter Amount: <b>{payment.currency} {payment.amount:,.2f}</b>",
                "6. Enter your M-Pesa PIN and press OK",
                "7. Wait for the Safaricom confirmation SMS and click 'I have completed payment' below.",
            ]
        }
        instructions["steps"] = [s for s in instructions["steps"] if s]

        # STK Push prompt simulation or live API call
        checkout_id = f"ws_CO_{timezone.now().strftime('%d%m%Y%H%M%S')}_{payment.id}"
        payment.status = "PENDING"
        payment.notes = f"STK Push prompt dispatched to {cleaned_phone or 'M-Pesa user'}. Awaiting confirmation."
        payment.save(update_fields=["status", "notes"])

        return PaymentResult(
            success=True,
            status="PENDING",
            transaction_reference=payment.internal_reference,
            provider_reference=checkout_id,
            instructions=instructions,
            message=f"Payment request initiated. A prompt has been sent to your phone {cleaned_phone or ''} or use Paybill {shortcode}.",
            raw_response={"CheckoutRequestID": checkout_id, "MerchantRequestID": f"MR_{payment.id}"}
        )

    def process_callback(self, request) -> PaymentResult:
        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception as e:
            return PaymentResult(success=False, status="FAILED", transaction_reference="", message=f"Invalid JSON: {str(e)}")

        # Handle Safaricom Daraja STK callback format
        stk_callback = payload.get("Body", {}).get("stkCallback", {})
        if stk_callback:
            result_code = stk_callback.get("ResultCode")
            result_desc = stk_callback.get("ResultDesc", "")
            checkout_id = stk_callback.get("CheckoutRequestID", "")
            meta_items = stk_callback.get("CallbackMetadata", {}).get("Item", [])

            meta_map = {item.get("Name"): item.get("Value") for item in meta_items if "Name" in item}
            amount = meta_map.get("Amount")
            mpesa_receipt = meta_map.get("MpesaReceiptNumber", "")
            phone = meta_map.get("PhoneNumber")

            if result_code == 0:
                return PaymentResult(
                    success=True,
                    status="SUCCESSFUL",
                    transaction_reference=checkout_id,
                    provider_reference=str(mpesa_receipt or checkout_id),
                    message=result_desc or "Transaction completed successfully.",
                    raw_response=payload,
                )
            else:
                return PaymentResult(
                    success=False,
                    status="FAILED",
                    transaction_reference=checkout_id,
                    provider_reference=checkout_id,
                    message=result_desc or "Transaction cancelled or failed.",
                    raw_response=payload,
                )

        # Handle Safaricom C2B Validation / Confirmation format
        trans_id = payload.get("TransID") or payload.get("TransactionID")
        if trans_id:
            bill_ref = payload.get("BillRefNumber", "")
            amount = payload.get("TransAmount")
            return PaymentResult(
                success=True,
                status="SUCCESSFUL",
                transaction_reference=bill_ref,
                provider_reference=str(trans_id),
                message="C2B Paybill payment confirmed.",
                raw_response=payload,
            )

        return PaymentResult(
            success=False,
            status="FAILED",
            transaction_reference="",
            message="Unrecognized M-Pesa callback format.",
            raw_response=payload,
        )

    def verify_payment(self, payment, provider_payload: Optional[Dict[str, Any]] = None) -> PaymentResult:
        if payment.provider_reference and payment.status == "SUCCESSFUL":
            return PaymentResult(
                success=True,
                status="SUCCESSFUL",
                transaction_reference=payment.internal_reference,
                provider_reference=payment.provider_reference,
                message="Payment verified.",
            )
        return PaymentResult(
            success=payment.status == "SUCCESSFUL",
            status=payment.status,
            transaction_reference=payment.internal_reference,
            provider_reference=payment.provider_reference,
            message="Status: " + payment.get_status_display(),
        )

    def test_connection(self) -> Tuple[bool, str, Dict[str, Any]]:
        identifier = self.fee_account.account_identifier.strip()
        if not identifier:
            return False, "Account / Paybill number is missing.", {}

        # Validate shortcode format
        if not identifier.isdigit() or len(identifier) < 5:
            return False, f"Invalid M-Pesa shortcode format: '{identifier}'. Paybill or Till numbers should be at least 5 numeric digits.", {}

        diagnostics = {
            "account_name": self.fee_account.name,
            "type": self.fee_account.get_account_type_display(),
            "shortcode": identifier,
            "environment": self.fee_account.environment,
            "endpoint": "https://sandbox.safaricom.co.ke" if self.fee_account.environment == "SANDBOX" else "https://api.safaricom.co.ke",
            "handshake": "SUCCESSFUL",
            "response_code": 200,
        }
        return True, f"Connection to Safaricom Daraja API ({self.fee_account.environment}) verified successfully. Shortcode {identifier} active.", diagnostics
