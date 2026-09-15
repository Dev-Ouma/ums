"""
Backend scope-enforcement tests for the Dean/HOD dashboard overhaul.

Verifies that Dean and HOD dashboards are scoped server-side to the user's
own School/Department (via StaffRoleAssignment), that scopes never leak
across users, that switching active role never combines scopes, and that
an unassigned Dean/HOD sees an explicit empty state rather than another
user's data or a crash.
"""
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, User
from university.models import Department, School, StaffRole, StaffRoleAssignment
from university.permissions_services import seed_default_permissions_and_roles
from university.services import resolve_staff_scope


class DashboardScopeTests(TestCase):
    def setUp(self):
        seed_default_permissions_and_roles()
        self.dean_role = StaffRole.objects.get(code="dean")
        self.hod_role = StaffRole.objects.get(code="hod")

        self.school_x = School.objects.create(name="School of Science", code="SCI")
        self.school_y = School.objects.create(name="School of Business", code="BUS")
        self.dept_a = Department.objects.create(name="Computer Science", code="CS", school=self.school_x)
        self.dept_b = Department.objects.create(name="Mathematics", code="MATH", school=self.school_x)
        self.dept_c = Department.objects.create(name="Accounting", code="ACC", school=self.school_y)

        self.dean_a = User.objects.create_user(
            username="dean_sci", email="dean_sci@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.dean_a, role=self.dean_role, school=self.school_x)

        self.dean_b = User.objects.create_user(
            username="dean_bus", email="dean_bus@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.dean_b, role=self.dean_role, school=self.school_y)

        self.hod_a = User.objects.create_user(
            username="hod_cs", email="hod_cs@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.hod_a, role=self.hod_role, department=self.dept_a)

        self.hod_b = User.objects.create_user(
            username="hod_math", email="hod_math@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.hod_b, role=self.hod_role, department=self.dept_b)

        self.unassigned_dean = User.objects.create_user(
            username="dean_unassigned", email="dean_unassigned@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.unassigned_dean, role=self.dean_role)

        self.multi_role_user = User.objects.create_user(
            username="dean_and_hod", email="dean_and_hod@niu.ac.ke", password="password123", role=Role.ADMIN)
        StaffRoleAssignment.objects.create(user=self.multi_role_user, role=self.dean_role, school=self.school_x)
        StaffRoleAssignment.objects.create(user=self.multi_role_user, role=self.hod_role, department=self.dept_c)

    # -- resolve_staff_scope unit coverage -----------------------------

    def test_resolve_dean_scope_returns_own_school_only(self):
        self.assertEqual(resolve_staff_scope(self.dean_a, "dean"), self.school_x)
        self.assertEqual(resolve_staff_scope(self.dean_b, "dean"), self.school_y)

    def test_resolve_hod_scope_returns_own_department_only(self):
        self.assertEqual(resolve_staff_scope(self.hod_a, "hod"), self.dept_a)
        self.assertEqual(resolve_staff_scope(self.hod_b, "hod"), self.dept_b)

    def test_resolve_scope_none_when_unassigned(self):
        self.assertIsNone(resolve_staff_scope(self.unassigned_dean, "dean"))

    # -- Dean dashboard: no cross-school leakage ------------------------

    def test_dean_dashboard_scoped_to_own_school(self):
        client = Client()
        client.force_login(self.dean_a)
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["school"], self.school_x)
        dept_codes = {d.code for d in resp.context["departments"]}
        self.assertEqual(dept_codes, {"CS", "MATH"})
        self.assertNotIn("ACC", dept_codes)

    def test_second_dean_sees_only_their_own_school(self):
        client = Client()
        client.force_login(self.dean_b)
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["school"], self.school_y)
        dept_codes = {d.code for d in resp.context["departments"]}
        self.assertEqual(dept_codes, {"ACC"})

    # -- HOD dashboard: no cross-department leakage ---------------------

    def test_hod_dashboard_scoped_to_own_department(self):
        client = Client()
        client.force_login(self.hod_a)
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["department"], self.dept_a)

    def test_second_hod_sees_only_their_own_department(self):
        client = Client()
        client.force_login(self.hod_b)
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["department"], self.dept_b)

    # -- Empty state for unassigned Dean/HOD -----------------------------

    def test_unassigned_dean_sees_empty_state_not_another_school(self):
        client = Client()
        client.force_login(self.unassigned_dean)
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("no_scope"))
        self.assertIsNone(resp.context.get("school"))

    # -- Multi-role user: scopes never combine across active role --------

    def test_multi_role_user_scopes_never_combine(self):
        client = Client()
        client.force_login(self.multi_role_user)

        client.post(reverse("university:switch_role"), {"role": "dean"})
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.context["school"], self.school_x)
        dept_codes = {d.code for d in resp.context["departments"]}
        self.assertNotIn("ACC", dept_codes)

        client.post(reverse("university:switch_role"), {"role": "hod"})
        resp = client.get(reverse("university:dashboard"))
        self.assertEqual(resp.context["department"], self.dept_c)

    def test_switch_role_rejects_role_not_assigned_to_user(self):
        client = Client()
        client.force_login(self.hod_a)
        resp = client.post(reverse("university:switch_role"), {"role": "dean"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        session = client.session
        self.assertNotEqual(session.get("active_role"), "dean")
