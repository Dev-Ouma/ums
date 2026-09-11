"""End-to-end tests for the account menu, profile settings and logout flow."""
import io
import shutil
import tempfile

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university.models import Department, Program

MEDIA = tempfile.mkdtemp(prefix="ums-test-media-")

PASSWORD = "OriginalPass123"


def png_upload(name="avatar.png"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (108, 92, 231)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def oversized_png(name="big.png"):
    """A genuine PNG larger than the 2 MB limit (random noise resists deflate)."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    import os
    buf = io.BytesIO()
    Image.frombytes("RGB", (1400, 1400), os.urandom(1400 * 1400 * 3)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def bmp_upload(name="avatar.bmp"):
    """A decodable image in a format the form does not accept."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 40, 40)).save(buf, format="BMP")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/bmp")


class AccountTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Computer Science", code="CSE")
        cls.program = Program.objects.create(
            name="BSc Computer Science", code="BSCS", department=cls.department, duration_years=4)

        cls.admin = User.objects.create_user(
            username="admin.jo", password=PASSWORD, role=Role.ADMIN,
            first_name="Sam", last_name="Ahmed", email="admin.jo@ums.test")
        cls.faculty = User.objects.create_user(
            username="prof.kay", password=PASSWORD, role=Role.FACULTY,
            first_name="Ada", last_name="Kay", email="prof.kay@ums.test")
        cls.student = User.objects.create_user(
            username="stu.lee", password=PASSWORD, role=Role.STUDENT,
            first_name="Lee", last_name="Ono", email="stu.lee@ums.test")

        FacultyProfile.objects.create(
            user=cls.faculty, employee_id="EMP001", department=cls.department,
            designation="Senior Lecturer", specialization="Compilers")
        StudentProfile.objects.create(
            user=cls.student, roll_no="UMS0001", program=cls.program, current_semester=3)

    def login(self, user, password=PASSWORD):
        """Sign in through the real login view.

        Client.login() shortcuts the view and hands the user_logged_in signal a
        bare HttpRequest with no REMOTE_ADDR, so it cannot exercise the login
        activity stamping. Posting the form is what a browser actually does.
        """
        client = Client()
        response = client.post(reverse("accounts:login"),
                               {"username": user.username, "password": password})
        self.assertEqual(response.status_code, 302, "login form was rejected")
        return client


class AccountMenuTests(AccountTestBase):
    def test_menu_renders_for_every_role(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                html = client.get(reverse("university:dashboard")).content.decode()
                self.assertIn('data-acct-trigger', html)
                self.assertIn(reverse("accounts:profile_settings"), html)
                self.assertIn(user.display_name, html)
                # Logout must be a POST form carrying a CSRF token, not a link.
                self.assertIn(f'action="{reverse("accounts:logout")}"', html)
                self.assertIn("csrfmiddlewaretoken", html)

    def test_menu_shows_initials_when_no_custom_avatar(self):
        client = self.login(self.admin)
        html = client.get(reverse("university:dashboard")).content.decode()
        self.assertIn("SA", html)  # Sam Ahmed

    def test_menu_absent_for_anonymous_public_page(self):
        html = Client().get(reverse("university:home")).content.decode()
        self.assertNotIn("data-acct-trigger", html)
        self.assertIn(reverse("accounts:login"), html)

    def test_menu_present_on_public_pages_when_signed_in(self):
        client = self.login(self.student)
        html = client.get(reverse("university:home")).content.decode()
        self.assertIn("data-acct-trigger", html)


class ProfileUpdateTests(AccountTestBase):
    def post_profile(self, client, user=None, **overrides):
        """Post the Details tab exactly as the rendered form would."""
        data = {
            "form_type": "profile",
            "profile-first_name": "Updated",
            "profile-last_name": "Name",
            "profile-email": "updated@ums.test",
            "profile-phone": "+254700111222",
        }
        if user is not None and user.is_student:
            data.update({"details-gender": "O", "details-address": "Campus",
                         "details-guardian_name": "Guardian"})
        elif user is not None and user.is_faculty:
            data.update({"details-specialization": "Compilers"})
        data.update(overrides)
        return client.post(reverse("accounts:profile_settings"), data, follow=True)

    def test_every_role_can_update_their_own_details(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                email = f"new-{user.username}@ums.test"
                response = self.post_profile(client, user, **{"profile-email": email})
                self.assertEqual(response.status_code, 200)
                user.refresh_from_db()
                self.assertEqual(user.first_name, "Updated")
                self.assertEqual(user.email, email)
                self.assertEqual(user.phone, "+254700111222")

    def test_updated_name_is_reflected_in_the_header_immediately(self):
        client = self.login(self.student)
        response = self.post_profile(client, self.student,
                                     **{"profile-first_name": "Zuri",
                                        "profile-last_name": "Mwangi"})
        self.assertContains(response, "Zuri Mwangi")

    def test_success_message_is_shown(self):
        client = self.login(self.admin)
        response = self.post_profile(client, self.admin)
        notes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Your profile has been updated.", notes)

    def test_duplicate_email_is_rejected(self):
        client = self.login(self.student)
        response = self.post_profile(client, self.student, **{"profile-email": self.admin.email})
        self.assertContains(response, "already in use")
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, "stu.lee@ums.test")

    def test_invalid_email_is_rejected_and_nothing_is_saved(self):
        client = self.login(self.faculty)
        response = self.post_profile(client, self.faculty, **{"profile-email": "not-an-email"})
        self.assertEqual(response.status_code, 200)
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.first_name, "Ada")

    def test_student_can_edit_their_own_student_details(self):
        client = self.login(self.student)
        self.post_profile(client, self.student, **{
            "details-gender": "F",
            "details-date_of_birth": "2002-04-11",
            "details-address": "PO Box 42, Nairobi",
            "details-guardian_name": "A Guardian",
            "details-guardian_relationship": "Parent",
            "details-guardian_phone": "+254711222333",
            "details-guardian_email": "parent@ums.test",
            "details-guardian_address": "PO Box 7, Nairobi",
        })
        profile = StudentProfile.objects.get(pk=self.student.student_profile.pk)
        self.assertEqual(profile.address, "PO Box 42, Nairobi")
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.guardian_name, "A Guardian")
        self.assertEqual(profile.guardian_relationship, "Parent")
        self.assertEqual(profile.guardian_phone, "+254711222333")
        self.assertEqual(profile.guardian_email, "parent@ums.test")
        self.assertEqual(profile.guardian_address, "PO Box 7, Nairobi")

    def test_student_profile_displays_parent_guardian_details(self):
        profile = self.student.student_profile
        profile.guardian_name = "A Guardian"
        profile.guardian_relationship = "Sponsor"
        profile.guardian_phone = "+254700123456"
        profile.guardian_email = "guardian@ums.test"
        profile.guardian_address = "Nairobi"
        profile.save()
        response = self.login(self.student).get(reverse("accounts:profile"))
        self.assertContains(response, "A Guardian")
        self.assertContains(response, "Sponsor")
        self.assertContains(response, "+254700123456")
        self.assertContains(response, "guardian@ums.test")

    def test_future_date_of_birth_is_rejected(self):
        client = self.login(self.student)
        response = self.post_profile(client, self.student, **{
            "details-gender": "M",
            "details-date_of_birth": "2099-01-01",
            "details-address": "Somewhere",
            "details-guardian_name": "G",
        })
        self.assertContains(response, "cannot be in the future")


class FieldAuthorisationTests(AccountTestBase):
    """A user may only change the fields they are authorised to manage."""

    def test_student_cannot_rewrite_registry_fields(self):
        client = self.login(self.student)
        client.post(reverse("accounts:profile_settings"), {
            "form_type": "profile",
            "profile-first_name": "Lee", "profile-last_name": "Ono",
            "profile-email": "stu.lee@ums.test", "profile-phone": "1",
            "details-gender": "M", "details-address": "Hostel", "details-guardian_name": "G",
            # Injected fields that are not part of the form:
            "details-roll_no": "HACKED",
            "details-current_semester": "8",
            "profile-role": Role.ADMIN,
            "profile-is_superuser": "true",
        }, follow=True)
        self.student.refresh_from_db()
        profile = self.student.student_profile
        self.assertEqual(profile.roll_no, "UMS0001")
        self.assertEqual(profile.current_semester, 3)
        self.assertEqual(self.student.role, Role.STUDENT)
        self.assertFalse(self.student.is_superuser)

    def test_faculty_cannot_change_hr_controlled_fields(self):
        client = self.login(self.faculty)
        client.post(reverse("accounts:profile_settings"), {
            "form_type": "profile",
            "profile-first_name": "Ada", "profile-last_name": "Kay",
            "profile-email": "prof.kay@ums.test", "profile-phone": "1",
            "details-specialization": "Type Theory",
            "details-employee_id": "EMP999",
            "details-designation": "Professor",
        }, follow=True)
        profile = FacultyProfile.objects.get(pk=self.faculty.faculty_profile.pk)
        self.assertEqual(profile.specialization, "Type Theory")   # permitted
        self.assertEqual(profile.employee_id, "EMP001")           # blocked
        self.assertEqual(profile.designation, "Senior Lecturer")  # blocked

    def test_settings_page_requires_authentication(self):
        response = Client().get(reverse("accounts:profile_settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_a_user_only_ever_edits_their_own_record(self):
        """There is no id in the URL, so one account cannot target another."""
        client = self.login(self.student)
        client.post(reverse("accounts:profile_settings"), {
            "form_type": "profile",
            "profile-first_name": "Intruder", "profile-last_name": "X",
            "profile-email": "intruder@ums.test", "profile-phone": "1",
            "profile-id": self.admin.pk, "id": self.admin.pk,
            "details-gender": "M", "details-address": "a", "details-guardian_name": "g",
        }, follow=True)
        self.admin.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Sam")
        self.assertEqual(self.student.first_name, "Intruder")


@override_settings(MEDIA_ROOT=MEDIA)
class AvatarTests(AccountTestBase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_upload_persists_and_becomes_the_avatar(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                response = client.post(reverse("accounts:profile_settings"), {
                    "form_type": "avatar",
                    "avatar-avatar_image": png_upload(f"{user.username}.png"),
                }, follow=True)
                self.assertEqual(response.status_code, 200)
                user.refresh_from_db()
                self.assertTrue(user.avatar_image)
                self.assertTrue(user.has_custom_avatar)
                self.assertIn(user.avatar_image.url, response.content.decode())

    def test_oversized_upload_is_rejected(self):
        """A *valid* image over the limit must still be refused.

        Junk bytes are caught earlier by ImageField's own verification, so the
        size guard is only genuinely exercised by a real, large image.
        """
        client = self.login(self.admin)
        big = oversized_png()
        self.assertGreater(big.size, 2 * 1024 * 1024)
        response = client.post(reverse("accounts:profile_settings"), {
            "form_type": "avatar", "avatar-avatar_image": big}, follow=True)
        self.assertContains(response, "under 2 MB")
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.avatar_image)

    def test_disallowed_image_type_is_rejected(self):
        """A decodable image in a format we do not accept is refused."""
        client = self.login(self.student)
        response = client.post(reverse("accounts:profile_settings"), {
            "form_type": "avatar", "avatar-avatar_image": bmp_upload()}, follow=True)
        self.assertContains(response, "Unsupported file type")
        self.student.refresh_from_db()
        self.assertFalse(self.student.avatar_image)

    def test_non_image_payload_is_rejected(self):
        """Anything that is not a decodable image never reaches storage."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        client = self.login(self.faculty)
        payload = SimpleUploadedFile("payload.svg", b"<svg onload=alert(1)/>",
                                     content_type="image/svg+xml")
        response = client.post(reverse("accounts:profile_settings"), {
            "form_type": "avatar", "avatar-avatar_image": payload}, follow=True)
        self.assertContains(response, "Upload a valid image")
        self.faculty.refresh_from_db()
        self.assertFalse(self.faculty.avatar_image)

    def test_falls_back_to_generated_avatar_when_none_uploaded(self):
        self.assertFalse(self.admin.has_custom_avatar)
        self.assertIn("ui-avatars.com", self.admin.avatar)


class PasswordChangeTests(AccountTestBase):
    def post_password(self, client, old=PASSWORD, new="BrandNewPass456"):
        return client.post(reverse("accounts:profile_settings"), {
            "form_type": "password",
            "password-old_password": old,
            "password-new_password1": new,
            "password-new_password2": new,
        }, follow=True)

    def test_every_role_can_change_their_password(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                new = f"Fresh{user.pk}Password!"
                self.post_password(client, new=new)
                user.refresh_from_db()
                self.assertTrue(user.check_password(new))
                self.assertFalse(user.check_password(PASSWORD))

    def test_session_survives_the_password_change(self):
        client = self.login(self.student)
        self.post_password(client)
        # Still authenticated on a protected page rather than bounced to login.
        self.assertEqual(client.get(reverse("university:dashboard")).status_code, 200)

    def test_wrong_current_password_is_rejected(self):
        client = self.login(self.admin)
        response = self.post_password(client, old="TotallyWrong")
        self.assertContains(response, "entered incorrectly")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(PASSWORD))

    def test_mismatched_confirmation_is_rejected(self):
        client = self.login(self.faculty)
        response = client.post(reverse("accounts:profile_settings"), {
            "form_type": "password",
            "password-old_password": PASSWORD,
            "password-new_password1": "MismatchOne123",
            "password-new_password2": "MismatchTwo123",
        }, follow=True)
        self.assertContains(response, "didn")  # "didn’t match"
        self.faculty.refresh_from_db()
        self.assertTrue(self.faculty.check_password(PASSWORD))

    def test_weak_password_is_rejected_by_validators(self):
        client = self.login(self.student)
        response = self.post_password(client, new="123")
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(PASSWORD))


class LogoutTests(AccountTestBase):
    def test_logout_rejects_get(self):
        """A GET logout can be triggered by any embedded link or a prefetch."""
        client = self.login(self.admin)
        self.assertEqual(client.get(reverse("accounts:logout")).status_code, 405)
        self.assertEqual(client.get(reverse("university:dashboard")).status_code, 200)

    def test_post_logs_out_and_redirects_to_login_surface(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                response = client.post(reverse("accounts:logout"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("university:home"))

    def test_protected_pages_are_closed_after_logout(self):
        client = self.login(self.student)
        client.post(reverse("accounts:logout"))
        for name in ("university:dashboard", "accounts:profile", "accounts:profile_settings"):
            with self.subTest(view=name):
                response = client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response.url)

    def test_session_is_flushed_not_merely_marked(self):
        client = self.login(self.faculty)
        session_key = client.cookies["sessionid"].value
        client.post(reverse("accounts:logout"))
        from django.contrib.sessions.models import Session
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())


class LoginActivityTests(AccountTestBase):
    def test_login_stamps_first_access_last_access_and_ip(self):
        self.assertIsNone(self.student.first_seen_at)
        self.login(self.student)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.first_seen_at)
        self.assertIsNotNone(self.student.last_seen_at)
        self.assertEqual(self.student.last_login_ip, "127.0.0.1")

    def test_first_access_is_not_overwritten_by_later_logins(self):
        self.login(self.admin)
        self.admin.refresh_from_db()
        first = self.admin.first_seen_at
        self.login(self.admin)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_seen_at, first)

    def test_activity_is_not_shown_in_the_account_dropdown(self):
        """The menu stays lean; activity belongs on the profile page."""
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                html = client.get(reverse("university:dashboard")).content.decode()
                self.assertIn("data-acct-trigger", html)
                self.assertNotIn("Login activity", html)
                self.assertNotIn("First access to site", html)

    def test_activity_is_rendered_on_the_profile_page(self):
        for user in (self.admin, self.faculty, self.student):
            with self.subTest(role=user.role):
                client = self.login(user)
                html = client.get(reverse("accounts:profile")).content.decode()
                self.assertIn("Login activity", html)
                self.assertIn("First access to site", html)
                self.assertIn("Last access to site", html)
                self.assertIn("127.0.0.1", html)

    def test_activity_is_rendered_on_the_settings_tab(self):
        client = self.login(self.student)
        html = client.get(reverse("accounts:profile_settings")).content.decode()
        self.assertIn("Login activity", html)
        self.assertIn("Last IP address", html)

    def test_browsing_refreshes_last_access(self):
        client = self.login(self.student)
        self.student.refresh_from_db()
        before = self.student.last_seen_at
        # Clear the throttle marker so the next request writes through.
        session = client.session
        del session["_last_seen_written"]
        session.save()
        client.get(reverse("university:dashboard"))
        self.student.refresh_from_db()
        self.assertGreater(self.student.last_seen_at, before)

    def test_each_user_only_sees_their_own_activity(self):
        self.login(self.admin)
        client = self.login(self.student)
        self.admin.refresh_from_db()
        html = client.get(reverse("accounts:profile_settings")).content.decode()
        self.assertNotIn(self.admin.email, html)
