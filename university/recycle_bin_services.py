from datetime import date, datetime
from decimal import Decimal
import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.models import FacultyProfile, Role, StudentProfile
from university.identity_models import UserType
from university.audit_services import detect_device_type, get_client_ip, log_activity
from university.models import (
    AuditLog, ClassSchedule, Course, Department, Event,
    FeeInvoice, FeeStructure, Notice, Program, RecycleBinItem, School,
    AcademicYear, AcademicTerm,
)

User = get_user_model()


def serialize_model_instance(obj):
    """
    Serialize a model instance into a comprehensive JSON-compatible dictionary,
    including field values, foreign key IDs, and linked user info where applicable.
    """
    data = {}
    for field in obj._meta.fields:
        val = getattr(obj, field.name)
        if isinstance(val, (date, datetime)):
            data[field.name] = val.isoformat()
        elif isinstance(val, Decimal):
            data[field.name] = str(val)
        elif hasattr(val, "pk"):
            data[field.name] = val.pk
        else:
            data[field.name] = val

    # Extra capture for profile models that have an associated User account
    if hasattr(obj, "user") and obj.user:
        u = obj.user
        data["_user_snapshot"] = {
            "username": u.username,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "role": getattr(u, "role", "STUDENT"),
            "phone": getattr(u, "phone", ""),
        }

    return data


@transaction.atomic
def move_to_recycle_bin(obj, user=None, request=None, module=None, is_protected=False):
    """
    Soft-delete an entity and archive it in the Recycle Bin with full metadata.
    """
    content_type = obj.__class__.__name__
    object_id = str(obj.pk)
    object_repr = str(obj)

    # Determine module category
    if not module:
        if isinstance(obj, StudentProfile):
            module = RecycleBinItem.Module.STUDENTS
        elif isinstance(obj, FacultyProfile):
            module = RecycleBinItem.Module.FACULTY
        elif isinstance(obj, Course):
            module = RecycleBinItem.Module.COURSES
        elif isinstance(obj, Program):
            module = RecycleBinItem.Module.PROGRAMMES
        elif isinstance(obj, School):
            module = RecycleBinItem.Module.SCHOOLS
        elif isinstance(obj, Department):
            module = RecycleBinItem.Module.DEPARTMENTS
        elif isinstance(obj, ClassSchedule):
            module = RecycleBinItem.Module.TIMETABLE
        elif isinstance(obj, (FeeInvoice, FeeStructure)):
            module = RecycleBinItem.Module.FEES
        elif isinstance(obj, (AcademicYear, AcademicTerm)):
            module = RecycleBinItem.Module.CALENDAR
        elif isinstance(obj, Notice):
            module = RecycleBinItem.Module.NOTICES
        elif isinstance(obj, Event):
            module = RecycleBinItem.Module.EVENTS
        else:
            module = RecycleBinItem.Module.OTHER

    # Client IP & device
    ip = get_client_ip(request) if request else None
    ua = request.META.get("HTTP_USER_AGENT", "") if request else ""
    device = detect_device_type(ua) if ua else "Desktop"
    actor = user or (request.user if request and hasattr(request, "user") and request.user.is_authenticated else None)

    serialized = serialize_model_instance(obj)

    # Create RecycleBinItem
    item = RecycleBinItem.objects.create(
        content_type=content_type,
        object_id=object_id,
        object_repr=object_repr,
        module=module,
        serialized_data=serialized,
        deleted_by=actor,
        deleted_at=timezone.now(),
        ip_address=ip,
        user_agent=ua[:500] if ua else "",
        device_type=device,
        is_protected=is_protected,
    )

    # Audit log entry
    log_activity(
        request=request,
        user=actor,
        action=AuditLog.Action.DELETE,
        module=module,
        entity=content_type,
        entity_id=object_id,
        description=f"Moved to Recycle Bin: {object_repr} ({content_type} #{object_id})",
        previous_state=serialized,
    )

    # Delete actual DB record safely (cascade handling)
    if hasattr(obj, "user") and obj.user and isinstance(obj, (StudentProfile, FacultyProfile)):
        # Deleting User automatically cascades to Profile cleanly
        obj.user.delete()
    else:
        obj.delete()

    return item


def _ensure_identity(user_inst, user_type, actor=None):
    """
    Give a restored account its identity envelope back.

    The account returns as Pending rather than Active: a restore reinstates the
    record, not the right to sign in, so an administrator still has to activate
    it deliberately. (Without an explicit status=, ensure_account() would default
    a brand-new envelope to ACTIVE, which is right for pre-existing/seeded users
    but wrong here -- set_unusable_password() alone is a real but silent safety
    net; PENDING makes the "not yet reinstated" state visible and explicit.)
    """
    from university.identity_models import AccountStatus
    from university.identity_services import ensure_account
    return ensure_account(user_inst, user_type=user_type, status=AccountStatus.PENDING, created_by=actor)


@transaction.atomic
def restore_from_recycle_bin(item_id, user=None, request=None):
    """
    Restore an item from the Recycle Bin back to the live database,
    reconnecting foreign key relationships where possible.
    """
    item = RecycleBinItem.objects.select_for_update().get(pk=item_id)
    if item.is_restored:
        return None, "Item is already restored."

    data = item.serialized_data
    content_type = item.content_type
    actor = user or (request.user if request and hasattr(request, "user") and request.user.is_authenticated else None)

    restored_obj = None

    if content_type == "StudentProfile":
        user_snap = data.get("_user_snapshot", {})
        username = user_snap.get("username", f"stu.{item.object_id}")
        user_inst, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": user_snap.get("email", ""),
                "first_name": user_snap.get("first_name", ""),
                "last_name": user_snap.get("last_name", ""),
                "role": Role.STUDENT,
            }
        )
        if not user_inst.has_usable_password():
            # A restored identity never comes back with a known password. It
            # returns unable to authenticate until someone sends an activation
            # link from User Management, so a restore cannot hand out access.
            user_inst.set_unusable_password()
            user_inst.save(update_fields=["password"])
        _ensure_identity(user_inst, UserType.STUDENT, actor)

        prog = Program.objects.filter(pk=data.get("program")).first()
        restored_obj, _ = StudentProfile.objects.update_or_create(
            roll_no=data.get("roll_no"),
            defaults={
                "user": user_inst,
                "program": prog,
                "current_semester": data.get("current_semester", 1),
                "address": data.get("address", ""),
                "guardian_name": data.get("guardian_name", "Guardian"),
            }
        )

    elif content_type == "FacultyProfile":
        user_snap = data.get("_user_snapshot", {})
        username = user_snap.get("username", f"fac.{item.object_id}")
        user_inst, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": user_snap.get("email", ""),
                "first_name": user_snap.get("first_name", ""),
                "last_name": user_snap.get("last_name", ""),
                "role": Role.FACULTY,
            }
        )
        if not user_inst.has_usable_password():
            user_inst.set_unusable_password()
            user_inst.save(update_fields=["password"])
        _ensure_identity(user_inst, UserType.STAFF, actor)

        dept = Department.objects.filter(pk=data.get("department")).first()
        restored_obj, _ = FacultyProfile.objects.update_or_create(
            employee_id=data.get("employee_id"),
            defaults={
                "user": user_inst,
                "department": dept,
                "designation": data.get("designation", "Lecturer"),
            }
        )

    elif content_type == "Course":
        dept = Department.objects.filter(pk=data.get("department")).first()
        prog = Program.objects.filter(pk=data.get("program")).first()
        fac = FacultyProfile.objects.filter(pk=data.get("faculty")).first()
        restored_obj, _ = Course.objects.update_or_create(
            code=data.get("code"),
            defaults={
                "title": data.get("title", ""),
                "department": dept or Department.objects.first(),
                "program": prog,
                "faculty": fac,
                "credits": data.get("credits", 4),
                "description": data.get("description", ""),
                "status": data.get("status", "Active"),
            }
        )

    elif content_type == "School":
        restored_obj, _ = School.objects.update_or_create(
            code=data.get("code"),
            defaults={
                "name": data.get("name"),
                "description": data.get("description", ""),
                "dean_name": data.get("dean_name", ""),
            }
        )

    elif content_type == "Department":
        school = School.objects.filter(pk=data.get("school")).first()
        restored_obj, _ = Department.objects.update_or_create(
            code=data.get("code"),
            defaults={
                "name": data.get("name"),
                "school": school,
                "description": data.get("description", ""),
            }
        )

    elif content_type == "Program":
        dept = Department.objects.filter(pk=data.get("department")).first()
        # Any cascade-deleted child recycle-binned alongside this Program
        # (ExamSchedule/Application/FeeStructure) still references it by its
        # *original* pk, so preserve that pk on restore when it's free rather
        # than letting update_or_create's natural-key match hand it a new one
        # — otherwise those children's FK columns would dangle once restored.
        original_pk = data.get("id")
        defaults = {
            "name": data.get("name"),
            "department": dept or Department.objects.first(),
            "program_type": data.get("program_type", Program.ProgramType.DEGREE),
            "level": data.get("level", "UG"),
            "award_title": data.get("award_title", ""),
            "study_mode": data.get("study_mode", Program.StudyMode.FULL_TIME),
            "duration_value": data.get("duration_value", data.get("duration_years", 4)),
            "duration_unit": data.get("duration_unit", Program.DurationUnit.YEARS),
            "duration_years": data.get("duration_years", 4),
            "min_credits": data.get("min_credits", 120),
            "max_credits": data.get("max_credits"),
            "total_seats": data.get("total_seats", 120),
            "status": data.get("status", Program.Status.ACTIVE),
            "description": data.get("description", ""),
            "career_prospects": data.get("career_prospects", ""),
        }
        if original_pk is not None and not Program.objects.filter(pk=original_pk).exists():
            defaults["code"] = data.get("code")
            restored_obj = Program.objects.create(pk=original_pk, **defaults)
        else:
            restored_obj, _ = Program.objects.update_or_create(
                code=data.get("code"), defaults=defaults)

    elif content_type == "ClassSchedule":
        course = Course.objects.filter(pk=data.get("course")).first()
        if course:
            from university.models import ExamRoom, AcademicTerm
            room = ExamRoom.objects.filter(pk=data.get("room")).first()
            term = AcademicTerm.objects.filter(pk=data.get("term")).first()
            restored_obj = ClassSchedule.objects.create(
                course=course,
                room=room,
                term=term,
                day=data.get("day", "MON"),
                start_time=data.get("start_time"),
                end_time=data.get("end_time"),
                session_type=data.get("session_type", "LECTURE"),
            )

    elif content_type == "SystemMessage":
        from .control_services import require_permission
        require_permission(actor, 'messages.edit')
        restored_obj = Notice.objects.get(pk=item.object_id, status='DELETED')
        restored_obj.status = 'DRAFT'
        restored_obj.updated_by = actor
        restored_obj.save(update_fields=['status', 'updated_by', 'updated_at'])

    # Notice and Event previously had hand-written restore branches here that
    # only repopulated 3-4 of Notice's ~20 fields / Event's date+location
    # (dropping banner styling, scheduling window, pinning, etc.) — a
    # misleadingly incomplete "restore" versus the full snapshot
    # serialize_model_instance() actually captured. Both now fall through to
    # the generic fallback below, which reconstructs every concrete field
    # (M2M relations like Notice.target_roles/departments/recipients are the
    # one thing that fallback can't restore, the same limitation every other
    # unmapped content_type already has).

    elif content_type == "FeeStructure":
        prog = Program.objects.filter(pk=data.get("program")).first()
        if prog:
            restored_obj, _ = FeeStructure.objects.update_or_create(
                program=prog,
                year_of_study=data.get("year_of_study", 1),
                semester=data.get("semester", 1),
                defaults={
                    "tuition_fee": Decimal(data.get("tuition_fee", "45000.00")),
                    "registration_fee": Decimal(data.get("registration_fee", "1500.00")),
                    "examination_fee": Decimal(data.get("examination_fee", "3000.00")),
                    "library_fee": Decimal(data.get("library_fee", "1000.00")),
                    "activity_fee": Decimal(data.get("activity_fee", "1000.00")),
                    "medical_fee": Decimal(data.get("medical_fee", "1500.00")),
                    "ict_fee": Decimal(data.get("ict_fee", "2000.00")),
                    "student_union_fee": Decimal(data.get("student_union_fee", "500.00")),
                }
            )

    elif content_type == "AcademicYear":
        restored_obj, _ = AcademicYear.objects.update_or_create(
            name=data.get("name"),
            defaults={
                "code": data.get("code", ""),
                "start_date": data.get("start_date"),
                "end_date": data.get("end_date"),
                "status": data.get("status", AcademicYear.Status.DRAFT),
                "is_current": data.get("is_current", False),
                "description": data.get("description", ""),
                "reference_no": data.get("reference_no", ""),
                "max_programmes_allowed": data.get("max_programmes_allowed", 1),
            }
        )

    elif content_type in ("AcademicTerm", "Semester"):
        ay = AcademicYear.objects.filter(pk=data.get("academic_year")).first()
        restored_obj, _ = AcademicTerm.objects.update_or_create(
            name=data.get("name"),
            defaults={
                "academic_year": ay,
                "term_type": data.get("term_type", "SEMESTER"),
                "semester_number": data.get("semester_number", 1),
                "start_date": data.get("start_date"),
                "end_date": data.get("end_date"),
                "registration_start_date": data.get("registration_start_date"),
                "registration_end_date": data.get("registration_end_date"),
                "exam_start_date": data.get("exam_start_date"),
                "exam_end_date": data.get("exam_end_date"),
                "status": data.get("status", AcademicYear.Status.PUBLISHED),
                "is_current": data.get("is_current", False),
            }
        )

    if restored_obj is None:
        # Generic fallback for any content_type with no hand-written branch
        # above. serialize_model_instance() already captures every concrete
        # field (FK values as their pk), so any model can be reconstructed
        # from it directly. Before this fallback existed, an unmatched
        # content_type fell straight through the if/elif chain, still got
        # marked is_restored=True below, and still returned "Successfully
        # restored record." with nothing actually recreated — a false-success
        # report on exactly the class of bug already fixed elsewhere this
        # session for other silent failures.
        try:
            model_cls = apps.get_model("university", content_type)
        except LookupError:
            model_cls = None
        if model_cls is None:
            return None, (
                f"Cannot restore: no restore handler is registered for "
                f"'{content_type}'. The original data is preserved in the "
                f"Recycle Bin, but no record was recreated."
            )
        kwargs = {}
        pk_field = model_cls._meta.pk
        for field in model_cls._meta.fields:
            if field.primary_key or field.name not in data:
                continue
            kwargs[field.attname] = data[field.name]
        # Any other cascade-deleted sibling restored earlier may already
        # reference this object by its *original* pk (serialize_model_instance
        # captures FK values as the referenced row's pk) — so restoring with a
        # freshly auto-assigned pk would leave those FK columns dangling.
        # Reuse the original pk when it's free; only auto-assign if it's been
        # taken by something else since deletion.
        original_pk = data.get(pk_field.name)
        if original_pk is not None and not model_cls.objects.filter(pk=original_pk).exists():
            kwargs[pk_field.attname] = original_pk
        restored_obj = model_cls.objects.create(**kwargs)

    item.is_restored = True
    item.restored_at = timezone.now()
    item.restored_by = actor
    item.save(update_fields=["is_restored", "restored_at", "restored_by"])

    # Audit log
    log_activity(
        request=request,
        user=actor,
        action=AuditLog.Action.RESTORE,
        module=item.module,
        entity=item.content_type,
        entity_id=item.object_id,
        description=f"Restored record from Recycle Bin: {item.object_repr} ({item.content_type})",
        new_state=item.serialized_data,
    )

    return restored_obj, "Successfully restored record."


def bulk_restore(item_ids, user=None, request=None):
    """Restore multiple items."""
    restored_count = 0
    fail_count = 0
    errors = []
    for iid in item_ids:
        try:
            obj, msg = restore_from_recycle_bin(iid, user=user, request=request)
            if obj:
                restored_count += 1
            else:
                fail_count += 1
                errors.append(msg)
        except Exception as e:
            fail_count += 1
            errors.append(str(e))
    return restored_count, fail_count, errors


@transaction.atomic
def purge_recycle_item(item_id, user=None, request=None):
    """
    Permanently delete an item from the Recycle Bin.
    Protected items (such as academic marks/final records) require superuser privileges.
    """
    item = RecycleBinItem.objects.get(pk=item_id)
    actor = user or (request.user if request and hasattr(request, "user") and request.user.is_authenticated else None)

    if item.is_protected:
        if not (actor and actor.is_superuser):
            raise PermissionDenied("Only superusers can permanently purge protected academic records.")

    # Audit log permanent purge
    log_activity(
        request=request,
        user=actor,
        action=AuditLog.Action.PERMANENT_DELETE,
        module=item.module,
        entity=item.content_type,
        entity_id=item.object_id,
        description=f"PERMANENT PURGE from Recycle Bin: {item.object_repr} ({item.content_type})",
        previous_state=item.serialized_data,
    )

    item_repr = item.object_repr
    item.delete()
    return True, f"Permanently deleted '{item_repr}'."


def bulk_purge(item_ids, user=None, request=None):
    """Permanently delete multiple items from the Recycle Bin."""
    actor = user or (request.user if request and hasattr(request, "user") and request.user.is_authenticated else None)
    purged_count = 0
    fail_count = 0
    errors = []
    for iid in item_ids:
        try:
            success, msg = purge_recycle_item(iid, user=actor, request=request)
            if success:
                purged_count += 1
            else:
                fail_count += 1
                errors.append(msg)
        except Exception as e:
            fail_count += 1
            errors.append(str(e))
    return purged_count, fail_count, errors
