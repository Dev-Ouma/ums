"""
Authentication enforcement for the central identity layer.

Four pieces live here and they are deliberately kept together because they are
the same rule seen from four angles:

* ``IdentityModelBackend`` — a disabled, suspended, locked or expired account
  cannot authenticate even with the correct password.
* signal receivers — every attempt, successful or not, lands in the login
  ledger, and repeated failures drive the lockout counter.
* ``PasswordChangeRequiredMiddleware`` — an account flagged
  ``must_change_password`` cannot reach the rest of the system until it does.
* ``SessionIdleTimeoutMiddleware`` — a session that has been idle longer than
  the configured ``session_timeout_minutes`` is signed out server-side, not
  just left to the cookie's own expiry.
"""

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import (user_logged_in, user_logged_out,
                                         user_login_failed)
from django.contrib import messages
from django.dispatch import receiver
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.utils import timezone


def _account_for(user):
    from university.identity_models import UserAccount
    return UserAccount.objects.filter(user=user).first()


class IdentityModelBackend(ModelBackend):
    """Django's model backend plus the account-status gate."""

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        account = _account_for(user)
        # Accounts created before the identity layer existed have no envelope
        # yet; they keep the behaviour they already had.
        if account is None:
            return True
        return account.can_authenticate


# ==============================================================================
# LOGIN LEDGER & LOCKOUT
# ==============================================================================

def _failure_reason_for(user):
    from university.identity_models import AccountStatus, LoginRecord
    if user is None:
        return LoginRecord.Failure.UNKNOWN_USER
    account = _account_for(user)
    if account is None:
        return LoginRecord.Failure.BAD_CREDENTIALS
    status = account.effective_status
    return {
        AccountStatus.LOCKED: LoginRecord.Failure.LOCKED,
        AccountStatus.SUSPENDED: LoginRecord.Failure.SUSPENDED,
        AccountStatus.DISABLED: LoginRecord.Failure.DISABLED,
        AccountStatus.EXPIRED: LoginRecord.Failure.EXPIRED,
        AccountStatus.PENDING: LoginRecord.Failure.INACTIVE,
        AccountStatus.INACTIVE: LoginRecord.Failure.INACTIVE,
        AccountStatus.ARCHIVED: LoginRecord.Failure.DISABLED,
    }.get(status, LoginRecord.Failure.BAD_CREDENTIALS)


@receiver(user_login_failed)
def identity_login_failed(sender, credentials=None, request=None, **kwargs):
    from accounts.models import User
    from university.identity_models import LoginRecord
    from university.identity_services import register_failed_login

    username = (credentials or {}).get("username") or ""
    if not username:
        return
    user = User.objects.filter(username__iexact=username).first()

    # A correct password against a blocked account is a status failure, not a
    # credential failure — recording it accurately is what makes the login
    # history usable during an incident.
    password = (credentials or {}).get("password") or ""
    if user is not None and password and user.check_password(password):
        reason = _failure_reason_for(user)
    else:
        reason = (LoginRecord.Failure.BAD_CREDENTIALS if user
                  else LoginRecord.Failure.UNKNOWN_USER)

    register_failed_login(username, request=request, reason=reason)


@receiver(user_logged_in)
def identity_login_success(sender, request, user, **kwargs):
    from university.identity_services import clear_failed_logins, ensure_account, record_login_attempt
    ensure_account(user)
    clear_failed_logins(user)
    session_key = getattr(getattr(request, "session", None), "session_key", "") or ""
    record_login_attempt(user, user.username, success=True, request=request,
                         session_key=session_key)


@receiver(user_logged_out)
def identity_logout(sender, request, user, **kwargs):
    if user is None or not getattr(user, "is_authenticated", False):
        return
    from university.identity_services import close_login_record
    session_key = getattr(getattr(request, "session", None), "session_key", "") or ""
    close_login_record(user, session_key=session_key)


# ==============================================================================
# FORCED PASSWORD CHANGE
# ==============================================================================

# Routes a user must still reach while their password change is outstanding.
_EXEMPT_ROUTE_NAMES = {
    "password_change_required", "logout", "login", "password_reset_request",
    "password_reset_confirm", "password_reset_done",
}
_EXEMPT_PREFIXES = ("/static/", "/media/", "/django-admin/")


class PasswordChangeRequiredMiddleware:
    """
    Funnel accounts flagged for a mandatory password change to the change form.

    Enforced server-side on every request, so it cannot be skipped by typing a
    different URL.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        path = request.path
        if path.startswith(_EXEMPT_PREFIXES):
            return self.get_response(request)

        account = _account_for(user)
        if account is None:
            return self.get_response(request)

        expired_password = bool(account.password_expires_at
                                and account.password_expires_at <= timezone.now())
        if not (account.must_change_password or expired_password):
            return self.get_response(request)

        try:
            match = resolve(path)
        except Exception:
            return self.get_response(request)
        if match.url_name in _EXEMPT_ROUTE_NAMES:
            return self.get_response(request)

        return redirect(reverse("accounts:password_change_required"))


# ==============================================================================
# IDLE SESSION TIMEOUT
# ==============================================================================

# Session key holding the timestamp of the last request seen for this session.
_IDLE_ACTIVITY_KEY = "_last_activity_at"
# Only rewrite the timestamp this often, so an active session costs at most
# one extra session write per interval instead of one per request — the same
# throttling pattern as LastSeenMiddleware.
_IDLE_WRITE_THROTTLE_SECONDS = 60


class SessionIdleTimeoutMiddleware:
    """
    Sign a user out server-side once their session has been idle too long.

    The window is the administrator-configured ``session_timeout_minutes``
    setting, read fresh on every request so a policy change takes effect
    immediately rather than only for sessions created afterwards. A timeout
    of ``0`` (or unset) disables idle enforcement entirely.

    Must run after ``MessageMiddleware`` in ``MIDDLEWARE`` so the "signed out
    due to inactivity" notice has somewhere to land.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        path = request.path
        if path.startswith(_EXEMPT_PREFIXES):
            return self.get_response(request)

        from university.settings_services import get_setting
        timeout_minutes = get_setting("session_timeout_minutes", 30) or 0
        if timeout_minutes <= 0:
            return self.get_response(request)

        now = timezone.now().timestamp()
        last_seen = request.session.get(_IDLE_ACTIVITY_KEY)
        if last_seen is not None and (now - last_seen) > timeout_minutes * 60:
            auth_logout(request)
            messages.warning(request, "Your session expired due to 30 minutes of inactivity. Please log in again.")
            return redirect(f"{reverse('accounts:login')}?reason=inactivity")

        if last_seen is None or (now - last_seen) >= _IDLE_WRITE_THROTTLE_SECONDS:
            request.session[_IDLE_ACTIVITY_KEY] = now

        return self.get_response(request)
