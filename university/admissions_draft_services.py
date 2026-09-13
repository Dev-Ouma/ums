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


def get_or_create_applicant_draft(request) -> Application:
    """
    Retrieves the single active draft application for the authenticated applicant
    or anonymous session. If none exists, creates an idempotent draft record.
    If an applicant registers/logs in after starting an anonymous session draft,
    automatically claims and merges that draft into their user account.
    """
    active_intake = Intake.objects.filter(is_active=True).first()
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
    total_points = 100

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

    fields = {
        "program": str(application.program_id) if application.program_id else "",
        "first_name": application.first_name or "",
        "last_name": application.last_name or "",
        "email": application.email or "",
        "phone": application.phone or "",
        "date_of_birth": application.date_of_birth.isoformat() if application.date_of_birth else "",
        "gender": application.gender or "MALE",
        "national_id": application.national_id or "",
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
    }


def update_applicant_draft(
    application: Application,
    data: Dict[str, Any],
    step: Optional[int] = None,
    client_version: Optional[int] = None,
    request=None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Updates the draft application with partial form inputs.
    Applies soft validation, increments draft_version, and enforces optimistic locking.
    """
    if not application:
        return False, {"error": "Application draft not found."}

    # Optimistic locking concurrency check
    if client_version is not None:
        try:
            cv = int(client_version)
            if cv < application.draft_version:
                logger.warning(
                    "Concurrency conflict on application %s: client version %d < server version %d",
                    application.application_number,
                    cv,
                    application.draft_version,
                )
                # Return current server state so client can reconcile
                return False, {
                    "conflict": True,
                    "message": "A newer save exists on the server.",
                    "server_version": application.draft_version,
                    "current_state": serialize_draft_state(application, request),
                }
        except (ValueError, TypeError):
            pass

    # Map model fields
    field_handlers = {
        "first_name": lambda v: setattr(application, "first_name", str(v).strip()[:80]),
        "last_name": lambda v: setattr(application, "last_name", str(v).strip()[:80]),
        "email": lambda v: setattr(application, "email", str(v).strip().lower()[:254]),
        "phone": lambda v: setattr(application, "phone", str(v).strip()[:30]),
        "gender": lambda v: setattr(application, "gender", str(v).strip()[:20] if str(v).strip() in ["MALE", "FEMALE", "OTHER"] else "MALE"),
        "national_id": lambda v: setattr(application, "national_id", str(v).strip()[:50]),
        "address": lambda v: setattr(application, "address", str(v).strip()),
        "guardian_name": lambda v: setattr(application, "guardian_name", str(v).strip()[:120]),
        "guardian_relationship": lambda v: setattr(application, "guardian_relationship", str(v).strip()[:60]),
        "guardian_phone": lambda v: setattr(application, "guardian_phone", str(v).strip()[:30]),
        "guardian_alternative_phone": lambda v: setattr(application, "guardian_alternative_phone", str(v).strip()[:30]),
        "guardian_email": lambda v: setattr(application, "guardian_email", str(v).strip().lower()[:254]),
        "guardian_address": lambda v: setattr(application, "guardian_address", str(v).strip()[:255]),
        "guardian_country": lambda v: setattr(application, "guardian_country", str(v).strip()[:80]),
        "guardian_occupation": lambda v: setattr(application, "guardian_occupation", str(v).strip()[:120]),
        "guardian_employer": lambda v: setattr(application, "guardian_employer", str(v).strip()[:150]),
        "secondary_school": lambda v: setattr(application, "secondary_school", str(v).strip()[:160]),
        "kcse_index_number": lambda v: setattr(application, "kcse_index_number", str(v).strip()[:60]),
        "kcse_mean_grade": lambda v: setattr(application, "kcse_mean_grade", str(v).strip()[:10]),
    }

    # Update Program
    if "program" in data:
        prog_val = data["program"]
        if prog_val:
            prog = Program.objects.filter(pk=prog_val, status=Program.Status.ACTIVE).first()
            if prog:
                application.program = prog
        else:
            application.program = None

    # Update Date of Birth (soft validation)
    if "date_of_birth" in data:
        dob_val = data["date_of_birth"]
        if dob_val:
            try:
                application.date_of_birth = date.fromisoformat(str(dob_val).strip())
            except (ValueError, TypeError):
                pass
        else:
            application.date_of_birth = None

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

    # Apply standard string fields
    for field_name, handler in field_handlers.items():
        if field_name in data:
            handler(data[field_name])

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
    }


def validate_and_submit_application(
    application: Application,
    payload: Optional[Dict[str, Any]] = None,
    request=None,
    require_documents: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Performs validation across all application sections.
    Transitions status from DRAFT / IN_PROGRESS to READY_FOR_PAYMENT.
    Migrates any session draft documents to ApplicationAttachment models.
    """
    if not application:
        return False, {"errors": ["Application record not found."]}

    errors = []
    p = dict(payload or {})

    # 1. Programme Selection
    prog_id = p.get("program") or (application.program_id if application.program else None)
    program_obj = None
    if prog_id:
        program_obj = Program.objects.filter(pk=prog_id, status=Program.Status.ACTIVE).first()
    if not program_obj and not application.program:
        errors.append("Please select a valid programme of study.")

    # 2. Personal Information
    first_name = p.get("first_name", application.first_name or "").strip()
    last_name = p.get("last_name", application.last_name or "").strip()
    email = p.get("email", application.email or "").strip().lower()
    phone = p.get("phone", application.phone or "").strip()
    dob_raw = p.get("date_of_birth", application.date_of_birth.isoformat() if application.date_of_birth else "").strip()
    gender = p.get("gender", application.gender or "").strip()
    national_id = p.get("national_id", application.national_id or "").strip()
    address = p.get("address", application.address or "").strip()

    if not (first_name and last_name and email and phone and dob_raw and national_id):
        errors.append("Please fill in all mandatory personal details.")

    parsed_dob = None
    if dob_raw:
        try:
            parsed_dob = date.fromisoformat(dob_raw)
        except (ValueError, TypeError):
            errors.append("Enter a valid date of birth.")

    if email:
        try:
            EmailField().clean(email)
        except ValidationError:
            errors.append("Enter a valid email address.")

    if gender and gender not in ["MALE", "FEMALE", "OTHER"]:
        errors.append("Please select a valid gender.")

    # 3. Guardian Details
    guardian_name = p.get("guardian_name", application.guardian_name or "").strip()
    guardian_relationship = p.get("guardian_relationship", application.guardian_relationship or "Parent").strip()
    guardian_phone = p.get("guardian_phone", application.guardian_phone or "").strip()
    guardian_alt_phone = p.get("guardian_alternative_phone", application.guardian_alternative_phone or "").strip()
    guardian_email = p.get("guardian_email", application.guardian_email or "").strip().lower()
    guardian_address = p.get("guardian_address", application.guardian_address or "").strip()
    guardian_country = p.get("guardian_country", application.guardian_country or "Kenya").strip()
    guardian_occupation = p.get("guardian_occupation", application.guardian_occupation or "").strip()
    guardian_employer = p.get("guardian_employer", application.guardian_employer or "").strip()
    is_guardian_emergency = p.get("is_guardian_emergency_contact", application.is_guardian_emergency_contact) in ("on", "true", "True", "1", 1, True)

    if "guardian_name" in p or "guardian_phone" in p:
        if not guardian_name:
            errors.append("Please provide the full name of your parent, guardian, or sponsor.")
        if not guardian_phone:
            errors.append("Please provide the primary contact phone number for your guardian.")
        elif len(re.sub(r"[^0-9+]", "", guardian_phone)) < 7:
            errors.append("Please enter a valid primary phone number for your guardian (at least 7 digits).")
    else:
        if not guardian_name:
            guardian_name = f"Parent of {first_name}" if first_name else "Parent / Guardian"
        if not guardian_phone:
            guardian_phone = phone

    if guardian_email:
        try:
            EmailField().clean(guardian_email)
        except ValidationError:
            errors.append("Enter a valid email address for your guardian.")

    # 4. Academic Details
    secondary_school = p.get("secondary_school", application.secondary_school or "").strip()
    kcse_index = p.get("kcse_index_number", application.kcse_index_number or "").strip()
    kcse_grade = p.get("kcse_mean_grade", application.kcse_mean_grade or "C+").strip()
    kcse_year_raw = p.get("kcse_year", str(application.kcse_year or 2025)).strip()
    try:
        kcse_year = int(kcse_year_raw)
    except (ValueError, TypeError):
        kcse_year = 2025

    # 5. Required Documents (if strictly enforced)
    if require_documents:
        docs = get_draft_documents_metadata(application, request)
        required_docs = [
            ("kcse_document", "KCSE Result Slip / Certificate"),
            ("id_document", "National ID / Birth Certificate / Passport"),
        ]
        for doc_key, label in required_docs:
            if doc_key not in docs or not docs[doc_key].get("uploaded"):
                errors.append(f"{label} is required before final submission.")

    if errors:
        return False, {"errors": errors}

    # Apply updates to application
    if program_obj:
        application.program = program_obj
    application.first_name = first_name
    application.last_name = last_name
    application.email = email
    application.phone = phone
    application.date_of_birth = parsed_dob
    application.gender = gender or "MALE"
    application.national_id = national_id
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

    # Custom fields in payload
    for k, v in p.items():
        if k.startswith("custom_"):
            cf_name = k.replace("custom_", "", 1)
            cf = ApplicationCustomField.objects.filter(name=cf_name, is_active=True).first()
            if cf:
                ApplicationCustomFieldValue.objects.update_or_create(
                    application=application,
                    field=cf,
                    defaults={"value": str(v).strip()},
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
                            existing_att.file.save(d_info.get("original_name", "doc.pdf"), ContentFile(content), save=True)
                            existing_att.file_name = d_info.get("original_name", os.path.basename(stored_path))
                            existing_att.file_size = d_info.get("file_size", len(content))
                            existing_att.save()
                        else:
                            att = ApplicationAttachment(
                                application=application,
                                document_type=doc_type,
                                name=title,
                                file_name=d_info.get("original_name", os.path.basename(stored_path)),
                                file_size=d_info.get("file_size", len(content)),
                                mime_type=d_info.get("mime_type", "application/pdf"),
                                verification_status=ApplicationAttachment.VerificationStatus.PENDING,
                                is_visible_to_student=True,
                            )
                            att.file.save(d_info.get("original_name", os.path.basename(stored_path)), ContentFile(content), save=True)

                        try:
                            default_storage.delete(stored_path)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error("Error attaching session document %s: %s", doc_key, e)

        # Clear session documents
        request.session.pop("draft_application_documents", None)
        request.session.modified = True

    # Transition status
    application.status = Application.Status.READY_FOR_PAYMENT
    application.save()

    log_activity(
        request=request,
        user=request.user if request and request.user.is_authenticated else None,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.ACADEMICS,
        entity="Application",
        entity_id=application.pk,
        description=f"Application {application.application_number} submitted and ready for payment.",
    )

    from university.admissions_views import application_access_token
    token = application_access_token(application)
    pay_url = f"{reverse('university:pay_application_fee', args=[application.pk])}?access={token}"

    return True, {
        "success": True,
        "application_id": application.pk,
        "application_number": application.application_number,
        "status": application.status,
        "redirect_url": pay_url,
    }
