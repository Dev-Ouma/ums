import decimal
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from university.models import (
    AcademicTerm,
    AcademicYear,
    AuditLog,
    FeeAccount,
    FeeAccountLog,
    FeeInvoice,
    FeeReceipt,
    Notice,
    Payment,
    PaymentAllocation,
    PaymentReconciliation,
    PaymentReversal,
)
from university.audit_services import log_activity
from university.payment_providers.registry import get_payment_adapter

logger = logging.getLogger(__name__)


def get_active_fee_accounts_for_student(student) -> List[FeeAccount]:
    """
    Returns only ACTIVE Fee Accounts configured for student payments,
    respecting academic year, campus, or program scoping if configured.
    """
    qs = FeeAccount.objects.filter(status=FeeAccount.Status.ACTIVE)

    # Scoping filters if configured
    if student.program_id:
        qs = qs.filter(Q(program__isnull=True) | Q(program_id=student.program_id))
    else:
        qs = qs.filter(program__isnull=True)

    dept_id = getattr(student, "department_id", None) or (student.program.department_id if student.program_id else None)
    if dept_id:
        qs = qs.filter(Q(department__isnull=True) | Q(department_id=dept_id))
    else:
        qs = qs.filter(department__isnull=True)

    return list(qs.order_by("-is_default", "account_type", "name"))


def get_student_balance_summary(student) -> Dict[str, Any]:
    """
    Calculates total billed, total paid, and net balance due for a student.
    Formula: Opening Balance + Charges - Successful Payments - Credits = Outstanding Balance.
    """
    invoices = FeeInvoice.objects.filter(student=student)
    total_billed = sum((inv.amount for inv in invoices), Decimal("0.00"))
    total_paid = sum((inv.amount_paid for inv in invoices), Decimal("0.00"))
    balance = max(Decimal("0.00"), total_billed - total_paid)

    return {
        "total_billed": total_billed,
        "total_paid": total_paid,
        "balance": balance,
        "currency": "KES",
    }


def initiate_student_payment(
    student,
    fee_account_id: int,
    amount: Decimal,
    request,
    extra_data: Optional[Dict[str, Any]] = None,
) -> Tuple[Payment, Any]:
    """
    Initiates a new fee payment transaction through the selected active Fee Account.
    Validates amount, reserves an internal payment reference, and dispatches to the provider adapter.
    """
    if amount <= Decimal("0.00"):
        raise ValueError("Payment amount must be greater than zero.")

    fee_account = FeeAccount.objects.filter(
        pk=fee_account_id, status=FeeAccount.Status.ACTIVE
    ).first()
    if not fee_account:
        raise ValueError("The selected payment channel is currently unavailable or inactive.")

    # Current academic context
    ay = AcademicYear.objects.filter(is_current=True).first() or AcademicYear.objects.order_by("-start_date").first()
    term = AcademicTerm.objects.filter(academic_year=ay).first() or AcademicTerm.objects.order_by("-start_date").first()

    payment = Payment(
        student=student,
        fee_account=fee_account,
        amount=amount,
        currency=fee_account.currency or "KES",
        method=fee_account.get_account_type_display(),
        academic_year=ay,
        term=term,
        status=Payment.Status.INITIATED,
        payer_name=getattr(student.user, "display_name", str(student.user)),
        payer_phone=getattr(student.user, "phone", ""),
    )
    payment.save()

    # Delegate to payment provider adapter
    adapter = get_payment_adapter(fee_account)
    result = adapter.initiate_payment(payment, request, extra_data=extra_data)

    payment.status = result.status
    if result.provider_reference:
        payment.provider_reference = result.provider_reference
    payment.save(update_fields=["status", "provider_reference"])

    # Record initialization log
    FeeAccountLog.objects.create(
        fee_account=fee_account,
        event_type=FeeAccountLog.EventType.INITIATE_PAYMENT,
        message=f"Payment {payment.internal_reference} initiated for {payment.currency} {payment.amount:,.2f} by {student.roll_no}.",
        payload_preview={"status": result.status, "message": result.message},
    )

    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.FEES,
        entity="Payment",
        entity_id=payment.internal_reference,
        description=f"Initiated fee payment of {payment.currency} {payment.amount:,.2f} via {fee_account.name}.",
    )

    return payment, result


@transaction.atomic
def process_payment_confirmation(
    payment_id_or_ref=None,
    provider_reference: str = "",
    raw_payload: Optional[Dict[str, Any]] = None,
    confirmed_by=None,
    request=None,
    payment=None,
) -> FeeReceipt:
    """
    Idempotent payment confirmation & allocation engine.
    Atomically locks the payment record, ensures no duplicate execution,
    allocates amount across invoices, issues the official receipt, and dispatches notifications.
    """
    target = payment if payment is not None else payment_id_or_ref
    if target is None:
        raise ValueError("A payment instance or payment reference ID is required.")

    # Find and row-lock the payment record
    qs = Payment.objects.select_for_update().select_related("student", "fee_account", "student__user")
    if isinstance(target, Payment):
        payment = qs.filter(pk=target.pk).first()
    elif isinstance(target, int):
        payment = qs.filter(pk=target).first()
    else:
        payment = qs.filter(internal_reference=target).first() or qs.filter(reference=target).first()

    if not payment:
        raise ValueError(f"Payment record '{target}' not found.")

    # Idempotency check: if already confirmed, do not double allocate or duplicate receipt
    if payment.status == Payment.Status.SUCCESSFUL:
        receipt = FeeReceipt.objects.filter(payment=payment).first()
        if receipt:
            return receipt

    # Check for duplicate provider reference on other payments
    if provider_reference and Payment.objects.filter(provider_reference=provider_reference).exclude(pk=payment.pk).exists():
        PaymentReconciliation.objects.create(
            fee_account=payment.fee_account or FeeAccount.objects.first(),
            payment=payment,
            provider_reference=provider_reference,
            internal_reference=payment.internal_reference or "",
            amount=payment.amount,
            status=PaymentReconciliation.Status.DUPLICATE,
            notes="Duplicate provider transaction reference received; flagged for investigation.",
        )

    # Compute balance before allocation
    bal_summary = get_student_balance_summary(payment.student)
    prev_balance = bal_summary["balance"]

    payment.status = Payment.Status.SUCCESSFUL
    payment.provider_reference = provider_reference or payment.provider_reference or f"PRV-{timezone.now().strftime('%H%M%S')}"
    payment.completed_at = timezone.now()
    payment.raw_callback_payload = raw_payload or {}
    payment.save()

    # Allocate payment to student's invoices
    allocations = allocate_payment_to_invoices(payment)

    # Balance after allocation
    new_bal_summary = get_student_balance_summary(payment.student)
    rem_balance = new_bal_summary["balance"]

    # Issue verified Fee Receipt
    receipt_no = f"REC-{payment.paid_on.year}-{payment.id:06d}"
    receipt, _ = FeeReceipt.objects.get_or_create(
        payment=payment,
        defaults={
            "receipt_number": receipt_no,
            "student": payment.student,
            "issued_at": timezone.now(),
            "previous_balance": prev_balance,
            "amount_paid": payment.amount,
            "remaining_balance": rem_balance,
        }
    )

    # Auto-create matched reconciliation entry
    if payment.fee_account:
        PaymentReconciliation.objects.update_or_create(
            provider_reference=payment.provider_reference,
            defaults={
                "fee_account": payment.fee_account,
                "payment": payment,
                "internal_reference": payment.internal_reference or "",
                "amount": payment.amount,
                "currency": payment.currency,
                "status": PaymentReconciliation.Status.MATCHED,
                "reconciled_at": timezone.now(),
                "reconciled_by": confirmed_by,
                "notes": f"Automatically reconciled via backend verification on {timezone.now().strftime('%Y-%m-%d %H:%M')}.",
            }
        )

        FeeAccountLog.objects.create(
            fee_account=payment.fee_account,
            event_type=FeeAccountLog.EventType.VERIFICATION,
            message=f"Payment {payment.internal_reference} verified ({payment.currency} {payment.amount:,.2f}). Receipt {receipt.receipt_number} issued.",
            payload_preview={"provider_reference": payment.provider_reference, "receipt": receipt.receipt_number},
        )

    # Notify student via in-app Notice / targeted message
    student_user = getattr(payment.student, "user", None)
    if student_user:
        n = Notice.objects.create(
            title=f"Fee Payment Received ({receipt.receipt_number})",
            body=f"Your fee payment of {payment.currency} {payment.amount:,.2f} has been verified and applied to your account. Remaining balance: {payment.currency} {rem_balance:,.2f}.",
            message_type="SUCCESS",
            priority="NORMAL",
            status="PUBLISHED",
            starts_at=timezone.now(),
            ends_at=timezone.now() + timezone.timedelta(days=14),
            locations=["IN_APP", "STUDENT"],
            created_by=confirmed_by or student_user,
        )
        n.recipients.add(student_user)

    # Log to AuditLog
    log_activity(
        request=request,
        user=confirmed_by or student_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.FEES,
        entity="Payment",
        entity_id=payment.internal_reference or str(payment.id),
        description=f"Confirmed payment of {payment.currency} {payment.amount:,.2f} for {payment.student.roll_no}. Receipt: {receipt.receipt_number}.",
    )

    return receipt


def allocate_payment_to_invoices(payment: Payment) -> List[PaymentAllocation]:
    """
    Distributes the payment amount across the student's unpaid invoices
    (from oldest due date to newest). Updates invoice amount_paid and status.
    """
    student = payment.student
    invoices = FeeInvoice.objects.filter(student=student).order_by("due_date", "id")

    allocations = []
    unallocated_amount = payment.amount

    for inv in invoices:
        due = inv.balance
        if due <= Decimal("0.00"):
            continue

        alloc_amount = min(unallocated_amount, due)
        inv.amount_paid += alloc_amount
        inv.save(update_fields=["amount_paid"])

        alloc = PaymentAllocation.objects.create(
            payment=payment,
            invoice=inv,
            amount=alloc_amount,
            allocated_at=timezone.now(),
        )
        allocations.append(alloc)
        unallocated_amount -= alloc_amount

        if unallocated_amount <= Decimal("0.00"):
            break

    # If payment wasn't attached to a primary invoice, link to the first allocated invoice
    if not payment.invoice_id and allocations:
        payment.invoice = allocations[0].invoice
        payment.save(update_fields=["invoice"])

    return allocations


@transaction.atomic
def reverse_or_refund_payment(
    payment: Payment,
    reversal_type: str,
    amount: Decimal,
    reason: str,
    user,
    request=None,
) -> PaymentReversal:
    """
    Reverses or refunds a confirmed payment.
    Deducts the refunded amount from invoice balances and logs a reversal record.
    Never hard-deletes the original payment.
    """
    if payment.status != Payment.Status.SUCCESSFUL:
        raise ValueError("Only successful payments can be reversed or refunded.")

    if amount <= Decimal("0.00") or amount > payment.amount:
        raise ValueError("Invalid refund amount.")

    reversal = PaymentReversal.objects.create(
        original_payment=payment,
        reversal_type=reversal_type,
        amount=amount,
        reason=reason,
        status=PaymentReversal.Status.APPROVED,
        requested_by=user,
        approved_by=user,
        processed_at=timezone.now(),
    )

    # Reverse invoice allocations in reverse order
    remaining_reversal = amount
    for alloc in payment.allocations.order_by("-allocated_at"):
        deduct = min(remaining_reversal, alloc.amount)
        inv = alloc.invoice
        inv.amount_paid = max(Decimal("0.00"), inv.amount_paid - deduct)
        inv.save(update_fields=["amount_paid"])
        alloc.amount -= deduct
        alloc.save(update_fields=["amount"])
        remaining_reversal -= deduct
        if remaining_reversal <= Decimal("0.00"):
            break

    # Update payment status
    if amount == payment.amount:
        payment.status = Payment.Status.REVERSED if reversal_type == PaymentReversal.ReversalType.REVERSAL else Payment.Status.REFUNDED
    else:
        payment.status = Payment.Status.PARTIALLY_REFUNDED

    payment.notes = f"{payment.notes}\n[REVERSED {timezone.now().strftime('%Y-%m-%d')}]: {reversal_type} of KES {amount:,.2f}. Reason: {reason}."
    payment.save(update_fields=["status", "notes"])

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.FEES,
        entity="PaymentReversal",
        entity_id=payment.internal_reference or str(payment.id),
        description=f"Processed {reversal_type} of KES {amount:,.2f} on {payment.internal_reference}. Reason: {reason}.",
    )

    return reversal


def test_fee_account_connection(fee_account: FeeAccount, user=None, request=None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Runs diagnostic connection and credential check against the payment provider.
    Updates FeeAccount test telemetry and records a FeeAccountLog.
    """
    adapter = get_payment_adapter(fee_account)
    success, message, diagnostics = adapter.test_connection()

    fee_account.last_tested_at = timezone.now()
    fee_account.last_test_status = "PASSED" if success else "FAILED"
    fee_account.last_test_message = message
    fee_account.save(update_fields=["last_tested_at", "last_test_status", "last_test_message"])

    FeeAccountLog.objects.create(
        fee_account=fee_account,
        event_type=FeeAccountLog.EventType.TEST_CONNECTION,
        message=message,
        payload_preview=diagnostics,
    )

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.FEES,
        entity="FeeAccount",
        entity_id=str(fee_account.id),
        description=f"Tested connection for {fee_account.name}: {fee_account.last_test_status}.",
    )

    return success, message, diagnostics


def seed_default_fee_accounts() -> List[FeeAccount]:
    """
    Seeds default institutional payment accounts if none exist,
    ensuring instant out-of-the-box payment options for students.
    """
    defaults = [
        {
            "name": "University Main Tuition Paybill",
            "account_type": FeeAccount.AccountType.MPESA_PAYBILL,
            "provider": FeeAccount.Provider.SAFARICOM,
            "account_identifier": "522123",
            "account_name": "University Main Operating Account",
            "currency": "KES",
            "status": FeeAccount.Status.ACTIVE,
            "is_default": True,
            "environment": FeeAccount.Environment.SANDBOX,
            "description": "Standard institutional M-Pesa Paybill for tuition and examination fees.",
            "configuration": {"account_ref_format": "STUDENT_REG_NO", "callback_url": "/api/payments/callback/mpesa/"},
        },
        {
            "name": "Student Services Cashier Till",
            "account_type": FeeAccount.AccountType.MPESA_TILL,
            "provider": FeeAccount.Provider.SAFARICOM,
            "account_identifier": "889900",
            "account_name": "University Student Cashier Desk",
            "currency": "KES",
            "status": FeeAccount.Status.ACTIVE,
            "is_default": True,
            "environment": FeeAccount.Environment.SANDBOX,
            "description": "Lipa Na M-Pesa Buy Goods & Services Till for on-campus and immediate fee clearances.",
            "configuration": {"callback_url": "/api/payments/callback/mpesa/"},
        },
        {
            "name": "Debit & Credit Card Gateway",
            "account_type": FeeAccount.AccountType.CARD_GATEWAY,
            "provider": FeeAccount.Provider.STRIPE,
            "account_identifier": "acct_univ_card_portal",
            "account_name": "University International & Card Portal",
            "currency": "KES",
            "status": FeeAccount.Status.ACTIVE,
            "is_default": True,
            "environment": FeeAccount.Environment.SANDBOX,
            "description": "Compliant hosted card payment gateway for Visa, Mastercard, and international cards.",
            "configuration": {"supported_cards": ["Visa", "Mastercard", "American Express"], "callback_url": "/api/payments/callback/card/"},
        },
        {
            "name": "Co-operative Bank Main Collection Account",
            "account_type": FeeAccount.AccountType.BANK_ACCOUNT,
            "provider": FeeAccount.Provider.COOP,
            "account_identifier": "01129000123400",
            "account_name": "University Tuition Fees Collection Account",
            "currency": "KES",
            "status": FeeAccount.Status.ACTIVE,
            "is_default": True,
            "environment": FeeAccount.Environment.PRODUCTION,
            "description": "Direct bank branch deposit and EFT / RTGS transfers for student fees.",
            "configuration": {"branch": "University Way Branch", "bank_code": "11", "ref_format": "STUDENT_REG_NO"},
        },
    ]

    created_accounts = []
    for item in defaults:
        account, created = FeeAccount.objects.get_or_create(
            account_identifier=item["account_identifier"],
            account_type=item["account_type"],
            defaults=item,
        )
        created_accounts.append(account)
    return created_accounts
