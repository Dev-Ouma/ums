import json
from decimal import Decimal
from django.utils import timezone
from university.models import SystemSetting, AuditLog
from university.audit_services import log_activity


DEFAULT_SETTINGS = [
    # ACADEMIC
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "academic_year_current",
        "label": "Current Academic Year",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "2025/2026",
        "description": "Active operational academic calendar year.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "current_semester",
        "label": "Current Active Semester",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "1",
        "description": "Current semester in progress (1, 2, or 3).",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "pass_mark",
        "label": "Minimum Pass Mark (%)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "40",
        "description": "Minimum aggregate mark required to achieve a passing grade (CUE standard: 40%).",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "max_credits_per_semester",
        "label": "Maximum Credits Allowed Per Semester",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "36",
        "description": "Upper credit limit for regular unit registration without academic dean approval.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "min_credits_per_semester",
        "label": "Minimum Credits Required Per Semester",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "18",
        "description": "Minimum workload required for full-time enrolled student status.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.ACADEMIC,
        "key": "grading_system_cue",
        "label": "CUE Standard Grading Matrix",
        "value_type": SystemSetting.ValueType.JSON,
        "value": json.dumps([
            {"grade": "A", "min": 70, "max": 100, "points": 4.0, "status": "Distinction"},
            {"grade": "B", "min": 60, "max": 69, "points": 3.0, "status": "Credit"},
            {"grade": "C", "min": 50, "max": 59, "points": 2.0, "status": "Pass"},
            {"grade": "D", "min": 40, "max": 49, "points": 1.0, "status": "Subsidiary Pass"},
            {"grade": "F", "min": 0, "max": 39, "points": 0.0, "status": "Fail"},
        ], indent=2),
        "description": "Standard Commission for University Education (CUE) grade conversion breakdown.",
        "is_public": True,
    },

    # UNIVERSITY STRUCTURE
    {
        "category": SystemSetting.Category.UNIVERSITY,
        "key": "institution_name",
        "label": "Institution Name",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "Nexus International University",
        "description": "Legal institutional university title appearing on official transcripts and credentials.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.UNIVERSITY,
        "key": "institution_code",
        "label": "Institution Identification Code",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "NIU",
        "description": "Short code used in matriculation numbers, serials, and course prefixes.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.UNIVERSITY,
        "key": "institution_motto",
        "label": "Institution Motto",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "Excellence in Knowledge, Integrity in Leadership",
        "description": "Official institutional philosophy.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.UNIVERSITY,
        "key": "active_campuses",
        "label": "Active University Campuses",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "Main Campus, Nairobi City Campus, Mombasa Campus",
        "description": "Accredited campus branches for cohort assignment.",
        "is_public": True,
    },

    # EXAMINATION & ASSESSMENT
    {
        "category": SystemSetting.Category.EXAMINATION,
        "key": "cat_weight_percent",
        "label": "Continuous Assessment Test (CAT) Weight (%)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "30",
        "description": "Percentage weight allocated to coursework and continuous assessments.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.EXAMINATION,
        "key": "exam_weight_percent",
        "label": "Final Examination Weight (%)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "70",
        "description": "Percentage weight allocated to end-of-semester written examinations.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.EXAMINATION,
        "key": "internal_examiner_workflow",
        "label": "Require Internal Examiner Review",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Enforce departmental internal moderation workflow before results release.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.EXAMINATION,
        "key": "external_examiner_workflow",
        "label": "Require External Examiner Approval",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Mandate external referee vetting for degree classification conferment.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.EXAMINATION,
        "key": "exam_clearance_fee_threshold",
        "label": "Exam Clearance Minimum Fee Payment (%)",
        "value_type": SystemSetting.ValueType.DECIMAL,
        "value": "100.0",
        "description": "Fee clearance percentage required to generate exam card and sit examinations.",
        "is_public": True,
    },

    # TIMETABLE
    {
        "category": SystemSetting.Category.TIMETABLE,
        "key": "timetable_start_time",
        "label": "Daily Scheduling Start Time",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "07:00",
        "description": "Earliest permissible lecture start time.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.TIMETABLE,
        "key": "timetable_end_time",
        "label": "Daily Scheduling End Time",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "19:00",
        "description": "Latest permissible lecture conclusion time.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.TIMETABLE,
        "key": "timetable_slot_minutes",
        "label": "Standard Lecture Block Duration (minutes)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "60",
        "description": "Standard time block granularity for lecture and lab bookings.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.TIMETABLE,
        "key": "enforce_venue_clash_detection",
        "label": "Strict Venue Clash Detection",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Block scheduling overlapping lectures in the same physical venue.",
        "is_public": False,
    },

    # STUDENT & REGISTRATION
    {
        "category": SystemSetting.Category.REGISTRATION,
        "key": "unit_registration_active",
        "label": "Unit Registration Window Open",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Globally permit or close student online unit self-registration.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.REGISTRATION,
        "key": "unit_registration_deadline",
        "label": "Unit Registration Cut-off Date",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "2026-10-31",
        "description": "Enforced deadline after which late penalty fees apply.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.REGISTRATION,
        "key": "admissions_portal_active",
        "label": "Online Admissions Portal Active",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Permit prospective candidates to register and submit intake applications.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.REGISTRATION,
        "key": "application_fee_default",
        "label": "Standard Application Processing Fee (KES)",
        "value_type": SystemSetting.ValueType.DECIMAL,
        "value": "1000.00",
        "description": "Required processing fee levied on prospective applicant dossiers.",
        "is_public": True,
    },

    # FINANCE & BRANDING
    {
        "category": SystemSetting.Category.FINANCE,
        "key": "contact_official_email",
        "label": "Official Registrar Contact Email",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "registrar@university.edu",
        "description": "Primary administrative desk email on official communications.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.FINANCE,
        "key": "contact_phone_number",
        "label": "Central Inquiries Phone Number",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "+254 (020) 790-0000",
        "description": "University switchboard telephone contact line.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.FINANCE,
        "key": "system_primary_color",
        "label": "Brand Primary Theme Accent Color",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "#206bc4",
        "description": "Hex color code utilized across student portal and header banners.",
        "is_public": True,
    },
    {
        "category": SystemSetting.Category.FINANCE,
        "key": "document_serial_prefix",
        "label": "Transcript & Result Document Serial Prefix",
        "value_type": SystemSetting.ValueType.STRING,
        "value": "NIU/ACAD/CERT/",
        "description": "Standard prefix formatted into official sealed transcripts and award slips.",
        "is_public": False,
    },

    # SECURITY
    {
        "category": SystemSetting.Category.SECURITY,
        "key": "max_login_attempts",
        "label": "Maximum Consecutive Failed Logins Before Lockout",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "5",
        "description": "Failed authentication threshold before temporary IP rate-limiting applies.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.SECURITY,
        "key": "session_timeout_minutes",
        "label": "Session Inactivity Timeout (Minutes)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "60",
        "description": "Automatic idle user logout expiration window.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.SECURITY,
        "key": "require_strong_passwords",
        "label": "Enforce Complex Password Complexity Rules",
        "value_type": SystemSetting.ValueType.BOOLEAN,
        "value": "true",
        "description": "Require minimum length, mixed casing, numeric digits, and special characters.",
        "is_public": False,
    },
    {
        "category": SystemSetting.Category.SECURITY,
        "key": "audit_retention_days",
        "label": "Audit Trail Retention Period (Days)",
        "value_type": SystemSetting.ValueType.INTEGER,
        "value": "365",
        "description": "Minimum duration system activity traces must be preserved before archival.",
        "is_public": False,
    },
]


def seed_default_settings():
    """Populate default system settings if they do not exist yet."""
    created_count = 0
    for s_def in DEFAULT_SETTINGS:
        _, created = SystemSetting.objects.get_or_create(
            key=s_def["key"],
            defaults=s_def
        )
        if created:
            created_count += 1
    return created_count


def get_setting(key, default=None):
    """
    Retrieve and parse a typed configuration setting.
    Returns python typed object (bool, int, Decimal, dict/list, or str).
    """
    try:
        setting = SystemSetting.objects.filter(key=key).first()
        if not setting:
            return default
        val = setting.value
        v_type = setting.value_type

        if v_type == SystemSetting.ValueType.BOOLEAN:
            return str(val).strip().lower() in ("true", "1", "yes", "on")
        elif v_type == SystemSetting.ValueType.INTEGER:
            return int(val)
        elif v_type == SystemSetting.ValueType.DECIMAL:
            return Decimal(str(val))
        elif v_type == SystemSetting.ValueType.JSON:
            return json.loads(val)
        return val
    except Exception:
        return default


def set_setting(key, value, user=None, request=None):
    """
    Update or create a setting with audit trail logging.
    """
    setting = SystemSetting.objects.filter(key=key).first()
    prev_val = setting.value if setting else None

    # Format value for storage
    if isinstance(value, bool):
        val_str = "true" if value else "false"
        v_type = SystemSetting.ValueType.BOOLEAN
    elif isinstance(value, int):
        val_str = str(value)
        v_type = SystemSetting.ValueType.INTEGER
    elif isinstance(value, Decimal):
        val_str = str(value)
        v_type = SystemSetting.ValueType.DECIMAL
    elif isinstance(value, (dict, list)):
        val_str = json.dumps(value, indent=2)
        v_type = SystemSetting.ValueType.JSON
    else:
        val_str = str(value)
        v_type = SystemSetting.ValueType.STRING

    if setting:
        setting.value = val_str
        setting.save()
    else:
        # Create with default attributes
        label = key.replace("_", " ").title()
        setting = SystemSetting.objects.create(
            key=key,
            label=label,
            category=SystemSetting.Category.ACADEMIC,
            value_type=v_type,
            value=val_str,
            is_public=False
        )

    # Synchronize CMS branding if institution_name is updated
    if key == "institution_name":
        try:
            from cms.models import SiteSettings
            cms_site = SiteSettings.load()
            if cms_site and cms_site.site_name != val_str:
                cms_site.site_name = val_str
                cms_site.save(update_fields=["site_name", "updated_at"])
        except Exception:
            pass

    # Log to Audit Log
    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.CONFIG_CHANGE,
        module=AuditLog.Module.CONFIG,
        entity="SystemSetting",
        entity_id=key,
        description=f"Updated system setting '{key}' from '{prev_val}' to '{val_str}'",
        previous_state={"key": key, "value": prev_val},
        new_state={"key": key, "value": val_str},
    )

    return setting


# ==============================================================================
# USER MANAGEMENT & IDENTITY ADMINISTRATION
# ==============================================================================
# Every rule the identity layer enforces — usernames, passwords, lockout, email
# domains, notifications — is stored here so an institution can change policy
# without a code change.

def _sec(key, label, value, description, value_type=SystemSetting.ValueType.STRING,
         category=SystemSetting.Category.SECURITY):
    return {
        "category": category,
        "key": key,
        "label": label,
        "value_type": value_type,
        "value": value,
        "description": description,
        "is_public": False,
    }


_BOOL = SystemSetting.ValueType.BOOLEAN
_INT = SystemSetting.ValueType.INTEGER

IDENTITY_SETTINGS = [
    # --- Username rules ---
    _sec("username_student_format", "Student Username Format", "{REGNO}",
         "Pattern tokens: {REGNO} {FIRST} {LAST} {INITIAL} {PROG} {YEAR} {SEQ}."),
    _sec("username_staff_format", "Staff Username Format", "{FIRST}.{LAST}",
         "Pattern tokens: {STAFFID} {FIRST} {LAST} {INITIAL} {YEAR} {SEQ}."),
    _sec("username_prefix", "Username Prefix", "", "Optional institutional prefix."),
    _sec("username_suffix", "Username Suffix", "", "Optional institutional suffix."),
    _sec("username_separator", "Username Separator", ".",
         "Character joining name parts when a pattern contains spaces."),
    _sec("username_case", "Username Case", "LOWER", "LOWER or UPPER."),
    _sec("username_max_length", "Maximum Username Length", "30",
         "Usernames are truncated to this length before collision handling.", _INT),
    _sec("username_allowed_characters", "Allowed Username Characters", "a-zA-Z0-9._-",
         "Regex character class of permitted characters."),
    _sec("username_sequential_numbering", "Use Sequential Username Numbering", "false",
         "Feed an incrementing {SEQ} token into the username pattern.", _BOOL),
    _sec("username_sequence_padding", "Username Sequence Padding", "4",
         "Zero-padding width for the {SEQ} token.", _INT),

    # --- Password policy ---
    _sec("password_min_length", "Minimum Password Length", "8",
         "Shortest password accepted anywhere in the system.", _INT),
    _sec("password_max_length", "Maximum Password Length", "128",
         "Longest password accepted.", _INT),
    _sec("password_require_uppercase", "Require Uppercase Letter", "true",
         "Passwords must contain at least one A-Z character.", _BOOL),
    _sec("password_require_lowercase", "Require Lowercase Letter", "true",
         "Passwords must contain at least one a-z character.", _BOOL),
    _sec("password_require_number", "Require Number", "true",
         "Passwords must contain at least one digit.", _BOOL),
    _sec("password_require_special", "Require Special Character", "false",
         "Passwords must contain at least one non-alphanumeric character.", _BOOL),
    _sec("password_history_depth", "Prohibited Previous Passwords", "5",
         "How many recent passwords may not be reused. 0 disables the check.", _INT),
    _sec("password_expiry_days", "Password Expiry (Days)", "0",
         "Force a password change after this many days. 0 means never.", _INT),
    _sec("temporary_password_expiry_hours", "Temporary Password Expiry (Hours)", "48",
         "How long an administrator-issued temporary password stays usable.", _INT),
    _sec("password_reset_token_minutes", "Reset Link Validity (Minutes)", "60",
         "Lifetime of a self-service password reset link.", _INT),
    _sec("activation_token_minutes", "Activation Link Validity (Minutes)", "4320",
         "Lifetime of a new-account activation link (default 3 days).", _INT),
    _sec("generated_password_length", "Generated Password Length", "12",
         "Length used by the secure password generator.", _INT),

    # --- Lockout & sessions ---
    _sec("account_lock_duration_minutes", "Account Lock Duration (Minutes)", "30",
         "How long an automatic lockout lasts before it lifts on its own.", _INT),
    _sec("login_progressive_delay", "Progressive Login Delay", "true",
         "Apply an increasing delay after repeated failed sign-in attempts.", _BOOL),
    _sec("force_logout_after_password_reset", "Force Logout After Password Reset", "true",
         "Invalidate every other active session when a password changes.", _BOOL),

    # --- Account lifecycle ---
    _sec("auto_create_student_accounts", "Auto-Create Student Accounts", "true",
         "Provision a user account automatically when a student is admitted.", _BOOL),
    _sec("auto_activate_student_accounts", "Auto-Activate Student Accounts", "true",
         "Activate provisioned student accounts immediately instead of leaving them pending.", _BOOL),
    _sec("auto_create_staff_accounts", "Auto-Create Staff Accounts", "true",
         "Provision a user account automatically when a staff record is created.", _BOOL),
    _sec("auto_activate_staff_accounts", "Auto-Activate Staff Accounts", "true",
         "Activate provisioned staff accounts immediately.", _BOOL),
    _sec("auto_generate_email", "Auto-Generate Institutional Email", "true",
         "Generate an institutional address whenever an account is created.", _BOOL),

    # --- Email identity ---
    _sec("email_student_domain", "Student Email Domain", "students.university.edu",
         "Domain used for generated student addresses."),
    _sec("email_staff_domain", "Staff Email Domain", "university.edu",
         "Domain used for generated staff addresses."),
    _sec("email_admin_domain", "Administrator Email Domain", "university.edu",
         "Domain used for generated administrator addresses."),
    _sec("email_student_format", "Student Email Format", "{REGNO}",
         "Tokens: {REGNO} {FIRST} {LAST} {INITIAL} {USERNAME}."),
    _sec("email_staff_format", "Staff Email Format", "{FIRST}.{LAST}",
         "Tokens: {STAFFID} {FIRST} {LAST} {INITIAL} {USERNAME}."),
    _sec("email_admin_format", "Administrator Email Format", "{FIRST}.{LAST}",
         "Tokens: {FIRST} {LAST} {INITIAL} {USERNAME}."),
    _sec("email_case", "Email Address Case", "LOWER", "LOWER or UPPER for generated local parts."),

    # --- Email provider ---
    _sec("email_enabled", "Outbound Email Enabled", "true",
         "Master switch for all system-generated email.", _BOOL),
    _sec("email_provider", "Email Provider", "CONSOLE",
         "CONSOLE, SMTP, MICROSOFT365, GOOGLE_WORKSPACE or DISABLED."),
    _sec("email_host", "Email Host / API Endpoint", "",
         "SMTP host. Left blank, Microsoft 365 and Google Workspace use their default relays."),
    _sec("email_port", "Email Port", "587", "SMTP port.", _INT),
    _sec("email_encryption", "Email Encryption", "TLS", "TLS, SSL or NONE."),
    _sec("email_host_user", "Email Account Username", "", "Authentication username for the provider."),
    _sec("email_host_password", "Email Account Password / API Key", "",
         "Provider secret. Never rendered back to the browser."),
    _sec("email_from_name", "Sender Display Name", "University Management System",
         "Friendly name on outbound messages."),
    _sec("email_from_address", "Sender Email Address", "no-reply@university.edu",
         "Envelope sender for system mail."),
    _sec("email_reply_to", "Reply-To Address", "", "Optional reply-to header."),
    _sec("email_timeout_seconds", "Email Timeout (Seconds)", "20",
         "Connection timeout for the mail provider.", _INT),
    _sec("email_provisioning_enabled", "Automatic Mailbox Provisioning", "false",
         "Ask the provider API to create mailboxes. Requires a provider driver.", _BOOL),
    _sec("site_base_url", "System Base URL", "",
         "Absolute base URL used in emailed links when no request context exists."),

    # --- Notifications ---
    _sec("notify_account_created", "Notify On Account Creation", "true",
         "Email the user when their account is created.", _BOOL),
    _sec("notify_password_reset", "Notify On Password Reset Request", "true",
         "Email the secure reset link when a reset is requested.", _BOOL),
    _sec("notify_password_changed", "Notify On Password Change", "true",
         "Email a security confirmation after a password changes.", _BOOL),
    _sec("notify_account_status", "Notify On Account Status Change", "true",
         "Email the user when their account is activated, suspended or disabled.", _BOOL),
]

DEFAULT_SETTINGS.extend(IDENTITY_SETTINGS)
