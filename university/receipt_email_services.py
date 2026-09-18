"""
Automatic Branded Payment Receipt Email Service for UMS.

Handles:
- Primary recipient (student current User email) & CC recipient (application personal email) resolution
- Strict deduplication and validation
- Dynamic subject and body templating with SystemSetting configurability
- PDF receipt generation and email attachment
- Reliable, non-blocking delivery isolation (payments never fail due to SMTP outages)
- Immutable delivery audit logs (FeeReceiptDeliveryLog)
- Automatic retry mechanics and authorized staff resends
"""
import logging
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape

from university.audit_services import log_activity
from university.document_design import get_branding
from university.financial_services import generate_fee_receipt_pdf
from university.models import (
    AuditLog,
    FeeReceipt,
    FeeReceiptDeliveryLog,
    Payment,
    SystemSetting,
)

logger = logging.getLogger(__name__)

# Secret scrubbing pattern for error messages
SECRET_PATTERN = re.compile(r'(password|secret|key|token|auth|credential)[^\s,;]*[:=]\s*[^\s,;]+', re.IGNORECASE)


def sanitize_error_message(msg: str) -> str:
    """Strip any passwords, tokens, or sensitive strings from exception messages."""
    if not msg:
        return ""
    cleaned = SECRET_PATTERN.sub(r'\1: [REDACTED]', str(msg))
    return cleaned[:1000]


def is_valid_email_address(email_str: Optional[str]) -> bool:
    """Strictly validates an email address format."""
    if not email_str:
        return False
    try:
        validate_email(email_str.strip())
        return True
    except ValidationError:
        return False


def resolve_receipt_email_recipients(student) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Retrieves and validates receipt email recipients:
    1. Primary recipient: Student's current central User Management email (student.user.email)
    2. CC recipient: Student's original personal email provided during admission/application
    
    Ensures:
    - No duplicate email addresses in To and CC.
    - Format validation on both addresses.
    - Warning messages if any address is missing or invalid.
    
    Returns:
        (primary_email, cc_email, warnings)
    """
    warnings: List[str] = []
    primary_email: Optional[str] = None
    cc_email: Optional[str] = None

    if not student:
        warnings.append("Payment has no linked student profile.")
        return None, None, warnings

    # 1. Primary email from central User Management
    user = getattr(student, "user", None)
    if user and getattr(user, "email", None):
        candidate_primary = user.email.strip().lower()
        if is_valid_email_address(candidate_primary):
            primary_email = candidate_primary
        else:
            warnings.append(f"Student primary email '{user.email}' failed validation.")
    else:
        warnings.append(f"Student {getattr(student, 'roll_no', 'N/A')} has no user email configured.")

    # 2. CC recipient from linked Admission Application
    from university.models import Application
    from django.core.exceptions import ObjectDoesNotExist

    app = None
    try:
        app = getattr(student, "admission_application", None)
        if app and not Application.objects.filter(pk=app.pk).exists():
            app = None
    except (ObjectDoesNotExist, Exception):
        app = None

    if not app:
        # Query Application by student profile, admission roll number, or applicant user
        roll_no = getattr(student, "roll_no", "")
        app = Application.objects.filter(
            Q(student=student) |
            (Q(admitted_reg_no=roll_no) if roll_no else Q(pk__in=[])) |
            (Q(applicant_user=user) if user else Q(pk__in=[]))
        ).order_by("-created_at").first()

    if app and getattr(app, "email", None):
        candidate_cc = app.email.strip().lower()
        if is_valid_email_address(candidate_cc):
            # Avoid duplicate in CC if personal email is identical to primary email
            if candidate_cc != primary_email:
                cc_email = candidate_cc
            else:
                logger.debug("Application personal email is identical to primary email; omitting CC duplicate.")
        else:
            warnings.append(f"Invalid email format: application personal email '{app.email}' failed validation.")
    else:
        logger.debug("No personal application email found for student %s", getattr(student, "roll_no", ""))

    return primary_email, cc_email, warnings


def get_institution_name() -> str:
    """Resolves authoritative institution name from SystemSettings, CMS, or client branding."""
    branding = get_branding()
    name = branding.get("site_name")
    if not name or name == "University Management System":
        inst = SystemSetting.objects.filter(key="institution_name").first()
        if inst and inst.value and inst.value.strip():
            return inst.value.strip()
        return "Wigot School of Hospitality"
    return name


def render_receipt_email_content(payment: Payment, receipt: FeeReceipt) -> Tuple[str, str, str]:
    """
    Renders email subject, plain text body, and responsive HTML body.
    Supports customization through SystemSetting keys:
    - 'payment_receipt_email_subject'
    - 'payment_receipt_email_template'
    
    Returns:
        (subject, text_body, html_body)
    """
    student = receipt.student or payment.student
    student_user = getattr(student, "user", None) if student else None
    student_name = getattr(student_user, "display_name", None) or getattr(payment, "payer_name", "Student")

    institution_name = get_institution_name()
    currency = payment.currency or "KES"
    amount_str = f"{payment.amount:,.2f}"
    payment_reference = payment.provider_reference or payment.reference or payment.internal_reference
    receipt_number = receipt.receipt_number
    payment_date = payment.paid_on.strftime("%d %B %Y") if payment.paid_on else timezone.now().strftime("%d %B %Y")

    context: Dict[str, str] = {
        "student_name": student_name,
        "currency": currency,
        "amount": amount_str,
        "payment_reference": payment_reference,
        "receipt_number": receipt_number,
        "payment_date": payment_date,
        "institution_name": institution_name,
        "previous_balance": f"{receipt.previous_balance:,.2f}",
        "remaining_balance": f"{receipt.remaining_balance:,.2f}",
        "payment_method": payment.method or "Online Payment",
    }

    # 1. Subject
    subject_setting = SystemSetting.objects.filter(key="payment_receipt_email_subject").first()
    if subject_setting and subject_setting.value and subject_setting.value.strip():
        subject_template = subject_setting.value.strip()
    else:
        subject_template = "Official Payment Receipt – {{institution_name}} – {{receipt_number}}"

    subject = subject_template
    for k, v in context.items():
        subject = subject.replace(f"{{{{{k}}}}}", v)

    # 2. Text Body
    body_setting = SystemSetting.objects.filter(key="payment_receipt_email_template").first()
    if body_setting and body_setting.value and body_setting.value.strip():
        body_template = body_setting.value.strip()
    else:
        body_template = (
            "Dear {{student_name}},\n\n"
            "We confirm that your payment of {{currency}} {{amount}} has been successfully received "
            "and recorded in your student fee account.\n\n"
            "Please find your official payment receipt attached to this email.\n\n"
            "Payment Reference: {{payment_reference}}\n"
            "Receipt Number: {{receipt_number}}\n"
            "Date: {{payment_date}}\n\n"
            "Thank you.\n"
            "{{institution_name}}\n\n"
            "Powered by STACKGee Technologies\n"
            "Intelligent Systems. Built Secure."
        )

    text_body = body_template
    for k, v in context.items():
        text_body = text_body.replace(f"{{{{{k}}}}}", v)

    # 3. Rich Branded HTML Body
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(subject)}</title>
  <style>
    body {{
      font-family: 'Quicksand', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      margin: 0; padding: 0; background-color: #f8fafc; color: #1e293b; line-height: 1.6;
    }}
    .wrapper {{ width: 100%; max-width: 600px; margin: 0 auto; padding: 24px 16px; }}
    .card {{
      background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0;
      box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06); overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #047857 100%);
      color: #ffffff; padding: 28px 24px; text-align: center;
    }}
    .header h1 {{ margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }}
    .header p {{ margin: 6px 0 0; font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; }}
    .badge {{
      display: inline-block; margin-top: 12px; padding: 4px 14px;
      background: rgba(217, 119, 6, 0.25); border: 1px solid rgba(217, 119, 6, 0.5);
      border-radius: 999px; color: #fef08a; font-size: 12px; font-weight: 700;
    }}
    .content {{ padding: 28px 24px; }}
    .greeting {{ font-size: 16px; font-weight: 700; color: #0f172a; margin-bottom: 12px; }}
    .receipt-box {{
      background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
      padding: 16px 20px; margin: 20px 0;
    }}
    .receipt-row {{
      display: flex; justify-content: space-between; padding: 6px 0;
      border-bottom: 1px dashed #e2e8f0; font-size: 13px;
    }}
    .receipt-row:last-child {{ border-bottom: none; }}
    .receipt-label {{ color: #64748b; font-weight: 500; }}
    .receipt-val {{ font-weight: 700; color: #0f172a; text-align: right; }}
    .amount-highlight {{
      font-size: 18px; color: #047857; font-weight: 800;
    }}
    .notice {{
      background: #ecfdf5; border-left: 4px solid #059669; padding: 12px 16px;
      border-radius: 0 8px 8px 0; margin: 20px 0; font-size: 13px; color: #065f46;
    }}
    .footer {{
      background: #f1f5f9; padding: 20px 24px; text-align: center;
      font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0;
    }}
    .footer-brand {{
      font-weight: 700; color: #0f172a; margin-bottom: 4px;
    }}
    .stackgee {{
      margin-top: 12px; font-size: 11px; color: #94a3b8;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="card">
      <div class="header">
        <h1>{escape(institution_name)}</h1>
        <p>Finance &amp; Student Accounts Department</p>
        <span class="badge">OFFICIAL PAYMENT RECEIPT</span>
      </div>
      <div class="content">
        <div class="greeting">Dear {escape(student_name)},</div>
        <p>
          We confirm that your payment of <strong>{escape(currency)} {escape(amount_str)}</strong>
          has been successfully received and recorded in your student fee account.
        </p>
        <div class="receipt-box">
          <table style="width: 100%; border-collapse: collapse;">
            <tr style="border-bottom: 1px dashed #e2e8f0;">
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Receipt Number:</td>
              <td style="padding: 6px 0; font-weight: 700; color: #0f172a; text-align: right; font-size: 13px;">{escape(receipt_number)}</td>
            </tr>
            <tr style="border-bottom: 1px dashed #e2e8f0;">
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Transaction Reference:</td>
              <td style="padding: 6px 0; font-weight: 700; color: #0f172a; text-align: right; font-size: 13px;">{escape(payment_reference)}</td>
            </tr>
            <tr style="border-bottom: 1px dashed #e2e8f0;">
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Payment Date:</td>
              <td style="padding: 6px 0; font-weight: 700; color: #0f172a; text-align: right; font-size: 13px;">{escape(payment_date)}</td>
            </tr>
            <tr style="border-bottom: 1px dashed #e2e8f0;">
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Payment Channel:</td>
              <td style="padding: 6px 0; font-weight: 700; color: #0f172a; text-align: right; font-size: 13px;">{escape(context['payment_method'])}</td>
            </tr>
            <tr style="border-bottom: 1px dashed #e2e8f0;">
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Amount Paid:</td>
              <td style="padding: 6px 0; font-weight: 800; color: #047857; text-align: right; font-size: 15px;">{escape(currency)} {escape(amount_str)}</td>
            </tr>
            <tr>
              <td style="padding: 6px 0; color: #64748b; font-size: 13px;">Remaining Balance:</td>
              <td style="padding: 6px 0; font-weight: 700; color: #0f172a; text-align: right; font-size: 13px;">{escape(currency)} {escape(context['remaining_balance'])}</td>
            </tr>
          </table>
        </div>
        <div class="notice">
          <strong>Attachment Notice:</strong> Your formal, digitally signed payment receipt is attached as a PDF (<strong>Receipt_{escape(receipt_number)}.pdf</strong>). Please keep this for your records and examination clearance.
        </div>
        <p style="margin-bottom: 0;">Thank you.<br><strong>{escape(institution_name)}</strong></p>
      </div>
      <div class="footer">
        <div class="footer-brand">{escape(institution_name)}</div>
        <div>Mamboleo, Kisumu, Off Kisumu–Kakamega Road &middot; +254 706 063 799 &middot; info@wigotschoolofhospitality.com</div>
        <div class="stackgee">
          Powered by <strong>STACKGee Technologies</strong> &middot; Intelligent Systems. Built Secure.
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""

    return subject, text_body, html_body


def dispatch_fee_receipt_email(
    receipt: FeeReceipt,
    trigger: str = FeeReceiptDeliveryLog.Trigger.AUTO,
    user=None,
    async_delivery: bool = False,
) -> Optional[FeeReceiptDeliveryLog]:
    """
    Executes the generation of the PDF payment receipt and dispatches it via email.
    
    Guards:
    1. NEVER sends an official receipt for pending, failed, cancelled, or unverified payments.
    2. Primary recipient is student user email; CC is personal application email.
    3. Failure in email delivery is isolated: logged to FeeReceiptDeliveryLog and does
       NOT roll back or disrupt the payment confirmation.
    4. Updates FeeReceipt.email_status and retry counters.
    """
    payment = receipt.payment
    if not payment:
        logger.error("Cannot send receipt email: FeeReceipt %s has no linked Payment.", receipt.pk)
        return None

    # Strict status enforcement: only confirmed, successful payments may receive an official receipt
    if payment.status != Payment.Status.SUCCESSFUL:
        logger.warning(
            "Refusing to generate or send receipt for unverified/non-successful payment %s (status=%s)",
            payment.internal_reference, payment.status,
        )
        return None

    student = receipt.student or payment.student
    primary_email, cc_email, warnings = resolve_receipt_email_recipients(student)

    if warnings:
        for w in warnings:
            logger.info("Receipt recipient notice for %s: %s", receipt.receipt_number, w)

    if not primary_email and not cc_email:
        err_msg = "No valid recipient email address found for student."
        logger.error("Failed to send receipt %s: %s", receipt.receipt_number, err_msg)
        attempt_no = receipt.retry_count + 1
        with transaction.atomic():
            receipt.email_status = FeeReceipt.EmailStatus.FAILED
            # retry_count must advance here too -- otherwise
            # retry_failed_receipt_emails() (retry_count__lt=max_retries)
            # would keep re-selecting this receipt on every tick forever,
            # since a missing email address never resolves itself and this
            # branch never left retry_count for the engine to eventually
            # give up on.
            receipt.retry_count = attempt_no
            receipt.last_error = err_msg
            receipt.save(update_fields=["email_status", "retry_count", "last_error"])

            log_entry = FeeReceiptDeliveryLog.objects.create(
                receipt=receipt,
                to_email="missing@student.email",
                cc_email="",
                subject="Receipt Delivery Aborted",
                status=FeeReceiptDeliveryLog.Status.FAILED,
                trigger=trigger,
                attempt_number=attempt_no,
                error_reason=err_msg,
                initiated_by=user,
            )
        return log_entry

    # Determine recipient routing
    to_address = primary_email or cc_email
    cc_addresses = [cc_email] if (cc_email and to_address != cc_email) else []

    subject, text_body, html_body = render_receipt_email_content(payment, receipt)
    attempt_no = receipt.retry_count + 1

    # Route through the system's one configured email funnel (get_email_config
    # / get_email_connection / sender_identity in email_services.py) instead
    # of Django's raw default backend -- otherwise receipt emails would
    # silently ignore whatever SMTP/Microsoft365/Google Workspace provider
    # an admin actually configured via SystemSetting, and use an
    # unconfigured default backend instead. Attachments and CC aren't
    # supported by send_system_email()'s simpler signature, so the
    # connection is built directly here rather than duplicating it, but the
    # provider selection and enabled/disabled switch are still respected.
    from university.email_services import get_email_config, get_email_connection, sender_identity, Provider

    email_cfg = get_email_config()
    if not email_cfg["enabled"] or email_cfg["provider"] == Provider.DISABLED:
        err_msg = "Outbound email is disabled in User Management settings."
        logger.warning("Skipping receipt email for %s: %s", receipt.receipt_number, err_msg)
        with transaction.atomic():
            receipt.email_status = FeeReceipt.EmailStatus.FAILED
            receipt.retry_count = attempt_no
            receipt.recipient_email = to_address
            receipt.cc_email = ", ".join(cc_addresses)
            receipt.last_error = err_msg
            receipt.save(update_fields=["email_status", "retry_count", "recipient_email", "cc_email", "last_error"])
            log_entry = FeeReceiptDeliveryLog.objects.create(
                receipt=receipt, to_email=to_address, cc_email=", ".join(cc_addresses),
                subject=subject, status=FeeReceiptDeliveryLog.Status.FAILED, trigger=trigger,
                attempt_number=attempt_no, sent_at=None, error_reason=err_msg, initiated_by=user,
            )
        return log_entry

    from_email = sender_identity(email_cfg)

    try:
        # Generate the authoritative branded PDF receipt
        pdf_bytes = generate_fee_receipt_pdf(payment)
        if hasattr(pdf_bytes, "getvalue"):
            pdf_bytes = pdf_bytes.getvalue()

        # Build multipart email message on the system's configured connection
        connection = get_email_connection(email_cfg)
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[to_address],
            cc=cc_addresses,
            connection=connection,
        )
        email.attach_alternative(html_body, "text/html")

        # Attach PDF
        filename = f"Receipt_{receipt.receipt_number}.pdf".replace("/", "_")
        email.attach(filename, pdf_bytes, "application/pdf")

        # Dispatch via the configured provider connection built above
        email.send(fail_silently=False)

        # Update receipt record atomically
        with transaction.atomic():
            receipt.email_status = FeeReceipt.EmailStatus.SENT
            receipt.retry_count = attempt_no
            receipt.recipient_email = to_address
            receipt.cc_email = ", ".join(cc_addresses)
            receipt.email_sent_at = timezone.now()
            receipt.last_error = ""
            receipt.save(update_fields=["email_status", "retry_count", "recipient_email", "cc_email", "email_sent_at", "last_error"])

            log_entry = FeeReceiptDeliveryLog.objects.create(
                receipt=receipt,
                to_email=to_address,
                cc_email=", ".join(cc_addresses),
                subject=subject,
                status=FeeReceiptDeliveryLog.Status.SENT,
                trigger=trigger,
                attempt_number=attempt_no,
                sent_at=timezone.now(),
                error_reason="",
                initiated_by=user,
            )

        log_activity(
            request=None,
            user=user or getattr(student, "user", None),
            action=AuditLog.Action.CREATE,
            module=AuditLog.Module.FEES,
            entity="FeeReceiptEmail",
            entity_id=receipt.receipt_number,
            description=f"Emailed official payment receipt {receipt.receipt_number} to {to_address} (CC: {', '.join(cc_addresses) or 'None'}).",
        )
        logger.info(
            "Successfully sent official payment receipt %s to %s (CC: %s)",
            receipt.receipt_number, to_address, cc_addresses,
        )
        return log_entry

    except Exception as exc:
        sanitized_err = sanitize_error_message(str(exc))
        logger.exception("Failed delivering receipt email for %s: %s", receipt.receipt_number, sanitized_err)

        next_status = (
            FeeReceipt.EmailStatus.RETRYING
            if attempt_no < 3
            else FeeReceipt.EmailStatus.FAILED
        )

        with transaction.atomic():
            receipt.email_status = next_status
            receipt.retry_count = attempt_no
            receipt.recipient_email = to_address
            receipt.cc_email = ", ".join(cc_addresses)
            receipt.last_error = sanitized_err
            receipt.save(update_fields=["email_status", "retry_count", "recipient_email", "cc_email", "last_error"])

            log_entry = FeeReceiptDeliveryLog.objects.create(
                receipt=receipt,
                to_email=to_address,
                cc_email=", ".join(cc_addresses),
                subject=subject,
                status=FeeReceiptDeliveryLog.Status.FAILED,
                trigger=trigger,
                attempt_number=attempt_no,
                sent_at=None,
                error_reason=sanitized_err,
                initiated_by=user,
            )

        return log_entry


def resend_fee_receipt_email(receipt: FeeReceipt, user=None) -> FeeReceiptDeliveryLog:
    """Manual resend initiated by an authorized staff or finance user."""
    return dispatch_fee_receipt_email(
        receipt=receipt,
        trigger=FeeReceiptDeliveryLog.Trigger.RESEND,
        user=user,
        async_delivery=False,
    )


def retry_failed_receipt_emails(max_retries: int = 3) -> Dict[str, int]:
    """
    Automated retry engine for receipts in RETRYING or FAILED status under the retry limit.
    Can be invoked via management commands or scheduled background ticks.
    """
    failed_receipts = FeeReceipt.objects.filter(
        email_status__in=[FeeReceipt.EmailStatus.RETRYING, FeeReceipt.EmailStatus.FAILED],
        retry_count__lt=max_retries,
        payment__status=Payment.Status.SUCCESSFUL,
    ).select_related("payment", "student", "student__user")

    total = failed_receipts.count()
    succeeded = 0
    failed = 0

    for rcpt in failed_receipts:
        result = dispatch_fee_receipt_email(
            receipt=rcpt,
            trigger=FeeReceiptDeliveryLog.Trigger.RETRY,
            async_delivery=False,
        )
        if result and result.status == FeeReceiptDeliveryLog.Status.SENT:
            succeeded += 1
        else:
            failed += 1

    return {"total": total, "succeeded": succeeded, "failed": failed}
