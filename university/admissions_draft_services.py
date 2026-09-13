"""
Admissions Application Draft & State Recovery Service.
Handles draft auto-save, optimistic concurrency, state serialization,
document attachment linking, and strict final submission validation.
"""

from datetime import date
from decimal import Decimal
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.forms import EmailField
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from university.admissions_services import generate_application_number
from university.audit_services import log_activity
from university.models import (
    Application,
    ApplicationAttachment,
    ApplicationCustomField,
    ApplicationCustomFieldValue,
    AuditLog,
    Intake,
    Program,
)

logger = logging.getLogger(__name__)


KENYAN_COUNTIES = [
    "Baringo", "Bomet", "Bungoma", "Busia", "Elgeyo-Marakwet", "Embu",
    "Garissa", "Homa Bay", "Isiolo", "Kajiado", "Kakamega", "Kericho",
    "Kiambu", "Kilifi", "Kirinyaga", "Kisii", "Kisumu", "Kitui",
    "Kwale", "Laikipia", "Lamu", "Machakos", "Makueni", "Mandera",
    "Marsabit", "Meru", "Migori", "Mombasa", "Murang'a", "Nairobi",
    "Nakuru", "Nandi", "Narok", "Nyamira", "Nyandarua", "Nyeri",
    "Samburu", "Siaya", "Taita-Taveta", "Tana River", "Tharaka-Nithi",
    "Trans Nzoia", "Turkana", "Uasin Gishu", "Vihiga", "Wajir", "West Pokot",
]

COUNTRIES_LIST = [
    "Kenya", "Uganda", "Tanzania", "Rwanda", "Burundi", "South Sudan",
    "Ethiopia", "Somalia", "Nigeria", "Ghana", "South Africa", "Egypt",
    "United Kingdom", "United States", "Canada", "Australia", "India",
    "Germany", "France", "China", "United Arab Emirates", "Other",
]


def get_default_active_intake() -> Optional[Intake]:
    """
    Determines the most active/current intake based on date ranges
    (start_date <= today <= end_date and is_active=True).
    Falls back to closest upcoming active intake or latest active intake.
    """
    today = timezone.now().date()
    # 1. Currently active and within date range
    active_current = Intake.objects.filter(is_active=True, start_date__lte=today, end_date__gte=today).order_by("start_date").first()
    if active_current:
        return active_current
    # 2. Active intake whose end_date is in future
    upcoming_active = Intake.objects.filter(is_active=True, end_date__gte=today).order_by("start_date").first()
    if upcoming_active:
        return upcoming_active
    # 3. Any active intake
    return Intake.objects.filter(is_active=True).order_by("-start_date").first()


def get_active_applicant_draft(request) -> Optional[Application]:
    """Return the caller's current draft without creating or modifying one."""
    draft_statuses = [Application.Status.DRAFT, Application.Status.IN_PROGRESS]
    user = request.user if request.user.is_authenticated else None
    session_key = request.session.session_key

    if user and getattr(user, "role", "") in (Role.APPLICANT, Role.STUDENT, ""):
        draft = (
            Application.objects.filter(applicant_user=user, status__in=draft_statuses)
            .order_by("-updated_at")
            .first()
        )
        if draft:
            return draft

    if session_key:
        filters = {"session_key": session_key, "status__in": draft_statuses}
        if user:
            filters["applicant_user__isnull"] = True
        return (
            Application.objects.filter(**filters)
            .order_by("-updated_at")
            .first()
        )
    return None


def empty_draft_state() -> Dict[str, Any]:
    """Build form defaults without allocating an application reference."""
    intakes_data = get_available_intakes_data()
    return {
        "application_id": None,
        "application_number": "",
        "status": Application.Status.DRAFT,
        "is_draft": True,
        "version": 1,
        "step": 1,
        "completion_percentage": 0,
        "updated_at": timezone.now().isoformat(),
        "fields": {
            "intake": str(intakes_data.get("default_intake_id") or ""),
            "intake_id": intakes_data.get("default_intake_id"),
            "program": "",
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "date_of_birth": "",
            "gender": "MALE",
            "national_id": "",
            "country": "Kenya",
            "county": "Nairobi",
            "nationality": "Kenyan",
            "address": "",
            "guardian_name": "",
            "guardian_relationship": "Parent",
            "guardian_phone": "",
            "guardian_alternative_phone": "",
            "guardian_email": "",
            "guardian_address": "",
            "guardian_country": "Kenya",
            "guardian_occupation": "",
            "guardian_employer": "",
            "is_guardian_emergency_contact": True,
            "secondary_school": "",
            "kcse_index_number": "",
            "kcse_mean_grade": "C+",
            "kcse_year": 2025,
        },
        "documents": {},
        "intakes_data": intakes_data,
        "locations": {
            "countries": COUNTRIES_LIST,
            "counties": KENYAN_COUNTIES,
            "default_country": "Kenya",
            "default_county": "Nairobi",
        },
    }


def get_available_intakes_data() -> Dict[str, Any]:
    """
    Returns structured list of all available intakes with status flags and default active ID.
    """
    today = timezone.now().date()
    default_intake = get_default_active_intake()
    default_id = default_intake.id if default_intake else None

    intakes = []
    for intake in Intake.objects.all().select_related("academic_year").order_by("-start_date"):
        is_current = (intake.id == default_id)
        if intake.is_active and intake.start_date <= today <= intake.end_date:
            status_label = "ACTIVE"
            status_badge = "bg-success"
        elif intake.is_active and intake.start_date > today:
            status_label = "Upcoming"
            status_badge = "bg-primary"
        elif intake.is_active:
            status_label = "Active (Extended)"
            status_badge = "bg-info"
        else:
            status_label = "Closed"
            status_badge = "bg-secondary"

        intakes.append({
            "id": intake.id,
            "name": intake.name,
            "academic_year": intake.academic_year.name if intake.academic_year else "",
            "start_date": intake.start_date.isoformat(),
            "end_date": intake.end_date.isoformat(),
            "is_active": intake.is_active,
            "is_current": is_current,
            "is_default": is_current,
            "status_label": status_label,
            "status_badge": status_badge,
        })

    return {
        "intakes": intakes,
        "default_intake_id": default_id,
        "has_active_intake": bool(default_intake),
    }


def normalize_and_validate_phone(phone_str: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates mobile phone format (Kenyan mobile & E.164 international).
    Rejects text/garbage (e.g. 07hbhbh09784, abc123456, 07!!!!!!!!!).
    Normalizes Kenyan numbers to +254XXXXXXXXX.
    """
    if not phone_str or not str(phone_str).strip():
        return False, None, "Mobile phone number is required."

    raw = str(phone_str).strip()

    # Reject if contains letters or special garbage chars (other than +, -, space, brackets)
    if re.search(r"[a-zA-Z]", raw) or re.search(r"[^\d+\-\s().]", raw):
        return False, None, "Please enter a valid mobile phone number without letters or special characters."

    has_plus = raw.startswith("+")
    digits_only = re.sub(r"\D", "", raw)

    if len(digits_only) < 9 or len(digits_only) > 15:
        return False, None, "Please enter a valid mobile phone number (9 to 15 digits)."

    # Case 1: 07XXXXXXXX or 01XXXXXXXX (10 digits starting with 07 or 01)
    if digits_only.startswith("0") and len(digits_only) == 10 and digits_only[1] in ("7", "1"):
        normalized = f"+254{digits_only[1:]}"
        return True, normalized, None

    # Case 2: 2547XXXXXXXX or 2541XXXXXXXX (12 digits starting with 2547 or 2541)
    if digits_only.startswith("254") and len(digits_only) == 12 and digits_only[3] in ("7", "1"):
        normalized = f"+{digits_only}"
        return True, normalized, None

    # Case 3: 7XXXXXXXX or 1XXXXXXXX (9 digits starting with 7 or 1)
    if len(digits_only) == 9 and digits_only[0] in ("7", "1"):
        normalized = f"+254{digits_only}"
        return True, normalized, None

    # Case 4: International format starting with +
    if has_plus and 8 <= len(digits_only) <= 15:
        return True, f"+{digits_only}", None

    # Generic 10-15 digit phone format
    if 9 <= len(digits_only) <= 15:
        normalized = f"+{digits_only}" if has_plus else (f"+254{digits_only}" if len(digits_only) == 9 else f"+{digits_only}")
        return True, normalized, None

    return False, None, "Please enter a valid mobile phone number."


EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
)


def normalize_and_validate_email(email_str: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates RFC email format, rejecting consecutive dots, missing TLDs, and invalid chars.
    Normalizes by trimming whitespace and lowercasing.
    """
    if not email_str or not str(email_str).strip():
        return False, None, "Email address is required."

    raw = str(email_str).strip().lower()

    if ".." in raw or "@." in raw or ".@" in raw or raw.startswith(".") or raw.endswith("."):
        return False, None, "Please enter a valid email address."

    if not EMAIL_REGEX.match(raw):
        return False, None, "Please enter a valid email address."

    try:
        EmailField().clean(raw)
    except ValidationError:
        return False, None, "Please enter a valid email address."

    return True, raw, None


NAME_REGEX = re.compile(r"^[a-zA-Z\s'-]+$")


def validate_name_field(name_str: str, field_label: str = "Name", min_len: int = 2, max_len: int = 60, required: bool = True) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates names to contain only alphabetic characters, hyphens, and apostrophes.
    """
    if not name_str or not str(name_str).strip():
        if required:
            return False, None, f"{field_label} is required."
        return True, "", None

    raw = " ".join(str(name_str).strip().split())

    if len(raw) < min_len:
        return False, None, f"{field_label} must be at least {min_len} characters long."
    if len(raw) > max_len:
        return False, None, f"{field_label} cannot exceed {max_len} characters."

    if not NAME_REGEX.match(raw):
        return False, None, f"{field_label} must contain only letters, hyphens, or apostrophes."

    normalized = raw.title()
    return True, normalized, None


def validate_date_of_birth(dob_val: Any, min_age: int = 16, max_age: int = 100) -> Tuple[bool, Optional[date], Optional[str]]:
    """
    Validates date of birth: must be a valid date, not future, and applicant >= 16 years old.
    """
    if not dob_val or not str(dob_val).strip():
        return False, None, "Date of birth is required."

    parsed_date = None
    if isinstance(dob_val, date):
        parsed_date = dob_val
    else:
        try:
            parsed_date = date.fromisoformat(str(dob_val).strip())
        except (ValueError, TypeError):
            return False, None, "Please enter a valid date of birth (YYYY-MM-DD)."

    today = timezone.now().date()
    if parsed_date > today:
        return False, None, "Date of birth cannot be in the future."

    age = today.year - parsed_date.year - ((today.month, today.day) < (parsed_date.month, parsed_date.day))
    if age < min_age:
        return False, None, f"Applicant must be at least {min_age} years of age for tertiary admission."
    if age > max_age:
        return False, None, "Please enter a valid date of birth."

    return True, parsed_date, None


def get_or_create_applicant_draft(request) -> Application:
    """
    Retrieves the single active draft application for the authenticated applicant
    or anonymous session. If none exists, creates an idempotent draft record with
    active intake and location defaults.
    """
    active_intake = get_default_active_intake()
    user = request.user if request.user.is_authenticated else None
    session = request.session
    if not session.session_key:
        session.save()
    session_key = session.session_key

    draft_statuses = [
        Application.Status.DRAFT,
        Application.Status.IN_PROGRESS,
    ]

    with transaction.atomic():
        if user and getattr(user, "role", "") in (Role.APPLICANT, Role.STUDENT, ""):
            # Check for existing draft owned by this user
            draft = (
                Application.objects.select_for_update()
                .filter(applicant_user=user, status__in=draft_statuses)
                .order_by("-updated_at")
                .first()
            )

            # If no user draft, check if an unassigned draft exists with this session key or email
            if not draft and session_key:
                draft = (
                    Application.objects.select_for_update()
                    .filter(session_key=session_key, applicant_user__isnull=True, status__in=draft_statuses)
                    .order_by("-updated_at")
                    .first()
                )
                if draft:
                    draft.applicant_user = user
                    if not draft.email and user.email:
                        draft.email = user.email
                    if not draft.first_name and user.first_name:
                        draft.first_name = user.first_name
                    if not draft.last_name and user.last_name:
                        draft.last_name = user.last_name
                    if not draft.phone and user.phone and user.phone != "0000":
                        draft.phone = user.phone
                    draft.save()

            # If still none, create a new draft for this user
            if not draft:
                app_num = generate_application_number(active_intake)
                draft = Application.objects.create(
                    application_number=app_num,
                    applicant_user=user,
                    session_key=session_key,
                    intake=active_intake,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    email=user.email or "",
                    phone=user.phone if getattr(user, "phone", "") != "0000" else "",
                    country="Kenya",
                    county="Nairobi",
                    nationality="Kenyan",
                    status=Application.Status.DRAFT,
                    draft_step=1,
                    draft_version=1,
                )
            return draft

        else:
            # Anonymous applicant with session_key
            draft = (
                Application.objects.select_for_update()
                .filter(session_key=session_key, status__in=draft_statuses)
                .order_by("-updated_at")
                .first()
            )
            if not draft:
                app_num = generate_application_number(active_intake)
                draft = Application.objects.create(
                    application_number=app_num,
                    session_key=session_key,
                    intake=active_intake,
                    country="Kenya",
                    county="Nairobi",
                    nationality="Kenyan",
                    status=Application.Status.DRAFT,
                    draft_step=1,
                    draft_version=1,
                )
            return draft


def calculate_completion_percentage(application: Application, request=None) -> int:
    """
    Computes real-time progress percentage (0 - 100%) across application sections.
    """
    if not application:
        return 0

    points = 0

    # 1. Programme Selection (15 pts)
    if application.program_id:
        points += 15

    # 2. Bio-Data / Personal Details (25 pts)
    bio_fields = [
        bool(application.first_name and application.first_name.strip()),
        bool(application.last_name and application.last_name.strip()),
        bool(application.email and application.email.strip()),
        bool(application.phone and application.phone.strip()),
        bool(application.date_of_birth),
        bool(application.gender and application.gender.strip()),
        bool(application.national_id and application.national_id.strip()),
    ]
    bio_score = sum(bio_fields)
    points += int((bio_score / len(bio_fields)) * 25)

    # 3. Guardian / Emergency Contact (20 pts)
    guardian_fields = [
        bool(application.guardian_name and application.guardian_name.strip()),
        bool(application.guardian_relationship and application.guardian_relationship.strip()),
        bool(application.guardian_phone and application.guardian_phone.strip()),
    ]
    guardian_score = sum(guardian_fields)
    points += int((guardian_score / len(guardian_fields)) * 20)

    # 4. Academic Details (20 pts)
    academic_fields = [
        bool(application.secondary_school and application.secondary_school.strip()),
        bool(application.kcse_index_number and application.kcse_index_number.strip()),
        bool(application.kcse_mean_grade and application.kcse_mean_grade.strip()),
        bool(application.kcse_year),
    ]
    acad_score = sum(academic_fields)
    points += int((acad_score / len(academic_fields)) * 20)

    # 5. Documents Uploaded (20 pts)
    # Check both ApplicationAttachment records and session documents
    docs = get_draft_documents_metadata(application, request)
    required_doc_types = ["kcse_document", "id_document", "passport_photo"]
    doc_score = sum(1 for dt in required_doc_types if dt in docs and docs[dt].get("uploaded"))
    points += int((doc_score / len(required_doc_types)) * 20)

    return min(100, max(0, points))


def get_draft_documents_metadata(application: Application, request=None) -> Dict[str, Any]:
    """
    Returns structured metadata for all uploaded documents associated with the draft.
    """
    result = {}
    doc_mapping = {
        ApplicationAttachment.DocType.KCSE_CERTIFICATE: "kcse_document",
        ApplicationAttachment.DocType.NATIONAL_ID: "id_document",
        ApplicationAttachment.DocType.PASSPORT_PHOTO: "passport_photo",
        ApplicationAttachment.DocType.OTHER: "other_document",
    }

    # 1. Inspect DB attachments
    if application and application.pk:
        for att in application.attachments.all():
            mapped_key = doc_mapping.get(att.document_type, att.document_type)
            result[mapped_key] = {
                "uploaded": True,
                "attachment_id": att.pk,
                "file_name": att.file_name or (os.path.basename(att.file.name) if att.file else "document.pdf"),
                "file_size": att.file_size or 0,
                "file_size_formatted": _format_bytes(att.file_size or 0),
                "mime_type": att.mime_type or "application/pdf",
                "uploaded_at": att.uploaded_at.isoformat() if att.uploaded_at else "",
            }

    # 2. Inspect session documents if request provided
    if request:
        session_docs = request.session.get("draft_application_documents", {})
        for key, sdoc in session_docs.items():
            if key not in result:
                result[key] = {
                    "uploaded": True,
                    "attachment_id": None,
                    "file_name": sdoc.get("original_name", "document.pdf"),
                    "file_size": sdoc.get("file_size", 0),
                    "file_size_formatted": sdoc.get("file_size_formatted", _format_bytes(sdoc.get("file_size", 0))),
                    "mime_type": sdoc.get("mime_type", "application/pdf"),
                    "uploaded_at": sdoc.get("uploaded_at", ""),
                }

    return result


def _format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def serialize_draft_state(application: Application, request=None) -> Dict[str, Any]:
    """
    Serializes current draft state for hydration on page load or after partial auto-saves.
    """
    draft_docs = get_draft_documents_metadata(application, request)
    completion_pct = calculate_completion_percentage(application, request)

    # Custom fields
    custom_field_values = {}
    if application.pk:
        for cfv in application.custom_values.select_related("field").all():
            custom_field_values[f"custom_{cfv.field.name}"] = cfv.value

    # Merge with extra draft_data JSON
    extra_data = application.draft_data or {}
    for k, v in extra_data.items():
        if k not in custom_field_values and k.startswith("custom_"):
            custom_field_values[k] = v

    intakes_data = get_available_intakes_data()

    fields = {
        "intake": str(application.intake_id) if application.intake_id else str(intakes_data.get("default_intake_id") or ""),
        "intake_id": application.intake_id or intakes_data.get("default_intake_id"),
        "program": str(application.program_id) if application.program_id else "",
        "first_name": application.first_name or "",
        "middle_name": application.middle_name or "",
        "last_name": application.last_name or "",
        "email": application.email or "",
        "phone": application.phone or "",
        "date_of_birth": application.date_of_birth.isoformat() if application.date_of_birth else "",
        "gender": application.gender or "MALE",
        "national_id": application.national_id or "",
        "country": application.country or "Kenya",
        "county": application.county or "Nairobi",
        "nationality": application.nationality or "Kenyan",
        "address": application.address or "",
        "guardian_name": application.guardian_name or "",
        "guardian_relationship": application.guardian_relationship or "Parent",
        "guardian_phone": application.guardian_phone or "",
        "guardian_alternative_phone": application.guardian_alternative_phone or "",
        "guardian_email": application.guardian_email or "",
        "guardian_address": application.guardian_address or "",
        "guardian_country": application.guardian_country or "Kenya",
        "guardian_occupation": application.guardian_occupation or "",
        "guardian_employer": application.guardian_employer or "",
        "is_guardian_emergency_contact": application.is_guardian_emergency_contact,
        "secondary_school": application.secondary_school or "",
        "kcse_index_number": application.kcse_index_number or "",
        "kcse_mean_grade": application.kcse_mean_grade or "C+",
        "kcse_year": application.kcse_year or 2025,
    }
    fields.update(custom_field_values)

    return {
        "application_id": application.pk,
        "application_number": application.application_number,
        "status": application.status,
        "is_draft": application.is_draft,
        "version": application.draft_version,
        "step": application.draft_step,
        "completion_percentage": completion_pct,
        "updated_at": application.updated_at.isoformat() if application.updated_at else timezone.now().isoformat(),
        "fields": fields,
        "documents": draft_docs,
        "intakes_data": intakes_data,
        "locations": {
            "countries": COUNTRIES_LIST,
            "counties": KENYAN_COUNTIES,
            "default_country": "Kenya",
            "default_county": "Nairobi",
        },
    }


def update_applicant_draft(
    application: Application,
    data: Dict[str, Any],
    step: Optional[int] = None,
    client_version: Optional[int] = None,
    request=None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Applies partial form updates with optimistic locking and strict normalization/validation.
    """
    if application.status not in [Application.Status.DRAFT, Application.Status.IN_PROGRESS]:
        return False, {
            "success": False,
            "message": "Application cannot be modified after submission.",
            "errors": ["Application cannot be modified after submission."],
        }

    # Optimistic locking check
    if client_version is not None:
        try:
            c_ver = int(client_version)
            if c_ver < application.draft_version:
                current_state = serialize_draft_state(application, request)
                logger.warning(
                    f"Concurrency conflict on application {application.application_number}: "
                    f"client version {c_ver} < server version {application.draft_version}"
                )
                return False, {
                    "conflict": True,
                    "server_version": application.draft_version,
                    "client_version": c_ver,
                    "current_state": current_state,
                    "message": "Application draft has been updated elsewhere. Form state refreshed.",
                }
        except (ValueError, TypeError):
            pass

    errors = {}

    # Validate Intake & Programme
    if "intake_id" in data or "intake" in data:
        raw_intake_id = data.get("intake_id") or data.get("intake")
        if raw_intake_id:
            itk = Intake.objects.filter(pk=raw_intake_id, is_active=True).first()
            if not itk:
                errors["intake_id"] = "The selected intake cycle is not currently available for applications."
            elif itk.end_date and itk.end_date < timezone.now().date():
                errors["intake_id"] = "The selected academic intake has closed and is no longer accepting applications."
            else:
                application.intake = itk

    if "program" in data:
        prog_id = str(data["program"]).strip()
        if prog_id:
            prog = Program.objects.filter(pk=prog_id, status=Program.Status.ACTIVE).first()
            if prog:
                application.program = prog
            else:
                errors["program"] = "Please select a valid active academic programme."

    # Validate Names
    if "first_name" in data:
        val = str(data["first_name"]).strip()
        if val:
            valid, norm, err = validate_name_field(val, "First Name", min_len=2, max_len=60, required=False)
            if not valid:
                errors["first_name"] = err
            else:
                application.first_name = norm
        else:
            application.first_name = ""

    if "middle_name" in data:
        val = str(data["middle_name"]).strip()
        if val:
            valid, norm, err = validate_name_field(val, "Middle Name", min_len=1, max_len=60, required=False)
            if not valid:
                errors["middle_name"] = err
            else:
                application.middle_name = norm
        else:
            application.middle_name = ""

    if "last_name" in data:
        val = str(data["last_name"]).strip()
        if val:
            valid, norm, err = validate_name_field(val, "Last Name", min_len=2, max_len=60, required=False)
            if not valid:
                errors["last_name"] = err
            else:
                application.last_name = norm
        else:
            application.last_name = ""

    # Validate Email
    if "email" in data:
        val = str(data["email"]).strip()
        if val:
            valid, norm, err = normalize_and_validate_email(val)
            if not valid:
                errors["email"] = err
            else:
                application.email = norm
        else:
            application.email = ""

    # Validate Mobile Phone
    if "phone" in data:
        val = str(data["phone"]).strip()
        if val:
            valid, norm, err = normalize_and_validate_phone(val)
            if not valid:
                errors["phone"] = err
            else:
                application.phone = norm
        else:
            application.phone = ""

    # Validate Date of Birth
    if "date_of_birth" in data:
        val = str(data["date_of_birth"]).strip()
        if val:
            valid, parsed_dob, err = validate_date_of_birth(val)
            if not valid:
                errors["date_of_birth"] = err
            else:
                application.date_of_birth = parsed_dob
        else:
            application.date_of_birth = None

    # Validate Gender
    if "gender" in data:
        val = str(data["gender"]).strip().upper()
        if val in ["MALE", "FEMALE", "OTHER"]:
            application.gender = val
        elif val:
            errors["gender"] = "Please select a valid gender."

    # Validate Country, County, Nationality
    if "country" in data:
        val = str(data["country"]).strip()[:80]
        application.country = val or "Kenya"
        if application.country == "Kenya":
            application.nationality = "Kenyan"

    if "county" in data:
        val = str(data["county"]).strip()[:80]
        target_country = data.get("country") or application.country or "Kenya"
        if target_country.lower() == "kenya":
            if val and val not in KENYAN_COUNTIES:
                errors["county"] = "Please select a valid Kenyan county (e.g. Nairobi, Mombasa)."
            else:
                application.county = val or "Nairobi"
        else:
            application.county = val

    if "nationality" in data:
        val = str(data["nationality"]).strip()[:80]
        application.nationality = val or "Kenyan"

    # Validate Guardian details
    if "guardian_phone" in data:
        val = str(data["guardian_phone"]).strip()
        if val:
            valid, norm, err = normalize_and_validate_phone(val)
            if not valid:
                errors["guardian_phone"] = err
            else:
                application.guardian_phone = norm
        else:
            application.guardian_phone = ""

    if "guardian_email" in data:
        val = str(data["guardian_email"]).strip()
        if val:
            valid, norm, err = normalize_and_validate_email(val)
            if not valid:
                errors["guardian_email"] = err
            else:
                application.guardian_email = norm
        else:
            application.guardian_email = ""

    if "guardian_name" in data:
        application.guardian_name = str(data["guardian_name"]).strip()[:120]
    if "guardian_relationship" in data:
        application.guardian_relationship = str(data["guardian_relationship"]).strip()[:60]
    if "guardian_alternative_phone" in data:
        application.guardian_alternative_phone = str(data["guardian_alternative_phone"]).strip()[:30]
    if "guardian_address" in data:
        application.guardian_address = str(data["guardian_address"]).strip()[:255]
    if "guardian_country" in data:
        application.guardian_country = str(data["guardian_country"]).strip()[:80]
    if "guardian_occupation" in data:
        application.guardian_occupation = str(data["guardian_occupation"]).strip()[:120]
    if "guardian_employer" in data:
        application.guardian_employer = str(data["guardian_employer"]).strip()[:150]

    if "national_id" in data:
        application.national_id = str(data["national_id"]).strip()[:50]
    if "address" in data:
        application.address = str(data["address"]).strip()
    if "secondary_school" in data:
        application.secondary_school = str(data["secondary_school"]).strip()[:160]
    if "kcse_index_number" in data:
        application.kcse_index_number = str(data["kcse_index_number"]).strip()[:60]
    if "kcse_mean_grade" in data:
        application.kcse_mean_grade = str(data["kcse_mean_grade"]).strip()[:10]

    # Update KCSE Year
    if "kcse_year" in data:
        try:
            application.kcse_year = int(data["kcse_year"])
        except (ValueError, TypeError):
            pass

    # Update is_guardian_emergency_contact
    if "is_guardian_emergency_contact" in data:
        val = data["is_guardian_emergency_contact"]
        application.is_guardian_emergency_contact = val in (True, "true", "True", "1", 1, "on")

    # If format errors exist, reject update and do not persist corrupted draft
    if errors:
        return False, {
            "success": False,
            "errors": errors,
            "field_errors": errors,
            "error": next(iter(errors.values())),
            "message": "Validation failed for one or more fields.",
        }

    # Handle Custom fields & dynamic data
    custom_updates = {}
    for k, v in data.items():
        if k.startswith("custom_"):
            custom_updates[k] = v
            cf_name = k.replace("custom_", "", 1)
            cf = ApplicationCustomField.objects.filter(name=cf_name, is_active=True).first()
            if cf:
                ApplicationCustomFieldValue.objects.update_or_create(
                    application=application,
                    field=cf,
                    defaults={"value": str(v).strip()},
                )

    if custom_updates:
        curr_draft_data = dict(application.draft_data or {})
        curr_draft_data.update(custom_updates)
        application.draft_data = curr_draft_data

    # Update active step
    if step is not None:
        try:
            s = int(step)
            if 1 <= s <= 6:
                application.draft_step = s
        except (ValueError, TypeError):
            pass

    # Increment version
    application.draft_version += 1
    application.save()

    return True, {
        "success": True,
        "application_id": application.pk,
        "application_number": application.application_number,
        "version": application.draft_version,
        "step": application.draft_step,
        "completion_percentage": calculate_completion_percentage(application, request),
        "updated_at": application.updated_at.isoformat(),
        "fields": serialize_draft_state(application, request)["fields"],
    }


def validate_and_submit_application(
    application: Application,
    payload: Optional[Dict[str, Any]] = None,
    request=None,
    require_documents: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Performs strict validation across all application sections.
    Transitions status from DRAFT / IN_PROGRESS to READY_FOR_PAYMENT.
    Migrates any session draft documents to ApplicationAttachment models.
    """
    from accounts.email_identity_service import EmailIdentityService

    if not application:
        return False, {"errors": ["Application record not found."], "field_errors": {}}

    if application.status not in [Application.Status.DRAFT, Application.Status.IN_PROGRESS]:
        return False, {
            "success": False,
            "errors": [f"Application is currently in '{application.get_status_display()}' state and cannot be resubmitted."],
            "message": f"Application is currently in '{application.get_status_display()}' state and cannot be resubmitted.",
            "field_errors": {},
        }

    errors = []
    field_errors = {}
    p = dict(payload or {})

    # 1. Academic Intake Selection
    intake_id = p.get("intake") or (application.intake_id if application.intake else None)
    intake_obj = None
    if intake_id:
        intake_obj = Intake.objects.filter(pk=intake_id, is_active=True).first()
    else:
        intake_obj = application.intake if (application.intake and application.intake.is_active) else None

    if not intake_obj:
        errors.append("Please select a valid academic intake.")
        field_errors["intake"] = "Please select an available intake."
    elif intake_obj.end_date and intake_obj.end_date < timezone.now().date():
        errors.append(f"The selected intake '{intake_obj.name}' has closed and is no longer accepting submissions.")
        field_errors["intake"] = "This intake has closed."

    # 2. Programme Selection
    prog_id = p.get("program") or (application.program_id if application.program else None)
    program_obj = None
    if prog_id:
        program_obj = Program.objects.filter(pk=prog_id, status=Program.Status.ACTIVE).first()
    else:
        program_obj = application.program if (application.program and application.program.status == Program.Status.ACTIVE) else None

    if not program_obj:
        errors.append("Please select a valid active programme of study.")
        field_errors["program"] = "Please select an active programme."

    # 3. Personal Information
    first_name_raw = p.get("first_name", application.first_name or "").strip()
    middle_name_raw = p.get("middle_name", application.middle_name or "").strip()
    last_name_raw = p.get("last_name", application.last_name or "").strip()
    email_raw = p.get("email", application.email or "").strip().lower()
    phone_raw = p.get("phone", application.phone or "").strip()
    dob_raw = p.get("date_of_birth", application.date_of_birth.isoformat() if application.date_of_birth else "").strip()
    gender = p.get("gender", application.gender or "").strip().upper()
    national_id = p.get("national_id", application.national_id or "").strip()
    country = p.get("country", application.country or "Kenya").strip()
    county = p.get("county", application.county or "Nairobi").strip()
    nationality = p.get("nationality", application.nationality or "Kenyan").strip()
    address = p.get("address", application.address or "").strip()

    # First Name
    valid_fn, first_name, fn_err = validate_name_field(first_name_raw, "First Name", min_len=2, max_len=60, required=True)
    if not valid_fn:
        errors.append(fn_err)
        field_errors["first_name"] = fn_err

    # Middle Name (optional)
    valid_mn, middle_name, mn_err = validate_name_field(middle_name_raw, "Middle Name", min_len=2, max_len=60, required=False)
    if not valid_mn:
        errors.append(mn_err)
        field_errors["middle_name"] = mn_err

    # Last Name
    valid_ln, last_name, ln_err = validate_name_field(last_name_raw, "Last Name", min_len=2, max_len=60, required=True)
    if not valid_ln:
        errors.append(ln_err)
        field_errors["last_name"] = ln_err

    # Email
    valid_em, email, em_err = normalize_and_validate_email(email_raw)
    if not valid_em:
        errors.append(em_err)
        field_errors["email"] = em_err
    else:
        ignore_id = application.applicant_user.id if application.applicant_user else None
        if not EmailIdentityService.is_available(email, ignore_user_id=ignore_id):
            conflict = EmailIdentityService.get_conflict_response(email)
            errors.append(conflict["error"])
            field_errors["email"] = conflict["error"]

    # Mobile Phone
    valid_ph, phone, ph_err = normalize_and_validate_phone(phone_raw)
    if not valid_ph:
        errors.append(ph_err)
        field_errors["phone"] = ph_err

    # Date of birth
    valid_dob, parsed_dob, dob_err = validate_date_of_birth(dob_raw, min_age=16)
    if not valid_dob:
        errors.append(dob_err)
        field_errors["date_of_birth"] = dob_err

    # Gender
    if gender not in ["MALE", "FEMALE", "OTHER"]:
        errors.append("Please select a valid gender.")
        field_errors["gender"] = "Please select a valid gender."

    # National ID
    if not national_id:
        errors.append("National ID / Passport Number is required.")
        field_errors["national_id"] = "National ID / Passport is required."
    elif len(national_id) < 4:
        errors.append("National ID / Passport Number must be at least 4 characters.")
        field_errors["national_id"] = "National ID / Passport must be at least 4 characters."

    # Location (Country & County)
    if not country:
        errors.append("Country of residence is required.")
        field_errors["country"] = "Country is required."
    if not county:
        errors.append("County / State / Province is required.")
        field_errors["county"] = "County/State is required."
    elif country == "Kenya" and county not in KENYAN_COUNTIES:
        errors.append(f"Please select a valid Kenyan county (e.g. Nairobi, Mombasa).")
        field_errors["county"] = "Please select a valid Kenyan county."

    # 4. Guardian Details
    guardian_name = p.get("guardian_name", application.guardian_name or "").strip()
    guardian_relationship = p.get("guardian_relationship", application.guardian_relationship or "Parent").strip()
    guardian_phone_raw = p.get("guardian_phone", application.guardian_phone or "").strip()
    guardian_alt_phone = p.get("guardian_alternative_phone", application.guardian_alternative_phone or "").strip()
    guardian_email_raw = p.get("guardian_email", application.guardian_email or "").strip().lower()
    guardian_address = p.get("guardian_address", application.guardian_address or "").strip()
    guardian_country = p.get("guardian_country", application.guardian_country or "Kenya").strip()
    guardian_occupation = p.get("guardian_occupation", application.guardian_occupation or "").strip()
    guardian_employer = p.get("guardian_employer", application.guardian_employer or "").strip()
    is_guardian_emergency = p.get("is_guardian_emergency_contact", application.is_guardian_emergency_contact) in ("on", "true", "True", "1", 1, True)

    if not guardian_name:
        errors.append("Please provide the full name of your parent, guardian, or sponsor.")
        field_errors["guardian_name"] = "Guardian full name is required."

    if not guardian_phone_raw:
        errors.append("Please provide the primary contact phone number for your guardian.")
        field_errors["guardian_phone"] = "Guardian phone number is required."
    else:
        valid_gph, guardian_phone, gph_err = normalize_and_validate_phone(guardian_phone_raw)
        if not valid_gph:
            errors.append(f"Guardian phone: {gph_err}")
            field_errors["guardian_phone"] = gph_err
    guardian_phone = guardian_phone if 'guardian_phone' in locals() else guardian_phone_raw

    if guardian_email_raw:
        valid_gem, guardian_email, gem_err = normalize_and_validate_email(guardian_email_raw)
        if not valid_gem:
            errors.append(f"Guardian email: {gem_err}")
            field_errors["guardian_email"] = gem_err
    else:
        guardian_email = ""

    # 5. Academic Details
    secondary_school = p.get("secondary_school", application.secondary_school or "").strip()
    kcse_index = p.get("kcse_index_number", application.kcse_index_number or "").strip()
    kcse_grade = p.get("kcse_mean_grade", application.kcse_mean_grade or "C+").strip()
    kcse_year_raw = p.get("kcse_year", str(application.kcse_year or 2025)).strip()
    try:
        kcse_year = int(kcse_year_raw)
    except (ValueError, TypeError):
        kcse_year = 2025

    # 6. Required Documents (if strictly enforced)
    if require_documents:
        docs = get_draft_documents_metadata(application, request)
        required_docs = [
            ("kcse_document", "KCSE Result Slip / Certificate"),
            ("id_document", "National ID / Birth Certificate / Passport"),
        ]
        for doc_key, label in required_docs:
            if doc_key not in docs or not docs[doc_key].get("uploaded"):
                errors.append(f"{label} is required before final submission.")
                field_errors[doc_key] = f"{label} is required."

    if errors:
        return False, {
            "success": False,
            "errors": errors,
            "field_errors": field_errors,
            "error": errors[0],
            "message": "Please correct the highlighted errors.",
        }

    # Apply updates to application
    if intake_obj:
        application.intake = intake_obj
    if program_obj:
        application.program = program_obj
    application.first_name = first_name
    application.middle_name = middle_name
    application.last_name = last_name
    application.email = email
    application.phone = phone
    application.date_of_birth = parsed_dob
    application.gender = gender or "MALE"
    application.national_id = national_id
    application.country = country
    application.county = county
    application.nationality = nationality
    application.address = address
    application.guardian_name = guardian_name
    application.guardian_relationship = guardian_relationship
    application.guardian_phone = guardian_phone
    application.guardian_alternative_phone = guardian_alt_phone
    application.guardian_email = guardian_email
    application.guardian_address = guardian_address
    application.guardian_country = guardian_country
    application.guardian_occupation = guardian_occupation
    application.guardian_employer = guardian_employer
    application.is_guardian_emergency_contact = is_guardian_emergency
    application.secondary_school = secondary_school
    application.kcse_index_number = kcse_index
    application.kcse_mean_grade = kcse_grade
    application.kcse_year = kcse_year

    # Custom fields in payload — prefetch once to avoid N+1 queries.
    # Cap the number of accepted custom_ keys to prevent query flooding.
    MAX_CUSTOM_FIELDS = 50
    custom_keys = [k for k in p if k.startswith("custom_")][:MAX_CUSTOM_FIELDS]
    if custom_keys:
        active_custom_fields = {
            cf.name: cf
            for cf in ApplicationCustomField.objects.filter(is_active=True)
        }
        for k in custom_keys:
            cf_name = k.replace("custom_", "", 1)
            cf = active_custom_fields.get(cf_name)
            if cf:
                ApplicationCustomFieldValue.objects.update_or_create(
                    application=application,
                    field=cf,
                    defaults={"value": str(p[k]).strip()},
                )

    # Migrate session documents to ApplicationAttachment if any
    if request:
        session_docs = request.session.get("draft_application_documents", {})
        file_mappings = {
            "kcse_document": (ApplicationAttachment.DocType.KCSE_CERTIFICATE, "KCSE Result Slip / Certificate"),
            "id_document": (ApplicationAttachment.DocType.NATIONAL_ID, "National ID / Birth Certificate / Passport"),
            "passport_photo": (ApplicationAttachment.DocType.PASSPORT_PHOTO, "Passport Size Photograph"),
            "other_document": (ApplicationAttachment.DocType.OTHER, "Other Supporting Document"),
        }
        for doc_key, (doc_type, title) in file_mappings.items():
            if doc_key in session_docs:
                d_info = session_docs[doc_key]
                stored_path = d_info.get("file_path")
                if stored_path and default_storage.exists(stored_path):
                    try:
                        existing_att = application.attachments.filter(document_type=doc_type).first()
                        with default_storage.open(stored_path, "rb") as f:
                            content = f.read()

                        if existing_att:
                            existing_att.file.save(d_info.get("original_name", f"{doc_key}.pdf"), ContentFile(content), save=True)
                            existing_att.file_name = d_info.get("original_name", os.path.basename(stored_path))
                            existing_att.file_size = d_info.get("file_size", len(content))
                            existing_att.save()
                        else:
                            att = ApplicationAttachment(
                                application=application,
                                document_type=doc_type,
                                title=title,
                                file_name=d_info.get("original_name", os.path.basename(stored_path)),
                                file_size=d_info.get("file_size", len(content)),
                                mime_type=d_info.get("mime_type", "application/pdf"),
                                is_verified=False,
                            )
                            att.file.save(d_info.get("original_name", f"{doc_key}.pdf"), ContentFile(content), save=True)

                        try:
                            default_storage.delete(stored_path)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error("Error attaching session document %s: %s", doc_key, e)

        # Clear session documents
        request.session.pop("draft_application_documents", None)
        request.session.modified = True

    # Transition to READY_FOR_PAYMENT
    application.status = Application.Status.READY_FOR_PAYMENT
    application.draft_step = 5
    application.draft_version += 1
    application.save()

    log_activity(
        request=request,
        user=application.applicant_user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.ADMISSIONS,
        entity="Application",
        entity_id=str(application.pk),
        description=f"Application {application.application_number} submitted and transitioned to READY_FOR_PAYMENT.",
    )

    from university.admissions_views import application_access_token
    token = application_access_token(application)
    pay_url = f"{reverse('university:admissions_fee_pay', kwargs={'pk': application.pk})}?access={token}"

    return True, {
        "success": True,
        "application_id": application.pk,
        "application_number": application.application_number,
        "status": application.status,
        "redirect_url": pay_url,
        "message": "Application saved successfully! Please proceed to pay the application fee.",
    }
