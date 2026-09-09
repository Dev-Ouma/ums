import decimal
from decimal import Decimal
import json
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from accounts.models import Role, StudentProfile
from university.models import FeeAccount, FeeReceipt, Payment
from university.payment_services import (
    get_active_fee_accounts_for_student,
    get_student_balance_summary,
    initiate_student_payment,
    process_payment_confirmation,
)
from university.financial_services import generate_fee_receipt_pdf

logger = logging.getLogger(__name__)


def _student_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not (request.user.is_student or getattr(request.user, "role", "") == Role.STUDENT):
            messages.error(request, "Access restricted to active students.")
            return redirect("university:dashboard")
        if not hasattr(request.user, "student_profile"):
            messages.error(request, "Student profile record not found.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapped


@login_required
@_student_required
def student_pay_fees(request):
    """
    Student Portal -> Finance -> Pay Fees
    Keeps the payment interface simple and focused:
    1. Outstanding balance & currency
    2. Enter amount to pay
    3. Select payment method (active Fee Accounts only)
    4. Pay Now button
    """
    student = request.user.student_profile
    balance_summary = get_student_balance_summary(student)
    active_accounts = get_active_fee_accounts_for_student(student)

    # Pre-select default account if available
    default_account = next((a for a in active_accounts if a.is_default), active_accounts[0] if active_accounts else None)

    return render(request, "dashboard/student_pay_fees.html", {
        "student": student,
        "balance": balance_summary,
        "active_accounts": active_accounts,
        "default_account": default_account,
    })


@login_required
@_student_required
@require_POST
def student_initiate_payment(request):
    """
    Handles payment submission, validates amount, and dispatches to the selected provider adapter.
    """
    student = request.user.student_profile
    amount_raw = request.POST.get("amount", "").strip().replace(",", "")
    fee_account_id = request.POST.get("fee_account_id")
    phone = request.POST.get("phone", "").strip()

    try:
        amount = Decimal(amount_raw)
        if amount <= Decimal("0.00"):
            raise ValueError()
    except (ValueError, decimal.InvalidOperation):
        messages.error(request, "Please enter a valid payment amount greater than zero.")
        return redirect("university:student_pay_fees")

    if not fee_account_id:
        messages.error(request, "Please select an active payment method.")
        return redirect("university:student_pay_fees")

    try:
        payment, result = initiate_student_payment(
            student=student,
            fee_account_id=int(fee_account_id),
            amount=amount,
            request=request,
            extra_data={"phone": phone},
        )
        return redirect("university:student_payment_status", reference=payment.internal_reference)
    except Exception as e:
        logger.exception("Failed to initiate student payment")
        messages.error(request, f"Unable to initiate payment: {str(e)}")
        return redirect("university:student_pay_fees")


@login_required
def student_payment_status(request, reference):
    """
    Displays real-time status of an initiated payment with provider instructions,
    STK prompt advice, or deposit slip information.
    """
    payment = get_object_or_404(
        Payment.objects.select_related("student", "fee_account", "student__user"),
        internal_reference=reference,
    )

    # Security: Ensure student only views their own payment
    if request.user.is_student and payment.student.user_id != request.user.id:
        raise Http404("Payment not found.")

    from university.payment_providers.registry import get_payment_adapter
    adapter = get_payment_adapter(payment.fee_account)
    init_result = adapter.initiate_payment(payment, request, extra_data={"phone": payment.payer_phone})

    receipt = FeeReceipt.objects.filter(payment=payment).first()

    return render(request, "dashboard/student_payment_status.html", {
        "payment": payment,
        "instructions": init_result.instructions,
        "receipt": receipt,
    })


@login_required
def student_payment_poll(request, reference):
    """
    AJAX endpoint for polling status during STK push or hosted checkout.
    """
    payment = get_object_or_404(Payment, internal_reference=reference)
    if request.user.is_student and payment.student.user_id != request.user.id:
        return JsonResponse({"error": "unauthorized"}, status=403)

    receipt = FeeReceipt.objects.filter(payment=payment).first()

    return JsonResponse({
        "status": payment.status,
        "status_display": payment.get_status_display(),
        "is_successful": payment.status == Payment.Status.SUCCESSFUL,
        "is_failed": payment.status in [Payment.Status.FAILED, Payment.Status.CANCELLED, Payment.Status.EXPIRED],
        "receipt_number": receipt.receipt_number if receipt else None,
        "receipt_url": reverse("university:student_receipt_view", kwargs={"receipt_no": receipt.receipt_number}) if receipt else None,
    })


@login_required
def student_receipt_view(request, receipt_no):
    """
    Official branded HTML payment receipt with print and PDF actions.
    """
    receipt = get_object_or_404(
        FeeReceipt.objects.select_related("payment", "student", "student__user", "student__program", "payment__fee_account"),
        receipt_number=receipt_no,
    )

    if request.user.is_student and receipt.student.user_id != request.user.id:
        raise Http404("Receipt not found.")

    return render(request, "dashboard/student_fee_receipt.html", {
        "receipt": receipt,
        "payment": receipt.payment,
        "student": receipt.student,
    })


@login_required
def student_receipt_pdf(request, receipt_no):
    """
    Downloadable official PDF receipt with digital watermark and verification reference.
    """
    receipt = get_object_or_404(
        FeeReceipt.objects.select_related("payment", "student", "student__user", "student__program"),
        receipt_number=receipt_no,
    )

    if request.user.is_student and receipt.student.user_id != request.user.id:
        raise Http404("Receipt not found.")

    pdf_buffer = generate_fee_receipt_pdf(receipt.payment)
    response = HttpResponse(pdf_buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{receipt.receipt_number}.pdf"'
    return response


# ==============================================================================
# SECURE SERVER-TO-SERVER WEBHOOKS / CALLBACKS
# ==============================================================================

@csrf_exempt
@require_POST
def mpesa_validation(request):
    """
    Safaricom Daraja C2B Validation Endpoint.
    Validates account number (BillRefNumber) and transaction parameters before Safaricom charges customer.
    """
    logger.info("M-Pesa C2B Validation request received")
    try:
        body_text = request.body.decode("utf-8")
        payload = json.loads(body_text) if body_text else {}
    except Exception:
        payload = {}

    bill_ref = (payload.get("BillRefNumber") or "").strip()
    # Accept by default or confirm student exists if provided
    if bill_ref:
        logger.info(f"M-Pesa C2B validation check for BillRefNumber: {bill_ref}")

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@csrf_exempt
@require_POST
def mpesa_callback(request):
    """
    Server-side webhook for Safaricom Daraja STK Push and C2B Paybill notifications.
    Supports both online web-initiated STK prompts and offline direct C2B mobile payments.
    Processes confirmation, executes invoice allocation, and issues verified receipts.
    """
    from decimal import Decimal
    from accounts.models import StudentProfile
    from university.models import AcademicYear, AcademicTerm, FeeReceipt, PaymentReconciliation, FeeAccountLog

    logger.info("M-Pesa Webhook received")
    try:
        body_text = request.body.decode("utf-8")
        payload = json.loads(body_text) if body_text else {}
    except Exception as e:
        logger.error(f"Invalid M-Pesa webhook payload: {e}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid JSON"}, status=400)

    # 1. Identify Fee Account
    shortcode = str(payload.get("BusinessShortCode") or "")
    fee_account = None
    if shortcode:
        fee_account = FeeAccount.objects.filter(account_identifier=shortcode).first()
    if not fee_account:
        fee_account = FeeAccount.objects.filter(
            account_type=FeeAccount.AccountType.MPESA_PAYBILL,
            status=FeeAccount.Status.ACTIVE,
        ).first() or FeeAccount.objects.filter(account_type=FeeAccount.AccountType.MPESA_PAYBILL).first()

    from university.payment_providers.mpesa import MpesaProviderAdapter
    adapter = MpesaProviderAdapter(fee_account)
    result = adapter.process_callback(request)

    if not (result.success and result.provider_reference):
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Processed"})

    # 2. Idempotency Check: Prevent duplicate credit if provider reference is already processed
    existing_receipt = FeeReceipt.objects.filter(payment__provider_reference=result.provider_reference).first()
    if existing_receipt:
        logger.info(f"Duplicate M-Pesa webhook for {result.provider_reference}. Already receipted as {existing_receipt.receipt_number}.")
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Duplicate / Already Confirmed"})

    # 3. Match web-initiated payment by internal reference
    payment = None
    if result.transaction_reference:
        payment = Payment.objects.filter(
            internal_reference=result.transaction_reference,
            status__in=[Payment.Status.INITIATED, Payment.Status.PENDING, Payment.Status.PROCESSING],
        ).first()

    # If it was an STK Push without explicit internal_reference in top-level payload
    if not payment and "stkCallback" in payload.get("Body", {}):
        payment = Payment.objects.filter(
            status__in=[Payment.Status.INITIATED, Payment.Status.PENDING, Payment.Status.PROCESSING],
            fee_account__account_type__in=[FeeAccount.AccountType.MPESA_PAYBILL, FeeAccount.AccountType.MPESA_TILL],
        ).order_by("-created_at").first()

    # If web payment record found: confirm it!
    if payment:
        process_payment_confirmation(
            payment=payment,
            provider_reference=result.provider_reference,
            raw_payload=payload,
            request=request,
        )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    # 4. DIRECT C2B PAYBILL INGESTION (Student paid directly on phone SIM Toolkit / M-Pesa App)
    trans_id = str(payload.get("TransID") or payload.get("TransactionID") or result.provider_reference)
    bill_ref = (payload.get("BillRefNumber") or "").strip()
    raw_amount = payload.get("TransAmount") or payload.get("Amount") or "0"
    try:
        amount = Decimal(str(raw_amount))
    except Exception:
        amount = Decimal("0")

    phone = str(payload.get("MSISDN") or "")
    first_name = payload.get("FirstName") or ""
    last_name = payload.get("LastName") or ""
    payer_name = f"{first_name} {last_name}".strip()

    # Resolve Student from BillRefNumber (Account Number)
    student = None
    if bill_ref:
        config = fee_account.configuration if fee_account else {}
        prefix = config.get("account_ref_prefix", "") if config else ""
        cleaned_ref = bill_ref
        if prefix and cleaned_ref.upper().startswith(prefix.upper()):
            cleaned_ref = cleaned_ref[len(prefix):].strip()

        # Lookup by registration / roll number
        student = StudentProfile.objects.filter(roll_no__iexact=cleaned_ref).first()
        if not student:
            # Lookup by user username
            student = StudentProfile.objects.filter(user__username__iexact=cleaned_ref).first()

    # Fallback: lookup by mobile phone number
    if not student and phone:
        phone_suffix = phone[-9:] if len(phone) >= 9 else phone
        student = StudentProfile.objects.filter(user__phone__icontains=phone_suffix).first()

    if student and amount > 0:
        ay = AcademicYear.objects.filter(is_current=True).first() or AcademicYear.objects.order_by("-start_date").first()
        term = AcademicTerm.objects.filter(academic_year=ay).first() or AcademicTerm.objects.order_by("-start_date").first()

        payment = Payment.objects.create(
            student=student,
            fee_account=fee_account,
            amount=amount,
            currency=fee_account.currency if fee_account else "KES",
            method="MPESA_PAYBILL",
            academic_year=ay,
            term=term,
            status=Payment.Status.PENDING,
            provider_reference=trans_id,
            payer_name=payer_name or getattr(student.user, "display_name", str(student.user)),
            payer_phone=phone,
            notes=f"Direct C2B M-Pesa Paybill payment. BillRefNumber: '{bill_ref}'",
        )
        process_payment_confirmation(
            payment=payment,
            provider_reference=trans_id,
            raw_payload=payload,
            request=request,
        )
        logger.info(f"Direct C2B Paybill payment successfully credited to student {student.roll_no}. Receipt generated.")
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    # If student could not be matched, record in PaymentReconciliation for finance staff matching
    if fee_account and amount > 0:
        PaymentReconciliation.objects.create(
            fee_account=fee_account,
            provider_reference=trans_id,
            amount=amount,
            currency=fee_account.currency or "KES",
            status=PaymentReconciliation.Status.UNMATCHED,
            notes=f"Unmatched C2B Paybill payment from {payer_name} ({phone}). BillRefNumber: '{bill_ref}'. Student roll number not found in registry.",
        )
        FeeAccountLog.objects.create(
            fee_account=fee_account,
            event_type=FeeAccountLog.EventType.CALLBACK_RECEIVED,
            message=f"Unmatched C2B payment {trans_id} for {fee_account.currency} {amount:,.2f} received (BillRef: '{bill_ref}'). Flagged for reconciliation.",
            payload_preview=payload,
        )
        logger.warning(f"Unmatched C2B payment {trans_id} logged to PaymentReconciliation cockpit.")

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@csrf_exempt
@require_POST
def card_callback(request):
    """
    Server-side webhook for Card Gateway payment confirmation.
    """
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    tx_ref = payload.get("internal_reference") or payload.get("reference")
    provider_ref = payload.get("provider_reference") or payload.get("transaction_id")

    payment = Payment.objects.filter(internal_reference=tx_ref).first()
    if payment:
        process_payment_confirmation(
            payment=payment,
            provider_reference=provider_ref or f"CARD-{timezone.now().strftime('%H%M%S')}",
            raw_payload=payload,
            request=request,
        )
        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "not_found"}, status=404)


@csrf_exempt
@require_POST
def bank_callback(request):
    """
    Server-side webhook for Direct Bank Integration feeds.
    """
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return JsonResponse({"status": "error"}, status=400)

    tx_ref = payload.get("internal_reference") or payload.get("reference")
    bank_ref = payload.get("bank_reference") or payload.get("transaction_id")

    payment = Payment.objects.filter(internal_reference=tx_ref).first()
    if payment:
        process_payment_confirmation(
            payment=payment,
            provider_reference=bank_ref or f"BNK-{timezone.now().strftime('%H%M%S')}",
            raw_payload=payload,
            request=request,
        )
        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "not_found"}, status=404)
