"""
Central Identity Service — the single authority for user accounts in UMS.

Every module that needs to create a user, generate a username, issue a
credential, change an account's status or resolve an institutional email calls
into this module. Nothing else is permitted to hash a password, mint a token or
invent a username, which is what keeps one identity per person across
Admissions, Finance, Academics, Examinations and System Administration.
"""

import hashlib
import re
import secrets
import string
import unicodedata

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university.audit_services import get_client_ip, log_activity
from university.identity_models import (
    ALLOWED_STATUS_TRANSITIONS,
    AccountStatus,
    InstitutionalEmail,
    LoginRecord,
    PasswordHistoryEntry,
    PasswordResetToken,
    UserAccount,
    UserGroup,
    UserGroupMembership,
    UserType,
)
from university.models import AuditLog, StaffRole, StaffRoleAssignment
from university.settings_services import get_setting


AUDIT_MODULE = AuditLog.Module.AUTH


# ==============================================================================
# 1. ACCOUNT RESOLUTION
# ==============================================================================

def resolve_user_type(user):
    """Map the base auth role onto an identity user type."""
    role = getattr(user, "role", "")
    if role == Role.STUDENT:
        return UserType.STUDENT
    if role == Role.FACULTY:
        return UserType.STAFF
    if role == Role.ADMIN:
        return UserType.ADMIN
    return UserType.OTHER


def ensure_account(user, user_type=None, status=None, created_by=None):
    """
    Return the ``UserAccount`` for ``user``, creating it on first touch.

    Pre-existing users (seeded demo accounts, historical records) predate this
    module, so the envelope is materialised lazily and defaults to ACTIVE — the
    behaviour they already had — rather than locking anyone out on upgrade.
    """
    account = UserAccount.objects.filter(user=user).first()
    if account:
        if user_type and account.user_type != user_type:
            account.user_type = user_type
            account.save(update_fields=["user_type"])
        return account

    return UserAccount.objects.create(
        user=user,
        user_type=user_type or resolve_user_type(user),
        status=status or (AccountStatus.ACTIVE if user.is_active else AccountStatus.DISABLED),
        activated_at=timezone.now() if user.is_active else None,
        password_changed_at=user.date_joined,
        created_by=created_by,
    )


def get_account(user):
    """Account envelope for ``user`` without creating one (may return None)."""
    return UserAccount.objects.filter(user=user).first()


# ==============================================================================
# 2. USERNAME GENERATION
# ==============================================================================

DEFAULT_STUDENT_USERNAME_FORMAT = "{REGNO}"
DEFAULT_STAFF_USERNAME_FORMAT = "{FIRST}.{LAST}"


def _ascii(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return value.strip()


def get_username_config(user_type):
    """Username rules, all database-driven — no format is hard-coded."""
    if user_type == UserType.STUDENT:
        pattern = get_setting("username_student_format", DEFAULT_STUDENT_USERNAME_FORMAT)
    else:
        pattern = get_setting("username_staff_format", DEFAULT_STAFF_USERNAME_FORMAT)
    return {
        "pattern": pattern or (DEFAULT_STUDENT_USERNAME_FORMAT if user_type == UserType.STUDENT
                               else DEFAULT_STAFF_USERNAME_FORMAT),
        "prefix": get_setting("username_prefix", "") or "",
        "suffix": get_setting("username_suffix", "") or "",
        "separator": get_setting("username_separator", ".") or ".",
        "case": (get_setting("username_case", "LOWER") or "LOWER").upper(),
        "max_length": int(get_setting("username_max_length", 30) or 30),
        "allowed": get_setting("username_allowed_characters", "a-zA-Z0-9._-") or "a-zA-Z0-9._-",
        "sequential": bool(get_setting("username_sequential_numbering", False)),
        "padding": int(get_setting("username_sequence_padding", 4) or 4),
    }


def render_username_pattern(pattern, context, config):
    def replace(match):
        return _ascii(context.get(match.group(1).upper().strip(), ""))
    rendered = re.sub(r"\{([A-Za-z0-9_]+)\}", replace, pattern)
    rendered = rendered.replace(" ", config["separator"])
    rendered = re.sub(rf"[^{config['allowed']}]", "", rendered)
    return rendered.strip("._-")


def build_username_context(first_name="", last_name="", reg_no="", staff_id="",
                           program_code="", sequence=1, config=None):
    padding = (config or {}).get("padding", 4)
    return {
        "FIRST": first_name,
        "LAST": last_name,
        "INITIAL": (first_name or "")[:1],
        "REGNO": (reg_no or "").replace("/", ".").replace(" ", ""),
        "STUDENTID": (reg_no or "").replace("/", ".").replace(" ", ""),
        "STAFFID": (staff_id or "").replace("/", ".").replace(" ", ""),
        "PROG": program_code or "",
        "YEAR": str(timezone.now().year),
        "SEQ": f"{int(sequence):0{padding}d}",
    }


def username_exists(candidate):
    return User.objects.filter(username__iexact=candidate).exists()


def generate_username(user_type=UserType.STUDENT, first_name="", last_name="", reg_no="",
                      staff_id="", program_code="", exclude_user=None):
    """
    Build a unique username from the configured format.

    Collisions are resolved by appending an incrementing counter and rechecking,
    so this never returns a name that is already taken.
    """
    config = get_username_config(user_type)
    sequence = 1
    if config["sequential"]:
        sequence = User.objects.count() + 1

    context = build_username_context(first_name, last_name, reg_no, staff_id,
                                     program_code, sequence, config)
    base = render_username_pattern(config["pattern"], context, config)
    if not base:
        base = render_username_pattern("{FIRST}{SEP}{LAST}".replace("{SEP}", config["separator"]),
                                       context, config) or f"user{sequence}"

    base = f"{config['prefix']}{base}{config['suffix']}"
    base = base.lower() if config["case"] == "LOWER" else base.upper()
    base = base[: config["max_length"]].strip("._-")

    candidate = base
    counter = 1
    while _username_taken(candidate, exclude_user):
        counter += 1
        tail = str(counter)
        candidate = f"{base[: max(1, config['max_length'] - len(tail))]}{tail}"
    return candidate


def _username_taken(candidate, exclude_user=None):
    qs = User.objects.filter(username__iexact=candidate)
    if exclude_user is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.exists()


# ==============================================================================
# 3. PASSWORD POLICY, GENERATION & HISTORY
# ==============================================================================

def get_password_policy():
    """All password rules are configuration, never constants in code."""
    return {
        "min_length": int(get_setting("password_min_length", 8) or 8),
        "max_length": int(get_setting("password_max_length", 128) or 128),
        "require_upper": bool(get_setting("password_require_uppercase", True)),
        "require_lower": bool(get_setting("password_require_lowercase", True)),
        "require_digit": bool(get_setting("password_require_number", True)),
        "require_special": bool(get_setting("password_require_special", False)),
        "history_depth": int(get_setting("password_history_depth", 5) or 0),
        "expiry_days": int(get_setting("password_expiry_days", 0) or 0),
        "temp_expiry_hours": int(get_setting("temporary_password_expiry_hours", 48) or 48),
        "reset_token_minutes": int(get_setting("password_reset_token_minutes", 60) or 60),
        "max_attempts": int(get_setting("max_login_attempts", 5) or 5),
        "lock_minutes": int(get_setting("account_lock_duration_minutes", 30) or 30),
        "progressive_delay": bool(get_setting("login_progressive_delay", True)),
    }


def validate_password(password, user=None, policy=None):
    """Return a list of human-readable policy violations (empty means valid)."""
    policy = policy or get_password_policy()
    errors = []
    if len(password) < policy["min_length"]:
        errors.append(f"Password must be at least {policy['min_length']} characters long.")
    if len(password) > policy["max_length"]:
        errors.append(f"Password must be at most {policy['max_length']} characters long.")
    if policy["require_upper"] and not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter.")
    if policy["require_lower"] and not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter.")
    if policy["require_digit"] and not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number.")
    if policy["require_special"] and not any(not c.isalnum() for c in password):
        errors.append("Password must contain at least one special character.")
    if user is not None:
        if password.lower() == (user.username or "").lower():
            errors.append("Password must not match your username.")
        if is_password_reused(user, password, policy):
            errors.append(
                f"Password matches one of your last {policy['history_depth']} passwords. "
                "Choose a different one.")
    return errors


def is_password_reused(user, password, policy=None):
    """Compare against stored one-way hashes; plaintext history is never kept."""
    policy = policy or get_password_policy()
    depth = policy["history_depth"]
    if depth <= 0:
        return False
    recent = PasswordHistoryEntry.objects.filter(user=user).order_by("-created_at")[:depth]
    return any(check_password(password, entry.password_hash) for entry in recent)


def generate_password(length=None, use_upper=None, use_lower=None, use_digits=None,
                      use_special=None):
    """
    Cryptographically secure password generator.

    Character classes come from the active policy unless explicitly overridden,
    and the result is re-drawn until it satisfies every required class.
    """
    policy = get_password_policy()
    length = max(int(length or get_setting("generated_password_length", 12) or 12),
                 policy["min_length"])
    use_upper = policy["require_upper"] if use_upper is None else use_upper
    use_lower = True if use_lower is None else use_lower
    use_digits = policy["require_digit"] if use_digits is None else use_digits
    use_special = policy["require_special"] if use_special is None else use_special

    pools = []
    if use_lower:
        pools.append(string.ascii_lowercase)
    if use_upper:
        pools.append(string.ascii_uppercase)
    if use_digits:
        pools.append(string.digits)
    if use_special:
        pools.append("!@#$%^&*()-_=+[]{}?")
    if not pools:
        pools = [string.ascii_letters + string.digits]

    alphabet = "".join(pools)
    # One character guaranteed from each required class, remainder random,
    # then shuffled so the class positions are not predictable.
    while True:
        chars = [secrets.choice(pool) for pool in pools]
        chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        if not validate_password(candidate, policy=policy):
            return candidate


@transaction.atomic
def record_password_change(user, raw_password, actor=None, request=None,
                           must_change=False, notify=True, invalidate_sessions=None,
                           keep_session_key=None, reason=""):
    """
    Apply a new password and every consequence the security policy requires.

    Hashing, history, expiry stamps, token invalidation, session revocation,
    notification and audit all happen here so no caller can do half the job.

    ``invalidate_sessions`` defaults to the administrator-configured
    ``force_logout_after_password_reset`` setting when left unspecified; pass
    an explicit ``True``/``False`` to override that policy for a given flow.
    """
    if invalidate_sessions is None:
        invalidate_sessions = bool(get_setting("force_logout_after_password_reset", True))
    policy = get_password_policy()
    user.set_password(raw_password)
    user.save(update_fields=["password"])

    PasswordHistoryEntry.objects.create(user=user, password_hash=make_password(raw_password))
    depth = policy["history_depth"]
    if depth > 0:
        stale_ids = list(PasswordHistoryEntry.objects.filter(user=user)
                         .order_by("-created_at")
                         .values_list("id", flat=True)[depth:])
        if stale_ids:
            PasswordHistoryEntry.objects.filter(id__in=stale_ids).delete()

    account = ensure_account(user)
    now = timezone.now()
    account.password_changed_at = now
    account.must_change_password = must_change
    account.password_expires_at = (
        now + timezone.timedelta(days=policy["expiry_days"]) if policy["expiry_days"] else None)
    account.failed_login_attempts = 0
    account.locked_until = None
    account.lock_reason = ""
    if account.status == AccountStatus.LOCKED:
        account.status = AccountStatus.ACTIVE
    account.save()

    invalidate_reset_tokens(user)
    if invalidate_sessions:
        invalidate_user_sessions(user, keep_session_key=keep_session_key)

    log_activity(
        request=request, user=actor or user, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="User Password", entity_id=user.pk,
        description=(f"Password changed for '{user.username}'."
                     + (f" Reason: {reason}." if reason else "")
                     + (" User must change it at next sign-in." if must_change else "")),
    )

    if notify and user.email:
        from university.email_services import notify_password_changed
        notify_password_changed(user, request=request)
    return account


# ==============================================================================
# 4. RESET / ACTIVATION TOKENS
# ==============================================================================

def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def issue_reset_token(user, purpose=PasswordResetToken.Purpose.RESET, actor=None, request=None,
                      minutes=None):
    """
    Mint a single-use reset token and return ``(raw_token, token_record)``.

    Previous outstanding tokens for the user are invalidated first, so a reset
    request always supersedes anything already in flight. Only the digest is
    persisted — the raw value is returned once and never stored or logged.
    """
    invalidate_reset_tokens(user)
    policy = get_password_policy()
    minutes = int(minutes or policy["reset_token_minutes"])
    raw_token = secrets.token_urlsafe(48)
    record = PasswordResetToken.objects.create(
        user=user,
        token_hash=_hash_token(raw_token),
        purpose=purpose,
        expires_at=timezone.now() + timezone.timedelta(minutes=minutes),
        requested_ip=get_client_ip(request) if request else None,
        created_by=actor,
    )
    log_activity(
        request=request, user=actor or user, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="Password Reset Token", entity_id=user.pk,
        description=f"{record.get_purpose_display()} issued for '{user.username}' "
                    f"(valid {minutes} minutes).",
    )
    return raw_token, record


def get_valid_token(raw_token):
    """Look up a token by digest; returns None when missing, used or expired."""
    if not raw_token:
        return None
    record = PasswordResetToken.objects.filter(token_hash=_hash_token(raw_token)).first()
    if record and record.is_valid:
        return record
    return None


def invalidate_reset_tokens(user):
    return PasswordResetToken.objects.filter(
        user=user, used_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=timezone.now())


@transaction.atomic
def consume_reset_token(raw_token, new_password, request=None):
    """
    Complete a reset: validate the token, apply policy, rotate the credential.

    Returns ``(ok, message, user)``.
    """
    record = get_valid_token(raw_token)
    if not record:
        return False, "This reset link is invalid or has expired. Request a new one.", None

    user = record.user
    errors = validate_password(new_password, user=user)
    if errors:
        return False, " ".join(errors), user

    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])

    record_password_change(user, new_password, actor=user, request=request,
                           must_change=False, reason=record.get_purpose_display())

    account = ensure_account(user)
    if account.status in (AccountStatus.PENDING, AccountStatus.LOCKED):
        set_account_status(user, AccountStatus.ACTIVE, actor=user, request=request,
                           reason="Completed secure password setup.", notify=False)
    return True, "Your password has been set. You can now sign in.", user


# ==============================================================================
# 5. SESSIONS
# ==============================================================================

def invalidate_user_sessions(user, keep_session_key=None):
    """
    Drop every active Django session belonging to ``user``.

    Sessions are opaque blobs, so each one is decoded and matched on the stored
    auth user id. ``keep_session_key`` lets the actor stay signed in when they
    are changing their own password.
    """
    killed = 0
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now).iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id")) != str(user.pk):
            continue
        if keep_session_key and session.session_key == keep_session_key:
            continue
        session.delete()
        killed += 1
    return killed


def active_sessions_for(user):
    """List live sessions for a user, newest expiry first."""
    sessions = []
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now).iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id")) == str(user.pk):
            sessions.append(session)
    return sorted(sessions, key=lambda s: s.expire_date, reverse=True)


def enforce_concurrent_session_policy(user, keep_session_key=None):
    """Keep the configured number of live sessions and return sessions removed."""
    from django.conf import settings
    limit = int(getattr(settings, "MAX_CONCURRENT_SESSIONS", 5) or 0)
    if limit <= 0:
        return 0
    sessions = active_sessions_for(user)
    retained = [session for session in sessions if session.session_key == keep_session_key]
    retained += [session for session in sessions if session.session_key != keep_session_key]
    killed = 0
    for session in retained[limit:]:
        session.delete()
        killed += 1
    return killed


# ==============================================================================
# 6. ACCOUNT STATUS & LOCKOUT
# ==============================================================================

@transaction.atomic
def set_account_status(user, new_status, actor=None, request=None, reason="", notify=True,
                       force=False):
    """
    Move an account between lifecycle states with immediate backend effect.

    ``User.is_active`` is kept in lockstep so Django's own machinery agrees with
    the identity layer, and non-authenticable states revoke live sessions right
    away rather than waiting for them to expire.
    """
    account = ensure_account(user)
    old_status = account.status

    if old_status == new_status:
        return True, f"Account is already {account.get_status_display()}.", account

    allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status, set())
    if not force and new_status not in allowed:
        return False, (f"Cannot move an account from {old_status} to {new_status}."), account

    account.status = new_status
    account.status_reason = reason
    account.status_changed_at = timezone.now()
    account.status_changed_by = actor
    if new_status == AccountStatus.ACTIVE:
        account.activated_at = account.activated_at or timezone.now()
        account.failed_login_attempts = 0
        account.locked_until = None
        account.lock_reason = ""
    if new_status == AccountStatus.ARCHIVED:
        account.notes = (account.notes + f"\nArchived {timezone.now():%Y-%m-%d}: {reason}").strip()
    account.save()

    should_be_active = new_status in (AccountStatus.ACTIVE,)
    if user.is_active != should_be_active:
        user.is_active = should_be_active
        user.save(update_fields=["is_active"])

    if new_status != AccountStatus.ACTIVE:
        invalidate_user_sessions(user)

    log_activity(
        request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="User Account", entity_id=user.pk,
        description=f"Account status for '{user.username}' changed from {old_status} to "
                    f"{new_status}." + (f" Reason: {reason}" if reason else ""),
        previous_state={"status": old_status},
        new_state={"status": new_status, "reason": reason},
    )

    if notify and user.email:
        from university.email_services import notify_account_status
        notify_account_status(user, account.get_status_display(), reason, request=request)

    return True, f"Account is now {account.get_status_display()}.", account


def register_failed_login(username, request=None, reason=LoginRecord.Failure.BAD_CREDENTIALS):
    """
    Count a failed attempt and lock the account once the threshold is reached.

    An unknown username is still recorded (for forensics) but obviously cannot
    increment any counter.
    """
    user = User.objects.filter(username__iexact=username).first()
    record_login_attempt(user, username, success=False,
                         failure_reason=reason if user else LoginRecord.Failure.UNKNOWN_USER,
                         request=request)
    if not user:
        return None

    policy = get_password_policy()
    account = ensure_account(user)
    account.failed_login_attempts += 1
    account.last_failed_login_at = timezone.now()

    if account.failed_login_attempts >= policy["max_attempts"]:
        account.locked_until = timezone.now() + timezone.timedelta(minutes=policy["lock_minutes"])
        account.lock_reason = (f"Automatically locked after {account.failed_login_attempts} "
                               f"consecutive failed sign-in attempts.")
        account.status = AccountStatus.LOCKED
        account.save()
        log_activity(
            request=request, user=None, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
            entity="User Account", entity_id=user.pk,
            description=f"Account '{user.username}' locked until "
                        f"{account.locked_until:%Y-%m-%d %H:%M} after "
                        f"{account.failed_login_attempts} failed sign-in attempts.",
        )
    else:
        account.save()
    return account


def clear_failed_logins(user):
    account = ensure_account(user)
    if account.failed_login_attempts or account.locked_until:
        account.failed_login_attempts = 0
        account.locked_until = None
        account.lock_reason = ""
        if account.status == AccountStatus.LOCKED:
            account.status = AccountStatus.ACTIVE
        account.save()
    return account


def unlock_account(user, actor=None, request=None, reason="Manual administrator unlock"):
    account = ensure_account(user)
    was_locked = account.is_locked or account.status == AccountStatus.LOCKED
    account.failed_login_attempts = 0
    account.locked_until = None
    account.lock_reason = ""
    if account.status == AccountStatus.LOCKED:
        account.status = AccountStatus.ACTIVE
        account.status_reason = reason
        account.status_changed_at = timezone.now()
        account.status_changed_by = actor
    account.save()
    if not user.is_active and account.status == AccountStatus.ACTIVE:
        user.is_active = True
        user.save(update_fields=["is_active"])
    log_activity(
        request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="User Account", entity_id=user.pk,
        description=f"Account '{user.username}' unlocked. {reason}",
    )
    return was_locked, account


def login_delay_seconds(user):
    """Progressive back-off applied to repeated failures on the same account."""
    policy = get_password_policy()
    if not policy["progressive_delay"]:
        return 0
    account = get_account(user) if user else None
    if not account or account.failed_login_attempts < 2:
        return 0
    return min(2 ** (account.failed_login_attempts - 1), 30)


# ==============================================================================
# 7. LOGIN HISTORY
# ==============================================================================

def record_login_attempt(user, username, success, failure_reason="", request=None,
                         session_key="", mfa_used=False):
    """Write one row to the login ledger. Passwords are never touched here."""
    from university.audit_services import detect_device_type
    ua = request.META.get("HTTP_USER_AGENT", "") if request else ""
    return LoginRecord.objects.create(
        user=user,
        username_attempted=(username or "")[:150],
        user_type=resolve_user_type(user) if user else "",
        success=success,
        failure_reason=failure_reason or "",
        ip_address=get_client_ip(request) if request else None,
        user_agent=ua[:500],
        device_type=detect_device_type(ua) if ua else "",
        session_key=session_key or "",
        mfa_used=mfa_used,
    )


def close_login_record(user, session_key=""):
    """Stamp the logout time on the most recent open session record."""
    qs = LoginRecord.objects.filter(user=user, success=True, logout_at__isnull=True)
    if session_key:
        scoped = qs.filter(session_key=session_key)
        record = scoped.first() or qs.first()
    else:
        record = qs.first()
    if record:
        record.logout_at = timezone.now()
        record.save(update_fields=["logout_at"])
    return record


# ==============================================================================
# 8. GROUPS & ROLES
# ==============================================================================

def assign_group(user, group, actor=None, request=None):
    membership, created = UserGroupMembership.objects.get_or_create(
        user=user, group=group, defaults={"assigned_by": actor})
    if created:
        log_activity(
            request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
            entity="User Group Membership", entity_id=user.pk,
            description=f"Added '{user.username}' to group '{group.name}'.",
        )
        # Group roles resolve down to the same RBAC assignments the permission
        # engine already evaluates, so no parallel permission path is created.
        for role in group.roles.all():
            StaffRoleAssignment.objects.get_or_create(
                user=user, role=role, department=None,
                defaults={"assigned_by": actor, "is_active": True})
        invalidate_user_sessions(user)
    return membership, created


def remove_group(user, group, actor=None, request=None):
    deleted, _ = UserGroupMembership.objects.filter(user=user, group=group).delete()
    if deleted:
        invalidate_user_sessions(user)
        log_activity(
            request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
            entity="User Group Membership", entity_id=user.pk,
            description=f"Removed '{user.username}' from group '{group.name}'.",
        )
    return bool(deleted)


def effective_groups(user):
    """Groups ordered by the documented precedence (lowest number wins)."""
    return UserGroup.objects.filter(memberships__user=user).order_by("precedence", "name").distinct()


# ==============================================================================
# 9. USER CREATION & PROVISIONING
# ==============================================================================

class IdentityError(ValidationError):
    """Raised when an identity operation would violate a data-integrity rule."""


@transaction.atomic
def create_user_account(*, user_type, first_name, last_name, email="", username="", phone="",
                        role=None, password=None, password_mode="LINK", status=None,
                        must_change_password=True, groups=None, staff_role_codes=None,
                        student_profile=None, faculty_profile=None, campus="",
                        activation_date=None, expiry_date=None, generate_email=None,
                        notify=True, actor=None, request=None):
    """
    Create one central User plus its identity envelope.

    ``password_mode`` is ``LINK`` (send a secure activation link — preferred),
    ``GENERATE`` (issue a temporary password the caller must deliver out of
    band) or ``MANUAL`` (administrator supplied). Returns a dict describing what
    was created, including a one-time temporary password when applicable.
    """
    role = role or {
        UserType.STUDENT: Role.STUDENT,
        UserType.STAFF: Role.FACULTY,
        UserType.ADMIN: Role.ADMIN,
    }.get(user_type, Role.STUDENT)

    if not username:
        username = generate_username(
            user_type=user_type, first_name=first_name, last_name=last_name,
            reg_no=getattr(student_profile, "roll_no", ""),
            staff_id=getattr(faculty_profile, "employee_id", ""),
        )
    if username_exists(username):
        raise IdentityError(f"Username '{username}' is already taken.")
    if email and User.objects.filter(email__iexact=email).exists():
        raise IdentityError(f"Email '{email}' is already linked to another account.")

    user = User.objects.create(
        username=username,
        email=email or "",
        first_name=first_name or "",
        last_name=last_name or "",
        role=role,
        phone=phone or "",
        is_active=False,
    )
    # An unusable password until the account is activated: the identity exists,
    # but nothing can authenticate as it yet.
    user.set_unusable_password()
    user.save()

    # The envelope always starts Pending. Any other requested status is applied
    # through set_account_status below, because that is what keeps
    # ``User.is_active``, live sessions and the audit trail in lockstep.
    account = ensure_account(user, user_type=user_type,
                             status=AccountStatus.PENDING, created_by=actor)
    account.campus = campus or ""
    account.activation_date = activation_date
    account.expiry_date = expiry_date
    account.created_by = actor
    account.save()

    if student_profile is not None:
        _link_profile(student_profile, user)
    if faculty_profile is not None:
        _link_profile(faculty_profile, user)

    for group in (groups or []):
        assign_group(user, group, actor=actor, request=request)
    for code in (staff_role_codes or []):
        role_obj = StaffRole.objects.filter(code=code).first()
        if role_obj:
            StaffRoleAssignment.objects.get_or_create(
                user=user, role=role_obj, department=None,
                defaults={"assigned_by": actor, "is_active": True})

    institutional_email = None
    if generate_email if generate_email is not None else bool(get_setting("auto_generate_email", True)):
        institutional_email = provision_institutional_email(user, user_type=user_type, actor=actor)

    temporary_password = None
    activation_url = None
    if password_mode == "MANUAL" and password:
        errors = validate_password(password, user=user)
        if errors:
            raise IdentityError(" ".join(errors))
        record_password_change(user, password, actor=actor, request=request,
                               must_change=must_change_password, notify=False,
                               invalidate_sessions=False, reason="Account creation")
    elif password_mode == "GENERATE":
        temporary_password = generate_password()
        record_password_change(user, temporary_password, actor=actor, request=request,
                               must_change=True, notify=False, invalidate_sessions=False,
                               reason="Temporary password issued at account creation")
        policy = get_password_policy()
        account.refresh_from_db()
        account.password_expires_at = timezone.now() + timezone.timedelta(
            hours=policy["temp_expiry_hours"])
        account.save(update_fields=["password_expires_at"])
    else:
        raw_token, _record = issue_reset_token(
            user, purpose=PasswordResetToken.Purpose.ACTIVATION, actor=actor, request=request,
            minutes=int(get_setting("activation_token_minutes", 4320) or 4320))
        activation_url = build_reset_url(raw_token, request)

    if status and status != AccountStatus.PENDING:
        _, _, account = set_account_status(user, status, actor=actor, request=request,
                                           reason="Status set at account creation", notify=False, force=True)
    user.refresh_from_db()

    log_activity(
        request=request, user=actor, action=AuditLog.Action.CREATE, module=AUDIT_MODULE,
        entity="User", entity_id=user.pk,
        description=f"Created {account.get_user_type_display()} account '{username}' "
                    f"({user.get_full_name()}).",
        new_state={"username": username, "user_type": user_type, "status": account.status,
                   "institutional_email": getattr(institutional_email, "address", "")},
    )

    if notify:
        from university.email_services import notify_account_created
        notify_account_created(
            user, username, activation_url=activation_url,
            temporary_password=temporary_password, request=request,
            institutional_email=getattr(institutional_email, "address", None))

    return {
        "user": user,
        "account": account,
        "username": username,
        "temporary_password": temporary_password,
        "activation_url": activation_url,
        "institutional_email": institutional_email,
    }


def _link_profile(profile, user):
    """Attach an existing Student/Faculty record to the central user identity."""
    if profile.user_id and profile.user_id != user.pk:
        raise IdentityError(f"{profile} is already linked to another user account.")
    profile.user = user
    profile.save(update_fields=["user"])
    return profile


def provision_institutional_email(user, user_type=None, actor=None, notify=False, request=None):
    """
    Generate, record and attempt to provision the institutional address.

    The address becomes the authoritative contact identity: it is copied into
    ``User.email`` when that field is empty so downstream modules that read the
    login email keep working without holding a second source of truth.
    """
    from university.email_services import (generate_email_address, notify_email_ready,
                                           provision_mailbox)
    user_type = user_type or resolve_user_type(user)
    existing = InstitutionalEmail.objects.filter(user=user, is_primary=True).first()
    if existing:
        return existing

    address, record = generate_email_address(user, user_type=user_type, commit=True,
                                             created_by=actor)
    provision_mailbox(record, actor=actor)

    if not user.email:
        user.email = address
        user.save(update_fields=["email"])

    log_activity(
        request=request, user=actor, action=AuditLog.Action.CREATE, module=AUDIT_MODULE,
        entity="Institutional Email", entity_id=user.pk,
        description=f"Institutional email '{address}' generated for '{user.username}'.",
    )
    if notify:
        notify_email_ready(user, address, request=request)
    return record


@transaction.atomic
def provision_student_account(student_profile, actor=None, request=None, notify=True,
                              activate=None):
    """
    Ensure an admitted student has exactly one central account.

    Called from the admissions pipeline; it is idempotent, so re-running
    matriculation never produces a duplicate identity.
    """
    user = student_profile.user
    account = ensure_account(user, user_type=UserType.STUDENT, created_by=actor)
    email_record = provision_institutional_email(user, UserType.STUDENT, actor=actor,
                                                 request=request)

    auto_activate = bool(get_setting("auto_activate_student_accounts", True)) if activate is None else activate
    if auto_activate and account.status == AccountStatus.PENDING:
        _, _, account = set_account_status(user, AccountStatus.ACTIVE, actor=actor, request=request,
                                           reason="Student enrolment completed", notify=False)
        user.refresh_from_db()

    if notify and user.email:
        from university.email_services import notify_account_created
        notify_account_created(user, user.username, request=request,
                               institutional_email=getattr(email_record, "address", None))
    return account


@transaction.atomic
def provision_staff_account(faculty_profile, actor=None, request=None, notify=True):
    """Ensure a staff record has exactly one central account."""
    user = faculty_profile.user
    account = ensure_account(user, user_type=UserType.STAFF, created_by=actor)
    email_record = provision_institutional_email(user, UserType.STAFF, actor=actor,
                                                 request=request)
    if bool(get_setting("auto_activate_staff_accounts", True)) and account.status == AccountStatus.PENDING:
        set_account_status(user, AccountStatus.ACTIVE, actor=actor, request=request,
                           reason="Staff record activated", notify=False)
    if notify and user.email:
        from university.email_services import notify_account_created
        notify_account_created(user, user.username, request=request,
                               institutional_email=getattr(email_record, "address", None))
    return account


# ==============================================================================
# 10. ADMINISTRATOR PASSWORD OPERATIONS
# ==============================================================================

def build_reset_url(raw_token, request=None):
    from django.urls import reverse
    path = reverse("accounts:password_reset_confirm", args=[raw_token])
    if request is not None:
        return request.build_absolute_uri(path)
    base = (get_setting("site_base_url", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def send_reset_link(user, actor=None, request=None,
                    purpose=PasswordResetToken.Purpose.ADMIN_RESET):
    """Preferred administrator reset path: a secure link, never a password."""
    raw_token, record = issue_reset_token(user, purpose=purpose, actor=actor, request=request)
    url = build_reset_url(raw_token, request)
    policy = get_password_policy()
    from university.email_services import notify_password_reset
    ok, message = notify_password_reset(user, url, policy["reset_token_minutes"], request=request)
    return ok, message, url


def issue_temporary_password(user, actor=None, request=None):
    """
    Institutionally-required fallback when a link cannot be delivered.

    Returned once to the requesting administrator for controlled hand-over; the
    account is flagged ``must_change_password`` and the credential expires.
    """
    temporary = generate_password()
    record_password_change(user, temporary, actor=actor, request=request, must_change=True,
                           notify=False, reason="Temporary password issued by administrator")
    policy = get_password_policy()
    account = ensure_account(user)
    account.password_expires_at = timezone.now() + timezone.timedelta(
        hours=policy["temp_expiry_hours"])
    account.save(update_fields=["password_expires_at"])
    return temporary


def force_password_change(user, actor=None, request=None):
    account = ensure_account(user)
    account.must_change_password = True
    account.save(update_fields=["must_change_password"])
    invalidate_user_sessions(user)
    log_activity(
        request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="User Account", entity_id=user.pk,
        description=f"Forced password change at next sign-in for '{user.username}'.",
    )
    return account


# ==============================================================================
# 11. SEARCH
# ==============================================================================

def search_users(queryset=None, q="", user_type="", status="", role="", department="",
                 program="", group="", campus=""):
    """Server-side user search across identity, organisation and status fields."""
    from django.db.models import Q
    qs = queryset if queryset is not None else User.objects.all()
    qs = qs.select_related("account", "student_profile__program", "faculty_profile__department")

    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(email__icontains=q) | Q(phone__icontains=q)
            | Q(student_profile__roll_no__icontains=q)
            | Q(faculty_profile__employee_id__icontains=q)
            | Q(institutional_emails__address__icontains=q)
        ).distinct()
    if user_type:
        qs = qs.filter(account__user_type=user_type)
    if status:
        qs = qs.filter(account__status=status)
    if role:
        qs = qs.filter(role=role)
    if department:
        qs = qs.filter(Q(faculty_profile__department_id=department)
                       | Q(student_profile__program__department_id=department)).distinct()
    if program:
        qs = qs.filter(student_profile__program_id=program)
    if group:
        qs = qs.filter(group_memberships__group__code=group).distinct()
    if campus:
        qs = qs.filter(account__campus__iexact=campus)
    return qs


# ==============================================================================
# 12. BULK OPERATIONS
# ==============================================================================

def validate_import_rows(rows, user_type):
    """
    Dry-run validation for a bulk import.

    Every row is classified as CREATE, SKIP (identity already exists) or ERROR,
    and the generated username/email are shown before anything is written — an
    administrator confirms a preview, never a black box.
    """
    from university.identity_models import UserType as _UserType
    from university.models import Department, Program

    seen_ids, seen_usernames = set(), set()
    results = []

    for row in rows:
        errors = []
        identifier = (row.get("registration_number") or row.get("student_id")
                      if user_type == _UserType.STUDENT else row.get("staff_id")) or ""
        identifier = identifier.strip()
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()

        if not identifier:
            errors.append("Missing registration/staff identifier.")
        if not first:
            errors.append("Missing first name.")
        if not last:
            errors.append("Missing last name.")

        program = department = None
        if row.get("programme_code"):
            program = Program.objects.filter(code__iexact=row["programme_code"].strip()).first()
            if not program:
                errors.append(f"Unknown programme code '{row['programme_code']}'.")
        if row.get("department_code"):
            department = Department.objects.filter(
                code__iexact=row["department_code"].strip()).first()
            if not department:
                errors.append(f"Unknown department code '{row['department_code']}'.")

        email = (row.get("email") or "").strip()
        if email and User.objects.filter(email__iexact=email).exists():
            errors.append(f"Email '{email}' already belongs to another account.")

        duplicate_in_file = identifier.lower() in seen_ids if identifier else False
        if duplicate_in_file:
            errors.append("Duplicate identifier appears earlier in this file.")
        if identifier:
            seen_ids.add(identifier.lower())

        existing = _existing_identity(identifier, user_type)
        username = ""
        if not errors:
            username = generate_username(
                user_type=user_type, first_name=first, last_name=last,
                reg_no=identifier if user_type == _UserType.STUDENT else "",
                staff_id=identifier if user_type != _UserType.STUDENT else "",
                program_code=getattr(program, "code", ""))
            # Guard against two rows in the same file racing to the same name.
            counter = 1
            while username in seen_usernames:
                counter += 1
                username = f"{username}{counter}"
            seen_usernames.add(username)

        if errors:
            outcome = "ERROR"
        elif existing:
            outcome = "SKIP"
        else:
            outcome = "CREATE"

        results.append({
            "row": row.get("_row"),
            "identifier": identifier,
            "first_name": first,
            "last_name": last,
            "email": email,
            "programme": getattr(program, "code", ""),
            "department": getattr(department, "code", ""),
            "campus": (row.get("campus") or "").strip(),
            "group_code": (row.get("group_code") or "").strip(),
            "role_code": (row.get("role_code") or "").strip(),
            "designation": (row.get("designation") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "status": (row.get("status") or "").strip().upper(),
            "username": username,
            "outcome": outcome,
            "errors": errors,
            "existing_username": getattr(getattr(existing, "user", None), "username", ""),
        })

    return results


def _existing_identity(identifier, user_type):
    from university.identity_models import UserType as _UserType
    if not identifier:
        return None
    if user_type == _UserType.STUDENT:
        return StudentProfile.objects.filter(roll_no__iexact=identifier).first()
    return FacultyProfile.objects.filter(employee_id__iexact=identifier).first()


@transaction.atomic
def commit_import(preview_rows, user_type, actor=None, request=None, notify=True,
                  filename=""):
    """
    Create the accounts a validated preview described.

    Rows are processed inside savepoints so one bad record cannot abort the run,
    and every failure is reported back rather than silently dropped.
    """
    from university.identity_models import UserImportBatch, UserGroup
    from university.identity_models import UserType as _UserType
    from university.models import Department, Program

    created = skipped = failed = 0
    report_rows = []

    for row in preview_rows:
        if row["outcome"] != "CREATE":
            skipped += int(row["outcome"] == "SKIP")
            failed += int(row["outcome"] == "ERROR")
            report_rows.append({**row, "result": row["outcome"]})
            continue

        try:
            with transaction.atomic():
                student_profile = faculty_profile = None
                if user_type == _UserType.STUDENT:
                    program = Program.objects.filter(code__iexact=row["programme"]).first() \
                        if row["programme"] else None
                    student_profile = StudentProfile(roll_no=row["identifier"], program=program,
                                                     current_semester=1)
                else:
                    department = Department.objects.filter(code__iexact=row["department"]).first() \
                        if row["department"] else None
                    faculty_profile = FacultyProfile(employee_id=row["identifier"],
                                                     department=department,
                                                     designation=row["designation"] or "Lecturer")

                groups = list(UserGroup.objects.filter(code=row["group_code"])) \
                    if row["group_code"] else []
                status = row["status"] if row["status"] in AccountStatus.values else None

                result = create_user_account(
                    user_type=user_type,
                    first_name=row["first_name"], last_name=row["last_name"],
                    email=row["email"], username=row["username"], phone=row["phone"],
                    password_mode="LINK", status=status, groups=groups,
                    staff_role_codes=[row["role_code"]] if row["role_code"] else None,
                    campus=row["campus"], notify=notify, actor=actor, request=request,
                )
                # The profile record is saved against the freshly created user so
                # the academic identity and the login identity are one thing.
                if student_profile is not None:
                    student_profile.user = result["user"]
                    student_profile.save()
                if faculty_profile is not None:
                    faculty_profile.user = result["user"]
                    faculty_profile.save()

            created += 1
            report_rows.append({**row, "result": "CREATED"})
        except Exception as exc:
            failed += 1
            report_rows.append({**row, "result": "FAILED", "errors": [str(exc)]})

    status = (UserImportBatch.Status.COMPLETED if not failed
              else UserImportBatch.Status.PARTIAL if created
              else UserImportBatch.Status.FAILED)
    batch = UserImportBatch.objects.create(
        user_type=user_type, filename=filename, status=status,
        total_rows=len(preview_rows), created_count=created, skipped_count=skipped,
        error_count=failed, report={"rows": report_rows}, run_by=actor,
    )

    log_activity(
        request=request, user=actor, action=AuditLog.Action.IMPORT, module=AUDIT_MODULE,
        entity="Bulk User Import", entity_id=batch.pk,
        description=(f"Bulk {user_type} user import from '{filename or 'upload'}': "
                     f"{created} created, {skipped} skipped, {failed} failed."),
        new_state={"created": created, "skipped": skipped, "failed": failed},
    )
    return batch


def bulk_password_operation(users, operation, actor=None, request=None):
    """
    Apply a password action across many accounts.

    Only link-based and flag-based operations are offered in bulk; the system
    never renders a list of plaintext passwords.
    """
    succeeded, failed = [], []
    for user in users:
        try:
            if operation == "SEND_RESET_LINK":
                ok, message, _url = send_reset_link(user, actor=actor, request=request)
                (succeeded if ok else failed).append((user.username, message))
            elif operation == "FORCE_CHANGE":
                force_password_change(user, actor=actor, request=request)
                succeeded.append((user.username, "Flagged for password change."))
            elif operation == "UNLOCK":
                unlock_account(user, actor=actor, request=request)
                succeeded.append((user.username, "Account unlocked."))
            else:
                failed.append((user.username, f"Unsupported operation '{operation}'."))
        except Exception as exc:
            failed.append((user.username, str(exc)))

    log_activity(
        request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="Bulk Password Operation",
        description=f"Bulk '{operation}' applied to {len(succeeded)} accounts "
                    f"({len(failed)} failed).",
    )
    return succeeded, failed


def bulk_generate_emails(users, actor=None, request=None, notify=False):
    """Generate institutional addresses for users that do not have one yet."""
    created, skipped = [], []
    for user in users:
        if InstitutionalEmail.objects.filter(user=user, is_primary=True).exists():
            skipped.append(user.username)
            continue
        record = provision_institutional_email(user, actor=actor, request=request, notify=notify)
        created.append((user.username, record.address))

    log_activity(
        request=request, user=actor, action=AuditLog.Action.UPDATE, module=AUDIT_MODULE,
        entity="Bulk Email Generation",
        description=f"Generated {len(created)} institutional addresses "
                    f"({len(skipped)} already had one).",
    )
    return created, skipped
