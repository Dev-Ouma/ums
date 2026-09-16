"""
Automated SMS Notification Service for University Management System.
Dispatches critical transactional SMS (Fee Receipts, Registration Alerts, Exam Clearances)
via Africa's Talking, Advanta Africa, or local SMS gateways with automatic Kenyan number formatting
and resilient sandbox/fallback logging.

The actual provider call now goes through SmsProviderAdapter
(university/integrations/sms.py), which sources config from SystemSetting
instead of Django settings/env vars -- see that module's docstring. send_sms()
keeps its exact original signature and return shape so its existing callers
need no changes.
"""

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def format_kenyan_phone_number(raw_phone: str) -> Optional[str]:
    """
    Sanitize and format a phone number to standard E.164 (+254XXXXXXXXX).
    Handles:
      - 0712345678 -> +254712345678
      - 0112345678 -> +254112345678
      - 254712345678 -> +254712345678
      - +254 712 345 678 -> +254712345678
    """
    if not raw_phone:
        return None

    # Strip whitespace, dashes, parens
    cleaned = re.sub(r"[^\d+]", "", str(raw_phone).strip())

    if cleaned.startswith("+254") and len(cleaned) == 13:
        return cleaned
    if cleaned.startswith("254") and len(cleaned) == 12:
        return f"+{cleaned}"
    if cleaned.startswith("07") and len(cleaned) == 10:
        return f"+254{cleaned[1:]}"
    if cleaned.startswith("01") and len(cleaned) == 10:
        return f"+254{cleaned[1:]}"
    if cleaned.startswith("7") and len(cleaned) == 9:
        return f"+254{cleaned}"
    if cleaned.startswith("1") and len(cleaned) == 9:
        return f"+254{cleaned}"

    # Return cleaned if it looks like a valid international number with at least 10 digits
    if len(cleaned) >= 10:
        return cleaned if cleaned.startswith("+") else f"+{cleaned}"

    return None


def send_sms(
    phone_number: str,
    message: str,
    sender_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dispatches an SMS to a single recipient phone number.
    Supports Africa's Talking / HTTP REST gateway or developer sandbox logging.
    """
    from university.integrations.sms import SmsProviderAdapter

    formatted_phone = format_kenyan_phone_number(phone_number)
    if not formatted_phone:
        logger.warning(f"SMS dispatch skipped: Invalid phone number '{phone_number}'")
        return {"success": False, "error": f"Invalid phone number '{phone_number}'"}

    result = SmsProviderAdapter().send(formatted_phone, message, sender_id=sender_id)

    if result.status == "SENT_SANDBOX":
        return {
            "success": True,
            "status": "SENT_SANDBOX",
            "recipient": result.raw_response["recipient"],
            "sender": result.raw_response["sender"],
            "message": result.raw_response["message"],
            "dispatched_at": result.raw_response["dispatched_at"],
        }
    if result.success:
        return {"success": True, "status": result.status, "raw_response": result.raw_response}
    return {"success": False, "error": result.message}


def send_payment_confirmation_sms(payment, receipt, remaining_balance) -> Dict[str, Any]:
    """
    Automatically dispatches an official fee payment receipt SMS to the student/payer.
    """
    student = payment.student
    student_user = getattr(student, "user", None) if student else None

    # Identify recipient phone number
    recipient_phone = payment.payer_phone
    if not recipient_phone and student_user:
        recipient_phone = getattr(student_user, "phone", "")

    if not recipient_phone or recipient_phone == "0000":
        logger.info(f"Payment {payment.internal_reference}: No valid phone number found for SMS dispatch.")
        return {"success": False, "error": "No phone number registered"}

    first_name = student_user.first_name if (student_user and student_user.first_name) else "Student"
    receipt_no = getattr(receipt, "receipt_number", f"REC-{payment.id:06d}")
    ref_code = payment.provider_reference or payment.reference or payment.internal_reference

    # Construct succinct, professional SMS notice
    msg = (
        f"Dear {first_name}, KES {payment.amount:,.2f} received towards university fees. "
        f"Ref: {ref_code}. Receipt: {receipt_no}. Balance: KES {remaining_balance:,.2f}. Thank you."
    )

    return send_sms(recipient_phone, msg)
