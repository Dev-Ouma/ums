"""
Enterprise Backup & Disaster Recovery Views.
Provides comprehensive control for Backup Dashboard, Schedules, History,
Storage Targets, Cryptographic Verification, Safe Restoration, and Operational Logs.
"""

import csv
import io
from datetime import datetime
import json
import logging
import os
import shutil
from typing import Any, Dict

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from accounts.models import Role
from university.models import (
    AuditLog,
    BackupArtifact,
    BackupJob,
    BackupLog,
    BackupRestoreJob,
    BackupRetentionPolicy,
    BackupSchedule,
    BackupSetting,
    BackupStorage,
    BackupVerification,
)
from university.audit_services import log_activity
from university.permissions_services import has_user_permission
from university.backup_services import (
    apply_retention_policies,
    calculate_next_run,
    calculate_recovery_readiness,
    create_backup_job,
    ensure_default_storage_and_retention,
    execute_backup_job,
    execute_restore_job,
    test_storage_connection,
    verify_backup_archive,
)

logger = logging.getLogger(__name__)


def _backup_admin_required(view_func):
    """Decorator ensuring user is authenticated and authorized for backup administration."""
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not (request.user.is_admin_role or has_user_permission(request.user, "backups.view") or request.user.is_superuser):
            messages.error(request, "Access restricted. Backup & Disaster Recovery administration privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapped


# ==============================================================================
# 1. BACKUP DASHBOARD
# ==============================================================================

@login_required
@_backup_admin_required
def backup_dashboard(request):
    """
    Central Backup & Disaster Recovery Cockpit.
    Presents real-time recovery readiness, storage metrics, upcoming runs, and recent jobs.
    """
    ensure_default_storage_and_retention()
    readiness = calculate_recovery_readiness()
    
    # Statistical counters
    total_jobs = BackupJob.objects.count()
    successful_jobs = BackupJob.objects.filter(status=BackupJob.Status.SUCCESSFUL).count()
    failed_jobs = BackupJob.objects.filter(status=BackupJob.Status.FAILED).count()
    running_jobs = BackupJob.objects.filter(status__in=[BackupJob.Status.RUNNING, BackupJob.Status.VERIFYING]).count()
    
    # Storage metrics
    total_bytes = BackupJob.objects.filter(status=BackupJob.Status.SUCCESSFUL).aggregate(s=Sum("file_size_bytes"))["s"] or 0
    total_storage_mb = round(total_bytes / (1024 * 1024), 2)
    
    last_success = BackupJob.objects.filter(status=BackupJob.Status.SUCCESSFUL).order_by("-completed_at").first()
    last_failure = BackupJob.objects.filter(status=BackupJob.Status.FAILED).order_by("-completed_at").first()
    next_scheduled = BackupSchedule.objects.filter(is_active=True, next_run_at__isnull=False).order_by("next_run_at").first()

    recent_jobs = BackupJob.objects.select_related("storage", "schedule", "created_by").order_by("-created_at")[:8]
    active_schedules = BackupSchedule.objects.filter(is_active=True).order_by("next_run_at")[:5]

    return render(request, "system/backups/dashboard.html", {
        "readiness": readiness,
        "total_jobs": total_jobs,
        "successful_jobs": successful_jobs,
        "failed_jobs": failed_jobs,
        "running_jobs": running_jobs,
        "total_storage_mb": total_storage_mb,
        "last_success": last_success,
        "last_failure": last_failure,
        "next_scheduled": next_scheduled,
        "recent_jobs": recent_jobs,
        "active_schedules": active_schedules,
    })


# ==============================================================================
# 2. BACKUP SCHEDULES MANAGEMENT
# ==============================================================================

@login_required
@_backup_admin_required
def backup_schedules(request):
    """Manage recurring automated backup schedules."""
    ensure_default_storage_and_retention()
    schedules = BackupSchedule.objects.select_related("storage_destination", "retention_policy", "created_by").all()
    
    return render(request, "system/backups/schedules.html", {
        "schedules": schedules,
        "storages": BackupStorage.objects.filter(is_active=True),
        "retentions": BackupRetentionPolicy.objects.filter(is_active=True),
    })


@login_required
@_backup_admin_required
def backup_schedule_create(request):
    """Create a new recurring backup schedule."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        backup_type = request.POST.get("backup_type", BackupSchedule.BackupType.FULL)
        frequency = request.POST.get("frequency", BackupSchedule.Frequency.DAILY)
        start_date_str = request.POST.get("start_date", "")
        start_time_str = request.POST.get("start_time", "02:00")
        storage_id = request.POST.get("storage_destination")
        retention_id = request.POST.get("retention_policy")
        compression = request.POST.get("compression", BackupSchedule.Compression.GZIP)
        
        try:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
        except Exception:
            start_time = timezone.now().time()

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else timezone.now().date()
        except Exception:
            start_date = timezone.now().date()

        storage = BackupStorage.objects.filter(pk=storage_id).first() if storage_id else None
        retention = BackupRetentionPolicy.objects.filter(pk=retention_id).first() if retention_id else None

        schedule = BackupSchedule(
            name=name,
            description=description,
            backup_type=backup_type,
            frequency=frequency,
            start_date=start_date,
            start_time=start_time,
            storage_destination=storage,
            retention_policy=retention,
            compression=compression,
            include_database=bool(request.POST.get("include_database")),
            include_uploaded_files=bool(request.POST.get("include_uploaded_files")),
            include_documents=bool(request.POST.get("include_documents")),
            include_system_configuration=bool(request.POST.get("include_system_configuration")),
            include_logs=bool(request.POST.get("include_logs")),
            is_active=True,
            created_by=request.user,
        )
        schedule.next_run_at = calculate_next_run(schedule)
        schedule.save()

        messages.success(request, f"Backup schedule '{schedule.name}' created. Next execution at {schedule.next_run_at.strftime('%d %b %Y %H:%M')}.")
        return redirect("university:backup_schedules")

    return render(request, "system/backups/schedule_form.html", {
        "storages": BackupStorage.objects.filter(is_active=True),
        "retentions": BackupRetentionPolicy.objects.filter(is_active=True),
        "frequencies": BackupSchedule.Frequency.choices,
        "backup_types": BackupSchedule.BackupType.choices,
        "compressions": BackupSchedule.Compression.choices,
    })


@login_required
@_backup_admin_required
def backup_schedule_edit(request, pk):
    """Edit an existing backup schedule."""
    schedule = get_object_or_404(BackupSchedule, pk=pk)

    if request.method == "POST":
        schedule.name = request.POST.get("name", schedule.name).strip()
        schedule.description = request.POST.get("description", "").strip()
        schedule.backup_type = request.POST.get("backup_type", schedule.backup_type)
        schedule.frequency = request.POST.get("frequency", schedule.frequency)
        schedule.compression = request.POST.get("compression", schedule.compression)
        
        storage_id = request.POST.get("storage_destination")
        retention_id = request.POST.get("retention_policy")
        schedule.storage_destination = BackupStorage.objects.filter(pk=storage_id).first() if storage_id else None
        schedule.retention_policy = BackupRetentionPolicy.objects.filter(pk=retention_id).first() if retention_id else None

        schedule.include_database = bool(request.POST.get("include_database"))
        schedule.include_uploaded_files = bool(request.POST.get("include_uploaded_files"))
        schedule.include_documents = bool(request.POST.get("include_documents"))
        schedule.include_system_configuration = bool(request.POST.get("include_system_configuration"))
        schedule.include_logs = bool(request.POST.get("include_logs"))

        schedule.next_run_at = calculate_next_run(schedule)
        schedule.save()

        messages.success(request, f"Backup schedule '{schedule.name}' updated.")
        return redirect("university:backup_schedules")

    return render(request, "system/backups/schedule_form.html", {
        "schedule": schedule,
        "storages": BackupStorage.objects.filter(is_active=True),
        "retentions": BackupRetentionPolicy.objects.filter(is_active=True),
        "frequencies": BackupSchedule.Frequency.choices,
        "backup_types": BackupSchedule.BackupType.choices,
        "compressions": BackupSchedule.Compression.choices,
    })


@login_required
@_backup_admin_required
@require_POST
def backup_schedule_toggle(request, pk):
    """Toggles active state of a schedule."""
    schedule = get_object_or_404(BackupSchedule, pk=pk)
    schedule.is_active = not schedule.is_active
    if schedule.is_active:
        schedule.next_run_at = calculate_next_run(schedule)
    schedule.save(update_fields=["is_active", "next_run_at"])
    
    state_str = "activated" if schedule.is_active else "paused"
    messages.info(request, f"Schedule '{schedule.name}' {state_str}.")
    return redirect("university:backup_schedules")


@login_required
@_backup_admin_required
@require_POST
def backup_schedule_run_now(request, pk):
    """Immediately triggers execution of a scheduled backup."""
    schedule = get_object_or_404(BackupSchedule, pk=pk)
    job = create_backup_job(
        backup_type=schedule.backup_type,
        trigger_type=BackupJob.TriggerType.MANUAL,
        schedule=schedule,
        storage=schedule.storage_destination,
        created_by=request.user,
    )
    execute_backup_job(job.pk)
    schedule.last_run_at = timezone.now()
    schedule.next_run_at = calculate_next_run(schedule)
    schedule.save(update_fields=["last_run_at", "next_run_at"])

    messages.success(request, f"Scheduled backup '{schedule.name}' executed: {job.backup_id}.")
    return redirect("university:backup_detail", pk=job.pk)


@login_required
@_backup_admin_required
@require_POST
def backup_schedule_delete(request, pk):
    """Deletes a backup schedule."""
    schedule = get_object_or_404(BackupSchedule, pk=pk)
    name = schedule.name
    schedule.delete()
    messages.success(request, f"Backup schedule '{name}' removed.")
    return redirect("university:backup_schedules")


# ==============================================================================
# 3. BACKUP HISTORY & EXECUTION
# ==============================================================================

@login_required
@_backup_admin_required
def backup_history(request):
    """
    Searchable, filterable backup job history table.
    """
    qs = BackupJob.objects.select_related("storage", "schedule", "created_by").order_by("-created_at")

    # Filters
    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)

    b_type = request.GET.get("type", "").strip()
    if b_type:
        qs = qs.filter(backup_type=b_type)

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(backup_id__icontains=search) | Q(schedule__name__icontains=search))

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "system/backups/history.html", {
        "jobs": page_obj,
        "statuses": BackupJob.Status.choices,
        "types": BackupSchedule.BackupType.choices,
        "selected_status": status,
        "selected_type": b_type,
        "search_query": search,
        "storages": BackupStorage.objects.filter(is_active=True),
    })


@login_required
@_backup_admin_required
@require_POST
def backup_create_now(request):
    """
    Creates and executes an immediate on-demand backup.
    """
    backup_type = request.POST.get("backup_type", BackupSchedule.BackupType.FULL)
    storage_id = request.POST.get("storage_destination")
    storage = BackupStorage.objects.filter(pk=storage_id).first() if storage_id else None
    
    is_protected = bool(request.POST.get("is_protected"))
    
    custom_inclusions = None
    if backup_type == BackupSchedule.BackupType.CUSTOM:
        custom_inclusions = {
            "database": bool(request.POST.get("include_database")),
            "files": bool(request.POST.get("include_files")),
            "config": bool(request.POST.get("include_config")),
            "logs": bool(request.POST.get("include_logs")),
        }

    job = create_backup_job(
        backup_type=backup_type,
        trigger_type=BackupJob.TriggerType.MANUAL,
        storage=storage,
        created_by=request.user,
        is_protected=is_protected,
        custom_inclusions=custom_inclusions,
    )
    execute_backup_job(job.pk)

    if job.status == BackupJob.Status.SUCCESSFUL:
        messages.success(request, f"Backup {job.backup_id} completed successfully ({job.file_size_display}).")
    else:
        messages.error(request, f"Backup {job.backup_id} failed: {job.error_message}")

    return redirect("university:backup_detail", pk=job.pk)


@login_required
@_backup_admin_required
def backup_detail(request, pk):
    """
    Detailed inspection of an individual backup job:
    File specs, checksum, component artifacts, verification history, and logs.
    """
    job = get_object_or_404(BackupJob.objects.select_related("storage", "schedule", "created_by"), pk=pk)
    artifacts = job.artifacts.all()
    verifications = job.verifications.order_by("-verified_at")
    logs = job.operational_logs.order_by("timestamp")

    return render(request, "system/backups/detail.html", {
        "job": job,
        "artifacts": artifacts,
        "verifications": verifications,
        "logs": logs,
    })


@login_required
@_backup_admin_required
@require_POST
def backup_verify(request, pk):
    """Triggers on-demand cryptographic and structural integrity verification."""
    job = get_object_or_404(BackupJob, pk=pk)
    v = verify_backup_archive(job.pk, user=request.user)
    
    if v.status == BackupVerification.Status.PASSED:
        messages.success(request, f"Integrity verification PASSED for {job.backup_id} ({v.files_count} files confirmed).")
    else:
        messages.error(request, f"Integrity verification FAILED for {job.backup_id}: {v.errors}")

    return redirect("university:backup_detail", pk=job.pk)


@login_required
@_backup_admin_required
def backup_download(request, pk):
    """
    Secure streaming download of a verified backup archive.
    """
    if not (request.user.is_superuser or request.user.role == Role.ADMIN or has_user_permission(request.user, "backups.download")):
        messages.error(request, "Permission denied. Elevated authorization required to download backup archives.")
        return redirect("university:backup_detail", pk=pk)

    job = get_object_or_404(BackupJob, pk=pk)
    if not job.is_available:
        messages.error(request, f"Archive file {job.backup_id} is not accessible on storage.")
        return redirect("university:backup_detail", pk=pk)

    log_activity(
        user=request.user,
        action=AuditLog.Action.GENERATE_DOCUMENT,
        module=AuditLog.Module.BACKUPS,
        entity="BackupJob",
        entity_id=job.backup_id,
        description=f"Downloaded backup archive {job.backup_id} ({job.file_size_display}).",
    )

    filename = os.path.basename(job.archive_path)
    response = FileResponse(open(job.archive_path, "rb"), as_attachment=True, filename=filename)
    response["Content-Length"] = job.file_size_bytes
    return response


@login_required
@_backup_admin_required
@require_POST
def backup_delete(request, pk):
    """
    Permanently removes a backup archive from storage.
    Requires elevated permission.
    """
    if not (request.user.is_superuser or request.user.role == Role.ADMIN or has_user_permission(request.user, "backups.delete")):
        messages.error(request, "Permission denied. Elevated authorization required to delete backups.")
        return redirect("university:backup_history")

    job = get_object_or_404(BackupJob, pk=pk)
    backup_id = job.backup_id
    
    if job.archive_path and os.path.exists(job.archive_path):
        try:
            os.remove(job.archive_path)
        except Exception as e:
            logger.warning(f"Failed deleting physical file {job.archive_path}: {e}")

    job.delete()

    log_activity(
        user=request.user,
        action=AuditLog.Action.BACKUP_DELETE,
        module=AuditLog.Module.BACKUPS,
        entity="BackupJob",
        entity_id=backup_id,
        description=f"Permanently deleted backup archive {backup_id}.",
    )

    messages.success(request, f"Backup {backup_id} permanently removed.")
    return redirect("university:backup_history")


@login_required
@_backup_admin_required
@require_POST
def backup_toggle_protect(request, pk):
    """Toggle retention protection lock."""
    job = get_object_or_404(BackupJob, pk=pk)
    job.is_protected = not job.is_protected
    job.save(update_fields=["is_protected"])
    
    state_str = "Protected from retention cleanup" if job.is_protected else "Standard retention applied"
    messages.info(request, f"Backup {job.backup_id}: {state_str}.")
    return redirect("university:backup_detail", pk=job.pk)


# ==============================================================================
# 4. STORAGE DESTINATIONS
# ==============================================================================

@login_required
@_backup_admin_required
def backup_storage(request):
    """View and configure backup storage targets."""
    ensure_default_storage_and_retention()
    storages = BackupStorage.objects.annotate(job_count=Count("jobs")).all()

    return render(request, "system/backups/storage.html", {
        "storages": storages,
        "storage_types": BackupStorage.StorageType.choices,
    })


@login_required
@_backup_admin_required
@require_POST
def backup_storage_test(request, pk):
    """Tests connection to a specific storage target."""
    storage = get_object_or_404(BackupStorage, pk=pk)
    ok, msg = test_storage_connection(storage)
    if ok:
        messages.success(request, f"Storage test PASSED: {msg}")
    else:
        messages.error(request, f"Storage test FAILED: {msg}")
    return redirect("university:backup_storage")


# ==============================================================================
# 5. DISASTER RECOVERY & SYSTEM RESTORATION
# ==============================================================================

@login_required
@_backup_admin_required
def backup_restore_dashboard(request):
    """
    Disaster Recovery & System Restoration Control Center.
    Presents verified restore points, restore compatibility metrics, and restoration history.
    """
    available_points = BackupJob.objects.filter(
        status=BackupJob.Status.SUCCESSFUL,
    ).exclude(archive_path="").order_by("-created_at")

    restore_history = BackupRestoreJob.objects.select_related("backup", "requested_by", "approved_by").order_by("-created_at")[:15]

    return render(request, "system/backups/restore.html", {
        "available_points": available_points,
        "restore_history": restore_history,
        "restore_types": BackupRestoreJob.RestoreType.choices,
    })


@login_required
@_backup_admin_required
@require_POST
def backup_restore_start(request):
    """
    Executes safe disaster recovery restoration.
    Mandates:
      - Elevated authorization (`Role.ADMIN` or superuser).
      - Explicit safety confirmation token.
      - Reason documentation.
    """
    if not (request.user.is_superuser or request.user.role == Role.ADMIN or has_user_permission(request.user, "backups.restore")):
        messages.error(request, "Permission denied. Elevated System Administrator role required for disaster recovery.")
        return redirect("university:backup_restore_dashboard")

    job_id = request.POST.get("backup_id")
    restore_type = request.POST.get("restore_type", BackupRestoreJob.RestoreType.FULL)
    confirm_text = request.POST.get("confirmation_text", "").strip()
    reason = request.POST.get("reason", "").strip()

    if confirm_text != "RESTORE-SYSTEM-DATABASE":
        messages.error(request, "Restoration aborted: Confirmation safety phrase was invalid. You must type 'RESTORE-SYSTEM-DATABASE'.")
        return redirect("university:backup_restore_dashboard")

    if not reason:
        messages.error(request, "Restoration aborted: An institutional justification reason must be documented.")
        return redirect("university:backup_restore_dashboard")

    backup = get_object_or_404(BackupJob, pk=job_id)
    if not backup.is_available:
        messages.error(request, f"Cannot restore: Backup archive {backup.backup_id} is unavailable or corrupted.")
        return redirect("university:backup_restore_dashboard")

    rand_suffix = hashlib.md5(f"{time.time()}".encode("utf-8")).hexdigest()[:6].upper()
    restore_id = f"RST-{timezone.now().strftime('%Y%m%d-%H%M')}-{rand_suffix}"

    restore_job = BackupRestoreJob.objects.create(
        restore_id=restore_id,
        backup=backup,
        restore_type=restore_type,
        requested_by=request.user,
        approved_by=request.user,
        reason=reason,
    )

    # Execute restore procedure
    execute_restore_job(restore_job.pk, user=request.user)

    if restore_job.status == BackupRestoreJob.Status.COMPLETED:
        messages.success(request, f"Disaster Recovery {restore_id} completed successfully. System restored from {backup.backup_id}.")
    else:
        messages.error(request, f"Disaster Recovery {restore_id} encountered errors: {restore_job.error_message}")

    return redirect("university:backup_restore_dashboard")


# ==============================================================================
# 6. SETTINGS & OPERATIONAL LOGS
# ==============================================================================

@login_required
@_backup_admin_required
def backup_settings(request):
    """Configures global backup policies, retention rules, and notification alerts."""
    settings_obj = BackupSetting.get_settings()
    
    if request.method == "POST":
        storage_id = request.POST.get("default_storage")
        retention_id = request.POST.get("default_retention")
        
        settings_obj.default_storage = BackupStorage.objects.filter(pk=storage_id).first() if storage_id else None
        settings_obj.default_retention = BackupRetentionPolicy.objects.filter(pk=retention_id).first() if retention_id else None
        settings_obj.storage_warning_threshold_percent = int(request.POST.get("storage_warning_threshold", 85))
        settings_obj.health_overdue_threshold_hours = int(request.POST.get("health_overdue_threshold", 36))
        settings_obj.require_maintenance_on_restore = bool(request.POST.get("require_maintenance_on_restore"))
        settings_obj.rpo_minutes = int(request.POST.get("rpo_minutes", 15))
        settings_obj.rto_minutes = int(request.POST.get("rto_minutes", 120))
        settings_obj.disaster_recovery_procedure = request.POST.get("disaster_recovery_procedure", "").strip()
        settings_obj.notify_on_failure = bool(request.POST.get("notify_on_failure"))
        settings_obj.notify_on_success = bool(request.POST.get("notify_on_success"))
        settings_obj.notification_emails = request.POST.get("notification_emails", "").strip()
        settings_obj.save()

        messages.success(request, "Backup global settings saved.")
        return redirect("university:backup_settings")

    return render(request, "system/backups/settings.html", {
        "settings": settings_obj,
        "storages": BackupStorage.objects.filter(is_active=True),
        "retentions": BackupRetentionPolicy.objects.filter(is_active=True),
    })


@login_required
@_backup_admin_required
def backup_logs(request):
    """View granular operational logs for all backup executions."""
    qs = BackupLog.objects.select_related("job", "restore_job").order_by("-timestamp")
    
    level = request.GET.get("level", "").strip()
    if level:
        qs = qs.filter(level=level)

    component = request.GET.get("component", "").strip()
    if component:
        qs = qs.filter(component__icontains=component)

    paginator = Paginator(qs, 40)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "system/backups/logs.html", {
        "logs": page_obj,
        "levels": BackupLog.Level.choices,
        "selected_level": level,
        "component_query": component,
    })


# ==============================================================================
# 7. EXPORTS & REPORTING
# ==============================================================================

@login_required
@_backup_admin_required
def backup_export(request):
    """Exports backup ledger history to CSV or Excel (.xlsx)."""
    fmt = request.GET.get("format", "csv").lower()
    jobs = BackupJob.objects.select_related("storage", "schedule", "created_by").order_by("-created_at")

    headers = ["Backup ID", "Type", "Trigger", "Status", "Started At", "Completed At", "Duration (s)", "Size (Bytes)", "Checksum SHA-256", "Storage"]
    rows = []
    for j in jobs:
        rows.append([
            j.backup_id,
            j.get_backup_type_display(),
            j.get_trigger_type_display(),
            j.status,
            j.started_at.strftime("%Y-%m-%d %H:%M:%S") if j.started_at else "",
            j.completed_at.strftime("%Y-%m-%d %H:%M:%S") if j.completed_at else "",
            str(j.duration_seconds),
            str(j.file_size_bytes),
            j.checksum_sha256,
            j.storage.name if j.storage else "Default",
        ])

    if fmt in ["xlsx", "excel"]:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Backup History"
        ws.append(headers)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="UMS_Backups_Export_{timezone.now().strftime("%Y%m%d")}.xlsx"'
        return resp

    # CSV Export
    from university.document_design import build_clean_csv
    csv_bytes = build_clean_csv(headers, rows)
    resp = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="UMS_Backups_Export_{timezone.now().strftime("%Y%m%d")}.csv"'
    return resp
