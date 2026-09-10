from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Role
from university.models import School, Department, RecycleBinItem
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
