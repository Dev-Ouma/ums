"""Operational inventory for server-side scheduled and event-driven jobs."""

from datetime import timedelta

from django.utils import timezone

from university.backup_models import BackupJob, BackupSchedule
from university.control_models import ControlHeartbeat, MessageDelivery
from university.models import AuditLog


JOB_DEFINITIONS = (
    ("scheduled_backups", "Scheduled backups", "fa-database", "scheduled", "scheduler"),
    ("email_queue", "Email notifications", "fa-envelope", "queue", "delivery"),
    ("sms_queue", "SMS notifications", "fa-comment-sms", "event-driven", None),
    ("payment_verification", "Payment verification", "fa-money-check-dollar", "event-driven", None),
    ("webhook_processing", "Webhook processing", "fa-link", "event-driven", None),
    ("integration_sync", "Integration synchronization", "fa-plug-circle-bolt", "pending", None),
    ("notifications", "In-app notifications", "fa-bell", "queue", "delivery"),
    ("report_generation", "Report generation", "fa-chart-pie", "request/queue", None),
    ("reconciliation", "Payment reconciliation", "fa-arrows-rotate", "event-driven", None),
    ("retention_cleanup", "Retention cleanup", "fa-broom", "scheduled", "scheduler"),
    ("maintenance", "Maintenance scheduling", "fa-screwdriver-wrench", "scheduled", "scheduler"),
)


def _heartbeat_status(key):
    heartbeat = ControlHeartbeat.objects.filter(key=key).first()
    if not heartbeat:
        return "not started", None
    age = timezone.now() - heartbeat.last_success_at
    return ("healthy" if age <= timedelta(minutes=2) else "stale"), heartbeat.last_success_at


def build_job_snapshot():
    active_schedules = BackupSchedule.objects.filter(is_active=True).count()
    pending_email = MessageDelivery.objects.filter(method="EMAIL", status="PENDING").count()
    rows = []
    for key, label, icon, mode, heartbeat_key in JOB_DEFINITIONS:
        if key == "integration_sync":
            status, last_run = "pending", None
            detail = "No provider synchronization adapter is configured."
        elif heartbeat_key:
            status, last_run = _heartbeat_status(heartbeat_key)
            detail = "Server-side heartbeat"
            if key == "scheduled_backups" and not active_schedules:
                status, detail = "warning", "No active backup schedule"
            if key == "email_queue" and pending_email:
                detail = f"{pending_email} pending email deliveries"
        else:
            status, last_run = "ready", None
            detail = "Backend event-driven handler"
        rows.append({"key": key, "label": label, "icon": icon, "mode": mode, "status": status, "detail": detail, "last_run": last_run})

    return {
        "rows": rows,
        "recent_failures": BackupJob.objects.filter(status=BackupJob.Status.FAILED, created_at__gte=timezone.now() - timedelta(days=7)).count(),
        "last_audit_event": AuditLog.objects.order_by("-timestamp").first(),
    }
