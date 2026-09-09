"""
End-to-end Automated Test Suite for Scheduled System Backups & Disaster Recovery.
Tests database models, archive generation, SHA-256 verification, autonomous scheduling,
multi-tier retention pruning, disaster recovery safe restoration with maintenance lockdown,
granular permission controls, and data exports.
"""

import os
import shutil
import tempfile
from datetime import timedelta, date, time as dtime
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
    Notice,
    SystemPermission,
    SystemRestriction,
    UserPermissionOverride,
)
from university.backup_services import (
    apply_retention_policies,
    backup_tick,
    calculate_next_run,
    calculate_recovery_readiness,
    create_backup_job,
    ensure_default_storage_and_retention,
    execute_backup_job,
    execute_restore_job,
    test_storage_connection,
    verify_backup_archive,
)
from university.permissions_services import seed_default_permissions_and_roles

User = get_user_model()


class BaseBackupTestCase(TransactionTestCase):
    """Base setup with temporary storage workspace and test users."""

    def setUp(self):
        seed_default_permissions_and_roles()

        self.admin = User.objects.create_user(
            username="backup_admin",
            email="backup_admin@university.test",
            password="adminpassword123",
            role=Role.ADMIN,
        )

        self.super_admin = User.objects.create_superuser(
            username="backup_root",
            email="backup_root@university.test",
            password="rootpassword123",
        )

        self.regular_student = User.objects.create_user(
            username="backup_student",
            email="backup_student@university.test",
            password="studentpassword123",
            role=Role.STUDENT,
        )

        self.staff_member = User.objects.create_user(
            username="backup_staff",
            email="backup_staff@university.test",
            password="staffpassword123",
            role=Role.FACULTY,
        )

        # Create a temporary directory for backups during testing
        self.test_dir = tempfile.mkdtemp(prefix="ums_test_backups_")
        self.storage, self.retention = ensure_default_storage_and_retention()
        self.storage.destination_path = self.test_dir
        self.storage.save()

    def tearDown(self):
        # Clean up temporary test files
        shutil.rmtree(self.test_dir, ignore_errors=True)


class BackupStorageAndSettingsTests(BaseBackupTestCase):
    """Tests storage destinations, probe validation, and backup configurations."""

    def test_ensure_default_storage_and_retention(self):
        storage, retention = ensure_default_storage_and_retention()
        self.assertIsNotNone(storage)
        self.assertIsNotNone(retention)
        self.assertTrue(storage.is_default)
        self.assertTrue(retention.is_default)
        self.assertEqual(retention.keep_last_n, 14)

        settings_obj = BackupSetting.get_settings()
        self.assertEqual(settings_obj.default_storage, storage)
        self.assertEqual(settings_obj.default_retention, retention)

    def test_test_storage_connection_success(self):
        success, message = test_storage_connection(self.storage)
        self.assertTrue(success)
        self.assertIn("online", message.lower())
        self.assertEqual(self.storage.last_test_status, "PASSED")
        self.assertIsNotNone(self.storage.last_tested_at)

    def test_test_storage_connection_invalid_path(self):
        # Create storage pointing to an impossible non-writable directory
        storage = BackupStorage.objects.create(
            name="Unreachable Network Mount",
            storage_type=BackupStorage.StorageType.LOCAL,
            destination_path="/dev/null/forbidden/directory/test",
            is_active=True,
        )
        success, message = test_storage_connection(storage)
        self.assertFalse(success)
        self.assertEqual(storage.last_test_status, "FAILED")
        self.assertIn("failure", message.lower())


class BackupCreationAndExecutionTests(BaseBackupTestCase):
    """Tests job creation, archive compression, artifact logging, and verification."""

    def test_create_backup_job_initialization(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.FULL,
            trigger_type=BackupJob.TriggerType.MANUAL,
            created_by=self.admin,
        )
        self.assertTrue(job.backup_id.startswith("BKP-"))
        self.assertEqual(job.status, BackupJob.Status.QUEUED)
        self.assertIn("DATABASE", job.included_components)
        self.assertIn("FILES", job.included_components)
        self.assertIn("CONFIG", job.included_components)
        self.assertEqual(job.created_by, self.admin)
        self.assertTrue(BackupLog.objects.filter(job=job).exists())

    def test_execute_backup_job_and_verify_archive(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.DATABASE,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )

        completed_job = execute_backup_job(job.pk)
        self.assertEqual(completed_job.status, BackupJob.Status.SUCCESSFUL)
        self.assertIsNotNone(completed_job.completed_at)
        self.assertGreater(completed_job.file_size_bytes, 0)
        self.assertTrue(len(completed_job.checksum_sha256) == 64)  # SHA-256 hex length
        self.assertTrue(os.path.exists(completed_job.archive_path))

        # Check recorded artifacts
        artifacts = BackupArtifact.objects.filter(job=completed_job)
        self.assertGreaterEqual(artifacts.count(), 1)

        # Check verification status
        self.assertEqual(completed_job.verification_status, BackupJob.VerificationStatus.PASSED)
        self.assertTrue(BackupVerification.objects.filter(job=completed_job, status=BackupVerification.Status.PASSED).exists())

        # Check AuditLog
        audit_entry = AuditLog.objects.filter(
            action=AuditLog.Action.BACKUP_CREATE,
            entity_id=completed_job.backup_id,
        ).first()
        self.assertIsNotNone(audit_entry)

    def test_verify_backup_archive_detects_tampering(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.CONFIG,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )
        job = execute_backup_job(job.pk)

        # Intentionally alter the recorded checksum to simulate tamper/corruption
        job.checksum_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
        job.save()

        verification = verify_backup_archive(job.pk, user=self.admin)
        self.assertEqual(verification.status, BackupVerification.Status.FAILED)
        self.assertFalse(verification.checksum_match)
        self.assertIn("Checksum mismatch", verification.errors)


class BackupScheduleAndTickTests(BaseBackupTestCase):
    """Tests recurring schedules, next-run calculations, and autonomous heartbeat execution."""

    def test_calculate_next_run(self):
        now = timezone.now()
        sched = BackupSchedule(
            name="Daily Test",
            frequency=BackupSchedule.Frequency.HOURLY,
            start_date=now.date(),
            start_time=dtime(2, 0),
        )
        next_run = calculate_next_run(sched, from_time=now)
        self.assertEqual(next_run.hour, (now + timedelta(hours=1)).hour)

        sched.frequency = BackupSchedule.Frequency.EVERY_6_HOURS
        next_run_6 = calculate_next_run(sched, from_time=now)
        self.assertEqual(next_run_6.hour, (now + timedelta(hours=6)).hour)

    def test_backup_tick_triggers_due_schedules(self):
        now = timezone.now()
        # Create an active schedule that is due now
        schedule = BackupSchedule.objects.create(
            name="Automated Nightly Backup",
            backup_type=BackupSchedule.BackupType.CONFIG,
            frequency=BackupSchedule.Frequency.DAILY,
            start_date=now.date(),
            start_time=dtime(1, 0),
            storage_destination=self.storage,
            is_active=True,
            next_run_at=now - timedelta(minutes=5),  # Due 5 mins ago
            created_by=self.admin,
        )

        initial_jobs_count = BackupJob.objects.filter(schedule=schedule).count()
        self.assertEqual(initial_jobs_count, 0)

        # Trigger scheduler heartbeat
        backup_tick()

        # Job must have been created and executed
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.last_run_at)
        self.assertGreater(schedule.next_run_at, now)

        new_jobs = BackupJob.objects.filter(schedule=schedule)
        self.assertEqual(new_jobs.count(), 1)
        self.assertEqual(new_jobs.first().status, BackupJob.Status.SUCCESSFUL)
        self.assertEqual(new_jobs.first().trigger_type, BackupJob.TriggerType.SCHEDULED)

    def test_backup_tick_one_time_schedule_deactivates(self):
        now = timezone.now()
        schedule = BackupSchedule.objects.create(
            name="One-Time Migration Snapshot",
            backup_type=BackupSchedule.BackupType.CONFIG,
            frequency=BackupSchedule.Frequency.ONCE,
            start_date=now.date(),
            start_time=dtime(1, 0),
            storage_destination=self.storage,
            is_active=True,
            next_run_at=now - timedelta(minutes=1),
            created_by=self.admin,
        )

        backup_tick()

        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertIsNone(schedule.next_run_at)


class BackupRetentionPolicyTests(BaseBackupTestCase):
    """Tests automated retention pruning and safety locks."""

    def test_apply_retention_policies_preserves_protected_and_latest(self):
        # Configure retention to keep only 2 backups
        self.retention.keep_last_n = 2
        self.retention.save()

        # Create 4 backups
        jobs = []
        for i in range(4):
            j = create_backup_job(
                backup_type=BackupSchedule.BackupType.CONFIG,
                trigger_type=BackupJob.TriggerType.MANUAL,
                storage=self.storage,
                created_by=self.admin,
            )
            j = execute_backup_job(j.pk)
            jobs.append(j)

        # Mark the oldest backup as protected
        oldest = jobs[0]
        oldest.is_protected = True
        oldest.save()

        # Apply retention policy
        result = apply_retention_policies()
        
        # Protected backup must NOT be expired
        oldest.refresh_from_db()
        self.assertEqual(oldest.status, BackupJob.Status.SUCCESSFUL)
        self.assertTrue(os.path.exists(oldest.archive_path))

        # The most recent 2 backups must be kept
        jobs[3].refresh_from_db()
        self.assertEqual(jobs[3].status, BackupJob.Status.SUCCESSFUL)

        jobs[2].refresh_from_db()
        self.assertEqual(jobs[2].status, BackupJob.Status.SUCCESSFUL)

        # Unprotected older backup jobs[1] should be expired
        jobs[1].refresh_from_db()
        self.assertEqual(jobs[1].status, BackupJob.Status.EXPIRED)
        self.assertEqual(jobs[1].archive_path, "")


class BackupRestorationSafetyTests(BaseBackupTestCase):
    """Tests disaster recovery restoration, maintenance mode lockdown, and audit logging."""

    def test_execute_restore_job_lifecycle(self):
        # 1. Create and execute a valid backup
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.DATABASE,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )
        job = execute_backup_job(job.pk)

        # 2. Create a restore job
        restore = BackupRestoreJob.objects.create(
            restore_id="RST-TEST-001",
            backup=job,
            restore_type=BackupRestoreJob.RestoreType.DATABASE,
            status=BackupRestoreJob.Status.PENDING_CONFIRMATION,
            requested_by=self.admin,
            reason="Simulated catastrophic configuration failure disaster recovery.",
        )

        # 3. Execute restore
        finished_restore = execute_restore_job(restore.pk, user=self.admin)
        self.assertEqual(finished_restore.status, BackupRestoreJob.Status.COMPLETED)
        self.assertIsNotNone(finished_restore.completed_at)
        self.assertTrue(finished_restore.maintenance_mode_entered)

        # Pre-restore safety snapshot must exist
        self.assertIsNotNone(finished_restore.pre_restore_backup)
        self.assertEqual(finished_restore.pre_restore_backup.trigger_type, BackupJob.TriggerType.PRE_RESTORE)
        self.assertTrue(finished_restore.pre_restore_backup.is_protected)

        # Maintenance lockdown restriction must have been deactivated cleanly
        restriction = SystemRestriction.objects.filter(title__contains=restore.restore_id).first()
        self.assertIsNotNone(restriction)
        self.assertEqual(restriction.status, SystemRestriction.Status.COMPLETED)

        # AuditLog entry must be recorded
        audit_entry = AuditLog.objects.filter(
            action=AuditLog.Action.BACKUP_RESTORE,
            entity_id=finished_restore.restore_id,
        ).first()
        self.assertIsNotNone(audit_entry)


class BackupRecoveryReadinessTests(BaseBackupTestCase):
    """Tests system recovery readiness calculation and health metrics."""

    def test_readiness_critical_when_no_backups(self):
        readiness = calculate_recovery_readiness()
        self.assertEqual(readiness["status"], "CRITICAL")
        self.assertIn("No successful backup exists in the system.", readiness["reasons"])

    def test_readiness_healthy_after_successful_backup(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.CONFIG,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )
        execute_backup_job(job.pk)

        readiness = calculate_recovery_readiness()
        self.assertEqual(readiness["status"], "HEALTHY")
        self.assertEqual(readiness["score"], 100)
        self.assertEqual(readiness["total_backups"], 1)
        self.assertEqual(readiness["verified_backups"], 1)


class BackupViewsAndPermissionsTests(BaseBackupTestCase):
    """Tests UI endpoints, authorization guards, manual creation, and data export."""

    def test_unauthenticated_user_redirected(self):
        response = self.client.get(reverse("university:backup_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_student_forbidden(self):
        self.client.force_login(self.regular_student)
        response = self.client.get(reverse("university:backup_dashboard"))
        # Must be redirected away
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("university:dashboard"))

    def test_admin_can_access_backup_dashboard(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("university:backup_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Backups")
        self.assertContains(response, "Recovery Readiness")

    def test_admin_can_access_all_subsections(self):
        self.client.force_login(self.admin)
        
        # Schedules
        resp = self.client.get(reverse("university:backup_schedules"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Scheduled Backups")

        # History
        resp = self.client.get(reverse("university:backup_history"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backup History")

        # Storage
        resp = self.client.get(reverse("university:backup_storage"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backup Storage Targets")

        # Restore
        resp = self.client.get(reverse("university:backup_restore_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Disaster Recovery")

        # Settings
        resp = self.client.get(reverse("university:backup_settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backup Policies")

        # Logs
        resp = self.client.get(reverse("university:backup_logs"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Operational Backup Logs")

    def test_manual_backup_creation_view(self):
        self.client.force_login(self.admin)
        post_data = {
            "backup_type": BackupSchedule.BackupType.CONFIG,
            "storage_id": self.storage.id,
            "is_protected": "on",
        }
        response = self.client.post(reverse("university:backup_create_now"), post_data)
        self.assertEqual(response.status_code, 302)

        # Check job was created and completed
        latest_job = BackupJob.objects.order_by("-created_at").first()
        self.assertIsNotNone(latest_job)
        self.assertEqual(latest_job.status, BackupJob.Status.SUCCESSFUL)
        self.assertTrue(latest_job.is_protected)

    def test_backup_schedule_crud_views(self):
        self.client.force_login(self.admin)
        
        # 1. Create Schedule
        form_data = {
            "name": "Midday Snapshot",
            "description": "Daily midday database snapshot",
            "backup_type": BackupSchedule.BackupType.DATABASE,
            "frequency": BackupSchedule.Frequency.DAILY,
            "start_date": timezone.now().strftime("%Y-%m-%d"),
            "start_time": "12:00",
            "storage_destination": self.storage.id,
            "include_database": "on",
            "is_active": "on",
        }
        resp = self.client.post(reverse("university:backup_schedule_create"), form_data)
        self.assertEqual(resp.status_code, 302)

        sched = BackupSchedule.objects.filter(name="Midday Snapshot").first()
        self.assertIsNotNone(sched)
        self.assertTrue(sched.is_active)

        # 2. Toggle Status
        resp = self.client.post(reverse("university:backup_schedule_toggle", args=[sched.pk]))
        self.assertEqual(resp.status_code, 302)
        sched.refresh_from_db()
        self.assertFalse(sched.is_active)

        # 3. Delete Schedule
        resp = self.client.post(reverse("university:backup_schedule_delete", args=[sched.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BackupSchedule.objects.filter(name="Midday Snapshot").exists())

    def test_backup_download_and_permission_check(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.CONFIG,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )
        job = execute_backup_job(job.pk)

        # Test download as admin (who has backup permission)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("university:backup_download", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))

    def test_backup_csv_and_excel_exports(self):
        job = create_backup_job(
            backup_type=BackupSchedule.BackupType.CONFIG,
            trigger_type=BackupJob.TriggerType.MANUAL,
            storage=self.storage,
            created_by=self.admin,
        )
        job = execute_backup_job(job.pk)

        self.client.force_login(self.admin)

        # CSV Export
        csv_resp = self.client.get(reverse("university:backup_export") + "?format=csv")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertIn(job.backup_id, csv_resp.content.decode("utf-8"))

        # Excel Export
        excel_resp = self.client.get(reverse("university:backup_export") + "?format=excel")
        self.assertEqual(excel_resp.status_code, 200)
        self.assertEqual(
            excel_resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
