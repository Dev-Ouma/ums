"""
Persistent Models for System Backups, Recurring Schedules, Storage Destinations,
Integrity Verification, Automated Retention, and Safe Disaster Recovery.
"""

from decimal import Decimal
import os
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class BackupStorage(models.Model):
    """
    Storage destination for backup archives (Local Disk, Network Mount, or S3-compatible).
    """
    class StorageType(models.TextChoices):
        LOCAL = "LOCAL", "Local Server Storage"
        NETWORK_MOUNT = "NETWORK_MOUNT", "Configured Network Mount / NAS"
        S3_COMPATIBLE = "S3_COMPATIBLE", "S3-Compatible Cloud Storage"

    name = models.CharField(max_length=120, help_text="Human-readable storage target label")
    storage_type = models.CharField(max_length=30, choices=StorageType.choices, default=StorageType.LOCAL)
    destination_path = models.CharField(
        max_length=255,
        default="backups",
        help_text="Directory path for local/network storage or bucket path for S3"
    )
    is_default = models.BooleanField(default=False, help_text="Default destination for automated backups")
    is_active = models.BooleanField(default=True)
    capacity_bytes = models.BigIntegerField(default=0, help_text="Storage limit in bytes (0 for unconstrained / auto-detect)")
    used_bytes = models.BigIntegerField(default=0)
    encryption_at_rest = models.BooleanField(default=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(max_length=20, default="UNTESTED")
    last_test_message = models.TextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True, help_text="Storage parameters (bucket, endpoint, region)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = "Backup Storage Destination"
        verbose_name_plural = "Backup Storage Destinations"

    def __str__(self):
        return f"{self.name} ({self.get_storage_type_display()})"

    def save(self, *args, **kwargs):
        if self.is_default:
            BackupStorage.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def absolute_target_path(self):
        if os.path.isabs(self.destination_path):
            return self.destination_path
        return os.path.join(settings.BASE_DIR, self.destination_path)


class BackupRetentionPolicy(models.Model):
    """
    Retention policies governing automated pruning of aged backups.
    """
    class PolicyType(models.TextChoices):
        KEEP_COUNT = "KEEP_COUNT", "Keep Fixed Number of Recent Backups"
        KEEP_DAYS = "KEEP_DAYS", "Keep Backups for Fixed Days"
        TIERED_GFS = "TIERED_GFS", "Tiered Grandfather-Father-Son (Daily/Weekly/Monthly)"
        CUSTOM = "CUSTOM", "Custom Retention Rule"

    name = models.CharField(max_length=120)
    policy_type = models.CharField(max_length=30, choices=PolicyType.choices, default=PolicyType.KEEP_COUNT)
    keep_last_n = models.PositiveIntegerField(default=14, help_text="Number of most recent backups to retain")
    keep_daily_days = models.PositiveIntegerField(default=30, help_text="Days to keep daily backups")
    keep_weekly_weeks = models.PositiveIntegerField(default=12, help_text="Weeks to keep weekly backups")
    keep_monthly_months = models.PositiveIntegerField(default=12, help_text="Months to keep monthly archives")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = "Backup Retention Policy"
        verbose_name_plural = "Backup Retention Policies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default:
            BackupRetentionPolicy.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class BackupSchedule(models.Model):
    """
    Recurring automated backup schedules executed autonomously by the background engine.
    """
    class BackupType(models.TextChoices):
        FULL = "FULL", "Full System (Database + Files + Config)"
        DATABASE = "DATABASE", "Database Only"
        FILES = "FILES", "Uploaded Documents & Media Only"
        CONFIG = "CONFIG", "System Configuration Only"
        CUSTOM = "CUSTOM", "Custom Selected Components"

    class Frequency(models.TextChoices):
        ONCE = "ONCE", "Run Once at Scheduled Time"
        HOURLY = "HOURLY", "Every Hour"
        EVERY_6_HOURS = "EVERY_6_HOURS", "Every 6 Hours"
        EVERY_12_HOURS = "EVERY_12_HOURS", "Every 12 Hours"
        DAILY = "DAILY", "Daily (Once per day)"
        WEEKLY = "WEEKLY", "Weekly (Once per week)"
        MONTHLY = "MONTHLY", "Monthly (Once per month)"
        CUSTOM_CRON = "CUSTOM_CRON", "Custom Cron Expression"

    class Compression(models.TextChoices):
        GZIP = "GZIP", "GZIP (.tar.gz) - Balanced & Fast"
        BZIP2 = "BZIP2", "BZIP2 (.tar.bz2) - High Compression"
        ZIP = "ZIP", "Standard ZIP (.zip)"
        NONE = "NONE", "Uncompressed Archive"

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    backup_type = models.CharField(max_length=20, choices=BackupType.choices, default=BackupType.FULL)
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.DAILY)
    cron_expression = models.CharField(max_length=64, blank=True, default="", help_text="e.g. '0 2 * * *' for custom schedules")
    start_date = models.DateField(default=timezone.now)
    start_time = models.TimeField(default=timezone.now)
    time_zone = models.CharField(max_length=64, default="Africa/Nairobi")
    storage_destination = models.ForeignKey(BackupStorage, on_delete=models.SET_NULL, null=True, blank=True, related_name="schedules")
    retention_policy = models.ForeignKey(BackupRetentionPolicy, on_delete=models.SET_NULL, null=True, blank=True, related_name="schedules")
    compression = models.CharField(max_length=20, choices=Compression.choices, default=Compression.GZIP)
    encryption_enabled = models.BooleanField(default=False)
    
    # Inclusions
    include_database = models.BooleanField(default=True)
    include_uploaded_files = models.BooleanField(default=True)
    include_documents = models.BooleanField(default=True)
    include_system_configuration = models.BooleanField(default=True)
    include_logs = models.BooleanField(default=True)
    include_application_data = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    missed_runs_count = models.PositiveIntegerField(default=0)
    retry_on_failure = models.BooleanField(default=True)
    max_retries = models.PositiveIntegerField(default=3)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "next_run_at", "name"]
        verbose_name = "Backup Schedule"
        verbose_name_plural = "Backup Schedules"

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"


class BackupJob(models.Model):
    """
    Historical execution record of an individual backup job.
    """
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        VERIFYING = "VERIFYING", "Verifying Integrity"
        SUCCESSFUL = "SUCCESSFUL", "Successful"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired / Pruned"
        RESTORING = "RESTORING", "Restoring System"
        RESTORE_FAILED = "RESTORE_FAILED", "Restore Failed"
        RESTORE_SUCCESSFUL = "RESTORE_SUCCESSFUL", "Restore Completed"

    class TriggerType(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Automated Schedule"
        MANUAL = "MANUAL", "Manual On-Demand"
        PRE_RESTORE = "PRE_RESTORE", "Safety Pre-Restore Snapshot"
        SYSTEM_EVENT = "SYSTEM_EVENT", "System Event / Upgrade"

    backup_id = models.CharField(max_length=64, unique=True, db_index=True)
    schedule = models.ForeignKey(BackupSchedule, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    backup_type = models.CharField(max_length=20, choices=BackupSchedule.BackupType.choices, default=BackupSchedule.BackupType.FULL)
    trigger_type = models.CharField(max_length=20, choices=TriggerType.choices, default=TriggerType.MANUAL)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.QUEUED, db_index=True)
    
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(default=0.0)
    file_size_bytes = models.BigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    
    storage = models.ForeignKey(BackupStorage, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    archive_path = models.CharField(max_length=255, blank=True, default="")
    compression = models.CharField(max_length=20, choices=BackupSchedule.Compression.choices, default=BackupSchedule.Compression.GZIP)
    encryption_enabled = models.BooleanField(default=False)
    is_protected = models.BooleanField(default=False, help_text="Protected from automated retention pruning")

    included_components = models.JSONField(default=list, blank=True)
    database_engine = models.CharField(max_length=64, blank=True, default="")
    database_version = models.CharField(max_length=64, blank=True, default="")
    application_version = models.CharField(max_length=64, default="2.4.0")

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "UNVERIFIED", "Unverified"
        PASSED = "PASSED", "Verification Passed"
        WARNING = "WARNING", "Warning"
        FAILED = "FAILED", "Verification Failed"

    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.UNVERIFIED)
    verification_notes = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    retry_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Backup Job"
        verbose_name_plural = "Backup Jobs"

    def __str__(self):
        return f"{self.backup_id} [{self.status}]"

    @property
    def file_size_display(self):
        size = self.file_size_bytes
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:,.2f} {unit}"
            size /= 1024.0
        return f"{size:,.2f} B"

    @property
    def is_available(self):
        return bool(self.archive_path and os.path.exists(self.archive_path) and self.status == self.Status.SUCCESSFUL)


class BackupArtifact(models.Model):
    """
    Specific component artifact stored inside the backup archive.
    """
    job = models.ForeignKey(BackupJob, on_delete=models.CASCADE, related_name="artifacts")
    name = models.CharField(max_length=150)
    component_type = models.CharField(max_length=30)
    size_bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, default="")
    path_within_archive = models.CharField(max_length=255)

    class Meta:
        ordering = ["job", "name"]

    def __str__(self):
        return f"{self.job.backup_id} / {self.name}"


class BackupVerification(models.Model):
    """
    Cryptographic and structural verification log for backup archives.
    """
    class Status(models.TextChoices):
        PASSED = "PASSED", "Verification Passed"
        WARNING = "WARNING", "Warning Detected"
        FAILED = "FAILED", "Failed Integrity Check"

    job = models.ForeignKey(BackupJob, on_delete=models.CASCADE, related_name="verifications")
    verified_at = models.DateTimeField(default=timezone.now)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PASSED)
    archive_integrity = models.BooleanField(default=True)
    checksum_match = models.BooleanField(default=True)
    db_syntax_valid = models.BooleanField(default=True)
    files_count = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(default=0.0)
    details = models.JSONField(default=dict, blank=True)
    errors = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-verified_at"]

    def __str__(self):
        return f"Verify {self.job.backup_id} [{self.status}]"


class BackupRestoreJob(models.Model):
    """
    Safety-governed Disaster Recovery and Restoration Execution Record.
    """
    class Status(models.TextChoices):
        PENDING_CONFIRMATION = "PENDING_CONFIRMATION", "Pending Elevated Confirmation"
        QUEUED = "QUEUED", "Queued"
        IN_PROGRESS = "IN_PROGRESS", "Restoring Data"
        VALIDATING = "VALIDATING", "Validating Database Integrity"
        COMPLETED = "COMPLETED", "Restoration Completed Successfully"
        FAILED = "FAILED", "Restoration Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class RestoreType(models.TextChoices):
        FULL = "FULL", "Full System Restoration"
        DATABASE = "DATABASE", "Database Restoration Only"
        FILES = "FILES", "Documents & Media Restoration Only"
        SELECTIVE = "SELECTIVE", "Selective Component Restoration"

    restore_id = models.CharField(max_length=64, unique=True, db_index=True)
    backup = models.ForeignKey(BackupJob, on_delete=models.CASCADE, related_name="restores")
    restore_type = models.CharField(max_length=20, choices=RestoreType.choices, default=RestoreType.FULL)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING_CONFIRMATION)
    
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="requested_restores")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_restores")
    reason = models.TextField(help_text="Institutional reason for initiating disaster recovery / restore")
    
    maintenance_mode_entered = models.BooleanField(default=True)
    pre_restore_backup = models.ForeignKey(BackupJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="dependent_restores")
    
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    components_restored = models.JSONField(default=list, blank=True)
    logs = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.restore_id} from {self.backup.backup_id} [{self.status}]"


class BackupLog(models.Model):
    """
    Granular operational logging for backup generation, retention cleanup, and restorations.
    """
    class Level(models.TextChoices):
        INFO = "INFO", "Information"
        WARNING = "WARNING", "Warning"
        ERROR = "ERROR", "Error"
        SUCCESS = "SUCCESS", "Success"

    job = models.ForeignKey(BackupJob, on_delete=models.CASCADE, null=True, blank=True, related_name="operational_logs")
    restore_job = models.ForeignKey(BackupRestoreJob, on_delete=models.CASCADE, null=True, blank=True, related_name="operational_logs")
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.INFO)
    component = models.CharField(max_length=64, default="SYSTEM")
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.level}] {self.component}: {self.message[:60]}"


class BackupSetting(models.Model):
    """
    Global system backup policy settings.
    """
    default_storage = models.ForeignKey(BackupStorage, on_delete=models.SET_NULL, null=True, blank=True)
    default_retention = models.ForeignKey(BackupRetentionPolicy, on_delete=models.SET_NULL, null=True, blank=True)
    default_schedule = models.ForeignKey(BackupSchedule, on_delete=models.SET_NULL, null=True, blank=True)
    
    max_concurrent_jobs = models.PositiveIntegerField(default=1)
    storage_warning_threshold_percent = models.PositiveIntegerField(default=85)
    health_overdue_threshold_hours = models.PositiveIntegerField(default=36)
    require_maintenance_on_restore = models.BooleanField(default=True)
    rpo_minutes = models.PositiveIntegerField(default=15, help_text="Maximum acceptable data loss window in minutes.")
    rto_minutes = models.PositiveIntegerField(default=120, help_text="Maximum acceptable recovery window in minutes.")
    disaster_recovery_procedure = models.TextField(blank=True, default="", help_text="Approved recovery sequence, owners, escalation, and validation steps.")
    notify_on_failure = models.BooleanField(default=True)
    notify_on_success = models.BooleanField(default=False)
    notification_emails = models.TextField(blank=True, default="", help_text="Comma-separated emails for backup alerts")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Backup Global Settings"

    def __str__(self):
        return "Backup Global Settings"

    @classmethod
    def get_settings(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj
