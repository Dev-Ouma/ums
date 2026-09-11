"""Central monitoring snapshot built from UMS audit and operational logs."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from university.audit_services import log_activity
from university.models import AuditLog
from university.backup_models import BackupLog


MONITORING_AREAS = (
    ("authentication", "Authentication failures", "fa-fingerprint", "audit"),
    ("server", "Server errors", "fa-server", "audit"),
    ("database", "Database errors", "fa-database", "audit"),
    ("api", "API failures", "fa-code", "audit"),
    ("integrations", "Integration failures", "fa-plug-circle-bolt", "audit"),
    ("payments", "Payment failures", "fa-money-check-dollar", "audit"),
    ("webhooks", "Webhook failures", "fa-link-slash", "audit"),
    ("jobs", "Background jobs", "fa-gears", "audit"),
    ("queues", "Queue failures", "fa-layer-group", "audit"),
    ("backups", "Backup failures", "fa-database", "backup"),
    ("storage", "Storage failures", "fa-hard-drive", "backup"),
    ("suspicious", "Suspicious activity", "fa-triangle-exclamation", "audit"),
)


def _audit_count(query):
    return AuditLog.objects.filter(timestamp__gte=timezone.now() - timedelta(days=30)).filter(query).count()


def build_monitoring_snapshot():
    """Return a redacted, last-30-day monitoring view for administrators."""
    failure = Q(description__icontains="fail") | Q(description__icontains="error") | Q(description__icontains="reject")
    rows = []
    for key, label, icon, source in MONITORING_AREAS:
        if key == "authentication":
            count = _audit_count(Q(action=AuditLog.Action.FAILED_LOGIN))
            state = "warning" if count else "healthy"
        elif key == "payments":
            count = _audit_count(Q(module=AuditLog.Module.FEES) & failure)
            state = "warning" if count else "healthy"
        elif key == "webhooks":
            count = _audit_count(Q(module=AuditLog.Module.FEES) & (Q(entity__icontains="webhook") | failure))
            state = "warning" if count else "healthy"
        elif key == "integrations":
            count = _audit_count(Q(description__icontains="integration") & failure)
            state = "warning" if count else "pending"
        elif key in {"server", "database", "api", "jobs", "queues", "suspicious"}:
            count = _audit_count(failure & Q(module__in=[AuditLog.Module.AUTH, AuditLog.Module.CONFIG, AuditLog.Module.MODULE_MGMT]))
            state = "warning" if count else "instrumented"
        else:
            count = BackupLog.objects.filter(
                timestamp__gte=timezone.now() - timedelta(days=30),
                level=BackupLog.Level.ERROR,
            ).count()
            state = "warning" if count else "healthy"
        rows.append({"key": key, "label": label, "icon": icon, "count": count, "state": state, "source": source})

    return {
        "rows": rows,
        "window": "Last 30 days",
        "total_events": sum(row["count"] for row in rows),
        "last_audit_event": AuditLog.objects.order_by("-timestamp").first(),
        "last_backup_event": BackupLog.objects.order_by("-timestamp").first(),
    }
