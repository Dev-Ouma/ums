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


GO_LIVE_SEQUENCE = (
    ("Infrastructure Hardening", (GoLiveCategory.SECURITY,)),
    ("HTTPS/TLS/Security Headers", (GoLiveCategory.SECURITY,)),
    ("Authentication", (GoLiveCategory.SECURITY,)),
    ("Session & Cookie Security", (GoLiveCategory.SECURITY,)),
    ("RBAC/Authorization", (GoLiveCategory.ROLE_PERMISSIONS,)),
    ("Data Protection/GDPR", (GoLiveCategory.DATA_PROTECTION,)),
    ("Database Security", (GoLiveCategory.SECURITY,)),
    ("Payment Security", (GoLiveCategory.FINANCE_PAYMENTS,)),
    ("Backup & Disaster Recovery", (GoLiveCategory.BACKUP, GoLiveCategory.DISASTER_RECOVERY)),
    ("Email/SMS", (GoLiveCategory.INTEGRATIONS,)),
    ("LMS", (GoLiveCategory.INTEGRATIONS,)),
    ("HR/SMHR", (GoLiveCategory.INTEGRATIONS,)),
    ("Finance & Procurement", (GoLiveCategory.FINANCE_PAYMENTS,)),
    ("Library", (GoLiveCategory.INTEGRATIONS,)),
    ("Zoom/Teams/Blackboard/Meetings", (GoLiveCategory.INTEGRATIONS,)),
    ("Integration Synchronization", (GoLiveCategory.INTEGRATIONS,)),
    ("Monitoring & Logging", (GoLiveCategory.MONITORING,)),
    ("Load/Performance Testing", (GoLiveCategory.PERFORMANCE,)),
    ("Penetration/Security Testing", (GoLiveCategory.SECURITY,)),
    ("Disaster-Recovery Test", (GoLiveCategory.DISASTER_RECOVERY,)),
    ("Final User Acceptance Testing", (GoLiveCategory.UAT,)),
    ("GO-LIVE APPROVAL", (GoLiveCategory.INSTITUTIONAL_APPROVAL,)),
)


_SEQUENCE_STATUS_PRIORITY = {
    ReadinessStatus.FAIL: 5,
    ReadinessStatus.WARN: 4,
    ReadinessStatus.IN_PROGRESS: 3,
    ReadinessStatus.NOT_STARTED: 2,
    ReadinessStatus.PASS_: 1,
    ReadinessStatus.NOT_APPLICABLE: 0,
}


def _sequence_status(rows):
    """Return the most restrictive status across the categories behind a step."""
    if not rows:
        return ReadinessStatus.NOT_STARTED
    return max(rows, key=lambda row: _SEQUENCE_STATUS_PRIORITY.get(row.status, 2)).status


def build_go_live_sequence(rows, gate_status):
    """Project the authoritative readiness rows into the ordered certification flow."""
    by_category = {row.category: row for row in rows}
    sequence = []
    for order, (name, categories) in enumerate(GO_LIVE_SEQUENCE, start=1):
        source_rows = [by_category[category] for category in categories if category in by_category]
        status = _sequence_status(source_rows)
        source = next((row for row in source_rows if row.status == status), source_rows[0] if source_rows else None)
        is_approval = order == len(GO_LIVE_SEQUENCE)
        if is_approval:
            status = ReadinessStatus.PASS_ if gate_status == "GREEN" else (
                ReadinessStatus.WARN if gate_status == "AMBER" else ReadinessStatus.FAIL
            )
        sequence.append({
            "order": order,
            "name": name,
            "status": status,
            "status_label": dict(ReadinessStatus.choices).get(status, status),
            "category_label": ", ".join(row.get_category_display() for row in source_rows) or "Final approval",
            "tested_by": source.signed_off_by if source and source.is_signed_off else (source.owner if source else None),
            "tested_at": source.signed_off_at if source and source.is_signed_off else (source.updated_at if source else None),
            "evidence": source.evidence_url if source else "",
            "notes": source.notes if source else "",
            "retest": "See issue ledger" if source and status in (ReadinessStatus.WARN, ReadinessStatus.FAIL) else "",
        })
    return sequence


def ensure_default_categories():
    """Create a GoLiveReadiness row for every known category if missing."""
    existing = set(GoLiveReadiness.objects.values_list("category", flat=True))
    missing = [c for c in GoLiveCategory.values if c not in existing]
    if missing:
        GoLiveReadiness.objects.bulk_create([GoLiveReadiness(category=c) for c in missing])


def compute_readiness_summary():
    """
    Aggregate the status board + issue ledger into the go/no-go scoreboard.
    The formal gate has three outcomes:
    - RED: an unresolved critical/high issue or a failed readiness area.
    - AMBER: no critical/high blocker, but a documented readiness gap remains.
    - GREEN: every applicable area is signed off and no issues remain open.

    Warnings never silently pass. The legacy ``is_ready`` fields are retained
    because other dashboards consume them.
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
    warning_categories = [r for r in rows if r.status == ReadinessStatus.WARN]
    not_ready_reasons = []
    if blocking_categories:
        not_ready_reasons.append(f"{len(blocking_categories)} readiness area(s) marked FAIL")
    if warning_categories:
        not_ready_reasons.append(f"{len(warning_categories)} readiness area(s) marked WARNING")
    if critical_open:
        not_ready_reasons.append(f"{critical_open} open CRITICAL issue(s)")
    if high_open:
        not_ready_reasons.append(f"{high_open} open HIGH issue(s)")
    outstanding = status_counts.get(ReadinessStatus.NOT_STARTED, 0) + status_counts.get(ReadinessStatus.IN_PROGRESS, 0)
    if outstanding:
        not_ready_reasons.append(f"{outstanding} readiness area(s) not yet signed off")
    unsigned_pass = sum(
        1 for row in rows
        if row.status == ReadinessStatus.PASS_ and not row.is_signed_off
    )
    if unsigned_pass:
        not_ready_reasons.append(f"{unsigned_pass} passed readiness area(s) still need authorized sign-off")

    if critical_open or high_open or blocking_categories:
        gate_status = "RED"
        gate_title = "RED - DO NOT LAUNCH"
        gate_reasons = list(not_ready_reasons)
    elif not_ready_reasons or warning_open:
        gate_status = "AMBER"
        gate_title = "AMBER - CONDITIONAL"
        gate_reasons = list(not_ready_reasons)
        if warning_open:
            gate_reasons.append(f"{warning_open} open medium/low issue(s) require approved mitigation")
    else:
        gate_status = "GREEN"
        gate_title = "GREEN - READY"
        gate_reasons = ["No unresolved critical/high issues and all readiness areas are signed off."]

    sequence = build_go_live_sequence(rows, gate_status)

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
        "gate_status": gate_status,
        "gate_title": gate_title,
        "gate_reasons": gate_reasons,
        "gate_definitions": {
            "GREEN": "Ready: no unresolved critical/high issues and all applicable areas are signed off.",
            "AMBER": "Conditional: only non-blocking gaps remain and each has an owner, evidence, and approved mitigation.",
            "RED": "Do not launch: an unresolved critical/high issue or failed readiness area remains.",
        },
        "sequence": sequence,
        "generated_at": timezone.now(),
    }
