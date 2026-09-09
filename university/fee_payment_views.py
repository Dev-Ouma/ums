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
def mpesa_callback(request):
    """
    Server-side webhook for Safaricom Daraja STK Push and C2B Paybill notifications.
    Processes confirmation, executes invoice allocation, and issues verified receipts.
    """
    logger.info("M-Pesa Webhook received")
    try:
        body_text = request.body.decode("utf-8")
        payload = json.loads(body_text) if body_text else {}
    except Exception as e:
        logger.error(f"Invalid M-Pesa webhook payload: {e}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid JSON"}, status=400)

    # Find the M-Pesa fee account
    fee_account = FeeAccount.objects.filter(
        account_type__in=[FeeAccount.AccountType.MPESA_PAYBILL, FeeAccount.AccountType.MPESA_TILL],
        status=FeeAccount.Status.ACTIVE,
    ).first()
    if not fee_account:
        fee_account = FeeAccount.objects.filter(account_type=FeeAccount.AccountType.MPESA_PAYBILL).first()

    from university.payment_providers.mpesa import MpesaProviderAdapter
    adapter = MpesaProviderAdapter(fee_account)
    result = adapter.process_callback(request)

    if result.success and result.provider_reference:
        # Match payment by internal reference or find latest pending payment
        payment = None
        if result.transaction_reference:
            payment = Payment.objects.filter(
                internal_reference=result.transaction_reference,
                status__in=[Payment.Status.INITIATED, Payment.Status.PENDING, Payment.Status.PROCESSING],
            ).first()

        if not payment:
            # Match latest pending M-Pesa payment
            payment = Payment.objects.filter(
                status__in=[Payment.Status.INITIATED, Payment.Status.PENDING, Payment.Status.PROCESSING],
                fee_account__account_type__in=[FeeAccount.AccountType.MPESA_PAYBILL, FeeAccount.AccountType.MPESA_TILL],
            ).order_by("-created_at").first()

        if payment:
            process_payment_confirmation(
                payment=payment,
                provider_reference=result.provider_reference,
                raw_payload=payload,
                request=request,
            )
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Processed"})


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
