import datetime
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from university.models import AcademicYear, AcademicTerm, AuditLog, SystemSetting
from university.audit_services import log_activity
from university.settings_services import get_setting, set_setting


def get_current_academic_year():
    """
    Authoritative lookup for the single currently active AcademicYear.
    Falls back to latest PUBLISHED/CURRENT or latest recorded year.
    """
    ay = AcademicYear.objects.filter(is_current=True).first()
    if ay:
        return ay
    ay = AcademicYear.objects.filter(status__in=[AcademicYear.Status.CURRENT, AcademicYear.Status.PUBLISHED]).order_by("-start_date").first()
    if ay:
        return ay
    return AcademicYear.objects.order_by("-start_date").first()


def get_current_semester():
    """
    Authoritative lookup for the currently active semester / term.
    Enforces that the semester belongs to the current academic year.
    """
    # 1. Direct current semester
    sem = AcademicTerm.objects.filter(is_current=True).first()
    if sem:
        return sem

    # 2. Check current academic year's current semester
    current_ay = get_current_academic_year()
    if current_ay:
        sem = current_ay.semesters.filter(is_current=True).first()
        if sem:
            return sem
        sem = current_ay.semesters.filter(status__in=[AcademicYear.Status.CURRENT, AcademicYear.Status.PUBLISHED]).order_by("-start_date").first()
        if sem:
            return sem
        sem = current_ay.semesters.order_by("start_date").first()
        if sem:
            return sem

    # 3. Global fallback
    return AcademicTerm.objects.order_by("-start_date").first()


def get_active_academic_context():
    """
    Authoritative centralized academic calendar state for dashboards, admissions,
    registration, timetable, and examinations.
    """
    current_ay = get_current_academic_year()
    current_sem = get_current_semester()

    ay_name = current_ay.name if current_ay else "2026/2027"
    sem_name = current_sem.name if current_sem else "Semester 1"

    is_reg_open = False
    is_exam_period = False
    reg_window = (None, None)
    exam_window = (None, None)

    if current_sem:
        is_reg_open = current_sem.is_registration_open
        is_exam_period = current_sem.is_exam_period
        reg_window = (current_sem.registration_start_date, current_sem.registration_end_date)
        exam_window = (current_sem.exam_start_date, current_sem.exam_end_date)

    return {
        "academic_year": current_ay,
        "academic_year_name": ay_name,
        "semester": current_sem,
        "semester_name": sem_name,
        "is_current": bool(current_ay and current_ay.is_current and current_sem and current_sem.is_current),
        "status": current_ay.status if current_ay else AcademicYear.Status.DRAFT,
        "semester_status": current_sem.status if current_sem else AcademicYear.Status.DRAFT,
        "is_registration_open": is_reg_open,
        "is_exam_period": is_exam_period,
        "registration_start_date": reg_window[0],
        "registration_end_date": reg_window[1],
        "exam_start_date": exam_window[0],
        "exam_end_date": exam_window[1],
    }


def is_registration_open_for_semester(semester):
    """Check if student registration is currently open for a specific semester."""
    if not semester:
        return False
    return semester.is_registration_open


def is_examination_window_open(semester):
    """Check if the exam window is currently active for a specific semester."""
    if not semester:
        return False
    return semester.is_exam_period


@transaction.atomic
def set_current_academic_year(year_id, user=None, request=None):
    """
    Atomically set an Academic Year as the single Current academic year.
    Clears current flag on all other academic years.
    Syncs legacy SystemSetting key 'academic_year_current'.
    """
    ay = AcademicYear.objects.select_for_update().get(pk=year_id)
    old_cur = AcademicYear.objects.filter(is_current=True).exclude(pk=ay.pk)
    old_names = [y.name for y in old_cur]
    old_cur.update(is_current=False)

    ay.is_current = True
    ay.status = AcademicYear.Status.CURRENT
    ay.save()

    # Synchronize SystemSetting
    try:
        set_setting("academic_year_current", ay.name, user=user, request=request)
    except Exception:
        pass

    # Log to audit trail
    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.SET_CURRENT,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicYear",
        entity_id=ay.id,
        description=f"Set Academic Year '{ay.name}' as CURRENT.",
        previous_state={"previous_current": old_names},
        new_state={"current": ay.name, "status": ay.status},
    )
    return ay


@transaction.atomic
def set_current_semester(semester_id, user=None, request=None):
    """
    Atomically set a Semester as Current.
    Enforces that parent Academic Year is Published or Current.
    Syncs legacy SystemSetting key 'current_semester'.
    """
    sem = AcademicTerm.objects.select_for_update().get(pk=semester_id)

    # Validate parent academic year
    if sem.academic_year and sem.academic_year.status == AcademicYear.Status.DRAFT:
        raise ValidationError(
            f"Cannot make semester '{sem.name}' current because parent academic year "
            f"'{sem.academic_year.name}' is still in DRAFT status. Publish the academic year first."
        )

    # Unset other current semesters
    AcademicTerm.objects.exclude(pk=sem.pk).filter(is_current=True).update(is_current=False)

    sem.is_current = True
    sem.status = AcademicYear.Status.CURRENT
    sem.save()

    # If parent academic year is not current, optionally make it current
    if sem.academic_year and not sem.academic_year.is_current:
        set_current_academic_year(sem.academic_year.pk, user=user, request=request)

    # Synchronize SystemSetting
    try:
        set_setting("current_semester", str(sem.semester_number or 1), user=user, request=request)
    except Exception:
        pass

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.SET_CURRENT,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicTerm",
        entity_id=sem.id,
        description=f"Set Semester '{sem.name}' as CURRENT.",
        new_state={"semester": sem.name, "academic_year": sem.academic_year.name if sem.academic_year else ""},
    )
    return sem


def publish_academic_year(year_id, user=None, request=None):
    """Transition Academic Year from DRAFT to PUBLISHED."""
    ay = AcademicYear.objects.get(pk=year_id)
    if ay.status == AcademicYear.Status.CLOSED:
        raise ValidationError("Cannot publish a closed academic year. Use 'Reopen' instead.")

    ay.status = AcademicYear.Status.PUBLISHED if not ay.is_current else AcademicYear.Status.CURRENT
    ay.published_at = timezone.now()
    ay.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.PUBLISH,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicYear",
        entity_id=ay.id,
        description=f"Published Academic Year '{ay.name}'.",
        new_state={"status": ay.status, "published_at": str(ay.published_at)},
    )
    return ay


def unpublish_academic_year(year_id, user=None, request=None):
    """Revert Academic Year back to DRAFT if no active operational dependency."""
    ay = AcademicYear.objects.get(pk=year_id)
    if ay.is_current:
        raise ValidationError("Cannot unpublish the CURRENT academic year. Set another year as current first.")

    ay.status = AcademicYear.Status.DRAFT
    ay.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.UNPUBLISH,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicYear",
        entity_id=ay.id,
        description=f"Unpublished Academic Year '{ay.name}' back to DRAFT.",
        new_state={"status": ay.status},
    )
    return ay


def close_academic_year(year_id, user=None, request=None):
    """Mark Academic Year as CLOSED."""
    ay = AcademicYear.objects.get(pk=year_id)
    ay.status = AcademicYear.Status.CLOSED
    ay.is_current = False
    ay.closed_at = timezone.now()
    ay.save()

    # Also close child semesters
    ay.semesters.filter(status__in=[AcademicYear.Status.CURRENT, AcademicYear.Status.PUBLISHED]).update(
        status=AcademicYear.Status.CLOSED, is_current=False, closed_at=timezone.now()
    )

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.CLOSE,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicYear",
        entity_id=ay.id,
        description=f"Closed Academic Year '{ay.name}'.",
        new_state={"status": ay.status, "closed_at": str(ay.closed_at)},
    )
    return ay


def reopen_academic_year(year_id, user=None, request=None):
    """Reopen a CLOSED Academic Year into PUBLISHED."""
    ay = AcademicYear.objects.get(pk=year_id)
    if ay.status != AcademicYear.Status.CLOSED:
        return ay

    ay.status = AcademicYear.Status.PUBLISHED
    ay.closed_at = None
    ay.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.REOPEN,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicYear",
        entity_id=ay.id,
        description=f"Reopened Academic Year '{ay.name}' to PUBLISHED.",
        new_state={"status": ay.status},
    )
    return ay


def publish_semester(semester_id, user=None, request=None):
    """Transition Semester to PUBLISHED."""
    sem = AcademicTerm.objects.get(pk=semester_id)
    if sem.academic_year and sem.academic_year.status == AcademicYear.Status.DRAFT:
        raise ValidationError("Cannot publish semester while its parent Academic Year is DRAFT.")

    sem.status = AcademicYear.Status.PUBLISHED if not sem.is_current else AcademicYear.Status.CURRENT
    sem.published_at = timezone.now()
    sem.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.PUBLISH,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicTerm",
        entity_id=sem.id,
        description=f"Published Semester '{sem.name}'.",
        new_state={"status": sem.status},
    )
    return sem


def unpublish_semester(semester_id, user=None, request=None):
    """Revert Semester to DRAFT."""
    sem = AcademicTerm.objects.get(pk=semester_id)
    if sem.is_current:
        raise ValidationError("Cannot unpublish CURRENT semester. Set another semester as current first.")

    sem.status = AcademicYear.Status.DRAFT
    sem.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.UNPUBLISH,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicTerm",
        entity_id=sem.id,
        description=f"Unpublished Semester '{sem.name}' back to DRAFT.",
        new_state={"status": sem.status},
    )
    return sem


def close_semester(semester_id, user=None, request=None):
    """Close a Semester."""
    sem = AcademicTerm.objects.get(pk=semester_id)
    sem.status = AcademicYear.Status.CLOSED
    sem.is_current = False
    sem.closed_at = timezone.now()
    sem.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.CLOSE,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicTerm",
        entity_id=sem.id,
        description=f"Closed Semester '{sem.name}'.",
        new_state={"status": sem.status, "closed_at": str(sem.closed_at)},
    )
    return sem


def reopen_semester(semester_id, user=None, request=None):
    """Reopen a CLOSED Semester."""
    sem = AcademicTerm.objects.get(pk=semester_id)
    if sem.status != AcademicYear.Status.CLOSED:
        return sem

    sem.status = AcademicYear.Status.PUBLISHED
    sem.closed_at = None
    sem.save()

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.REOPEN,
        module=AuditLog.Module.CALENDAR,
        entity="AcademicTerm",
        entity_id=sem.id,
        description=f"Reopened Semester '{sem.name}' to PUBLISHED.",
        new_state={"status": sem.status},
    )
    return sem


def seed_default_academic_calendar():
    """
    Seed standard baseline Academic Years and Semesters, and link existing terms.
    Safe, idempotent operation.
    """
    now = timezone.now().date()
    cur_year = now.year

    # 1. Create or get Academic Year 2026/2027
    ay_2026, _ = AcademicYear.objects.get_or_create(
        name=f"{cur_year}/{cur_year + 1}",
        defaults={
            "code": f"AY-{cur_year}-{cur_year + 1}",
            "start_date": datetime.date(cur_year, 7, 1),
            "end_date": datetime.date(cur_year + 1, 6, 30),
            "status": AcademicYear.Status.CURRENT,
            "is_current": True,
            "description": f"Standard {cur_year}/{cur_year + 1} University Academic Calendar",
            "reference_no": f"GAZ/AY/{cur_year}/01",
        }
    )

    # 2. Previous year 2025/2026
    ay_2025, _ = AcademicYear.objects.get_or_create(
        name=f"{cur_year - 1}/{cur_year}",
        defaults={
            "code": f"AY-{cur_year - 1}-{cur_year}",
            "start_date": datetime.date(cur_year - 1, 7, 1),
            "end_date": datetime.date(cur_year, 6, 30),
            "status": AcademicYear.Status.CLOSED,
            "is_current": False,
            "description": f"Completed {cur_year - 1}/{cur_year} Academic Year",
            "reference_no": f"GAZ/AY/{cur_year - 1}/01",
        }
    )

    # Ensure single current
    if not AcademicYear.objects.filter(is_current=True).exists():
        ay_2026.is_current = True
        ay_2026.status = AcademicYear.Status.CURRENT
        ay_2026.save()

    # 3. Associate and enrich existing AcademicTerms
    for term in AcademicTerm.objects.all():
        if not term.academic_year:
            # Map based on start date
            if term.start_date and term.start_date >= datetime.date(cur_year, 1, 1):
                term.academic_year = ay_2026
            else:
                term.academic_year = ay_2025

        # Initialize windows if missing
        if not term.registration_start_date:
            term.registration_start_date = term.start_date
            term.registration_end_date = term.start_date + datetime.timedelta(days=45)
        if not term.exam_start_date:
            term.exam_start_date = term.end_date - datetime.timedelta(days=21)
            term.exam_end_date = term.end_date

        if "spring" in term.name.lower():
            term.semester_number = 2
        elif "fall" in term.name.lower() or "semester 1" in term.name.lower():
            term.semester_number = 1

        term.save()

    # Ensure single current semester
    cur_term = AcademicTerm.objects.filter(is_current=True).first()
    if not cur_term:
        fall_term = AcademicTerm.objects.filter(academic_year=ay_2026, name__icontains="fall").first()
        if fall_term:
            fall_term.is_current = True
            fall_term.status = AcademicYear.Status.CURRENT
            fall_term.save()
