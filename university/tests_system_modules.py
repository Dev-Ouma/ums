import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile, FacultyProfile
from university.models import (
    Department, Program, Course, AcademicTerm, ClassSchedule,
    RecycleBinItem, AuditLog, SystemSetting
)
from university.recycle_bin_services import (
    move_to_recycle_bin, restore_from_recycle_bin, bulk_restore,
    purge_recycle_item, bulk_purge
)
from university.audit_services import (
    log_activity, export_audit_csv, export_audit_excel, export_audit_pdf
)
from university.settings_services import (
    get_setting, set_setting, seed_default_settings
)

User = get_user_model()


class SystemModulesTestCase(TestCase):
    def setUp(self):
        # Users
        self.admin_user = User.objects.create_user(
            username="admin_sys", email="admin@test.com", password="password123",
            role=Role.ADMIN, is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="super_sys", email="super@test.com", password="password123"
        )
        self.student_user = User.objects.create_user(
            username="student_sys", email="student@test.com", password="password123",
            role=Role.STUDENT
        )

        # Baseline academic structure
        self.dept = Department.objects.create(name="Computer Science", code="CS")
        self.prog = Program.objects.create(name="BSc Computer Science", code="BCS", department=self.dept, duration_years=4)
        self.course = Course.objects.create(
            code="CS101", title="Introduction to Computing",
            department=self.dept, program=self.prog, credits=3
        )
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user, roll_no="CS/001/2026",
            program=self.prog, current_semester=1
        )
        from datetime import date
        self.term = AcademicTerm.objects.create(
            name="2025/2026 Sem 1",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 20),
            is_current=True
        )

        self.client = Client()

    # ==========================================
    # 1. RECYCLE BIN TESTS
    # ==========================================
    def test_move_to_recycle_bin_and_restore_student(self):
        """Test soft deleting a student archives to RecycleBinItem and can be fully restored."""
        item = move_to_recycle_bin(self.student_profile, user=self.admin_user)
        self.assertIsNotNone(item)
        self.assertEqual(item.content_type, "StudentProfile")
        self.assertEqual(item.module, RecycleBinItem.Module.STUDENTS)
        self.assertFalse(item.is_restored)

        # Confirm student is removed from live database
        self.assertFalse(StudentProfile.objects.filter(roll_no="CS/001/2026").exists())

        # Check Audit Log recorded DELETE
        audit = AuditLog.objects.filter(entity="StudentProfile", action=AuditLog.Action.DELETE).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.admin_user)

        # Restore
        restored, msg = restore_from_recycle_bin(item.id, user=self.admin_user)
        self.assertIsNotNone(restored)
        item.refresh_from_db()
        self.assertTrue(item.is_restored)

        # Verify student exists in live database again with relationship intact
        restored_sp = StudentProfile.objects.filter(roll_no="CS/001/2026").first()
        self.assertIsNotNone(restored_sp)
        self.assertEqual(restored_sp.program, self.prog)

        # Check Audit Log recorded RESTORE
        audit_restore = AuditLog.objects.filter(entity="StudentProfile", action=AuditLog.Action.RESTORE).first()
        self.assertIsNotNone(audit_restore)

    def test_soft_delete_and_restore_course(self):
        """Test soft deleting a Course and restoring it."""
        item = move_to_recycle_bin(self.course, user=self.admin_user)
        self.assertFalse(Course.objects.filter(code="CS101").exists())

        restored_course, msg = restore_from_recycle_bin(item.id, user=self.admin_user)
        self.assertIsNotNone(restored_course)
        self.assertTrue(Course.objects.filter(code="CS101").exists())

    def test_protected_record_purge_requires_superuser(self):
        """Test that permanent purge of a protected record is blocked for non-superusers."""
        from django.core.exceptions import PermissionDenied
        item = move_to_recycle_bin(self.course, user=self.admin_user, is_protected=True)
        self.assertTrue(item.is_protected)

        # Non-superuser purge attempt raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            purge_recycle_item(item.id, user=self.admin_user)
        self.assertTrue(RecycleBinItem.objects.filter(id=item.id).exists())

        # Superuser purge attempt
        success, msg = purge_recycle_item(item.id, user=self.superuser)
        self.assertTrue(success)
        self.assertFalse(RecycleBinItem.objects.filter(id=item.id).exists())

        # Check PERMANENT_DELETE was audited
        audit = AuditLog.objects.filter(action=AuditLog.Action.PERMANENT_DELETE).first()
        self.assertIsNotNone(audit)

    def test_bulk_restore(self):
        """Test restoring multiple items in a batch."""
        c2 = Course.objects.create(code="CS102", title="Data Structures", department=self.dept, credits=3)
        c3 = Course.objects.create(code="CS103", title="Algorithms", department=self.dept, credits=3)

        item1 = move_to_recycle_bin(c2, user=self.admin_user)
        item2 = move_to_recycle_bin(c3, user=self.admin_user)

        success_count, fail_count, errors = bulk_restore([item1.id, item2.id], user=self.admin_user)
        self.assertEqual(success_count, 2)
        self.assertEqual(fail_count, 0)
        self.assertTrue(Course.objects.filter(code="CS102").exists())
        self.assertTrue(Course.objects.filter(code="CS103").exists())

    # ==========================================
    # 2. AUDIT TRAILS TESTS
    # ==========================================
    def test_audit_logging_and_tamper_resistance(self):
        """Test log_activity correctly logs user, action, and JSON state diffs."""
        log_activity(
            user=self.admin_user,
            action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.ACADEMICS,
            entity="Course",
            entity_id="CS101",
            description="Updated course credits from 3 to 4",
            previous_state={"credits": 3},
            new_state={"credits": 4}
        )

        log = AuditLog.objects.filter(entity_id="CS101", action=AuditLog.Action.UPDATE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user_display, self.admin_user.username)
        self.assertIn(log.user_role, ["Administrator", "ADMIN"])
        self.assertEqual(log.previous_state["credits"], 3)
        self.assertEqual(log.new_state["credits"], 4)

    def test_audit_exports(self):
        """Test CSV, Excel, and PDF export generators produce valid content."""
        # Seed an audit entry
        log_activity(
            user=self.admin_user, action=AuditLog.Action.LOGIN,
            module=AuditLog.Module.AUTH, description="User logged in"
        )
        qs = AuditLog.objects.all()

        # Service bytes generators
        csv_bytes = export_audit_csv(qs)
        self.assertIsInstance(csv_bytes, bytes)
        self.assertIn(b"User logged in", csv_bytes)

        excel_bytes = export_audit_excel(qs)
        self.assertIsInstance(excel_bytes, bytes)

        pdf_bytes = export_audit_pdf(qs)
        self.assertIsInstance(pdf_bytes, bytes)

    # ==========================================
    # 3. ADMIN SETUPS TESTS
    # ==========================================
    def test_settings_seeding_and_typed_retrieval(self):
        """Test that settings are seeded, retrieved with types, and updated with audit logs."""
        count = seed_default_settings()
        self.assertGreater(count, 0)

        # Typed retrieval
        inst_name = get_setting("institution_name")
        self.assertEqual(inst_name, "Nexus International University")

        pass_mark = get_setting("pass_mark")
        self.assertIsInstance(pass_mark, int)
        self.assertEqual(pass_mark, 40)

        cat_weight = get_setting("cat_weight_percent")
        self.assertEqual(cat_weight, 30)

        unit_reg_active = get_setting("unit_registration_active")
        self.assertIsInstance(unit_reg_active, bool)
        self.assertTrue(unit_reg_active)

        # Update setting
        set_setting("pass_mark", 45, user=self.admin_user)
        self.assertEqual(get_setting("pass_mark"), 45)

        # Verify setting change was recorded in AuditLog
        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_CHANGE,
            entity_id="pass_mark"
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.new_state["value"], "45")

    # ==========================================
    # 4. VIEW & PERMISSION INTEGRATION TESTS
    # ==========================================
    def test_recycle_bin_view_access(self):
        """Test admin can view recycle bin, student is redirected/denied."""
        self.client.login(username="admin_sys", password="password123")
        res = self.client.get(reverse("university:recycle_bin_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Detail view
        item = move_to_recycle_bin(self.course, user=self.admin_user)
        res_detail = self.client.get(reverse("university:recycle_bin_detail", kwargs={"pk": item.pk}))
        self.assertEqual(res_detail.status_code, 200)

        # Student denied
        self.client.logout()
        self.client.login(username="student_sys", password="password123")
        res_student = self.client.get(reverse("university:recycle_bin_dashboard"))
        self.assertEqual(res_student.status_code, 302)

    def test_audit_trails_view_access(self):
        """Test admin can access audit trails dashboard and exports."""
        self.client.login(username="admin_sys", password="password123")
        res = self.client.get(reverse("university:audit_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Export routes
        res_csv = self.client.get(reverse("university:audit_export", kwargs={"fmt": "csv"}))
        self.assertEqual(res_csv.status_code, 200)

        res_excel = self.client.get(reverse("university:audit_export", kwargs={"fmt": "excel"}))
        self.assertEqual(res_excel.status_code, 200)

        res_pdf = self.client.get(reverse("university:audit_export", kwargs={"fmt": "pdf"}))
        self.assertEqual(res_pdf.status_code, 200)

    def test_admin_setups_view_and_update(self):
        """Test admin setups dashboard and updating settings via POST."""
        self.client.login(username="admin_sys", password="password123")
        res = self.client.get(reverse("university:admin_setups_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Post update to academic category
        update_url = reverse("university:admin_setups_update", kwargs={"category": "ACADEMIC"})
        post_data = {
            "setting_pass_mark": "50",
            "setting_max_credits_per_semester": "40",
            "setting_academic_year_current": "2026/2027",
            "setting_current_semester": "2",
            "setting_min_credits_per_semester": "15",
            "setting_grading_system_cue": json.dumps([{"grade": "A", "min": 70, "max": 100}]),
        }
        res_post = self.client.post(update_url, post_data)
        self.assertEqual(res_post.status_code, 302)

        # Verify values changed
        self.assertEqual(get_setting("pass_mark"), 50)
        self.assertEqual(get_setting("academic_year_current"), "2026/2027")
