import decimal
from decimal import Decimal
import json
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from accounts.models import Role
from university.models import (
    AuditLog,
    FeeAccount,
    FeeAccountLog,
    FeeInvoice,
    FeeReceipt,
    Payment,
    PaymentAllocation,
    PaymentReconciliation,
    PaymentReversal,
)
from university.audit_services import log_activity
from university.payment_services import (
    process_payment_confirmation,
    reverse_or_refund_payment,
    test_fee_account_connection,
)

logger = logging.getLogger(__name__)


def _finance_admin_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not (request.user.is_superuser or request.user.is_admin_role or request.user.role == Role.ADMIN):
            messages.error(request, "Access restricted to Finance & System Administrators.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapped


# ==============================================================================
# FEE ACCOUNTS DASHBOARD & CRUD
# ==============================================================================

@login_required
@_finance_admin_required
def fee_accounts_dashboard(request):
    """
    Central Administrative Fee Accounts Cockpit.
    Displays configured payment accounts, status health, today's payment metrics, and reconciliation state.
    """
    accounts = FeeAccount.objects.all().order_by("-is_default", "account_type", "name")

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_payments = Payment.objects.filter(created_at__gte=today_start)

    stats = {
        "total_accounts": accounts.count(),
        "active_accounts": accounts.filter(status=FeeAccount.Status.ACTIVE).count(),
        "inactive_accounts": accounts.exclude(status=FeeAccount.Status.ACTIVE).count(),
        "paybill_count": accounts.filter(account_type=FeeAccount.AccountType.MPESA_PAYBILL).count(),
        "till_count": accounts.filter(account_type=FeeAccount.AccountType.MPESA_TILL).count(),
        "card_count": accounts.filter(account_type=FeeAccount.AccountType.CARD_GATEWAY).count(),
        "bank_count": accounts.filter(account_type=FeeAccount.AccountType.BANK_ACCOUNT).count(),
        "today_successful_amount": today_payments.filter(status=Payment.Status.SUCCESSFUL).aggregate(s=Sum("amount"))["s"] or Decimal("0.00"),
        "today_successful_count": today_payments.filter(status=Payment.Status.SUCCESSFUL).count(),
        "today_pending_count": today_payments.filter(status__in=[Payment.Status.PENDING, Payment.Status.PROCESSING]).count(),
        "today_failed_count": today_payments.filter(status__in=[Payment.Status.FAILED, Payment.Status.CANCELLED]).count(),
        "unreconciled_count": PaymentReconciliation.objects.filter(status__in=[PaymentReconciliation.Status.UNMATCHED, PaymentReconciliation.Status.AMOUNT_MISMATCH, PaymentReconciliation.Status.REQUIRES_REVIEW]).count(),
    }

    return render(request, "finance/fee_accounts_dashboard.html", {
        "accounts": accounts,
        "stats": stats,
    })


@login_required
@_finance_admin_required
def fee_account_create(request):
    """
    Create a new institutional Fee Account (M-Pesa Paybill, Till, Card Gateway, or Bank Account).
    """
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        account_type = request.POST.get("account_type")
        provider = request.POST.get("provider")
        identifier = request.POST.get("account_identifier", "").strip()
        account_name = request.POST.get("account_name", "").strip()
        currency = request.POST.get("currency", "KES").strip().upper()
        environment = request.POST.get("environment", FeeAccount.Environment.SANDBOX)
        description = request.POST.get("description", "").strip()
        is_default = request.POST.get("is_default") == "on"

        # Additional configurations per account type
        config = {}
        if account_type in [FeeAccount.AccountType.MPESA_PAYBILL, FeeAccount.AccountType.MPESA_TILL]:
            config["callback_url"] = request.POST.get("callback_url", "/api/payments/callback/mpesa/").strip()
            config["validation_url"] = request.POST.get("validation_url", "").strip()
            config["account_ref_format"] = request.POST.get("account_ref_format", "STUDENT_REG_NO").strip()
            config["fixed_account_number"] = request.POST.get("fixed_account_number", "").strip()
            config["account_ref_prefix"] = request.POST.get("account_ref_prefix", "").strip()
            config["consumer_key"] = request.POST.get("consumer_key", "").strip()
            config["passkey"] = request.POST.get("passkey", "").strip()
        elif account_type == FeeAccount.AccountType.CARD_GATEWAY:
            config["callback_url"] = request.POST.get("callback_url", "/api/payments/callback/card/").strip()
            config["merchant_name"] = request.POST.get("merchant_name", "").strip()
        elif account_type == FeeAccount.AccountType.BANK_ACCOUNT:
            config["branch"] = request.POST.get("branch", "").strip()
            config["bank_code"] = request.POST.get("bank_code", "").strip()
            config["swift_code"] = request.POST.get("swift_code", "").strip()

        # Encrypted credentials (masked, never shown in logs)
        secret_raw = request.POST.get("secret_key", "").strip()

        if not name or not identifier:
            messages.error(request, "Account Name and Account Identifier / Number are required.")
            return render(request, "finance/fee_account_form.html", {
                "account_types": FeeAccount.AccountType.choices,
                "providers": FeeAccount.Provider.choices,
                "environments": FeeAccount.Environment.choices,
                "is_edit": False,
            })

        if is_default:
            FeeAccount.objects.filter(account_type=account_type, is_default=True).update(is_default=False)

        account = FeeAccount.objects.create(
            name=name,
            account_type=account_type,
            provider=provider,
            account_identifier=identifier,
            account_name=account_name,
            currency=currency,
            environment=environment,
            description=description,
            is_default=is_default,
            status=FeeAccount.Status.ACTIVE,
            configuration=config,
            encrypted_credentials=secret_raw,
            created_by=request.user,
        )

        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.CREATE,
            module=AuditLog.Module.FEES,
            entity="FeeAccount",
            entity_id=str(account.id),
            description=f"Created fee account: {account.name} ({account.get_account_type_display()}).",
        )

        messages.success(request, f"Fee Account '{account.name}' created and activated successfully.")
        return redirect("university:fee_account_detail", pk=account.pk)

    return render(request, "finance/fee_account_form.html", {
        "account_types": FeeAccount.AccountType.choices,
        "providers": FeeAccount.Provider.choices,
        "environments": FeeAccount.Environment.choices,
        "is_edit": False,
    })


@login_required
@_finance_admin_required
def fee_account_edit(request, pk):
    """
    Edit existing Fee Account parameters and credentials.
    """
    account = get_object_or_404(FeeAccount, pk=pk)

    if request.method == "POST":
        account.name = request.POST.get("name", "").strip()
        account.provider = request.POST.get("provider", account.provider)
        account.account_identifier = request.POST.get("account_identifier", "").strip()
        account.account_name = request.POST.get("account_name", "").strip()
        account.currency = request.POST.get("currency", "KES").strip().upper()
        account.environment = request.POST.get("environment", account.environment)
        account.description = request.POST.get("description", "").strip()
        is_default = request.POST.get("is_default") == "on"

        if is_default and not account.is_default:
            FeeAccount.objects.filter(account_type=account.account_type, is_default=True).update(is_default=False)
            account.is_default = True
        elif not is_default and account.is_default:
            account.is_default = False

        # Update configuration
        config = account.configuration or {}
        if account.account_type in [FeeAccount.AccountType.MPESA_PAYBILL, FeeAccount.AccountType.MPESA_TILL]:
            config["callback_url"] = request.POST.get("callback_url", config.get("callback_url", "")).strip()
            config["validation_url"] = request.POST.get("validation_url", config.get("validation_url", "")).strip()
            config["account_ref_format"] = request.POST.get("account_ref_format", "STUDENT_REG_NO").strip()
            config["fixed_account_number"] = request.POST.get("fixed_account_number", "").strip()
            config["account_ref_prefix"] = request.POST.get("account_ref_prefix", "").strip()
            consumer_key = request.POST.get("consumer_key", "").strip()
            if consumer_key and not consumer_key.startswith("••••"):
                config["consumer_key"] = consumer_key
            passkey = request.POST.get("passkey", "").strip()
            if passkey and not passkey.startswith("••••"):
                config["passkey"] = passkey
        elif account.account_type == FeeAccount.AccountType.CARD_GATEWAY:
            config["callback_url"] = request.POST.get("callback_url", config.get("callback_url", "")).strip()
        elif account.account_type == FeeAccount.AccountType.BANK_ACCOUNT:
            config["branch"] = request.POST.get("branch", config.get("branch", "")).strip()
            config["bank_code"] = request.POST.get("bank_code", config.get("bank_code", "")).strip()

        account.configuration = config

        # Only update secret if user typed a new one
        new_secret = request.POST.get("secret_key", "").strip()
        if new_secret and not new_secret.startswith("••••"):
            account.encrypted_credentials = new_secret

        account.updated_by = request.user
        account.save()

        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.FEES,
            entity="FeeAccount",
            entity_id=str(account.id),
            description=f"Updated fee account settings: {account.name}.",
        )

        messages.success(request, f"Fee Account '{account.name}' updated successfully.")
        return redirect("university:fee_account_detail", pk=account.pk)

    return render(request, "finance/fee_account_form.html", {
        "account": account,
        "account_types": FeeAccount.AccountType.choices,
        "providers": FeeAccount.Provider.choices,
        "environments": FeeAccount.Environment.choices,
        "is_edit": True,
    })


@login_required
@_finance_admin_required
def fee_account_detail(request, pk):
    """
    Detailed overview of a Fee Account, including connection diagnostics,
    recent transaction volume, and operational logs.
    """
    account = get_object_or_404(FeeAccount, pk=pk)
    recent_payments = Payment.objects.filter(fee_account=account).select_related("student", "student__user").order_by("-created_at")[:20]
    logs = FeeAccountLog.objects.filter(fee_account=account).order_by("-created_at")[:15]

    payment_stats = {
        "total_collected": Payment.objects.filter(fee_account=account, status=Payment.Status.SUCCESSFUL).aggregate(s=Sum("amount"))["s"] or Decimal("0.00"),
        "successful_count": Payment.objects.filter(fee_account=account, status=Payment.Status.SUCCESSFUL).count(),
        "pending_count": Payment.objects.filter(fee_account=account, status__in=[Payment.Status.PENDING, Payment.Status.PROCESSING]).count(),
        "failed_count": Payment.objects.filter(fee_account=account, status__in=[Payment.Status.FAILED, Payment.Status.CANCELLED]).count(),
    }

    return render(request, "finance/fee_account_detail.html", {
        "account": account,
        "payments": recent_payments,
        "logs": logs,
        "stats": payment_stats,
    })


@login_required
@_finance_admin_required
@require_POST
def fee_account_toggle_status(request, pk):
    """
    Toggles account between ACTIVE and INACTIVE.
    Changes immediately affect availability on the student's Pay Fees page.
    """
    account = get_object_or_404(FeeAccount, pk=pk)
    if account.status == FeeAccount.Status.ACTIVE:
        account.status = FeeAccount.Status.INACTIVE
        messages.warning(request, f"Fee Account '{account.name}' deactivated. Students will no longer see this payment method.")
    else:
        account.status = FeeAccount.Status.ACTIVE
        messages.success(request, f"Fee Account '{account.name}' activated. Available immediately on Pay Fees.")

    account.updated_by = request.user
    account.save(update_fields=["status", "updated_at", "updated_by"])

    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.FEES,
        entity="FeeAccount",
        entity_id=str(account.id),
        description=f"Toggled fee account status: {account.name} -> {account.status}.",
    )

    return redirect(request.META.get("HTTP_REFERER") or "university:fee_accounts_dashboard")


@login_required
@_finance_admin_required
@require_POST
def fee_account_set_default(request, pk):
    """
    Designates this account as the default for its account type.
    """
    account = get_object_or_404(FeeAccount, pk=pk)
    FeeAccount.objects.filter(account_type=account.account_type, is_default=True).update(is_default=False)
    account.is_default = True
    account.save(update_fields=["is_default"])

    messages.success(request, f"'{account.name}' set as the default {account.get_account_type_display()} account.")
    return redirect(request.META.get("HTTP_REFERER") or "university:fee_accounts_dashboard")


@login_required
@_finance_admin_required
@require_POST
def fee_account_test(request, pk):
    """
    Runs diagnostic connection and validation test against the payment provider.
    """
    account = get_object_or_404(FeeAccount, pk=pk)
    success, message, diagnostics = test_fee_account_connection(account, user=request.user, request=request)

    if success:
        messages.success(request, f"Connection Test Succeeded: {message}")
    else:
        messages.error(request, f"Connection Test Failed: {message}")

    return redirect(request.META.get("HTTP_REFERER") or reverse("university:fee_account_detail", kwargs={"pk": account.pk}))


@login_required
@_finance_admin_required
@require_POST
def fee_account_delete(request, pk):
    """
    Safe account deactivation. Preserves records if payments exist.
    """
    account = get_object_or_404(FeeAccount, pk=pk)
    if Payment.objects.filter(fee_account=account).exists():
        account.status = FeeAccount.Status.EXPIRED
        account.is_default = False
        account.save(update_fields=["status", "is_default"])
        messages.info(request, f"Account '{account.name}' has historical payments and was archived rather than deleted.")
    else:
        account.delete()
        messages.success(request, f"Fee Account '{account.name}' deleted.")

    return redirect("university:fee_accounts_dashboard")


# ==============================================================================
# FINANCE ADMIN PAYMENTS TRANSACTION CENTER & REVERSALS
# ==============================================================================

@login_required
@_finance_admin_required
def admin_payments_list(request):
    """
    Comprehensive Finance Payments Transaction Center.
    Search, filter, paginate, verify, and reverse student fee payments.
    """
    qs = Payment.objects.select_related("student", "student__user", "student__program", "fee_account").order_by("-created_at")

    # Filters
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(internal_reference__icontains=q) |
            Q(provider_reference__icontains=q) |
            Q(reference__icontains=q) |
            Q(student__roll_no__icontains=q) |
            Q(student__user__first_name__icontains=q) |
            Q(student__user__last_name__icontains=q)
        )

    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)

    account_id = request.GET.get("account_id")
    if account_id:
        qs = qs.filter(fee_account_id=account_id)

    # Aggregates
    summary = {
        "total_amount": qs.filter(status=Payment.Status.SUCCESSFUL).aggregate(s=Sum("amount"))["s"] or Decimal("0.00"),
        "total_count": qs.count(),
        "successful_count": qs.filter(status=Payment.Status.SUCCESSFUL).count(),
        "pending_count": qs.filter(status__in=[Payment.Status.PENDING, Payment.Status.PROCESSING]).count(),
    }

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "finance/admin_payments_list.html", {
        "payments": page_obj,
        "summary": summary,
        "accounts": FeeAccount.objects.all(),
        "statuses": Payment.Status.choices,
        "selected_status": status,
        "selected_account": account_id,
        "q": q,
    })


@login_required
@_finance_admin_required
def admin_payment_detail(request, pk):
    """
    Drill-down inspection of a specific payment, its invoice allocations, raw payloads, and audit history.
    """
    payment = get_object_or_404(
        Payment.objects.select_related("student", "student__user", "student__program", "fee_account", "invoice"),
        pk=pk,
    )
    allocations = payment.allocations.select_related("invoice").all()
    receipt = FeeReceipt.objects.filter(payment=payment).first()
    reversals = payment.reversals.select_related("requested_by", "approved_by").all()

    return render(request, "finance/admin_payment_detail.html", {
        "payment": payment,
        "allocations": allocations,
        "receipt": receipt,
        "reversals": reversals,
    })


@login_required
@_finance_admin_required
@require_POST
def admin_payment_verify(request, pk):
    """
    Finance Officer manual verification for pending bank transfers or unmatched payments.
    """
    payment = get_object_or_404(Payment, pk=pk)
    provider_ref = request.POST.get("provider_reference", "").strip() or payment.provider_reference or f"MANUAL-{timezone.now().strftime('%H%M%S')}"

    receipt = process_payment_confirmation(
        payment=payment,
        provider_reference=provider_ref,
        confirmed_by=request.user,
        request=request,
    )

    messages.success(request, f"Payment {payment.internal_reference} verified and settled. Receipt {receipt.receipt_number} issued.")
    return redirect("university:admin_payment_detail", pk=payment.pk)


@login_required
@_finance_admin_required
@require_POST
def admin_payment_reverse(request, pk):
    """
    Reverses or refunds a confirmed payment with a mandatory justification reason.
    """
    payment = get_object_or_404(Payment, pk=pk)
    amount_str = request.POST.get("amount", str(payment.amount)).strip().replace(",", "")
    reversal_type = request.POST.get("reversal_type", PaymentReversal.ReversalType.REVERSAL)
    reason = request.POST.get("reason", "").strip()

    if not reason:
        messages.error(request, "A detailed justification reason is required for reversals and refunds.")
        return redirect("university:admin_payment_detail", pk=payment.pk)

    try:
        amount = Decimal(amount_str)
        reversal = reverse_or_refund_payment(
            payment=payment,
            reversal_type=reversal_type,
            amount=amount,
            reason=reason,
            user=request.user,
            request=request,
        )
        messages.success(request, f"{reversal.get_reversal_type_display()} of KES {amount:,.2f} applied successfully.")
    except Exception as e:
        messages.error(request, f"Reversal failed: {str(e)}")

    return redirect("university:admin_payment_detail", pk=payment.pk)


# ==============================================================================
# RECONCILIATION DASHBOARD
# ==============================================================================

@login_required
@_finance_admin_required
def fee_reconciliation_dashboard(request):
    """
    Finance Reconciliation Control Center.
    Matches provider transaction reports against internal payment records.
    """
    qs = PaymentReconciliation.objects.select_related("fee_account", "payment", "payment__student").order_by("-transaction_date")

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    summary = {
        "matched_count": PaymentReconciliation.objects.filter(status=PaymentReconciliation.Status.MATCHED).count(),
        "unmatched_count": PaymentReconciliation.objects.filter(status=PaymentReconciliation.Status.UNMATCHED).count(),
        "mismatch_count": PaymentReconciliation.objects.filter(status=PaymentReconciliation.Status.AMOUNT_MISMATCH).count(),
        "review_count": PaymentReconciliation.objects.filter(status=PaymentReconciliation.Status.REQUIRES_REVIEW).count(),
    }

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "finance/reconciliation_dashboard.html", {
        "reconciliations": page_obj,
        "summary": summary,
        "statuses": PaymentReconciliation.Status.choices,
        "selected_status": status_filter,
    })


@login_required
@_finance_admin_required
@require_POST
def fee_reconciliation_match(request, pk):
    """
    Manually resolves or marks a reconciliation record as matched.
    """
    recon = get_object_or_404(PaymentReconciliation, pk=pk)
    recon.status = PaymentReconciliation.Status.MATCHED
    recon.reconciled_by = request.user
    recon.reconciled_at = timezone.now()
    recon.notes = f"{recon.notes}\n[RESOLVED]: Manually matched by {request.user.username} on {timezone.now().strftime('%Y-%m-%d')}."
    recon.save()

    messages.success(request, f"Reconciliation record {recon.provider_reference} marked as Matched.")
    return redirect("university:fee_reconciliation_dashboard")
