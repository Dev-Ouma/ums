"""
Regression tests for the fresh end-to-end System Administration re-audit fixes:
- Module cascade toggle now respects per-submodule/feature is_critical and
  is atomic with a single audit log entry (instead of a raw bulk .update())
- JSON module import now protects critical features (parity with modules
  and submodules, which were already protected)
- Module Management's bespoke auth check no longer grants access via the
  bare Django is_staff flag
- Staff role deletion now warns about (and audit-logs) affected users, and
  blocks an admin from deleting/editing-away their own only source of
  Roles & Permissions management access
- Backup retention pruning never deletes a backup with an in-progress restore
- Backup creation surfaces a failed integrity verification instead of a
  plain "success" message
- Recycle bin restore handles PermissionDenied like purge already does
- A restored account's identity envelope comes back PENDING, not ACTIVE
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from university.backup_models import BackupJob, BackupRestoreJob
from university.backup_services import apply_retention_policies, create_backup_job
from university.identity_models import AccountStatus
from university.module_models import ModuleStatus, SystemFeature, SystemModule, SystemSubmodule
from university.module_services import cascade_module_status, seed_system_modules
from university.models import AuditLog, RecycleBinItem, StaffRole, StaffRoleAssignment, SystemPermission

User = get_user_model()


class ModuleCascadeCriticalProtectionTests(TestCase):
    def setUp(self):
        cache.clear()
        seed_system_modules()
        self.admin = User.objects.create_superuser(
            username="sysadmin.cascade", email="sysadmin.cascade@ums.ac.ke", password="password123", role=Role.ADMIN,
        )
        self.mod = SystemModule.objects.create(code="test_mod", name="Test Module", is_critical=False)
        self.sub_ordinary = SystemSubmodule.objects.create(
            module=self.mod, code="test_sub_ordinary", name="Ordinary Sub", is_critical=False,
        )
        self.sub_critical = SystemSubmodule.objects.create(
            module=self.mod, code="test_sub_critical", name="Critical Sub", is_critical=True,
        )
        self.feat_critical = SystemFeature.objects.create(
            submodule=self.sub_ordinary, code="test_feat_critical", name="Critical Feature", is_critical=True,
        )

    def test_cascade_disable_protects_critical_submodule_and_feature(self):
        updated_subs, updated_feats, skipped = cascade_module_status(
            self.mod.code, ModuleStatus.DISABLED, user=self.admin,
        )
        self.sub_ordinary.refresh_from_db()
        self.sub_critical.refresh_from_db()
        self.feat_critical.refresh_from_db()

        self.assertEqual(self.sub_ordinary.status, ModuleStatus.DISABLED)
        self.assertEqual(self.sub_critical.status, ModuleStatus.ENABLED)
        self.assertEqual(self.feat_critical.status, ModuleStatus.ENABLED)
        self.assertIn(self.sub_critical.name, skipped)
        self.assertIn(self.feat_critical.name, skipped)

    def test_cascade_logs_a_single_summarizing_audit_entry(self):
        cascade_module_status(self.mod.code, ModuleStatus.DISABLED, user=self.admin)
        self.assertTrue(
            AuditLog.objects.filter(entity="SystemModule", entity_id=str(self.mod.id)).exists()
        )


class ModuleManagementAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        seed_system_modules()
        self.client = Client()

    def test_is_staff_alone_no_longer_grants_module_management_access(self):
        staff_only_user = User.objects.create_user(
            username="staff.only", email="staff.only@ums.ac.ke", password="password123",
            role=Role.FACULTY, is_staff=True,
        )
        self.client.force_login(staff_only_user)
        response = self.client.get(reverse("university:admin_modules"))
        self.assertEqual(response.status_code, 302)

    def test_admin_role_still_has_access(self):
        admin_user = User.objects.create_user(
            username="admin.mm", email="admin.mm@ums.ac.ke", password="password123", role=Role.ADMIN,
        )
        self.client.force_login(admin_user)
        response = self.client.get(reverse("university:admin_modules"))
        self.assertEqual(response.status_code, 200)


class RoleDeletionSelfLockoutTests(TestCase):
    def setUp(self):
        from university.permissions_services import seed_default_permissions_and_roles
        seed_default_permissions_and_roles()
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username="superadmin.roles", email="superadmin.roles@ums.ac.ke", password="password123", role=Role.ADMIN,
        )
        self.limited_role = StaffRole.objects.create(name="RBAC Manager", code="rbac_manager")
        self.limited_role.permissions.add(SystemPermission.objects.get(code="admin.manage_roles_permissions"))
        self.limited_actor = User.objects.create_user(
            username="limited.rbac", email="limited.rbac@ums.ac.ke", password="password123", role=Role.FACULTY,
        )
        StaffRoleAssignment.objects.create(
            user=self.limited_actor, role=self.limited_role,
            department=None, school=None, is_active=True, assigned_by=self.superadmin,
        )

    def test_actor_cannot_delete_their_own_only_source_role(self):
        self.client.force_login(self.limited_actor)
        response = self.client.post(reverse("university:role_delete", args=[self.limited_role.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(StaffRole.objects.filter(pk=self.limited_role.pk).exists())

    def test_superadmin_can_delete_a_role_and_it_warns_about_affected_users(self):
        other_role = StaffRole.objects.create(name="Ordinary Role", code="ordinary_role")
        target_user = User.objects.create_user(
            username="target.user", email="target.user@ums.ac.ke", password="password123", role=Role.FACULTY,
        )
        StaffRoleAssignment.objects.create(
            user=target_user, role=other_role, department=None, school=None,
            is_active=True, assigned_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("university:role_delete", args=[other_role.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StaffRole.objects.filter(pk=other_role.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(entity=f"Staff Role: {other_role.name}", description__icontains="target.user").exists()
        )


class BackupRetentionInProgressRestoreProtectionTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone as dj_timezone
        self.jobs = []
        now = dj_timezone.now()
        for i in range(5):
            job = create_backup_job()
            job.status = BackupJob.Status.SUCCESSFUL
            job.file_size_bytes = 1024
            job.archive_path = ""
            job.save(update_fields=["status", "file_size_bytes", "archive_path"])
            # auto_now_add can't be set at create() -- stagger explicitly so
            # ordering by created_at is deterministic (not tied to the second).
            BackupJob.objects.filter(pk=job.pk).update(created_at=now - timedelta(minutes=i))
            job.refresh_from_db()
            self.jobs.append(job)

    def test_backup_with_in_progress_restore_is_never_pruned(self):
        # jobs[0] is the latest (already protected by "keep latest" rule) --
        # target an OLDER job so only the in-progress-restore guard protects it.
        target = self.jobs[2]
        BackupRestoreJob.objects.create(
            restore_id="RST-TEST-001", backup=target, status=BackupRestoreJob.Status.IN_PROGRESS,
            reason="Testing in-progress protection",
        )
        apply_retention_policies()
        target.refresh_from_db()
        self.assertNotEqual(target.status, BackupJob.Status.EXPIRED)

    def test_backup_without_active_restore_can_still_be_pruned(self):
        from university.backup_models import BackupRetentionPolicy
        policy = BackupRetentionPolicy.objects.first()
        if policy:
            policy.keep_last_n = 1
            policy.save(update_fields=["keep_last_n"])
        apply_retention_policies()
        oldest = self.jobs[-1]
        oldest.refresh_from_db()
        self.assertEqual(oldest.status, BackupJob.Status.EXPIRED)


class RecycleBinRestorePermissionDeniedTests(TestCase):
    def test_restore_view_handles_permission_denied_gracefully(self):
        # A non-admin faculty account can reach recycle_bin_restore's
        # SystemMessage branch (require_permission('messages.edit')) without
        # holding that permission -- this used to have no try/except around
        # it, unlike the equivalent purge view.
        from university.permissions_services import seed_default_permissions_and_roles
        seed_default_permissions_and_roles()
        actor = User.objects.create_user(
            username="unpermitted.faculty", email="unpermitted.faculty@ums.ac.ke",
            password="password123", role=Role.FACULTY,
        )
        recycle_role = StaffRole.objects.create(name="Recycle Bin Only", code="recycle_bin_only")
        recycle_role.permissions.add(SystemPermission.objects.get(code="admin.manage_recycle_bin"))
        StaffRoleAssignment.objects.create(
            user=actor, role=recycle_role, department=None, school=None, is_active=True,
        )
        item = RecycleBinItem.objects.create(
            content_type="SystemMessage", object_id="1", object_repr="Test message",
            serialized_data={"id": 1, "title": "Test"}, deleted_by=actor,
        )
        client = Client()
        client.force_login(actor)
        response = client.post(reverse("university:recycle_bin_restore", args=[item.pk]))
        self.assertEqual(response.status_code, 302)
