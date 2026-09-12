"""Independent acceptance tests for the public login and recovery surface."""

from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from university.identity_models import AccountStatus
from university.identity_services import ensure_account, set_account_status
from university.settings_services import set_setting


class LoginPageSecurityTests(TestCase):
    def setUp(self):
        self.password = "Correct!Login2026"
        self.user = User.objects.create_user(
            username="login.audit", email="login.audit@example.test",
            password=self.password, role=Role.STUDENT)
        cache.clear()

    def test_login_form_has_csrf_and_safe_autofill_attributes(self):
        response = self.client.get(reverse("accounts:login"))
        body = response.content.decode()
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn('autocomplete="username"', body)
        self.assertIn('autocomplete="current-password"', body)
        self.assertNotIn("password=", response.request.get("QUERY_STRING", ""))

    def test_password_recovery_pages_do_not_reflect_reset_tokens(self):
        response = self.client.get(reverse("accounts:password_reset_confirm", args=["not-a-token"]))
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_csrf_is_required_for_login_post(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("accounts:login"), {
            "username": self.user.username,
            "password": self.password,
        })
        self.assertEqual(response.status_code, 403)

    def test_wrong_known_and_unknown_login_fail_generically(self):
        known = self.client.post(reverse("accounts:login"), {
            "username": self.user.username, "password": "wrong-password",
        })
        unknown = self.client.post(reverse("accounts:login"), {
            "username": "does-not-exist", "password": "wrong-password",
        })
        self.assertContains(known, "Invalid username or password.")
        self.assertContains(unknown, "Invalid username or password.")
        self.assertNotContains(known, self.password)
        self.assertNotContains(unknown, self.password)

    def test_external_next_redirect_is_rejected(self):
        response = self.client.post(
            f"{reverse('accounts:login')}?next=https://evil.example/steal",
            {"username": self.user.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.example", response["Location"])

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_login_throttle_is_independent_of_account_lockout(self):
        url = reverse("accounts:login")
        for _ in range(5):
            self.client.post(url, {"username": "unknown", "password": "wrong"})
        response = self.client.post(url, {"username": "unknown", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_password_reset_requests_are_throttled(self):
        url = reverse("accounts:password_reset_request")
        for _ in range(3):
            self.assertEqual(
                self.client.post(url, {"identifier": "unknown@example.test"}).status_code,
                302,
            )
        response = self.client.post(url, {"identifier": "unknown@example.test"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "3600")

    @override_settings(
        DEBUG=False, SECURITY_RATE_LIMIT_ENABLED=True, SECURE_SSL_REDIRECT=False,
    )
    def test_repeated_failures_receive_progressive_throttling(self):
        url = reverse("accounts:login")
        for _ in range(2):
            response = self.client.post(url, {
                "username": self.user.username, "password": "wrong-password",
            })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "2")
        self.user.account.refresh_from_db()
        self.assertEqual(self.user.account.failed_login_attempts, 2)

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_password_reset_confirmation_is_throttled(self):
        url = reverse("accounts:password_reset_confirm", args=["invalid-token"])
        for _ in range(10):
            self.assertEqual(self.client.post(url, {}).status_code, 400)
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")

    def test_login_rotates_a_pre_authentication_session_key(self):
        client = Client()
        client.get(reverse("accounts:login"))
        before = client.session.session_key
        response = client.post(reverse("accounts:login"), {
            "username": self.user.username, "password": self.password,
        })
        after = client.session.session_key
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(before, after)
        self.assertFalse(Session.objects.filter(session_key=before).exists())

    def test_logout_invalidates_the_server_session(self):
        client = Client()
        client.post(reverse("accounts:login"), {
            "username": self.user.username, "password": self.password,
        })
        session_key = client.session.session_key
        response = client.post(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertEqual(client.get(reverse("university:dashboard")).status_code, 302)

    def test_disabling_an_account_invalidates_existing_sessions(self):
        client = Client()
        client.force_login(self.user)
        session_key = client.session.session_key
        ensure_account(self.user)
        set_account_status(self.user, AccountStatus.DISABLED, force=True, notify=False)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertEqual(client.get(reverse("university:dashboard")).status_code, 302)

    def test_idle_timeout_invalidates_an_idle_session(self):
        set_setting("session_timeout_minutes", 1)
        client = Client()
        client.force_login(self.user)
        session = client.session
        session["_last_activity_at"] = (timezone.now() - timedelta(minutes=2)).timestamp()
        session.save()
        session_key = session.session_key
        response = client.get(reverse("university:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertIn("reason=inactivity", response.url)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_login_page_renders_reason_notifications(self):
        reasons = {
            "inactivity": "Your session expired due to 30 minutes of inactivity. Please log in again.",
            "manual": "You have been successfully logged out.",
            "session_conflict": "Logged out because your account was opened in another window.",
            "security": "Your session was terminated for security reasons. Please re-authenticate.",
            "expired": "Your session has expired. Please sign in again.",
        }
        for code, text in reasons.items():
            with self.subTest(reason=code):
                response = self.client.get(f"{reverse('accounts:login')}?reason={code}")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_authenticated_cookies_have_production_flags(self):
        client = Client()
        response = client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": self.password},
            secure=True,
        )
        cookie = response.cookies["sessionid"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_csrf_cookie_has_production_transport_flags(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("accounts:login"), secure=True)
        cookie = response.cookies["csrftoken"]
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")

    @override_settings(MAX_CONCURRENT_SESSIONS=1)
    def test_concurrent_session_policy_keeps_only_the_newest_session(self):
        first = Client()
        first.post(reverse("accounts:login"), {
            "username": self.user.username, "password": self.password,
        })
        first_key = first.session.session_key
        second = Client()
        second.post(reverse("accounts:login"), {
            "username": self.user.username, "password": self.password,
        })
        self.assertFalse(Session.objects.filter(session_key=first_key).exists())
        self.assertTrue(Session.objects.filter(session_key=second.session.session_key).exists())


class SessionHijackingProtectionTests(TestCase):
    """Acceptance tests for stolen-cookie replay and session lifecycle controls."""

    def setUp(self):
        self.password = "Correct!Session2026"
        self.user = User.objects.create_user(
            username="session.audit", email="session.audit@example.test",
            password=self.password, role=Role.STUDENT)
        self.target = User.objects.create_user(
            username="session.target", email="session.target@example.test",
            password=self.password, role=Role.STUDENT)
        cache.clear()

    def login(self):
        client = Client()
        response = client.post(reverse("accounts:login"), {
            "username": self.user.username, "password": self.password,
        })
        self.assertEqual(response.status_code, 302)
        return client

    def copied_session_client(self, client):
        stolen = Client()
        stolen.cookies["sessionid"] = client.cookies["sessionid"].value
        return stolen

    def protected_response(self, client):
        return client.get(reverse("accounts:profile"))

    def test_copied_cookie_is_a_bearer_token_until_revoked(self):
        """A server cannot distinguish a stolen valid cookie from its owner."""
        owner = self.login()
        copied = self.copied_session_client(owner)
        self.assertEqual(self.protected_response(copied).status_code, 200)

        owner.post(reverse("accounts:logout"))
        self.assertEqual(self.protected_response(copied).status_code, 302)

    def test_tampered_cookie_cannot_authenticate(self):
        owner = self.login()
        token = owner.cookies["sessionid"].value
        tampered = Client()
        tampered.cookies["sessionid"] = token[:-1] + ("x" if token[-1] != "x" else "y")
        self.assertEqual(self.protected_response(tampered).status_code, 302)

    def test_old_cookie_is_rejected_after_password_change(self):
        owner = self.login()
        remote = self.login()
        copied = self.copied_session_client(remote)

        response = owner.post(reverse("accounts:profile_settings"), {
            "form_type": "password",
            "password-old_password": self.password,
            "password-new_password1": "New!SessionPassword2026",
            "password-new_password2": "New!SessionPassword2026",
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        self.assertFalse(Session.objects.filter(
            session_key=copied.cookies["sessionid"].value).exists())
        self.assertEqual(self.protected_response(copied).status_code, 302)
        self.assertEqual(self.protected_response(owner).status_code, 200)

    def test_old_cookie_is_rejected_after_account_suspension(self):
        owner = self.login()
        copied = self.copied_session_client(owner)
        copied_key = copied.cookies["sessionid"].value

        ensure_account(self.user)
        set_account_status(self.user, AccountStatus.SUSPENDED, force=True, notify=False)

        self.assertFalse(Session.objects.filter(session_key=copied_key).exists())
        self.assertEqual(self.protected_response(copied).status_code, 302)

    def test_expired_session_cookie_is_rejected(self):
        owner = self.login()
        session = Session.objects.get(session_key=owner.cookies["sessionid"].value)
        session.expire_date = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expire_date"])
        self.assertEqual(self.protected_response(owner).status_code, 302)

    def test_user_id_substitution_does_not_cross_authorization_boundary(self):
        client = self.login()
        response = client.get(reverse("university:user_detail", args=[self.target.pk]))
        self.assertIn(response.status_code, (302, 403))

    def test_role_id_substitution_cannot_reach_staff_role_endpoint(self):
        client = self.login()
        response = client.post(
            reverse("university:staff_user_access_detail", args=[self.target.pk]),
            {"action": "assign", "role_id": "1"},
        )
        self.assertIn(response.status_code, (302, 403))

    def test_bearer_header_cannot_elevate_cookie_session(self):
        client = Client()
        response = client.get(
            reverse("university:dashboard"),
            HTTP_AUTHORIZATION="Bearer forged-token",
        )
        self.assertEqual(response.status_code, 302)

    def test_login_replay_creates_a_new_session_and_old_session_is_not_reused(self):
        first = self.login()
        first_key = first.session.session_key
        second = self.login()
        second_key = second.session.session_key
        self.assertNotEqual(first_key, second_key)
        self.assertTrue(Session.objects.filter(session_key=first_key).exists())
        self.assertTrue(Session.objects.filter(session_key=second_key).exists())

    def test_session_tokens_are_random_and_have_a_bounded_expiry(self):
        client = self.login()
        key = client.session.session_key
        session = Session.objects.get(session_key=key)
        self.assertGreaterEqual(len(key), 32)
        self.assertGreater(session.expire_date, timezone.now())
