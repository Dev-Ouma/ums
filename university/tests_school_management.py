from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Role
from university.models import School, Department, RecycleBinItem, AuditLog
from university.recycle_bin_services import restore_from_recycle_bin


class SchoolManagementTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="school.admin", email="school.admin@ums.ac.ke", password="password123",
            role=Role.ADMIN, is_staff=True, is_superuser=True
        )
        self.student_user = User.objects.create_user(
            username="school.student", email="school.student@ums.ac.ke", password="password123",
            role=Role.STUDENT
        )
        self.school = School.objects.create(
            name="School of Computing & Engineering", code="SCE", dean_name="Prof. Alan Turing"
        )
        self.dept = Department.objects.create(
            name="Computer Science & Engineering", code="CSE", school=self.school
        )

    def test_admin_schools_list_and_permissions(self):
        client = Client()

        res_anon = client.get(reverse("university:admin_schools"))
        self.assertEqual(res_anon.status_code, 302)

        client.force_login(self.student_user)
        res_student = client.get(reverse("university:admin_schools"))
        self.assertEqual(res_student.status_code, 403)

        client.force_login(self.admin_user)
        res_admin = client.get(reverse("university:admin_schools"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "School of Computing &amp; Engineering")

    def test_school_detail_lists_departments(self):
        client = Client()
        client.force_login(self.admin_user)

        res = client.get(reverse("university:school_detail", args=[self.school.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "CSE")
        self.assertContains(res, "Prof. Alan Turing")

    def test_school_create_and_edit(self):
        client = Client()
        client.force_login(self.admin_user)

        post_data = {
            "name": "School of Business & Economics",
            "code": "SBUS",
            "dean_name": "Dr. Jane Doe",
            "description": "Business school.",
        }
        res_create = client.post(reverse("university:school_create"), post_data)
        self.assertEqual(res_create.status_code, 302)

        new_school = School.objects.get(code="SBUS")
        self.assertEqual(new_school.name, "School of Business & Economics")

        edit_url = reverse("university:school_edit", args=[new_school.pk])
        post_data["dean_name"] = "Dr. John Smith"
        res_edit = client.post(edit_url, post_data)
        self.assertEqual(res_edit.status_code, 302)

        new_school.refresh_from_db()
        self.assertEqual(new_school.dean_name, "Dr. John Smith")

        # Regression guard: school create/edit previously had no audit trail at all.
        self.assertTrue(AuditLog.objects.filter(
            entity="School", entity_id=new_school.id, action=AuditLog.Action.CREATE
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity="School", entity_id=new_school.id, action=AuditLog.Action.UPDATE
        ).exists())

    def test_school_delete_moves_to_recycle_bin_and_restores(self):
        client = Client()
        client.force_login(self.admin_user)

        school_to_delete = School.objects.create(name="School of Law", code="SLAW")

        del_url = reverse("university:school_delete", args=[school_to_delete.pk])
        res = client.post(del_url)
        self.assertEqual(res.status_code, 302)

        self.assertFalse(School.objects.filter(code="SLAW").exists())

        recycle_item = RecycleBinItem.objects.filter(
            module=RecycleBinItem.Module.SCHOOLS,
            object_id=str(school_to_delete.pk)
        ).first()
        self.assertIsNotNone(recycle_item)
        self.assertFalse(recycle_item.is_restored)

        restored, msg = restore_from_recycle_bin(recycle_item.pk, user=self.admin_user)
        self.assertIsNotNone(restored)
        self.assertTrue(School.objects.filter(code="SLAW").exists())

    def test_department_form_assigns_school(self):
        """DepartmentForm must expose the `school` field end-to-end."""
        client = Client()
        client.force_login(self.admin_user)

        create_url = reverse("university:department_create")
        post_data = {
            "name": "Electrical Engineering",
            "code": "EEE",
            "school": self.school.pk,
            "description": "",
            "icon": "fa-building-columns",
            "color": "#6C5CE7",
            "image_url": "",
        }
        res = client.post(create_url, post_data)
        self.assertEqual(res.status_code, 302)

        new_dept = Department.objects.get(code="EEE")
        self.assertEqual(new_dept.school, self.school)

        # Regression guard: department create previously had no audit trail at all.
        self.assertTrue(AuditLog.objects.filter(
            entity="Department", entity_id=new_dept.id, action=AuditLog.Action.CREATE
        ).exists())

    def test_department_delete_and_restore_preserves_school_fk(self):
        del_url = reverse("university:department_delete", args=[self.dept.pk])
        client = Client()
        client.force_login(self.admin_user)

        res = client.post(del_url)
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Department.objects.filter(code="CSE").exists())

        recycle_item = RecycleBinItem.objects.filter(
            module=RecycleBinItem.Module.DEPARTMENTS,
            object_id=str(self.dept.pk)
        ).first()
        self.assertIsNotNone(recycle_item)

        restored, msg = restore_from_recycle_bin(recycle_item.pk, user=self.admin_user)
        self.assertIsNotNone(restored)

        restored_dept = Department.objects.get(code="CSE")
        self.assertEqual(restored_dept.school, self.school)


class DepartmentCascadeDeleteRollbackTests(TestCase):
    """
    Department -> Program and Department -> Course are CASCADE on_delete.
    Deleting a Department used to silently hard-destroy every Programme and
    Course beneath it with zero way to recover them. The delete must still
    proceed immediately on confirm (auto-approved, never blocked) — but each
    cascaded child now gets its own independently-restorable Recycle Bin
    entry, and the confirm page must show what's about to be affected.
    """
    def setUp(self):
        from university.models import Program, Course
        self.admin_user = User.objects.create_user(
            username="cascade.admin", email="cascade.admin@ums.ac.ke", password="password123",
            role=Role.ADMIN, is_staff=True, is_superuser=True
        )
        self.dept = Department.objects.create(name="School of Cascades", code="SOC-CAS")
        self.program = Program.objects.create(
            name="BSc Cascades", code="BSC-CAS", department=self.dept, level="UG"
        )
        self.course = Course.objects.create(
            code="CAS101", title="Intro to Cascades", department=self.dept, program=self.program,
        )

    def test_confirm_page_warns_about_dependents(self):
        client = Client()
        client.force_login(self.admin_user)
        res = client.get(reverse("university:department_delete", args=[self.dept.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "programme")
        self.assertContains(res, "course")

    def test_delete_proceeds_immediately_and_children_are_individually_restorable(self):
        from university.models import Program, Course
        client = Client()
        client.force_login(self.admin_user)

        res = client.post(reverse("university:department_delete", args=[self.dept.pk]))
        self.assertEqual(res.status_code, 302)

        # Auto-approved: the delete actually happened, nothing blocked it.
        self.assertFalse(Department.objects.filter(pk=self.dept.pk).exists())
        self.assertFalse(Program.objects.filter(pk=self.program.pk).exists())
        self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())

        # Rollback: each cascaded child has its own Recycle Bin entry.
        dept_item = RecycleBinItem.objects.get(content_type="Department", object_id=str(self.dept.pk))
        program_item = RecycleBinItem.objects.get(content_type="Program", object_id=str(self.program.pk))
        course_item = RecycleBinItem.objects.get(content_type="Course", object_id=str(self.course.pk))

        restore_from_recycle_bin(dept_item.pk, user=self.admin_user)
        restore_from_recycle_bin(program_item.pk, user=self.admin_user)
        restore_from_recycle_bin(course_item.pk, user=self.admin_user)

        self.assertTrue(Department.objects.filter(code="SOC-CAS").exists())
        self.assertTrue(Program.objects.filter(code="BSC-CAS").exists())
        self.assertTrue(Course.objects.filter(code="CAS101").exists())
