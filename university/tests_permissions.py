import io
from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.sessions.models import Session

from accounts.models import User, Role, FacultyProfile
from university.models import (
    Department,
    SystemPermission,
    StaffRole,
    StaffRoleAssignment,
    UserPermissionOverride,
    AuditLog,
)
from university.permissions_services import (
    DEFAULT_ROLES,
    seed_default_permissions_and_roles,
    has_user_permission,
    get_user_effective_permissions,
    set_user_permission_override,
    remove_user_permission_override,
    assign_staff_role,
)


class StaffPermissionsAndOverridesTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # Seed RBAC baseline
        seed_default_permissions_and_roles()

        # 1. Superuser
        self.admin_user = User.objects.create_superuser(
            username="admin_perm",
            email="admin_perm@niu.ac.ke",
            password="password123",
            first_name="Super",
            last_name="Admin"
        )

        # 2. Staff user (Academic Admin)
        self.staff_user = User.objects.create_user(
            username="officer_mary",
            email="mary@niu.ac.ke",
            password="password123",
            first_name="Mary",
            last_name="Wambui",
            role=Role.ADMIN
        )

        # 3. Faculty user (Lecturer)
        self.dept = Department.objects.create(name="Computer Science", code="CS")
        self.faculty_user = User.objects.create_user(
            username="dr_alex",
            email="alex@niu.ac.ke",
            password="password123",
            first_name="Alex",
            last_name="Kimani",
            role=Role.FACULTY
        )
        self.faculty_profile = FacultyProfile.objects.create(
            user=self.faculty_user,
            employee_id="EMP-CS-001",
            department=self.dept,
            designation="Senior Lecturer"
        )

        # 4. Student user
        self.student_user = User.objects.create_user(
            username="stud_ben",
            email="ben@niu.ac.ke",
            password="password123",
            first_name="Ben",
            last_name="Ochieng",
            role=Role.STUDENT
        )

    def test_seed_permissions_and_roles(self):
        """Verify baseline permissions and roles are properly initialized."""
        self.assertGreaterEqual(SystemPermission.objects.count(), 20)
        self.assertGreaterEqual(StaffRole.objects.count(), 7)

        # Verify specific permissions exist
        self.assertTrue(SystemPermission.objects.filter(code="exams.approve_senate").exists())
        self.assertTrue(SystemPermission.objects.filter(code="finance.record_payments").exists())

        # Verify role has permissions
        registrar_role = StaffRole.objects.get(code="academic_registrar")
        self.assertTrue(registrar_role.permissions.filter(code="exams.approve_senate").exists())

    def test_permission_resolution_and_overrides(self):
        """Verify priority rules: Superuser > User Override (Deny/Grant) > Role > Default."""
        # 1. Superuser always has all permissions
        self.assertTrue(has_user_permission(self.admin_user, "exams.approve_senate"))

        # 2. Faculty initially doesn't have finance permission
        self.assertFalse(has_user_permission(self.faculty_user, "finance.create_invoices"))

        # 3. Assign Finance Role -> Now has finance permission
        assign_staff_role(self.faculty_user, "finance_officer", actor=self.admin_user)
        self.assertTrue(has_user_permission(self.faculty_user, "finance.create_invoices"))

        # 4. Explicit DENY Override -> Blocks permission despite having the role!
        set_user_permission_override(
            user=self.faculty_user,
            permission_code="finance.create_invoices",
            override_type=UserPermissionOverride.OverrideType.DENY,
            reason="Temporary block during financial audit",
            granted_by=self.admin_user
        )
        self.assertFalse(has_user_permission(self.faculty_user, "finance.create_invoices"))

        # 5. Explicit GRANT Override for a completely unrelated permission (e.g. Hostels)
        self.assertFalse(has_user_permission(self.faculty_user, "hostels.allocate_room"))
        set_user_permission_override(
            user=self.faculty_user,
            permission_code="hostels.allocate_room",
            override_type=UserPermissionOverride.OverrideType.GRANT,
            reason="Special authorization as Acting Warden",
            granted_by=self.admin_user
        )
        self.assertTrue(has_user_permission(self.faculty_user, "hostels.allocate_room"))

        # 6. Remove override -> Reverts to role defaults
        remove_user_permission_override(self.faculty_user, "hostels.allocate_room", actor=self.admin_user)
        self.assertFalse(has_user_permission(self.faculty_user, "hostels.allocate_room"))

    def test_role_and_permission_changes_revoke_sessions_and_are_audited(self):
        self.client.force_login(self.faculty_user)
        session_key = self.client.session.session_key

        assignment = assign_staff_role(
            self.faculty_user, "finance_officer", actor=self.admin_user)

        self.assertIsNotNone(assignment)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertTrue(AuditLog.objects.filter(
            user=self.admin_user, entity__startswith="Staff Role:").exists())

        self.client.force_login(self.faculty_user)
        session_key = self.client.session.session_key
        set_user_permission_override(
            user=self.faculty_user,
            permission_code="finance.create_invoices",
            override_type=UserPermissionOverride.OverrideType.DENY,
            reason="Security review",
            granted_by=self.admin_user,
        )

        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertTrue(AuditLog.objects.filter(
            user=self.admin_user, entity__startswith="Permission Override:").exists())

    def test_every_seeded_staff_role_resolves_its_backend_permission_matrix(self):
        """Role labels are not enough; every seeded grant must work server-side."""
        for index, role_data in enumerate(DEFAULT_ROLES):
            with self.subTest(role=role_data["code"]):
                user = User.objects.create_user(
                    username=f"matrix_{index}", password="MatrixPass123!",
                    role=Role.FACULTY)
                assign_staff_role(user, role_data["code"], actor=self.admin_user)

                for permission_code in role_data["permissions"]:
                    self.assertTrue(
                        has_user_permission(user, permission_code),
                        f"{role_data['code']} did not receive {permission_code}",
                    )

    def test_student_cannot_call_admin_api_or_read_another_user_record(self):
        self.client.force_login(self.student_user)

        api_response = self.client.get(reverse("university:api_generate_password"))
        self.assertEqual(api_response.status_code, 403)

        record_response = self.client.get(
            reverse("university:user_detail", args=[self.faculty_user.pk]))
        self.assertEqual(record_response.status_code, 403)

    def test_staff_permissions_dashboard_view(self):
        """Verify staff permissions dashboard endpoints and tabs."""
        self.client.force_login(self.admin_user)

        # Tab 1: Staff Directory
        url = reverse("university:staff_permissions_dashboard")
        res = self.client.get(url, {"sub": "staff"})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "setups/staff_permissions_dashboard.html")
        self.assertIn("staff_items", res.context)

        # Tab 2: Roles Matrix
        res_roles = self.client.get(url, {"sub": "roles"})
        self.assertEqual(res_roles.status_code, 200)
        self.assertIn("roles", res_roles.context)

        # Tab 3: Permissions Catalog
        res_perms = self.client.get(url, {"sub": "permissions"})
        self.assertEqual(res_perms.status_code, 200)
        self.assertIn("modules", res_perms.context)

    def test_staff_user_access_detail_view(self):
        """Verify inspecting and modifying a single staff member's permissions."""
        self.client.force_login(self.admin_user)
        url = reverse("university:staff_user_access_detail", args=[self.faculty_user.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "setups/staff_user_access_detail.html")
        self.assertEqual(res.context["staff_user"], self.faculty_user)

    def test_permission_override_post_action(self):
        """Verify setting and removing overrides via POST view."""
        self.client.force_login(self.admin_user)
        url = reverse("university:set_permission_override_action", args=[self.faculty_user.id])

        # POST Grant
        res = self.client.post(url, {
            "perm_code": "students.create",
            "override_type": "GRANT",
            "reason": "Admissions desk support"
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(UserPermissionOverride.objects.filter(user=self.faculty_user, permission__code="students.create", override_type="GRANT").exists())

        # POST Remove
        res_rem = self.client.post(url, {
            "perm_code": "students.create",
            "override_type": "REMOVE"
        })
        self.assertEqual(res_rem.status_code, 302)
        self.assertFalse(UserPermissionOverride.objects.filter(user=self.faculty_user, permission__code="students.create").exists())

    def test_role_create_edit_view(self):
        """Verify creating and editing custom staff roles."""
        self.client.force_login(self.admin_user)
        url = reverse("university:role_create")

        # Create custom role
        res = self.client.post(url, {
            "name": "Deputy Examination Coordinator",
            "code": "deputy_exam_coord",
            "color": "#10ac84",
            "description": "Assists with exam paper moderation and marks audits.",
            "permissions": ["exams.view_marks", "exams.moderate_marks", "reports.view_catalog"]
        })
        self.assertEqual(res.status_code, 302)

        role = StaffRole.objects.get(code="deputy_exam_coord")
        self.assertEqual(role.permissions.count(), 3)
        self.assertFalse(role.is_system_role)

        # Edit role
        edit_url = reverse("university:role_edit", args=[role.id])
        res_edit = self.client.post(edit_url, {
            "name": "Chief Examination Coordinator",
            "code": "deputy_exam_coord",
            "color": "#ff9f43",
            "description": "Updated description",
            "permissions": ["exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "reports.view_catalog"]
        })
        self.assertEqual(res_edit.status_code, 302)
        role.refresh_from_db()
        self.assertEqual(role.name, "Chief Examination Coordinator")
        self.assertEqual(role.permissions.count(), 4)

    def test_student_access_rejected(self):
        """Verify non-admin/student users cannot access staff permissions hub."""
        self.client.force_login(self.student_user)
        url = reverse("university:staff_permissions_dashboard")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)


class ScopedPermissionTests(TestCase):
    """
    has_user_permission() never enforced StaffRoleAssignment.department/school
    -- an assignment scoped to "HOD of Computer Science" granted its
    permissions institution-wide, identically to an unscoped assignment.
    has_scoped_permission() fixes that for callers that pass a scope; every
    existing has_user_permission() caller is untouched.
    """
    @classmethod
    def setUpTestData(cls):
        seed_default_permissions_and_roles()
        cls.school = __import__("university.models", fromlist=["School"]).School.objects.create(
            name="School of Computing", code="SOC")
        cls.dept_a = Department.objects.create(name="Computer Science", code="CS", school=cls.school)
        cls.dept_b = Department.objects.create(name="Mathematics", code="MATH", school=cls.school)
        cls.other_school = __import__("university.models", fromlist=["School"]).School.objects.create(
            name="School of Business", code="SOB")
        cls.dept_c = Department.objects.create(name="Accounting", code="ACC", school=cls.other_school)

        # Custom roles carrying the permission under test, scoped in the way
        # the Staff Permissions console lets an admin scope any role.
        cls.role = StaffRole.objects.create(name="Dept Transfer Reviewer", code="dept_transfer_reviewer_scope_test")
        cls.role.permissions.add(SystemPermission.objects.get(code="academics.manage_transfers"))
        cls.dean_role = StaffRole.objects.create(name="School Transfer Reviewer", code="school_transfer_reviewer_scope_test")
        cls.dean_role.permissions.add(SystemPermission.objects.get(code="academics.manage_transfers"))

        cls.hod_user = User.objects.create_user(
            username="hod.cs", email="hod.cs@example.com", password="pass12345", role=Role.FACULTY)
        StaffRoleAssignment.objects.create(
            user=cls.hod_user, role=cls.role, department=cls.dept_a, school=None,
            is_active=True)

        cls.dean_user = User.objects.create_user(
            username="dean.soc", email="dean.soc@example.com", password="pass12345", role=Role.FACULTY)
        StaffRoleAssignment.objects.create(
            user=cls.dean_user, role=cls.dean_role, department=None, school=cls.school,
            is_active=True)

        cls.unscoped_user = User.objects.create_user(
            username="registrar", email="registrar@example.com", password="pass12345", role=Role.FACULTY)
        registrar_role = StaffRole.objects.get(code="academic_registrar")
        StaffRoleAssignment.objects.create(
            user=cls.unscoped_user, role=registrar_role, department=None, school=None,
            is_active=True)

    def _perm_code(self):
        # academics.manage_transfers is on the hod/dean/registrar default roles.
        return "academics.manage_transfers"

    def test_department_scoped_assignment_grants_within_its_own_department(self):
        from university.permissions_services import has_scoped_permission
        self.assertTrue(has_scoped_permission(
            self.hod_user, self._perm_code(), department=self.dept_a))

    def test_department_scoped_assignment_does_not_grant_a_different_department(self):
        from university.permissions_services import has_scoped_permission
        self.assertFalse(has_scoped_permission(
            self.hod_user, self._perm_code(), department=self.dept_b))

    def test_department_scoped_assignment_still_grants_institution_wide_when_no_scope_passed(self):
        """Callers that don't yet pass scope get identical behaviour to has_user_permission."""
        from university.permissions_services import has_scoped_permission, has_user_permission
        self.assertEqual(
            has_scoped_permission(self.hod_user, self._perm_code()),
            has_user_permission(self.hod_user, self._perm_code()))
        self.assertTrue(has_scoped_permission(self.hod_user, self._perm_code()))

    def test_school_scoped_dean_grants_every_department_in_that_school(self):
        from university.permissions_services import has_scoped_permission
        self.assertTrue(has_scoped_permission(
            self.dean_user, self._perm_code(), department=self.dept_a))
        self.assertTrue(has_scoped_permission(
            self.dean_user, self._perm_code(), department=self.dept_b))

    def test_school_scoped_dean_does_not_grant_a_department_in_another_school(self):
        from university.permissions_services import has_scoped_permission
        self.assertFalse(has_scoped_permission(
            self.dean_user, self._perm_code(), department=self.dept_c))

    def test_department_scoped_hod_does_not_grant_school_wide_access(self):
        from university.permissions_services import has_scoped_permission
        self.assertFalse(has_scoped_permission(
            self.hod_user, self._perm_code(), school=self.school))

    def test_unscoped_assignment_grants_every_department(self):
        from university.permissions_services import has_scoped_permission
        self.assertTrue(has_scoped_permission(
            self.unscoped_user, self._perm_code(), department=self.dept_a))
        self.assertTrue(has_scoped_permission(
            self.unscoped_user, self._perm_code(), department=self.dept_c))


class ScopedTransferAndRequestViewTests(TestCase):
    """
    admin_student_transfer_decision and admin_student_request_detail are the
    highest-value call sites for scope enforcement -- both let staff decide
    on another department's students today regardless of a scoped grant.
    """
    @classmethod
    def setUpTestData(cls):
        from university.models import Program, StudentRequest, StudentTransferRequest
        from accounts.models import StudentProfile
        seed_default_permissions_and_roles()
        cls.StudentRequest = StudentRequest
        cls.StudentTransferRequest = StudentTransferRequest

        cls.school = __import__("university.models", fromlist=["School"]).School.objects.create(
            name="School of Computing", code="SOC2")
        cls.dept_a = Department.objects.create(name="Computer Science", code="CS2", school=cls.school)
        cls.dept_b = Department.objects.create(name="Physics", code="PHY2", school=cls.school)
        cls.program_a = Program.objects.create(name="BSc CS", code="BCS2", department=cls.dept_a)
        cls.program_b = Program.objects.create(name="BSc Physics", code="BPH2", department=cls.dept_b)

        cls.hod_role = StaffRole.objects.create(name="Dept Reviewer", code="dept_reviewer_scope_test")
        cls.hod_role.permissions.add(
            SystemPermission.objects.get(code="academics.manage_transfers"),
            SystemPermission.objects.get(code="academics.manage_requests"))
        cls.hod_a = User.objects.create_user(
            username="hod.a", email="hod.a@example.com", password="pass12345", role=Role.FACULTY)
        StaffRoleAssignment.objects.create(
            user=cls.hod_a, role=cls.hod_role, department=cls.dept_a, school=None, is_active=True)

        cls.student_user = User.objects.create_user(
            username="stud.req", email="stud.req@example.com", password="pass12345", role=Role.STUDENT)
        cls.student = StudentProfile.objects.create(
            user=cls.student_user, roll_no="STU-SCOPE-1", program=cls.program_b, current_semester=1)

    def test_hod_cannot_view_a_student_request_from_a_different_department(self):
        req = self.StudentRequest.objects.create(
            student=self.student, request_type=self.StudentRequest.Type.DEFERMENT,
            reason="Medical", status=self.StudentRequest.Status.PENDING)
        client = Client()
        client.force_login(self.hod_a)
        res = client.get(reverse("university:admin_student_request_detail", args=[req.pk]))
        self.assertEqual(res.status_code, 403)

    def test_hod_cannot_decide_a_transfer_touching_only_another_department(self):
        transfer = self.StudentTransferRequest.objects.create(
            student=self.student, from_program=self.program_b, to_program=self.program_b,
            reason="Test reason with enough length")
        client = Client()
        client.force_login(self.hod_a)
        res = client.post(
            reverse("university:admin_student_transfer_decision", args=[transfer.pk]),
            {"decision": "APPROVED"})
        self.assertEqual(res.status_code, 403)

    def test_hod_can_decide_a_transfer_into_their_own_department(self):
        student_a_program = self.program_b
        transfer = self.StudentTransferRequest.objects.create(
            student=self.student, from_program=student_a_program, to_program=self.program_a,
            reason="Test reason with enough length")
        client = Client()
        client.force_login(self.hod_a)
        res = client.post(
            reverse("university:admin_student_transfer_decision", args=[transfer.pk]),
            {"decision": "APPROVED"})
        self.assertEqual(res.status_code, 302)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, self.StudentTransferRequest.Status.APPROVED)
