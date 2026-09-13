"""
Central email identity, provider abstraction and notification layer.

Every outbound message in the system funnels through :func:`send_system_email`
so that the provider, sender identity and delivery ledger live in exactly one
place. Provider credentials are read from ``SystemSetting`` at send time and are
never handed to a template.
"""

import re
import unicodedata

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from university.identity_models import EmailDeliveryRecord, InstitutionalEmail, UserType
from university.settings_services import get_setting


# ==============================================================================
# 1. PROVIDER CONFIGURATION
# ==============================================================================

class Provider:
    CONSOLE = "CONSOLE"
    SMTP = "SMTP"
    MICROSOFT365 = "MICROSOFT365"
    GOOGLE_WORKSPACE = "GOOGLE_WORKSPACE"
    DISABLED = "DISABLED"

    CHOICES = [
        (CONSOLE, "Console / Local (development)"),
        (SMTP, "SMTP Server"),
        (MICROSOFT365, "Microsoft 365"),
        (GOOGLE_WORKSPACE, "Google Workspace"),
        (DISABLED, "Disabled (no outbound mail)"),
    ]


# Secrets are listed explicitly so the settings UI can refuse to echo them back.
SECRET_SETTING_KEYS = {"email_host_password", "email_provider_api_key"}


def get_email_config():
    """Resolve the live email configuration from SystemSetting."""
    return {
        "provider": get_setting("email_provider", Provider.CONSOLE) or Provider.CONSOLE,
        "host": get_setting("email_host", "") or "",
        "port": int(get_setting("email_port", 587) or 587),
        "encryption": (get_setting("email_encryption", "TLS") or "TLS").upper(),
        "username": get_setting("email_host_user", "") or "",
        "password": get_setting("email_host_password", "") or "",
        "from_name": get_setting("email_from_name", "") or "University Management System",
        "from_address": get_setting("email_from_address", "") or "no-reply@localhost",
        "reply_to": get_setting("email_reply_to", "") or "",
        "timeout": int(get_setting("email_timeout_seconds", 20) or 20),
        "enabled": bool(get_setting("email_enabled", True)),
    }


def get_email_connection(config=None):
    """
    Build a Django mail connection for the configured provider.

    Microsoft 365 and Google Workspace are reached over their documented SMTP
    relays; treating them as SMTP variants keeps one code path while still
    letting an institution pick its provider by name.
    """
    cfg = config or get_email_config()
    provider = cfg["provider"]

    if provider == Provider.DISABLED or not cfg["enabled"]:
        return None

    if provider == Provider.CONSOLE:
        return get_connection("django.core.mail.backends.locmem.EmailBackend")

    host = cfg["host"]
    port = cfg["port"]
    if provider == Provider.MICROSOFT365 and not host:
        host, port = "smtp.office365.com", 587
    elif provider == Provider.GOOGLE_WORKSPACE and not host:
        host, port = "smtp.gmail.com", 587

    return get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=host,
        port=port,
        username=cfg["username"],
        password=cfg["password"],
        use_tls=cfg["encryption"] == "TLS",
        use_ssl=cfg["encryption"] == "SSL",
        timeout=cfg["timeout"],
        fail_silently=False,
    )


def sender_identity(cfg=None):
    cfg = cfg or get_email_config()
    name = cfg["from_name"].strip()
    address = cfg["from_address"].strip()
    return f"{name} <{address}>" if name else address


# ==============================================================================
# 2. SENDING
# ==============================================================================

def send_system_email(to_address, subject, body, html_body=None, template_code="",
                      user=None, record=True):
    """
    Send one message through the configured provider and record the outcome.

    Returns ``(ok, message)``. A disabled provider is reported as SKIPPED rather
    than silently swallowed, so an administrator can tell "not configured" apart
    from "delivered".
    """
    cfg = get_email_config()
    to_address = (to_address or "").strip()

    if not to_address:
        return False, "No destination address."

    if not cfg["enabled"] or cfg["provider"] == Provider.DISABLED:
        if record:
            EmailDeliveryRecord.objects.create(
                user=user, to_address=to_address, subject=subject,
                template_code=template_code, provider=cfg["provider"],
                status=EmailDeliveryRecord.Status.SKIPPED,
                error="Outbound email is disabled in User Management settings.",
            )
        return False, "Outbound email is disabled."

    try:
        connection = get_email_connection(cfg)
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=sender_identity(cfg),
            to=[to_address],
            reply_to=[cfg["reply_to"]] if cfg["reply_to"] else None,
            connection=connection,
        )
        if html_body:
            message.attach_alternative(html_body, "text/html")
        message.send()
    except Exception as exc:  # provider/network failures must stay visible
        if record:
            EmailDeliveryRecord.objects.create(
                user=user, to_address=to_address, subject=subject,
                template_code=template_code, provider=cfg["provider"],
                status=EmailDeliveryRecord.Status.FAILED, error=str(exc)[:2000],
            )
        return False, str(exc)

    if record:
        EmailDeliveryRecord.objects.create(
            user=user, to_address=to_address, subject=subject,
            template_code=template_code, provider=cfg["provider"],
            status=EmailDeliveryRecord.Status.SENT,
        )
    return True, "Message sent."


def send_test_email(to_address, actor=None):
    """Real delivery attempt used by the 'Send Test Email' control."""
    institution = get_setting("institution_name", "University Management System")
    body = (
        f"This is a test message from {institution}.\n\n"
        f"If you are reading it, the configured email provider is working.\n"
        f"Triggered by: {getattr(actor, 'username', 'system')}\n"
        f"Timestamp: {timezone.now():%Y-%m-%d %H:%M:%S}\n"
    )
    return send_system_email(to_address, f"[{institution}] Email configuration test",
                             body, template_code="test_email", user=actor)


# ==============================================================================
# 3. ADDRESS GENERATION
# ==============================================================================

def _slug(value):
    """ASCII-fold and strip anything not valid in the local part of an address."""
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9._-]+", "", value.replace(" ", "."))
    return value.strip("._-")


def get_domain_for(user_type):
    from university.institution_domain_services import get_staff_email_domain, get_student_email_domain
    if user_type == UserType.STUDENT:
        return get_student_email_domain()
    return get_staff_email_domain()


def get_format_for(user_type):
    key = {
        UserType.STUDENT: "email_student_format",
        UserType.STAFF: "email_staff_format",
        UserType.ADMIN: "email_admin_format",
    }.get(user_type, "email_staff_format")
    default = "{REGNO}" if user_type == UserType.STUDENT else "{FIRST}.{LAST}"
    return get_setting(key, default) or default


def render_local_part(pattern, context):
    """Substitute {TOKEN} placeholders from ``context`` into an address pattern."""
    def replace(match):
        return _slug(context.get(match.group(1).upper().strip(), ""))
    return re.sub(r"\{([A-Za-z0-9_]+)\}", replace, pattern)


def build_email_context(user):
    student = getattr(user, "student_profile", None)
    staff = getattr(user, "faculty_profile", None)
    return {
        "FIRST": user.first_name or user.username,
        "LAST": user.last_name or "",
        "INITIAL": (user.first_name or user.username)[:1],
        "USERNAME": user.username,
        "REGNO": getattr(student, "roll_no", "") or "",
        "STUDENTID": getattr(student, "roll_no", "") or "",
        "STAFFID": getattr(staff, "employee_id", "") or "",
    }


def generate_email_address(user, user_type=None, commit=False, created_by=None):
    """
    Produce a unique institutional address for ``user``.

    Collisions are resolved with a numeric suffix; the candidate is checked
    against both the institutional email table and the ``User.email`` column so
    a generated address can never shadow an existing login identity.
    """
    user_type = user_type or resolve_user_type(user)
    domain = get_domain_for(user_type)
    pattern = get_format_for(user_type)
    context = build_email_context(user)

    local = render_local_part(pattern, context) or _slug(user.username)
    if (get_setting("email_case", "LOWER") or "LOWER").upper() == "LOWER":
        local = local.lower()
    else:
        local = local.upper()

    base = local or user.username.lower()
    candidate = f"{base}@{domain}"
    counter = 1
    while _address_taken(candidate, user):
        counter += 1
        candidate = f"{base}{counter}@{domain}"

    if not commit:
        return candidate, None

    InstitutionalEmail.objects.filter(user=user, is_primary=True).update(
        is_primary=False, status=InstitutionalEmail.Status.ARCHIVED, archived_at=timezone.now())
    record = InstitutionalEmail.objects.create(
        user=user, address=candidate, kind=user_type,
        status=InstitutionalEmail.Status.PENDING, is_primary=True, created_by=created_by,
    )
    return candidate, record


def _address_taken(address, exclude_user=None):
    from accounts.models import User
    email_qs = InstitutionalEmail.objects.filter(address__iexact=address)
    user_qs = User.objects.filter(email__iexact=address)
    if exclude_user is not None:
        email_qs = email_qs.exclude(user=exclude_user)
        user_qs = user_qs.exclude(pk=exclude_user.pk)
    return email_qs.exists() or user_qs.exists()


def resolve_user_type(user):
    from accounts.models import Role
    if getattr(user, "role", "") == Role.STUDENT:
        return UserType.STUDENT
    if getattr(user, "role", "") == Role.FACULTY:
        return UserType.STAFF
    if getattr(user, "role", "") == Role.ADMIN:
        return UserType.ADMIN
    return UserType.OTHER


# ==============================================================================
# 4. MAILBOX PROVISIONING
# ==============================================================================

def provision_mailbox(email_record, actor=None):
    """
    Ask the configured provider to create the mailbox.

    No provider API is wired in by default, so the honest outcome is "identity
    recorded, mailbox not confirmed" — the record is never flipped to ACTIVE on
    a provider that has not actually confirmed creation.
    """
    cfg = get_email_config()
    provider = cfg["provider"]
    email_record.provider = provider

    api_enabled = bool(get_setting("email_provisioning_enabled", False))
    if not api_enabled or provider in (Provider.CONSOLE, Provider.DISABLED, Provider.SMTP):
        email_record.status = InstitutionalEmail.Status.PENDING
        email_record.provider_message = (
            "Institutional identity recorded. Automatic mailbox creation is not enabled "
            "for this provider, so the mailbox must be created in the provider console."
        )
        email_record.save()
        return False, email_record.provider_message

    # A directory API is configured but no credentials/driver are bound yet;
    # report the failure instead of pretending the mailbox exists.
    email_record.status = InstitutionalEmail.Status.FAILED
    email_record.provider_message = (
        f"No provisioning driver is bound for provider '{provider}'. "
        "Configure provider API credentials before enabling automatic provisioning."
    )
    email_record.save()
    return False, email_record.provider_message


def mark_mailbox_active(email_record, reference="", actor=None):
    """Record a confirmed mailbox — used when the provider (or an admin) confirms."""
    email_record.status = InstitutionalEmail.Status.ACTIVE
    email_record.provisioned_at = timezone.now()
    if reference:
        email_record.provider_reference = reference
    email_record.provider_message = "Mailbox confirmed active."
    email_record.save()
    return email_record


# ==============================================================================
# 5. NOTIFICATION TEMPLATES
# ==============================================================================

def _institution():
    return get_setting("institution_name", "University Management System")


def _login_url(request=None):
    from django.urls import reverse
    path = reverse("accounts:login")
    if request is not None:
        return request.build_absolute_uri(path)
    base = (get_setting("site_base_url", "") or "").rstrip("/")
    return f"{base}{path}" if base else path


def notify_account_created(user, username, activation_url=None, temporary_password=None,
                           request=None, institutional_email=None):
    """Welcome message. Prefers an activation link over shipping a password."""
    if not bool(get_setting("notify_account_created", True)):
        return False, "Notification disabled."
    to = institutional_email or user.email
    lines = [
        f"Hello {user.get_full_name() or username},",
        "",
        f"An account has been created for you at {_institution()}.",
        "",
        f"Username: {username}",
    ]
    if institutional_email:
        lines.append(f"Institutional email: {institutional_email}")
    lines.append(f"Sign in at: {_login_url(request)}")
    lines.append("")
    if activation_url:
        lines += ["Set your password using this secure link:", activation_url,
                  "", "The link can be used once and expires automatically."]
    elif temporary_password:
        lines += ["A temporary password has been issued to you separately.",
                  "You will be required to change it at first sign-in."]
    else:
        lines.append("Use the 'Forgot password?' link on the sign-in page to set your password.")
    lines += ["", "If you did not expect this message, contact the ICT service desk."]
    return send_system_email(to, f"[{_institution()}] Your account is ready", "\n".join(lines),
                             template_code="account_created", user=user)


def notify_password_reset(user, reset_url, expiry_minutes, request=None):
    if not bool(get_setting("notify_password_reset", True)):
        return False, "Notification disabled."
    body = "\n".join([
        f"Hello {user.get_full_name() or user.username},",
        "",
        f"A password reset was requested for your {_institution()} account.",
        "",
        "Use this secure link to choose a new password:",
        reset_url,
        "",
        f"The link expires in {expiry_minutes} minutes and can only be used once.",
        "If you did not request this, you can ignore this message — your password is unchanged.",
    ])
    return send_system_email(user.email, f"[{_institution()}] Password reset request", body,
                             template_code="password_reset", user=user)


def notify_password_changed(user, request=None):
    if not bool(get_setting("notify_password_changed", True)):
        return False, "Notification disabled."
    body = "\n".join([
        f"Hello {user.get_full_name() or user.username},",
        "",
        f"The password on your {_institution()} account was changed on "
        f"{timezone.now():%Y-%m-%d %H:%M}.",
        "",
        "All other sessions have been signed out.",
        "If this was not you, contact the ICT service desk immediately.",
    ])
    return send_system_email(user.email, f"[{_institution()}] Your password was changed", body,
                             template_code="password_changed", user=user)


def notify_account_status(user, status, reason="", request=None):
    if not bool(get_setting("notify_account_status", True)):
        return False, "Notification disabled."
    body = "\n".join([
        f"Hello {user.get_full_name() or user.username},",
        "",
        f"The status of your {_institution()} account is now: {status}.",
        f"Reason: {reason}" if reason else "",
        "",
        "Contact the ICT service desk if you believe this is an error.",
    ])
    return send_system_email(user.email, f"[{_institution()}] Account status: {status}", body,
                             template_code="account_status", user=user)


def notify_email_ready(user, address, request=None):
    if not bool(get_setting("notify_account_created", True)):
        return False, "Notification disabled."
    body = "\n".join([
        f"Hello {user.get_full_name() or user.username},",
        "",
        f"Your {_institution()} institutional email identity is: {address}",
        "",
        "It will be used for official correspondence, fee receipts, results notices "
        "and account security messages.",
    ])
    return send_system_email(user.email or address,
                             f"[{_institution()}] Your institutional email", body,
                             template_code="email_ready", user=user)
