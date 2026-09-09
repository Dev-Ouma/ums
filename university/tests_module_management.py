"""
Comprehensive Automated Tests for System Admin Module Management & Availability System.
"""

import io
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from django.core.cache import cache
from accounts.models import User, Role, StudentProfile
from university.models import (
    AuditLog,
    FeeInvoice,
    Program,
    School,
    Department,
    AcademicYear,
    AcademicTerm,
)
from university.module_models import (
    ModuleStatus,
    SystemModule,
    SystemSubmodule,
    SystemFeature,
    ModuleDependency,
)
from university.module_services import (
    seed_system_modules,
    set_module_status,
    set_submodule_status,
    set_feature_status,
    bulk_set_modules_status,
    enable_all_modules,
    disable_all_configurable_modules,
    is_module_active,
    is_submodule_active,
    is_feature_active,
    check_module_dependencies,
    invalidate_module_cache,
)


class ModuleManagementTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        seed_system_modules()

        # Create system admin user
        self.admin_user = User.objects.create_superuser(
            username="admin_sys",
            email="admin@ums.edu",
            password="adminpassword123",
            role=Role.ADMIN,
        )

        # Create regular student user
        self.student_user = User.objects.create_user(
            username="student_john",
            email="john@student.ums.edu",
            password="studentpassword123",
            role=Role.STUDENT,
        )

        # Create academic setup for student
        self.school = School.objects.create(name="School of Engineering", code="SOE")
        self.dept = Department.objects.create(name="Software Engineering", code="SE", school=self.school)
        self.program = Program.objects.create(name="BSc Software Engineering", code="BSE", department=self.dept)
        self.academic_year = AcademicYear.objects.create(
            name="2026/2027", code="AY-2026", start_date=timezone.now().date(), end_date=timezone.now().date()
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.academic_year, name="Semester 1", term_type="SEMESTER", semester_number=1,
            start_date=timezone.now().date(), end_date=timezone.now().date(), is_current=True
        )

        self.student_profile = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="STU/2026/001",
            program=self.program,
            current_semester=1,
        )

    def tearDown(self):
        cache.clear()

    def test_seed_system_modules_idempotency(self):
        """Verify seeding creates complete hierarchy and is safe to run multiple times."""
        modules_count = SystemModule.objects.count()
        submodules_count = SystemSubmodule.objects.count()
        features_count = SystemFeature.objects.count()
        deps_count = ModuleDependency.objects.count()

        self.assertGreaterEqual(modules_count, 20)
        self.assertGreaterEqual(submodules_count, 45)
        self.assertGreaterEqual(features_count, 7)
        self.assertGreaterEqual(deps_count, 5)

        # Re-run seeder - counts must not duplicate
        seed_system_modules()
        self.assertEqual(SystemModule.objects.count(), modules_count)
        self.assertEqual(SystemSubmodule.objects.count(), submodules_count)
        self.assertEqual(SystemFeature.objects.count(), features_count)

    def test_critical_module_protection(self):
        """Core modules (auth, system_admin) cannot be disabled."""
        auth_mod = SystemModule.objects.get(code="auth")
        self.assertTrue(auth_mod.is_critical)

        success, msg = set_module_status("auth", ModuleStatus.DISABLED, user=self.admin_user)
        self.assertFalse(success)
        self.assertIn("critical", msg.lower())

        auth_mod.refresh_from_db()
        self.assertEqual(auth_mod.status, ModuleStatus.ENABLED)

    def test_module_disable_backend_enforcement_and_templates(self):
        """When finance is disabled, accessing fee routes yields 503/403 with inactive page."""
        # 1. Verify initially active
        self.assertTrue(is_module_active("finance"))
        self.client.force_login(self.student_user)
        res = self.client.get(reverse("university:student_fees"))
        self.assertEqual(res.status_code, 200)

        # 2. Disable finance module
        success, msg = set_module_status(
            "finance",
            ModuleStatus.DISABLED,
            status_message="Tuition fee accounts temporarily offline for semester rollover.",
            user=self.admin_user,
            bypass_dependencies=True,
        )
        self.assertTrue(success)
        self.assertFalse(is_module_active("finance"))

        # 3. Requesting web route now returns inactive screen
        res = self.client.get(reverse("university:student_fees"))
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "Module Offline", status_code=403)
        self.assertContains(res, "Tuition fee accounts temporarily offline", status_code=403)

        # 4. JSON / API request returns structured JSON payload
        res_json = self.client.get(
            reverse("university:student_fees"),
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(res_json.status_code, 403)
        data = res_json.json()
        self.assertEqual(data["error"], "module_inactive")
        self.assertEqual(data["status"], "DISABLED")

    def test_maintenance_mode_and_coming_soon(self):
        """Modules can be placed in Maintenance or Coming Soon with custom ETA notices."""
        # Set examinations to maintenance
        set_module_status(
            "examinations",
            ModuleStatus.MAINTENANCE,
            status_message="Senate marks moderation scheduled from 14:00 to 18:00 EAT.",
            user=self.admin_user,
        )

        self.client.force_login(self.student_user)
        res = self.client.get(reverse("university:student_exam_card"))
        self.assertEqual(res.status_code, 503)
        self.assertContains(res, "Scheduled Maintenance", status_code=503)
        self.assertContains(res, "Senate marks moderation scheduled", status_code=503)

        # Set CMS to Coming Soon
        set_module_status(
            "cms",
            ModuleStatus.COMING_SOON,
            status_message="Revamped university public portal arriving in Fall 2026.",
            user=self.admin_user,
        )
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("cms:dashboard"))
        self.assertEqual(res.status_code, 403)
        self.assertContains(res, "Coming Soon", status_code=403)

    def test_submodule_and_feature_level_control(self):
        """Individual submodules and features can be toggled independently."""
        # Feature level: sick leave
        self.assertTrue(is_feature_active("SICK_LEAVE"))

        success, msg = set_feature_status("request_sick_leave", ModuleStatus.DISABLED, user=self.admin_user)
        self.assertTrue(success)
        self.assertFalse(is_feature_active("SICK_LEAVE"))
        # Deferment remains active
        self.assertTrue(is_feature_active("DEFERMENT"))

        # Submodule level: Document controls
        success, msg = set_submodule_status("document_controls", ModuleStatus.DISABLED, user=self.admin_user)
        self.assertTrue(success)
        self.assertFalse(is_submodule_active("document_controls"))

    def test_dependency_enforcement(self):
        """Disabling a module on which active modules depend is blocked with an alert."""
        # Ensure academics is active and depends on academic_calendar
        self.assertTrue(is_module_active("academics"))
        self.assertTrue(is_module_active("academic_calendar"))

        # Try disabling academic_calendar without bypass
        success, msg = set_module_status("academic_calendar", ModuleStatus.DISABLED, user=self.admin_user)
        self.assertFalse(success)
        self.assertIn("depend on it", msg)

        # check_module_dependencies directly
        allowed, conflicts = check_module_dependencies("academic_calendar", ModuleStatus.DISABLED)
        self.assertFalse(allowed)
        conflict_codes = [c["module_code"] for c in conflicts]
        self.assertIn("academics", conflict_codes)

    def test_bulk_operations_and_safety(self):
        """Bulk enable and disable operate correctly and respect critical module protection."""
        # Disable all configurable
        success, msg = disable_all_configurable_modules(user=self.admin_user)
        self.assertTrue(success)

        # Critical modules must still be enabled
        self.assertTrue(is_module_active("auth"))
        self.assertTrue(is_module_active("system_admin"))

        # Configurable modules are disabled
        self.assertFalse(is_module_active("curriculum"))
        self.assertFalse(is_module_active("finance"))

        # Enable all
        success, msg = enable_all_modules(user=self.admin_user)
        self.assertTrue(success)
        self.assertTrue(is_module_active("curriculum"))
        self.assertTrue(is_module_active("finance"))

    def test_data_preservation_on_disable(self):
        """Disabling a module never removes database records."""
        invoice = FeeInvoice.objects.create(
            student=self.student_profile,
            term=self.term,
            title="Semester 1 Tuition Fee",
            amount=Decimal("45000.00"),
            amount_paid=Decimal("0.00"),
            due_date=timezone.now().date(),
        )
        invoice_id = invoice.id

        # Disable finance module
        set_module_status("finance", ModuleStatus.DISABLED, user=self.admin_user, bypass_dependencies=True)

        # Record still exists and is untouched
        fetched = FeeInvoice.objects.get(id=invoice_id)
        self.assertEqual(fetched.amount, Decimal("45000.00"))

        # Re-enable finance
        set_module_status("finance", ModuleStatus.ENABLED, user=self.admin_user)
        self.assertTrue(FeeInvoice.objects.filter(id=invoice_id).exists())

    def test_audit_trail_logging(self):
        """Every module state change creates an AuditLog entry."""
        initial_log_count = AuditLog.objects.filter(module=AuditLog.Module.MODULE_MGMT).count()

        set_module_status("timetable", ModuleStatus.MAINTENANCE, status_message="Room scheduling test", user=self.admin_user)

        new_logs = AuditLog.objects.filter(module=AuditLog.Module.MODULE_MGMT)
        self.assertEqual(new_logs.count(), initial_log_count + 1)
        latest_log = new_logs.latest("timestamp")
        self.assertEqual(latest_log.action, AuditLog.Action.MODULE_STATUS_CHANGE)
        self.assertIn("Timetable", latest_log.description)

    def test_admin_cockpit_view_and_alias(self):
        """Admin can access /manage/system/modules/ and /system-admin/modules/."""
        self.client.force_login(self.admin_user)

        # 1. Standard route
        res = self.client.get(reverse("university:admin_modules"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Module Management")
        self.assertContains(res, "System Architecture Cockpit")

        # 2. Alias route /system-admin/modules/
        res_alias = self.client.get("/system-admin/modules/")
        self.assertEqual(res_alias.status_code, 200)
        self.assertContains(res_alias, "Module Management")

        # 3. Dependencies API
        mod = SystemModule.objects.get(code="academics")
        res_api = self.client.get(reverse("university:admin_module_dependencies_api", kwargs={"pk": mod.id}))
        self.assertEqual(res_api.status_code, 200)
        data = res_api.json()
        self.assertEqual(data["module_code"], "academics")
        self.assertIn("requires", data)

    def test_admin_modules_export_json(self):
        """Admin can export system module configuration as JSON."""
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("university:admin_modules_export_json"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/json")
        data = json.loads(res.content)
        self.assertIn("modules", data)
        self.assertIn("exported_at", data)
        codes = [m["code"] for m in data["modules"]]
        self.assertIn("academics", codes)
        self.assertIn("finance", codes)
        # Verify nested submodules and features are present
        acad_mod = next(m for m in data["modules"] if m["code"] == "academics")
        self.assertIn("submodules", acad_mod)

    def test_admin_modules_import_json(self):
        """Admin can import updated module configuration via JSON file upload."""
        self.client.force_login(self.admin_user)
        # 1. Fetch export data
        res_exp = self.client.get(reverse("university:admin_modules_export_json"))
        config_data = json.loads(res_exp.content)

        # 2. Modify timetable module in config
        for m in config_data["modules"]:
            if m["code"] == "timetable":
                m["status"] = ModuleStatus.MAINTENANCE
                m["status_message"] = "Imported maintenance window test"

        payload = io.BytesIO(json.dumps(config_data).encode("utf-8"))
        payload.name = "modules_config.json"

        # 3. Upload JSON to import endpoint
        res = self.client.post(
            reverse("university:admin_modules_import_json"),
            {"config_file": payload},
            follow=True,
        )
        self.assertEqual(res.status_code, 200)

        # 4. Verify timetable status was updated in database
        tt_mod = SystemModule.objects.get(code="timetable")
        self.assertEqual(tt_mod.status, ModuleStatus.MAINTENANCE)
        self.assertEqual(tt_mod.status_message, "Imported maintenance window test")

        # 5. Verify AuditLog was recorded
        log = AuditLog.objects.filter(module=AuditLog.Module.MODULE_MGMT, action=AuditLog.Action.MODULES_BULK_UPDATE).latest("timestamp")
        self.assertIn("Imported module availability configuration", log.description)
