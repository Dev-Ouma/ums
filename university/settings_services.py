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
        "value": "1500.00",
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
