"""
Go-Live Command Center views: the readiness status board and the
issue/warning ledger, with its full lifecycle
(Severity -> Description -> Impact -> Owner -> Due Date -> Resolution ->
Evidence -> Retest -> Final Status).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from university.models import AuditLog, GoLiveIssue, GoLiveReadiness, IssueStatus
from university.audit_services import log_activity
from university.golive_models import GoLiveCategory, IssueSeverity, ReadinessStatus
from university.golive_services import compute_readiness_summary
from university.permissions_services import has_user_permission

User = get_user_model()


def _golive_admin_required(view_func):
    """Decorator ensuring user is authenticated and authorized for go-live administration."""
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not (request.user.is_admin_role or has_user_permission(request.user, "golive.view") or request.user.is_superuser):
            messages.error(request, "Access restricted. Go-Live Command Center privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapped


def _can_manage(request):
    return bool(request.user.is_admin_role or request.user.is_superuser or has_user_permission(request.user, "golive.manage"))


@login_required
@_golive_admin_required
def golive_dashboard(request):
    summary = compute_readiness_summary()

    issues = GoLiveIssue.objects.select_related("owner", "created_by").order_by("-created_at")
    status_filter = request.GET.get("status")
    severity_filter = request.GET.get("severity")
    category_filter = request.GET.get("category")
    if status_filter:
        issues = issues.filter(status=status_filter)
    if severity_filter:
        issues = issues.filter(severity=severity_filter)
    if category_filter:
        issues = issues.filter(category=category_filter)

    return render(request, "system/golive_dashboard.html", {
        "summary": summary,
        "issues": issues[:200],
        "categories": GoLiveCategory.choices,
        "readiness_statuses": ReadinessStatus.choices,
        "severities": IssueSeverity.choices,
        "issue_statuses": IssueStatus.choices,
        "can_manage": _can_manage(request),
        "status_filter": status_filter or "",
        "severity_filter": severity_filter or "",
        "category_filter": category_filter or "",
        "staff_users": User.objects.filter(is_active=True).order_by("first_name", "last_name")[:500],
    })


@login_required
@_golive_admin_required
@require_POST
def golive_category_update(request, pk):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to update readiness categories.")
        return redirect("university:golive_dashboard")

    row = get_object_or_404(GoLiveReadiness, pk=pk)
    previous_status = row.status

    row.status = request.POST.get("status") or row.status
    owner_id = request.POST.get("owner")
    row.owner_id = owner_id or None
    row.notes = request.POST.get("notes", "").strip()
    row.evidence_url = request.POST.get("evidence_url", "").strip()
    row.updated_by = request.user

    if row.status == ReadinessStatus.PASS_ and request.POST.get("sign_off"):
        row.signed_off_by = request.user
        row.signed_off_at = timezone.now()
    elif row.status != ReadinessStatus.PASS_:
        row.signed_off_by = None
        row.signed_off_at = None

    row.save()

    log_activity(
        request=request, action=AuditLog.Action.UPDATE, module=AuditLog.Module.CONFIG,
        entity="GoLiveReadiness", entity_id=str(row.pk),
        description=f"Go-Live readiness for '{row.get_category_display()}' changed from {previous_status} to {row.status}.",
    )
    messages.success(request, f"Readiness status for '{row.get_category_display()}' updated.")
    return redirect("university:golive_dashboard")


@login_required
@_golive_admin_required
@require_POST
def golive_issue_create(request):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to raise readiness issues.")
        return redirect("university:golive_dashboard")

    description = request.POST.get("description", "").strip()
    if not description:
        messages.error(request, "A description is required to raise a readiness issue.")
        return redirect("university:golive_dashboard")

    issue = GoLiveIssue.objects.create(
        category=request.POST.get("category") or GoLiveCategory.FUNCTIONAL_TESTING,
        severity=request.POST.get("severity") or IssueSeverity.MEDIUM,
        description=description,
        impact=request.POST.get("impact", "").strip(),
        owner_id=request.POST.get("owner") or None,
        due_date=request.POST.get("due_date") or None,
        created_by=request.user,
    )

    log_activity(
        request=request, action=AuditLog.Action.CREATE, module=AuditLog.Module.CONFIG,
        entity="GoLiveIssue", entity_id=str(issue.pk),
        description=f"Go-Live issue raised [{issue.get_severity_display()}] under '{issue.get_category_display()}': {description[:120]}",
    )
    messages.success(request, "Readiness issue logged. It will not be silently ignored -- track it through to Closed/Retested.")
    return redirect("university:golive_dashboard")


@login_required
@_golive_admin_required
@require_POST
def golive_issue_update(request, pk):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to update readiness issues.")
        return redirect("university:golive_dashboard")

    issue = get_object_or_404(GoLiveIssue, pk=pk)
    previous_status = issue.status

    issue.category = request.POST.get("category") or issue.category
    issue.severity = request.POST.get("severity") or issue.severity
    issue.description = request.POST.get("description", issue.description).strip() or issue.description
    issue.impact = request.POST.get("impact", "").strip()
    owner_id = request.POST.get("owner")
    issue.owner_id = owner_id or None
    issue.due_date = request.POST.get("due_date") or None
    issue.resolution = request.POST.get("resolution", "").strip()
    issue.evidence_url = request.POST.get("evidence_url", "").strip()
    issue.retest_result = request.POST.get("retest_result", "").strip()
    issue.status = request.POST.get("status") or issue.status

    if issue.status in (IssueStatus.RESOLVED, IssueStatus.RETESTED, IssueStatus.CLOSED) and not issue.resolved_at:
        issue.resolved_at = timezone.now()
    elif issue.status in (IssueStatus.OPEN, IssueStatus.IN_PROGRESS):
        issue.resolved_at = None

    issue.save()

    log_activity(
        request=request, action=AuditLog.Action.UPDATE, module=AuditLog.Module.CONFIG,
        entity="GoLiveIssue", entity_id=str(issue.pk),
        description=f"Go-Live issue #{issue.pk} status changed from {previous_status} to {issue.status}.",
    )
    messages.success(request, f"Issue #{issue.pk} updated.")
    return redirect("university:golive_dashboard")


@login_required
@_golive_admin_required
@require_POST
def golive_issue_delete(request, pk):
    if not _can_manage(request):
        messages.error(request, "You do not have permission to delete readiness issues.")
        return redirect("university:golive_dashboard")

    issue = get_object_or_404(GoLiveIssue, pk=pk)
    description = issue.description[:120]
    issue_id = issue.pk
    issue.delete()

    log_activity(
        request=request, action=AuditLog.Action.DELETE, module=AuditLog.Module.CONFIG,
        entity="GoLiveIssue", entity_id=str(issue_id),
        description=f"Go-Live issue #{issue_id} deleted: {description}",
    )
    messages.success(request, f"Issue #{issue_id} deleted.")
    return redirect("university:golive_dashboard")
