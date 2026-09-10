from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, FacultyProfile, Role
from university.models import School, Department, RecycleBinItem
from university.recycle_bin_services import restore_from_recycle_bin


class FacultyManagementTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="fac.admin", email="fac.admin@ums.ac.ke", password="password123",
            role=Role.ADMIN, is_staff=True, is_superuser=True
        )
        self.student_user = User.objects.create_user(
            username="fac.student", email="fac.student@ums.ac.ke", password="password123",
            role=Role.STUDENT
        )
        self.school = School.objects.create(name="School of Computing", code="SOC")
        self.dept = Department.objects.create(name="Computer Science", code="CS", school=self.school)

        self.faculty_user = User.objects.create_user(
            username="prof.turing", email="turing@ums.ac.ke", password="password123",
            role=Role.FACULTY, first_name="Alan", last_name="Turing"
        )
        self.faculty_profile = FacultyProfile.objects.create(
            user=self.faculty_user, employee_id="FAC9001", department=self.dept,
            designation="Professor", specialization="Computation Theory"
        )

    def test_admin_faculty_list_and_permissions(self):
        client = Client()

        res_anon = client.get(reverse("university:admin_faculty"))
        self.assertEqual(res_anon.status_code, 302)

        client.force_login(self.student_user)
        res_student = client.get(reverse("university:admin_faculty"))
        self.assertEqual(res_student.status_code, 403)

        client.force_login(self.admin_user)
        res_admin = client.get(reverse("university:admin_faculty"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "FAC9001")

        res_search = client.get(reverse("university:admin_faculty") + "?q=Turing")
        self.assertContains(res_search, "FAC9001")

        res_dept = client.get(reverse("university:admin_faculty") + f"?department={self.dept.pk}")
        self.assertContains(res_dept, "FAC9001")

    def test_faculty_detail_view(self):
        client = Client()
        client.force_login(self.admin_user)

        res = client.get(reverse("university:faculty_detail", args=[self.faculty_profile.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "FAC9001")
        self.assertContains(res, "Professor")

    def test_faculty_create_and_edit(self):
        client = Client()
        client.force_login(self.admin_user)

        create_url = reverse("university:faculty_create")
        post_data = {
            "first_name": "Ada", "last_name": "Lovelace", "email": "ada@ums.ac.ke",
            "phone": "0712345678", "username": "prof.lovelace", "password": "",
            "employee_id": "FAC9002", "department": self.dept.pk,
            "designation": "Associate Professor", "specialization": "Algorithms",
        }
        res_create = client.post(create_url, post_data)
        self.assertEqual(res_create.status_code, 302)

        new_fp = FacultyProfile.objects.get(employee_id="FAC9002")
        self.assertEqual(new_fp.user.first_name, "Ada")
        self.assertEqual(new_fp.department, self.dept)

        edit_url = reverse("university:faculty_edit", args=[new_fp.pk])
        post_data["designation"] = "Professor"
        res_edit = client.post(edit_url, post_data)
        self.assertEqual(res_edit.status_code, 302)

        new_fp.refresh_from_db()
        self.assertEqual(new_fp.designation, "Professor")

    def test_faculty_create_rejects_duplicate_employee_id(self):
        client = Client()
        client.force_login(self.admin_user)

        post_data = {
            "first_name": "Grace", "last_name": "Hopper", "email": "grace@ums.ac.ke",
            "phone": "0712345678", "username": "prof.hopper", "password": "",
            "employee_id": "FAC9001", "department": self.dept.pk,
            "designation": "Professor", "specialization": "Compilers",
        }
        res = client.post(reverse("university:faculty_create"), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Employee ID already exists.")
        self.assertEqual(FacultyProfile.objects.filter(employee_id="FAC9001").count(), 1)

    def test_faculty_delete_moves_to_recycle_bin_and_restores(self):
        client = Client()
        client.force_login(self.admin_user)

        del_url = reverse("university:faculty_delete", args=[self.faculty_profile.pk])
        res = client.post(del_url)
        self.assertEqual(res.status_code, 302)

        self.assertFalse(FacultyProfile.objects.filter(employee_id="FAC9001").exists())
        self.assertFalse(User.objects.filter(username="prof.turing").exists())

        recycle_item = RecycleBinItem.objects.filter(
            module=RecycleBinItem.Module.FACULTY,
            object_id=str(self.faculty_profile.pk)
        ).first()
        self.assertIsNotNone(recycle_item)

        restored, msg = restore_from_recycle_bin(recycle_item.pk, user=self.admin_user)
        self.assertIsNotNone(restored)
        self.assertTrue(FacultyProfile.objects.filter(employee_id="FAC9001").exists())

    def test_faculty_module_registered_in_catalog(self):
        """The Faculty Staff Directory submodule must be discoverable and gate the admin routes."""
        from university.module_services import SYSTEM_MODULES_CATALOG

        faculty_ops = next((m for m in SYSTEM_MODULES_CATALOG if m["code"] == "faculty_ops"), None)
        self.assertIsNotNone(faculty_ops)

        directory = next((s for s in faculty_ops["submodules"] if s["code"] == "faculty_directory"), None)
        self.assertIsNotNone(directory)
        self.assertIn("admin_faculty", directory["route_names"])
        self.assertIn("faculty_delete", directory["route_names"])
