"""
Go-Live Command Center services: readiness board bootstrapping and the
aggregate summary used to render the final PASS/WARN/FAIL scoreboard.
"""

from django.utils import timezone

from university.golive_models import (
    GoLiveCategory,
    GoLiveIssue,
    GoLiveReadiness,
    IssueStatus,
    ReadinessStatus,
)


def ensure_default_categories():
    """Create a GoLiveReadiness row for every known category if missing."""
    existing = set(GoLiveReadiness.objects.values_list("category", flat=True))
    missing = [c for c in GoLiveCategory.values if c not in existing]
    if missing:
        GoLiveReadiness.objects.bulk_create([GoLiveReadiness(category=c) for c in missing])


def compute_readiness_summary():
    """
    Aggregate the status board + issue ledger into the go/no-go scoreboard.
    Overall readiness requires every category to be PASS or NOT_APPLICABLE
    and no open CRITICAL/HIGH issue -- warnings never silently pass.
    """
    ensure_default_categories()
    rows = list(GoLiveReadiness.objects.select_related("owner", "signed_off_by").order_by("category"))

    status_counts = {choice: 0 for choice in ReadinessStatus.values}
    for row in rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1

    open_issues = GoLiveIssue.objects.exclude(status__in=[IssueStatus.CLOSED, IssueStatus.RETESTED])
    critical_open = open_issues.filter(severity="CRITICAL").count()
    high_open = open_issues.filter(severity="HIGH").count()
    warning_open = open_issues.filter(severity__in=["MEDIUM", "LOW"]).count()
    overdue_open = sum(1 for i in open_issues if i.is_overdue)

    blocking_categories = [r for r in rows if r.status == ReadinessStatus.FAIL]
    not_ready_reasons = []
    if blocking_categories:
        not_ready_reasons.append(f"{len(blocking_categories)} readiness area(s) marked FAIL")
    if critical_open:
        not_ready_reasons.append(f"{critical_open} open CRITICAL issue(s)")
    if high_open:
        not_ready_reasons.append(f"{high_open} open HIGH issue(s)")
    outstanding = status_counts.get(ReadinessStatus.NOT_STARTED, 0) + status_counts.get(ReadinessStatus.IN_PROGRESS, 0)
    if outstanding:
        not_ready_reasons.append(f"{outstanding} readiness area(s) not yet signed off")

    return {
        "rows": rows,
        "status_counts": status_counts,
        "total_categories": len(rows),
        "critical_open": critical_open,
        "high_open": high_open,
        "warning_open": warning_open,
        "overdue_open": overdue_open,
        "total_open_issues": open_issues.count(),
        "is_ready": not not_ready_reasons,
        "not_ready_reasons": not_ready_reasons,
        "generated_at": timezone.now(),
    }
