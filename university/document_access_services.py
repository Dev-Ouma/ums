"""
Access control, scheduling windows, and financial clearance enforcement for academic documents.
Provides central gating logic across transcripts, exam cards, results, and progress reports.
"""
from decimal import Decimal
from django.utils import timezone
from accounts.models import Role
from university.models import DocumentReleaseControl
from university.financial_services import check_financial_clearance


def check_document_access(student, document_type, term=None, user=None):
    """
    Evaluate whether a student or requester can preview or download an academic document.

    Returns:
        (allowed: bool, reason: str, control: DocumentReleaseControl | None, is_staff_bypass: bool)
    """
    is_staff = False
    if user and user.is_authenticated:
        if user.is_staff or user.is_superuser or user.role in [Role.ADMIN, Role.FACULTY]:
            is_staff = True

    # 1. Look up release control policy (Term-specific first, then universal fallback)
    control = None
    if term:
        control = DocumentReleaseControl.objects.filter(term=term, document_type=document_type).first()
    if not control:
        control = DocumentReleaseControl.objects.filter(term__isnull=True, document_type=document_type).first()

    # If no control rule exists, default is open
    if not control:
        return True, "Access permitted (no restrictions configured)", None, is_staff

    # 2. If requester is staff/admin, allow bypass with note
    if is_staff:
        return True, "Staff bypass active: document accessed under administrative privilege.", control, True

    # 3. Master switch check
    if not control.is_open:
        reason = control.notes or f"Downloads for {control.get_document_type_display()} are currently closed by the Academic Registry."
        return False, reason, control, False

    # 4. Scheduling window check
    now = timezone.now()
    if control.open_date and now < control.open_date:
        reason = f"Downloads for {control.get_document_type_display()} will open on {control.open_date.strftime('%d %b %Y at %H:%M')}."
        return False, reason, control, False

    if control.lock_date and now > control.lock_date:
        reason = f"The download window for {control.get_document_type_display()} closed on {control.lock_date.strftime('%d %b %Y at %H:%M')}."
        return False, reason, control, False

    # 5. Financial Clearance Gate
    if control.require_financial_clearance:
        fin = check_financial_clearance(student, term=term)
        max_allowed = control.max_allowed_fee_balance
        if fin["balance"] > max_allowed:
            reason = (
                f"Document locked due to an outstanding fee balance of KES {fin['balance']:,.2f}. "
                f"Registry policy permits a maximum balance of KES {max_allowed:,.2f} for document issuance."
            )
            return False, reason, control, False

    # 6. Disciplinary / Academic Holds
    if hasattr(student, 'status') and student.status in ['SUSPENDED', 'DISCONTINUED', 'WITHDRAWN']:
        reason = f"Document issuance is locked due to student account status ({student.get_status_display()}). Please contact the Academic Registrar."
        return False, reason, control, False

    return True, "Access granted", control, False
