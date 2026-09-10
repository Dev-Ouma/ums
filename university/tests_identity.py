"""
Acceptance tests for the User Management & Identity Administration module.

These mirror the module's security contract rather than its implementation
details: one identity per person, no plaintext or predictable credentials,
single-use expiring reset links, account status that actually blocks
authentication, server-side permission enforcement, and archival instead of
deletion.
"""

from datetime import timedelta

from django.contrib.auth import authenticate
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university.identity_models import (AccountStatus, InstitutionalEmail, LoginRecord,
                                        PasswordHistoryEntry, PasswordResetToken,
                                        UserAccount, UserGroup, UserType)
from university.models import Department, Program, StaffRoleAssignment


def seed_identity_reference_data():
    from university.permissions_services import (seed_default_permissions_and_roles,
                                                 seed_default_user_groups)
    from university.settings_services import seed_default_settings
    seed_default_settings()
    seed_default_permissions_and_roles()
    seed_default_user_groups()


class IdentityTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_identity_reference_data()
        cls.department = Department.objects.create(name="Computing", code="COMP")
        cls.program = Program.objects.create(
            name="BSc Computer Science", code="BSC-CS", department=cls.department,
            duration_years=4)
        cls.admin = User.objects.create_superuser(
            username="identity.admin", email="identity.admin@example.com",
            password="Str0ng!Admin2026", first_name="Ida", last_name="Admin")
        cls.admin.role = Role.ADMIN
        cls.admin.save(update_fields=["role"])

    def make_account(self, username="test.user", user_type=UserType.STAFF,
                     status=AccountStatus.ACTIVE, password="Str0ng!Pass2026"):
        from university.identity_services import create_user_account
        created = create_user_account(
            user_type=user_type, first_name="Test", last_name="User",
            email=f"{username}@example.com", username=username,
            password_mode="MANUAL", password=password, must_change_password=False,
            status=status, actor=self.admin, notify=False)
        return created["user"]


# ==============================================================================
# 1. ONE CENTRAL IDENTITY
# ==============================================================================

class CentralIdentityTests(IdentityTestBase):
    def test_account_envelope_is_created_with_the_user(self):
        user = self.make_account("envelope.user")
        account = UserAccount.objects.get(user=user)
        self.assertEqual(account.user_type, UserType.STAFF)
        self.assertEqual(UserAccount.objects.filter(user=user).count(), 1)

    def test_username_collision_gets_a_distinct_username(self):
        from university.identity_services import generate_username
        first = generate_username(user_type=UserType.STAFF, first_name="Ada",
                                 last_name="Lovelace")
        User.objects.create(username=first, email="taken@example.com")
        second = generate_username(user_type=UserType.STAFF, first_name="Ada",
                                   last_name="Lovelace")
        self.assertNotEqual(first, second)
        self.assertFalse(User.objects.filter(username=second).exists())

    def test_duplicate_username_is_refused(self):
        from university.identity_services import IdentityError, create_user_account
        self.make_account("unique.person")
        with self.assertRaises(IdentityError):
            create_user_account(
                user_type=UserType.STAFF, first_name="Other", last_name="Person",
                email="other.person@example.com", username="unique.person",
                password_mode="LINK", actor=self.admin, notify=False)

    def test_duplicate_email_is_refused(self):
        from university.identity_services import IdentityError, create_user_account
        self.make_account("first.claimant")
        with self.assertRaises(IdentityError):
            create_user_account(
                user_type=UserType.STAFF, first_name="Second", last_name="Claimant",
                email="first.claimant@example.com", username="second.claimant",
                password_mode="LINK", actor=self.admin, notify=False)


# ==============================================================================
# 2. STUDENT PROVISIONING FROM ADMISSIONS
# ==============================================================================

class StudentProvisioningTests(IdentityTestBase):
    def make_application(self, email="applicant@example.com"):
        from university.models import Application
        return Application.objects.create(
            first_name="Amina", last_name="Otieno", email=email, phone="0700000000",
            program=self.program, date_of_birth=timezone.now().date() - timedelta(days=7500),
            address="Nairobi", status=Application.Status.ACCEPTED)

    def test_matriculation_creates_one_account_without_a_known_password(self):
        from university.admissions_services import matriculate_applicant
        app = self.make_application()
        student, user, credential = matriculate_applicant(app, created_by=self.admin)

        self.assertIsNone(credential, "Matriculation must not hand out a starting password.")
        self.assertEqual(student.user, user)
        self.assertEqual(UserAccount.objects.filter(user=user).count(), 1)
        self.assertEqual(user.account.user_type, UserType.STUDENT)
        for predictable in ["demo1234", "password", "student123", "123456",
                            user.username, f"{user.username}123"]:
            self.assertFalse(user.check_password(predictable),
                             f"Account accepts the predictable password '{predictable}'.")

    def test_matriculation_issues_an_activation_token(self):
        from university.admissions_services import matriculate_applicant
        app = self.make_application(email="activation@example.com")
        _student, user, _ = matriculate_applicant(app, created_by=self.admin)
        token = PasswordResetToken.objects.filter(
            user=user, purpose=PasswordResetToken.Purpose.ACTIVATION).first()
        self.assertIsNotNone(token)
        self.assertGreater(token.expires_at, timezone.now())
        self.assertIsNone(token.used_at)

    def test_rematriculation_does_not_duplicate_the_identity(self):
        from university.admissions_services import matriculate_applicant
        app = self.make_application(email="idempotent@example.com")
        student1, user1, _ = matriculate_applicant(app, created_by=self.admin)
        student2, user2, _ = matriculate_applicant(app, created_by=self.admin)
        self.assertEqual(student1.pk, student2.pk)
        self.assertEqual(user1.pk, user2.pk)
        self.assertEqual(User.objects.filter(pk=user1.pk).count(), 1)
        self.assertEqual(StudentProfile.objects.filter(roll_no=student1.roll_no).count(), 1)

    def test_provisioning_grants_an_institutional_email(self):
        from university.admissions_services import matriculate_applicant
        app = self.make_application(email="mailbox@example.com")
        _student, user, _ = matriculate_applicant(app, created_by=self.admin)
        self.assertTrue(InstitutionalEmail.objects.filter(user=user, is_primary=True).exists())


# ==============================================================================
# 3. PASSWORD GENERATION & POLICY
# ==============================================================================

class PasswordPolicyTests(IdentityTestBase):
    def test_generated_passwords_satisfy_the_active_policy(self):
        from university.identity_services import generate_password, get_password_policy, validate_password
        policy = get_password_policy()
        seen = set()
        for _ in range(25):
            password = generate_password()
            self.assertEqual(validate_password(password), [])
            self.assertGreaterEqual(len(password), policy["min_length"])
            seen.add(password)
        self.assertGreater(len(seen), 20, "Generated passwords must not repeat predictably.")

    def test_generator_never_returns_a_known_weak_value(self):
        from university.identity_services import generate_password
        weak = {"123456", "password", "student123", "demo1234", "qwerty", "letmein"}
        for _ in range(50):
            self.assertNotIn(generate_password().lower(), weak)

    def test_policy_comes_from_settings_not_code(self):
        from university.identity_services import get_password_policy, validate_password
        from university.settings_services import set_setting
        set_setting("password_min_length", 14)
        self.assertEqual(get_password_policy()["min_length"], 14)
        self.assertTrue(validate_password("Short1!a"))
        set_setting("password_min_length", 8)

    def test_password_history_blocks_reuse_and_stores_only_hashes(self):
        from university.identity_services import record_password_change, validate_password
        user = self.make_account("history.user", password="Str0ng!First1")
        record_password_change(user, "Str0ng!Second2", actor=self.admin, notify=False)
        entries = PasswordHistoryEntry.objects.filter(user=user)
        self.assertTrue(entries.exists())
        for entry in entries:
            self.assertNotIn("Str0ng!Second2", entry.password_hash)
            self.assertNotIn("Str0ng!First1", entry.password_hash)
        self.assertTrue(validate_password("Str0ng!Second2", user=user))

    def test_password_is_stored_as_a_one_way_hash(self):
        user = self.make_account("hash.user", password="Str0ng!Hashed9")
        user.refresh_from_db()
        self.assertNotIn("Str0ng!Hashed9", user.password)
        self.assertTrue(user.check_password("Str0ng!Hashed9"))


# ==============================================================================
# 4. RESET TOKENS
# ==============================================================================

class ResetTokenTests(IdentityTestBase):
    def test_token_is_single_use(self):
        from university.identity_services import consume_reset_token, issue_reset_token
        user = self.make_account("single.use")
        raw, _record = issue_reset_token(user, actor=self.admin)
        ok, _msg, _user = consume_reset_token(raw, "Str0ng!Chosen1")
        self.assertTrue(ok)
        ok_again, message, _ = consume_reset_token(raw, "Str0ng!Chosen2")
        self.assertFalse(ok_again)
        self.assertIn("invalid or has expired", message)

    def test_expired_token_is_refused(self):
        from university.identity_services import consume_reset_token, issue_reset_token
        user = self.make_account("expired.token")
        raw, record = issue_reset_token(user, actor=self.admin)
        record.expires_at = timezone.now() - timedelta(minutes=1)
        record.save(update_fields=["expires_at"])
        ok, _msg, _ = consume_reset_token(raw, "Str0ng!Chosen3")
        self.assertFalse(ok)

    def test_token_is_never_stored_in_plaintext(self):
        from university.identity_services import issue_reset_token
        user = self.make_account("stored.token")
        raw, record = issue_reset_token(user, actor=self.admin)
        self.assertNotEqual(record.token_hash, raw)
        self.assertNotIn(raw, record.token_hash)
        self.assertFalse(PasswordResetToken.objects.filter(token_hash=raw).exists())

    def test_a_new_reset_invalidates_outstanding_tokens(self):
        from university.identity_services import consume_reset_token, issue_reset_token
        user = self.make_account("rotating.token")
        first_raw, _ = issue_reset_token(user, actor=self.admin)
        second_raw, _ = issue_reset_token(user, actor=self.admin)
        ok, _msg, _ = consume_reset_token(second_raw, "Str0ng!Chosen4")
        self.assertTrue(ok)
        ok_first, _msg, _ = consume_reset_token(first_raw, "Str0ng!Chosen5")
        self.assertFalse(ok_first)

    def test_reset_rejects_a_password_that_breaks_policy(self):
        from university.identity_services import consume_reset_token, issue_reset_token
        user = self.make_account("weak.reset")
        raw, _ = issue_reset_token(user, actor=self.admin)
        ok, message, _ = consume_reset_token(raw, "abc")
        self.assertFalse(ok)
        self.assertIn("at least", message)
        self.assertTrue(PasswordResetToken.objects.filter(user=user, used_at=None).exists())

    def test_forgot_password_does_not_reveal_whether_an_account_exists(self):
        client = Client()
        url = reverse("accounts:password_reset_request")
        known = client.post(url, {"identifier": "weak.reset@example.com"}, follow=True)
        unknown = client.post(url, {"identifier": "nobody.here@example.com"}, follow=True)
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.redirect_chain, unknown.redirect_chain)


# ==============================================================================
# 5. ACCOUNT STATUS BLOCKS AUTHENTICATION
# ==============================================================================

class AccountStatusTests(IdentityTestBase):
    def test_active_account_can_authenticate(self):
        self.make_account("status.active", password="Str0ng!Active1")
        self.assertIsNotNone(authenticate(username="status.active",
                                          password="Str0ng!Active1"))

    def test_blocked_statuses_cannot_authenticate(self):
        from university.identity_services import set_account_status
        for index, status in enumerate([AccountStatus.INACTIVE, AccountStatus.SUSPENDED,
                                        AccountStatus.DISABLED, AccountStatus.ARCHIVED,
                                        AccountStatus.PENDING]):
            username = f"status.blocked{index}"
            user = self.make_account(username, password="Str0ng!Blocked1")
            set_account_status(user, status, actor=self.admin, notify=False, force=True)
            self.assertIsNone(
                authenticate(username=username, password="Str0ng!Blocked1"),
                f"A {status} account authenticated with a correct password.")

    def test_expired_account_cannot_authenticate(self):
        user = self.make_account("status.expired", password="Str0ng!Expired1")
        account = user.account
        account.expiry_date = timezone.now().date() - timedelta(days=1)
        account.save(update_fields=["expiry_date"])
        self.assertIsNone(authenticate(username="status.expired",
                                       password="Str0ng!Expired1"))

    def test_status_change_is_recorded_for_audit(self):
        from university.identity_services import set_account_status
        from university.models import AuditLog
        user = self.make_account("status.audited")
        set_account_status(user, AccountStatus.SUSPENDED, actor=self.admin,
                           reason="Disciplinary hold", notify=False, force=True)
        self.assertTrue(AuditLog.objects.filter(
            module=AuditLog.Module.AUTH, entity_id=user.pk).exists())


# ==============================================================================
# 6. LOCKOUT
# ==============================================================================

class LockoutTests(IdentityTestBase):
    def test_account_locks_after_the_configured_number_of_failures(self):
        from university.identity_services import get_password_policy, register_failed_login
        from university.settings_services import set_setting
        set_setting("max_login_attempts", 3)
        user = self.make_account("lockout.user", password="Str0ng!Lock1")
        for _ in range(get_password_policy()["max_attempts"]):
            register_failed_login("lockout.user")
        user.account.refresh_from_db()
        self.assertIsNotNone(user.account.locked_until)
        self.assertIsNone(authenticate(username="lockout.user", password="Str0ng!Lock1"))

    def test_unlock_clears_the_counter_and_restores_access(self):
        from university.identity_services import register_failed_login, unlock_account
        from university.settings_services import set_setting
        set_setting("max_login_attempts", 3)
        user = self.make_account("unlock.user", password="Str0ng!Unlock1")
        for _ in range(3):
            register_failed_login("unlock.user")
        unlock_account(user, actor=self.admin)
        user.account.refresh_from_db()
        self.assertIsNone(user.account.locked_until)
        self.assertEqual(user.account.failed_login_attempts, 0)
        self.assertIsNotNone(authenticate(username="unlock.user",
                                          password="Str0ng!Unlock1"))

    def test_failed_attempts_are_written_to_the_login_ledger(self):
        client = Client()
        self.make_account("ledger.user", password="Str0ng!Ledger1")
        client.post(reverse("accounts:login"),
                    {"username": "ledger.user", "password": "wrong-password"})
        record = LoginRecord.objects.filter(username_attempted="ledger.user").first()
        self.assertIsNotNone(record)
        self.assertFalse(record.success)


# ==============================================================================
# 7. SERVER-SIDE PERMISSION ENFORCEMENT
# ==============================================================================

class PermissionEnforcementTests(IdentityTestBase):
    def setUp(self):
        self.plain = self.make_account("plain.staff", password="Str0ng!Plain1")
        self.target = self.make_account("target.staff", password="Str0ng!Target1")
        self.client = Client()

    def test_unprivileged_user_cannot_load_the_console(self):
        """Denial comes from the route, not from a hidden button."""
        self.client.force_login(self.plain)
        for name in ["user_dashboard", "user_list", "password_management",
                     "account_status_board", "user_settings", "login_history"]:
            response = self.client.get(reverse(f"university:{name}"))
            self.assertEqual(response.status_code, 403, f"{name} was not gated")

    def test_unprivileged_user_cannot_post_an_action(self):
        self.client.force_login(self.plain)
        response = self.client.post(
            reverse("university:user_action", args=[self.target.pk, "suspend"]))
        self.assertEqual(response.status_code, 403)
        self.target.account.refresh_from_db()
        self.assertEqual(self.target.account.status, AccountStatus.ACTIVE)

    def test_anonymous_user_is_sent_to_the_sign_in_page(self):
        response = Client().get(reverse("university:user_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_administrator_can_load_every_console_page(self):
        self.client.force_login(self.admin)
        for name in ["user_dashboard", "user_list", "user_create", "student_accounts",
                     "staff_accounts", "password_management", "account_status_board",
                     "username_management", "group_list", "email_accounts",
                     "login_security", "login_history", "user_activity",
                     "bulk_operations", "user_settings"]:
            response = self.client.get(reverse(f"university:{name}"))
            self.assertEqual(response.status_code, 200, f"{name} returned "
                                                        f"{response.status_code}")


# ==============================================================================
# 8. ADMINISTRATOR OPERATIONS
# ==============================================================================

class AdministratorActionTests(IdentityTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)
        self.target = self.make_account("action.target", password="Str0ng!Action1")

    def post_action(self, action):
        return self.client.post(
            reverse("university:user_action", args=[self.target.pk, action]))

    def test_administrator_cannot_see_an_existing_password(self):
        response = self.client.get(
            reverse("university:user_detail", args=[self.target.pk]))
        body = response.content.decode()
        self.assertNotIn("Str0ng!Action1", body)
        self.target.refresh_from_db()
        self.assertNotIn(self.target.password, body)

    def test_send_reset_link_creates_a_single_use_token(self):
        self.post_action("send_reset_link")
        tokens = PasswordResetToken.objects.filter(user=self.target, used_at=None)
        self.assertEqual(tokens.count(), 1)
        self.assertGreater(tokens.first().expires_at, timezone.now())

    def test_force_password_change_blocks_the_rest_of_the_system(self):
        self.post_action("force_password_change")
        self.target.account.refresh_from_db()
        self.assertTrue(self.target.account.must_change_password)

        user_client = Client()
        user_client.force_login(self.target)
        response = user_client.get(reverse("university:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:password_change_required"), response["Location"])

    def test_suspend_takes_effect_immediately(self):
        self.post_action("suspend")
        self.target.account.refresh_from_db()
        self.assertEqual(self.target.account.status, AccountStatus.SUSPENDED)
        self.assertIsNone(authenticate(username="action.target",
                                       password="Str0ng!Action1"))

    def test_administrator_cannot_change_their_own_status(self):
        response = self.client.post(
            reverse("university:user_action", args=[self.admin.pk, "suspend"]))
        self.assertIn(response.status_code, (302, 403))
        self.admin.refresh_from_db()
        account = UserAccount.objects.filter(user=self.admin).first()
        if account:
            self.assertNotEqual(account.status, AccountStatus.SUSPENDED)

    def test_group_assignment_materialises_real_role_assignments(self):
        group = UserGroup.objects.filter(roles__isnull=False).distinct().first()
        self.assertIsNotNone(group, "Seeding should provide at least one group with roles.")
        self.client.post(
            reverse("university:user_action", args=[self.target.pk, "assign_group"]),
            {"group": group.pk})
        self.assertTrue(self.target.group_memberships.filter(group=group).exists())
        self.assertTrue(StaffRoleAssignment.objects.filter(
            user=self.target, role__in=group.roles.all()).exists())


# ==============================================================================
# 9. ARCHIVE INSTEAD OF DELETE
# ==============================================================================

class ArchivalTests(IdentityTestBase):
    def test_archiving_keeps_the_record_and_its_history(self):
        from university.identity_services import set_account_status
        faculty_user = self.make_account("archive.lecturer")
        profile = FacultyProfile.objects.create(
            user=faculty_user, employee_id="EMP-ARCH-1", department=self.department,
            designation="Lecturer")
        set_account_status(faculty_user, AccountStatus.ARCHIVED, actor=self.admin,
                           notify=False, force=True)

        self.assertTrue(User.objects.filter(pk=faculty_user.pk).exists())
        self.assertTrue(FacultyProfile.objects.filter(pk=profile.pk).exists())
        faculty_user.account.refresh_from_db()
        self.assertEqual(faculty_user.account.status, AccountStatus.ARCHIVED)
        self.assertIsNone(authenticate(username="archive.lecturer",
                                       password="Str0ng!Pass2026"))

    def test_restore_from_recycle_bin_does_not_reinstate_a_known_password(self):
        from university.recycle_bin_services import move_to_recycle_bin, restore_from_recycle_bin
        student_user = self.make_account("recycle.student", user_type=UserType.STUDENT)
        profile = StudentProfile.objects.create(
            user=student_user, roll_no="STU-REC-1", program=self.program,
            current_semester=1)
        item = move_to_recycle_bin(profile, user=self.admin)
        restored, _message = restore_from_recycle_bin(item.pk, user=self.admin)
        self.assertIsNotNone(restored)
        restored.user.refresh_from_db()
        for predictable in ["demo1234", "password", "student123", "123456"]:
            self.assertFalse(restored.user.check_password(predictable))


# ==============================================================================
# 10. EXPORTS AND BULK IMPORT
# ==============================================================================

class ExportAndImportTests(IdentityTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_csv_export_contains_no_credentials(self):
        user = self.make_account("export.user", password="Str0ng!Export1")
        response = self.client.get(reverse("university:user_export", args=["csv"]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("export.user", body)
        self.assertNotIn("Str0ng!Export1", body)
        user.refresh_from_db()
        self.assertNotIn(user.password, body)
        for forbidden in ["password", "token", "secret"]:
            self.assertNotIn(forbidden, body.splitlines()[0].lower())

    def test_import_preview_reports_outcomes_without_writing(self):
        from university.identity_services import validate_import_rows
        before = User.objects.count()
        rows = [
            {"_row": 2, "registration_number": "STU-IMP-1", "first_name": "Neo",
             "last_name": "Mwangi", "programme_code": self.program.code},
            {"_row": 3, "registration_number": "", "first_name": "No",
             "last_name": "Identifier"},
        ]
        preview = validate_import_rows(rows, UserType.STUDENT)
        self.assertEqual(preview[0]["outcome"], "CREATE")
        self.assertEqual(preview[1]["outcome"], "ERROR")
        self.assertEqual(User.objects.count(), before)

    def test_import_commit_creates_accounts_without_shared_passwords(self):
        from university.identity_services import commit_import, validate_import_rows
        rows = [{"_row": 2, "registration_number": "STU-IMP-2", "first_name": "Zuri",
                 "last_name": "Kamau", "programme_code": self.program.code}]
        preview = validate_import_rows(rows, UserType.STUDENT)
        batch = commit_import(preview, UserType.STUDENT, actor=self.admin, notify=False)
        self.assertIsNotNone(batch)
        created = StudentProfile.objects.filter(roll_no__iexact="STU-IMP-2").first()
        self.assertIsNotNone(created)
        for predictable in ["demo1234", "Student@2026", "password", "student123"]:
            self.assertFalse(created.user.check_password(predictable))

    def test_import_template_has_no_password_column(self):
        for user_type in [UserType.STUDENT, UserType.STAFF]:
            response = self.client.get(
                reverse("university:bulk_import_template", args=[user_type, "csv"]))
            self.assertEqual(response.status_code, 200)
            header = response.content.decode().splitlines()[0].lower()
            self.assertNotIn("password", header)


# ==============================================================================
# 11. SETTINGS AND SECRETS
# ==============================================================================

class SettingsTests(IdentityTestBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_provider_secret_is_never_rendered(self):
        from university.settings_services import get_setting, set_setting
        set_setting("email_host_password", "super-secret-smtp-value")
        response = self.client.get(reverse("university:user_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("super-secret-smtp-value", response.content.decode())
        self.assertEqual(get_setting("email_host_password"), "super-secret-smtp-value")

    def test_blank_submission_keeps_the_existing_secret(self):
        from university.settings_services import get_setting, set_setting
        set_setting("email_host_password", "keep-this-secret")
        self.client.post(reverse("university:user_settings"),
                         {"group": "email", "email_host_password": ""})
        self.assertEqual(get_setting("email_host_password"), "keep-this-secret")

    def test_username_and_email_formats_are_configurable(self):
        from university.identity_services import generate_username
        from university.settings_services import set_setting
        set_setting("student_username_pattern", "{reg_no}")
        generated = generate_username(user_type=UserType.STUDENT, first_name="Pat",
                                      last_name="Njoroge", reg_no="STU-FMT-9")
        self.assertIn("stu-fmt-9", generated.lower())


# ==============================================================================
# 12. MODULE REGISTRATION
# ==============================================================================

class ModuleRegistrationTests(IdentityTestBase):
    def test_module_is_registered_in_module_management(self):
        from university.models import SystemModule, SystemSubmodule
        from university.module_services import seed_system_modules
        seed_system_modules()
        module = SystemModule.objects.filter(code="user_management").first()
        self.assertIsNotNone(module)
        self.assertTrue(SystemSubmodule.objects.filter(
            module=module, code="credential_management").exists())
