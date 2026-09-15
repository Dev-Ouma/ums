"""Examination workflow: all writes lock the exam and check role, scope and state."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Course, Enrollment, Exam, ExamAppeal, ExamAudit, Result, grade_point_for,
    MarksVersion, MarksWorkflowEvent
)


def is_admin(user):
    """Return true only for system administrators, not every ADMIN-labelled staff role."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.is_admin_role:
        return False
    from .models import StaffRoleAssignment
    assignments = StaffRoleAssignment.objects.filter(user=user, is_active=True).select_related('role')
    # A bare ADMIN user is retained as the legacy/system-admin fallback used
    # by local installations and tests. Once a scoped institutional role is
    # assigned, only identity administration is a system-admin role.
    codes = set(assignments.values_list('role__code', flat=True))
    return not codes or 'identity_admin' in codes


def can_create_exams(user):
    """
    Instructors/teachers cannot create exams.
    Exam creation is strictly restricted to HOD, Dean, Academic Registrar, Exam Officer, Admin, Super Admin.
    """
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    from .models import StaffRoleAssignment
    if StaffRoleAssignment.objects.filter(
        user=user,
        is_active=True,
        role__code__in=['hod', 'dean', 'academic_registrar', 'exam_officer', 'vc', 'dvcaa']
    ).exists():
        return True
    try:
        from .permissions_services import has_user_permission
        if has_user_permission(user, 'exams.create_exam'):
            return True
    except Exception:
        pass
    faculty = getattr(user, 'faculty_profile', None)
    if faculty:
        desig = (faculty.designation or '').lower()
        if any(k in desig for k in ['dean', 'hod', 'head of department', 'chair', 'registrar', 'director', 'vice chancellor', 'dvc']):
            return True
    sig = getattr(user, 'signature', None)
    if sig:
        title = (sig.title or '').lower()
        if any(k in title for k in ['dean', 'hod', 'head of department', 'chair', 'registrar', 'director', 'vice chancellor', 'dvc']):
            return True
    return False


def staff_scope(user):
    qs = Exam.objects.select_related(
        'course__faculty__user', 'course__department__school', 'term', 'room', 'invigilator__user',
        'internal_examiner__user', 'external_examiner__user', 'original_exam'
    )
    if is_admin(user):
        return qs
    if not (user.is_authenticated and user.is_faculty):
        return qs.none()

    from .models import StaffRoleAssignment
    roles = set(StaffRoleAssignment.objects.filter(user=user, is_active=True).values_list('role__code', flat=True))
    if any(r in roles for r in ['academic_registrar', 'exam_officer', 'vc', 'dvcaa']):
        return qs

    if 'dean' in roles:
        dean_assignments = StaffRoleAssignment.objects.filter(user=user, is_active=True, role__code='dean')
        school_ids = list(dean_assignments.filter(school__isnull=False).values_list('school_id', flat=True))
        return qs.filter(course__department__school_id__in=school_ids).distinct() if school_ids else qs.none()

    if 'hod' in roles:
        hod_depts = list(StaffRoleAssignment.objects.filter(user=user, is_active=True, role__code='hod', department__isnull=False).values_list('department_id', flat=True))
        faculty = getattr(user, 'faculty_profile', None)
        if faculty and faculty.department_id:
            hod_depts.append(faculty.department_id)
        if hod_depts:
            return qs.filter(
                Q(course__department_id__in=hod_depts) |
                Q(course__faculty__user=user) |
                Q(invigilator__user=user) |
                Q(internal_examiner__user=user) |
                Q(external_examiner__user=user)
            ).distinct()

    faculty = getattr(user, 'faculty_profile', None)
    if faculty:
        desig = (faculty.designation or '').lower()
        if any(k in desig for k in ['dean', 'director', 'registrar']):
            return qs
        if any(k in desig for k in ['hod', 'head of department']) and faculty.department_id:
            return qs.filter(
                Q(course__department_id=faculty.department_id) |
                Q(course__faculty__user=user) |
                Q(invigilator__user=user) |
                Q(internal_examiner__user=user) |
                Q(external_examiner__user=user)
            ).distinct()

    return qs.filter(
        Q(course__faculty__user=user) |
        Q(invigilator__user=user) |
        Q(internal_examiner__user=user) |
        Q(external_examiner__user=user)
    ).distinct()


def can_mark(user, exam):
    if is_admin(user):
        return True
    if not (user.is_authenticated and user.is_faculty):
        return False
    is_course_lecturer = bool(exam.course.faculty_id and exam.course.faculty.user_id == user.pk)
    is_internal_examiner = bool(exam.internal_examiner_id and exam.internal_examiner.user_id == user.pk)
    return is_course_lecturer or is_internal_examiner


def can_review_external(user, exam):
    if is_admin(user):
        return True
    if not (user.is_authenticated and user.is_faculty):
        return False
    return bool(exam.external_examiner_id and exam.external_examiner.user_id == user.pk)


def can_record_attendance(user, exam):
    """Attendance belongs to the invigilator and central examinations office."""
    if is_admin(user):
        return True
    if not user or not user.is_authenticated:
        return False
    if exam.invigilator_id and exam.invigilator.user_id == user.pk:
        return True
    from .permissions_services import has_user_permission
    return has_user_permission(user, 'exams.create_exam')


@transaction.atomic
def save_attendance(user, exam_id, entries):
    exam = Exam.objects.select_for_update().select_related('invigilator__user', 'course').get(pk=exam_id)
    if not can_record_attendance(user, exam):
        raise PermissionDenied('Only the assigned invigilator or examinations office may record attendance.')
    if exam.status not in (Exam.Status.SCHEDULED, Exam.Status.MARKING):
        raise ValidationError('Attendance is locked after the marksheet is submitted for review.')
    rows = {str(row.pk): row for row in exam.results.select_for_update().select_related('student')}
    changed = []
    for result_id, value in entries.items():
        row = rows.get(str(result_id))
        if not row:
            raise ValidationError('An attendance row does not belong to this examination.')
        attendance = str(value).strip().upper()
        if attendance not in ('PENDING', 'PRESENT', 'ABSENT'):
            raise ValidationError(f'{row.student.roll_no}: invalid attendance status.')
        if attendance == 'ABSENT' and any(v is not None for v in (row.cat_marks, row.exam_marks, row.marks_obtained)):
            raise ValidationError(f'{row.student.roll_no}: remove entered marks before recording the candidate absent.')
        if row.attendance != attendance:
            changed.append(f'{row.student.roll_no}: {row.attendance} → {attendance}')
            row.attendance = attendance
            row.save(update_fields=['attendance'])
    if changed:
        audit(exam, user, 'Attendance updated', '; '.join(changed))
    return len(changed)


def require_editor(user, exam):
    if not can_mark(user, exam):
        raise PermissionDenied


def get_user_exam_role(user, exam):
    """Determine effective role of a user for a specific exam."""
    if not user or not user.is_authenticated:
        return 'anonymous'
    if is_admin(user):
        return 'admin'
    from .models import StaffRoleAssignment
    roles = set(StaffRoleAssignment.objects.filter(user=user, is_active=True).values_list('role__code', flat=True))
    if any(r in roles for r in ['dean', 'academic_registrar', 'exam_officer', 'vc', 'dvcaa']):
        return 'dean'
    faculty = getattr(user, 'faculty_profile', None)
    if faculty:
        desig = (faculty.designation or '').lower()
        if any(k in desig for k in ['dean', 'director', 'registrar', 'vice chancellor', 'dvc']):
            return 'dean'
    dept_id = exam.course.department_id if (exam.course_id and hasattr(exam.course, 'department_id')) else None
    if 'hod' in roles:
        hod_depts = list(StaffRoleAssignment.objects.filter(user=user, is_active=True, role__code='hod', department__isnull=False).values_list('department_id', flat=True))
        if dept_id and dept_id in hod_depts:
            return 'hod'
    if faculty and dept_id and faculty.department_id == dept_id:
        desig = (faculty.designation or '').lower()
        if any(k in desig for k in ['hod', 'head of department', 'chair']):
            return 'hod'
    if can_mark(user, exam):
        return 'instructor'
    if can_review_external(user, exam):
        return 'external_examiner'
    return 'faculty' if getattr(user, 'is_faculty', False) else 'staff'


def can_hod_approve(user, exam):
    """Check whether user can approve or return marks at HoD level (enforces separation of duties)."""
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    if not getattr(user, 'is_faculty', False):
        return False

    dept_id = exam.course.department_id if (exam.course_id and hasattr(exam.course, 'department_id')) else None
    from .models import StaffRoleAssignment
    is_dept_hod = False
    if StaffRoleAssignment.objects.filter(user=user, is_active=True, role__code='hod', department_id=dept_id).exists():
        is_dept_hod = True
    else:
        faculty = getattr(user, 'faculty_profile', None)
        if faculty and dept_id and faculty.department_id == dept_id:
            desig = (faculty.designation or '').lower()
            if any(k in desig for k in ['hod', 'head of department', 'chair']):
                is_dept_hod = True

    if not is_dept_hod:
        return False

    # Separation of duties: HoD cannot approve their own submission unless admin
    is_submitter = bool(exam.submitted_by_id and exam.submitted_by_id == user.pk)
    is_course_lecturer = bool(exam.course.faculty_id and exam.course.faculty.user_id == user.pk)
    if (is_submitter or is_course_lecturer) and not is_admin(user):
        return False

    return True


def can_dean_publish(user, exam):
    """Check whether user can publish marks (Dean, Registrar, Exam Officer, VC, DVCAA, Admin).

    A Dean's authority is always scoped to their own School/Faculty — never a
    blanket bypass. An unscoped 'dean' role assignment (no school on record)
    is treated as "not yet configured", not "publish everywhere": it is
    denied rather than defaulted to full access, matching how dean_dashboard
    scoping works elsewhere in the app.
    """
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    from .models import StaffRoleAssignment
    exam_school_id = getattr(getattr(exam.course, 'department', None), 'school_id', None)
    assignments = StaffRoleAssignment.objects.filter(user=user, is_active=True)
    if assignments.filter(role__code__in=['academic_registrar', 'exam_officer', 'vc', 'dvcaa']).exists():
        return True
    dean_assignments = assignments.filter(role__code='dean')
    if dean_assignments.exists():
        scoped_schools = set(dean_assignments.filter(school__isnull=False).values_list('school_id', flat=True))
        return bool(exam_school_id) and exam_school_id in scoped_schools
    try:
        from .permissions_services import has_user_permission
        if has_user_permission(user, 'exams.publish_results'):
            return True
    except Exception:
        pass
    faculty = getattr(user, 'faculty_profile', None)
    if faculty:
        desig = (faculty.designation or '').lower()
        if any(k in desig for k in ['registrar', 'vice chancellor', 'dvc']):
            return True
        if any(k in desig for k in ['dean', 'director']):
            faculty_school_id = getattr(faculty.department, 'school_id', None) if faculty.department_id else None
            return bool(exam_school_id) and faculty_school_id == exam_school_id
    return False


def can_unpublish(user, exam):
    """Unpublishing requires Dean, Registrar, or Admin authorization."""
    return can_dean_publish(user, exam)


def snapshot_marks(exam, user=None, notes=''):
    """Create a persistent MarksVersion record for audit, tracking, and rollback."""
    results = exam.results.select_related('student__user').order_by('seat_number', 'student__roll_no')
    snapshot = [
        {
            'student_id': r.student_id,
            'roll_no': getattr(r.student, 'roll_no', '') or str(r.student_id),
            'name': r.student.user.display_name if (r.student and r.student.user) else '',
            'cat': float(r.cat_marks) if r.cat_marks is not None else None,
            'ie': float(r.exam_marks_ie) if r.exam_marks_ie is not None else None,
            'ee': float(r.exam_marks_ee) if r.exam_marks_ee is not None else None,
            'exam_marks': float(r.exam_marks) if r.exam_marks is not None else None,
            'total': float(r.marks_obtained) if r.marks_obtained is not None else None,
            'grade': r.grade,
            'outcome': r.outcome,
            'attendance': r.attendance,
            'remarks': r.remarks or '',
        }
        for r in results
    ]
    version, _ = MarksVersion.objects.update_or_create(
        exam=exam,
        version_number=exam.current_version,
        defaults={
            'status': exam.status,
            'snapshot': snapshot,
            'created_by': user if (user and user.is_authenticated) else None,
            'notes': notes,
        }
    )
    return version


def record_workflow_event(exam, action, from_status, to_status, user=None, role='', reason='',
                          reason_category='', comments='', marks_version=None,
                          ip_address='', user_agent='', submission_reference=''):
    """Create a MarksWorkflowEvent and sync with ExamAudit and central AuditLog."""
    from .models import AuditLog
    from .audit_services import log_activity

    event = MarksWorkflowEvent.objects.create(
        exam=exam,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor=user if (user and user.is_authenticated) else None,
        actor_role=role or (get_user_exam_role(user, exam) if user else ''),
        reason=reason[:250],
        reason_category=reason_category[:100],
        comments=comments,
        marks_version=marks_version or exam.current_version,
        submission_reference=submission_reference[:60],
        ip_address=ip_address[:50],
        user_agent=user_agent,
    )

    detail_parts = [f"{from_status} → {to_status}"]
    if reason:
        detail_parts.append(f"Reason: {reason}")
    if reason_category:
        detail_parts.append(f"Category: {reason_category}")
    if comments:
        detail_parts.append(f"Comments: {comments}")
    audit(exam, user, action.replace('_', ' ').title(), ". ".join(detail_parts))

    try:
        log_activity(
            user=user,
            action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.ACADEMICS,
            entity="Exam",
            entity_id=exam.pk,
            description=f"Marks Workflow [{action}]: {from_status} → {to_status}. {reason}".strip(),
            previous_state={"status": from_status},
            new_state={"status": to_status, "version": exam.current_version},
        )
    except Exception:
        pass

    return event


def audit(exam, user, action, detail=''):
    ExamAudit.objects.create(exam=exam, actor=user, action=action, detail=detail)


def eligible_students(exam):
    if exam.original_exam_id:
        from .models import SupplementaryExamRegistration
        failed_ids = {r.student_id for r in exam.original_exam.results.select_related('exam') if r.outcome in ('Fail', 'Absent')}
        approved_ids = set(SupplementaryExamRegistration.objects.filter(
            course=exam.course, term=exam.term,
            exam_type=SupplementaryExamRegistration.ExamType.SUPPLEMENTARY,
            status=SupplementaryExamRegistration.Status.APPROVED,
        ).values_list('student_id', flat=True))
        return list(failed_ids & approved_ids)
    return list(Enrollment.objects.filter(course=exam.course, term=exam.term, status=Enrollment.ACTIVE).values_list('student_id', flat=True))


def check_schedule(exam):
    # Auto-assign internal examiner if not set
    if not exam.internal_examiner_id and exam.course.faculty_id:
        exam.internal_examiner = exam.course.faculty

    exam.full_clean()
    if not all([exam.term_id, exam.room_id, exam.invigilator_id, exam.start_time, exam.end_time]):
        raise ValidationError('Set the academic term, room, invigilator, start and end times before scheduling.')
    if not exam.room.active:
        raise ValidationError('Choose an active room.')
    students = eligible_students(exam)
    if not students:
        raise ValidationError('No eligible candidates. Check active course enrollments for this term, or failed/absent original results.')
    if len(students) > exam.room.capacity:
        raise ValidationError(f'{len(students)} candidates exceed the room capacity of {exam.room.capacity}.')
    peers = Exam.objects.exclude(pk=exam.pk).exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED])
    overlaps = peers.filter(date=exam.date, start_time__lt=exam.end_time, end_time__gt=exam.start_time)
    if overlaps.filter(room=exam.room).exists():
        raise ValidationError('Room clash: another exam uses this room during the selected time.')
    if overlaps.filter(invigilator=exam.invigilator).exists():
        raise ValidationError('Invigilator clash: this faculty member is already assigned at that time.')
    if overlaps.filter(results__student_id__in=students).exists():
        raise ValidationError('Student clash: a candidate has another exam during the selected time.')
    if exam.original_exam_id:
        if peers.filter(original_exam_id=exam.original_exam_id).exists():
            raise ValidationError('A supplementary sitting already exists for this assessment.')
    elif peers.filter(course=exam.course, term=exam.term, kind=exam.kind, original_exam__isnull=True).exists():
        raise ValidationError('This course and term already has a scheduled CAT or final of the same type. Edit or cancel it first.')
    return students


def check_complete(exam):
    rows = list(exam.results.all())
    if not rows or any(r.attendance == 'PENDING' or (r.attendance == 'PRESENT' and r.marks_obtained is None) for r in rows):
        raise ValidationError('Record attendance for every candidate and marks for every present candidate before submission.')
    for row in rows:
        row.full_clean()


EDITABLE_STATUSES = {
    Exam.Status.MARKING,
    Exam.Status.RETURNED_TO_INSTRUCTOR,
    Exam.Status.CORRECTION,
    Exam.Status.UNPUBLISHED,
}


@transaction.atomic
def transition(user, exam_id, action, reason='', revision=None,
               reason_category='', comments='', target_stage='',
               ip_address='', user_agent=''):
    exam = Exam.objects.select_for_update().select_related(
        'course__faculty__user', 'internal_examiner__user', 'external_examiner__user',
        'room', 'term', 'original_exam'
    ).get(pk=exam_id)

    before = exam.status
    role = get_user_exam_role(user, exam)

    # Permission checks per action
    if action in ('review_external',) and not can_review_external(user, exam):
        raise PermissionDenied("Only the designated External Examiner can sign off on external review.")
    elif action in ('hod_approve', 'hod_send_back'):
        if not can_hod_approve(user, exam):
            raise PermissionDenied("You do not have permission to approve or return marks as HoD for this department, or you are restricted by separation of duties.")
    elif action in ('reopen_approved',):
        if not (can_hod_approve(user, exam) or can_dean_publish(user, exam)):
            raise PermissionDenied("Only an HoD or Dean can reopen approved marks.")
    elif action in ('publish', 'dean_send_back_hod', 'dean_send_back_instructor'):
        if not can_dean_publish(user, exam):
            raise PermissionDenied("Only a Dean, Academic Registrar, or Admin can perform this action.")
    elif action in ('unpublish',):
        if not can_unpublish(user, exam):
            raise PermissionDenied("Only a Dean, Academic Registrar, or Admin can unpublish marks.")
    elif action in ('return', 'reopen'):
        if not (can_hod_approve(user, exam) or can_dean_publish(user, exam)):
            raise PermissionDenied("Only an HoD, Dean, Academic Registrar, or Admin can return or reopen results.")
    elif action in ('approve', 'cancel', 'reschedule'):
        if not is_admin(user):
            raise PermissionDenied
    elif action in ('schedule', 'start', 'submit', 'resubmit', 'submit_internal', 'review_internal'):
        require_editor(user, exam)

    if revision is not None and str(revision).strip() and str(exam.revision) != str(revision):
        raise ValidationError('This exam changed in another window. Reload before continuing.')

    if action == 'schedule' and before == Exam.Status.DRAFT:
        Course.objects.select_for_update().get(pk=exam.course_id)
        students = check_schedule(exam)
        Result.objects.bulk_create([Result(exam=exam, student_id=pk, seat_number=i) for i, pk in enumerate(sorted(students), 1)])
        exam.status = Exam.Status.SCHEDULED
        audit(exam, user, 'Schedule', f'{before} → {exam.status}. {reason.strip()}')

    elif action == 'start' and before == Exam.Status.SCHEDULED:
        if exam.date > timezone.localdate():
            raise ValidationError('Marks entry opens on the examination date.')
        exam.status = Exam.Status.MARKING
        audit(exam, user, 'Start', f'{before} → {exam.status}. {reason.strip()}')

    elif action == 'submit' and before in (Exam.Status.MARKING, Exam.Status.RETURNED_TO_INSTRUCTOR, Exam.Status.CORRECTION, Exam.Status.INTERNAL_REVIEW, Exam.Status.EXTERNAL_REVIEW):
        check_complete(exam)
        exam.submitted_by = user
        exam.submitted_at = timezone.now()
        snapshot_marks(exam, user, notes=f'Submitted version {exam.current_version}')
        exam.status = Exam.Status.SUBMITTED
        record_workflow_event(exam, MarksWorkflowEvent.Action.SUBMIT, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'resubmit' and before in (Exam.Status.RETURNED_TO_INSTRUCTOR, Exam.Status.CORRECTION, Exam.Status.UNPUBLISHED):
        check_complete(exam)
        exam.current_version += 1
        exam.submitted_by = user
        exam.submitted_at = timezone.now()
        snapshot_marks(exam, user, notes=f'Resubmitted version {exam.current_version}')
        exam.status = Exam.Status.SUBMITTED
        record_workflow_event(exam, MarksWorkflowEvent.Action.RESUBMIT, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'hod_approve' and before in (Exam.Status.HOD_REVIEW, Exam.Status.SUBMITTED):
        check_complete(exam)
        exam.hod_approved_by = user
        exam.hod_approved_at = timezone.now()
        snapshot_marks(exam, user, notes=f'Approved by HoD (v{exam.current_version})')
        exam.status = Exam.Status.HOD_APPROVED
        record_workflow_event(exam, MarksWorkflowEvent.Action.HOD_APPROVE, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'hod_send_back' and before in (Exam.Status.HOD_REVIEW, Exam.Status.SUBMITTED):
        if not reason.strip():
            raise ValidationError('A specific reason is required to return marks to the instructor.')
        snapshot_marks(exam, user, notes=f'Returned to instructor by HoD: {reason}')
        exam.status = Exam.Status.RETURNED_TO_INSTRUCTOR
        record_workflow_event(exam, MarksWorkflowEvent.Action.HOD_SEND_BACK, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'reopen_approved' and before in (Exam.Status.HOD_APPROVED, Exam.Status.APPROVED):
        if not reason.strip():
            raise ValidationError('A specific reason is required to reopen approved marks.')
        snapshot_marks(exam, user, notes=f'Approved marks reopened: {reason}')
        exam.hod_approved_by = None
        exam.hod_approved_at = None
        exam.status = Exam.Status.RETURNED_TO_INSTRUCTOR
        record_workflow_event(exam, MarksWorkflowEvent.Action.REOPEN_APPROVED, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'dean_send_back_hod' and before in (Exam.Status.HOD_APPROVED, Exam.Status.DEAN_REVIEW, Exam.Status.APPROVED):
        if not reason.strip():
            raise ValidationError('A reason is required to return marks to the HoD.')
        snapshot_marks(exam, user, notes=f'Returned to HoD by Dean: {reason}')
        exam.status = Exam.Status.RETURNED_TO_HOD
        record_workflow_event(exam, MarksWorkflowEvent.Action.DEAN_SEND_BACK_HOD, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'dean_send_back_instructor' and before in (Exam.Status.HOD_APPROVED, Exam.Status.DEAN_REVIEW, Exam.Status.RETURNED_TO_HOD, Exam.Status.APPROVED):
        if not reason.strip():
            raise ValidationError('A reason is required to return marks to the instructor.')
        snapshot_marks(exam, user, notes=f'Returned to instructor by Dean: {reason}')
        exam.status = Exam.Status.RETURNED_TO_INSTRUCTOR
        record_workflow_event(exam, MarksWorkflowEvent.Action.DEAN_SEND_BACK_INSTRUCTOR, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'publish' and before in (Exam.Status.HOD_APPROVED, Exam.Status.DEAN_REVIEW, Exam.Status.APPROVED):
        check_complete(exam)
        exam.dean_published_by = user
        exam.published_at = timezone.now()
        snapshot_marks(exam, user, notes=f'Official publication of version {exam.current_version}')
        exam.status = Exam.Status.PUBLISHED
        record_workflow_event(exam, MarksWorkflowEvent.Action.PUBLISH, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    elif action == 'unpublish' and before == Exam.Status.PUBLISHED:
        if not reason.strip():
            raise ValidationError('An official justification and reason are required to unpublish marks.')
        snapshot_marks(exam, user, notes=f'Pre-unpublish baseline v{exam.current_version}: {reason}')
        exam.published_at = None
        exam.current_version += 1
        target = target_stage.strip() if target_stage else Exam.Status.UNPUBLISHED
        if target not in (Exam.Status.UNPUBLISHED, Exam.Status.CORRECTION, Exam.Status.RETURNED_TO_INSTRUCTOR):
            target = Exam.Status.UNPUBLISHED
        exam.status = target
        record_workflow_event(exam, MarksWorkflowEvent.Action.UNPUBLISH, before, exam.status,
                              user=user, role=role, reason=reason, reason_category=reason_category,
                              comments=comments, ip_address=ip_address, user_agent=user_agent)

    # Legacy workflow compatibility
    elif action == 'approve' and before in (Exam.Status.SUBMITTED, Exam.Status.HOD_REVIEW):
        check_complete(exam)
        exam.status = Exam.Status.APPROVED
        record_workflow_event(exam, MarksWorkflowEvent.Action.HOD_APPROVE, before, exam.status,
                              user=user, role=role, reason=reason, comments=comments)

    elif action in ('return', 'reopen') and before in ({Exam.Status.INTERNAL_REVIEW, Exam.Status.EXTERNAL_REVIEW, Exam.Status.SUBMITTED, Exam.Status.APPROVED, Exam.Status.HOD_APPROVED, Exam.Status.HOD_REVIEW} if action == 'return' else {Exam.Status.PUBLISHED}):
        if not reason.strip():
            raise ValidationError('A reason is required to return or reopen results.')
        if before == Exam.Status.PUBLISHED:
            snapshot_marks(exam, user, notes=f'Withdrawn from publication: {reason}')
            exam.published_at = None
            exam.current_version += 1
            exam.status = Exam.Status.MARKING
            record_workflow_event(exam, MarksWorkflowEvent.Action.UNPUBLISH, before, exam.status,
                                  user=user, role=role, reason=reason)
        else:
            snapshot_marks(exam, user, notes=f'Returned for correction: {reason}')
            exam.status = Exam.Status.MARKING
            record_workflow_event(exam, MarksWorkflowEvent.Action.HOD_SEND_BACK, before, exam.status,
                                  user=user, role=role, reason=reason)

    elif action == 'cancel' and before in (Exam.Status.DRAFT, Exam.Status.SCHEDULED):
        if not reason.strip():
            raise ValidationError('A cancellation reason is required.')
        exam.status = Exam.Status.CANCELLED
        record_workflow_event(exam, MarksWorkflowEvent.Action.CANCEL, before, exam.status,
                              user=user, role=role, reason=reason)

    elif action == 'reschedule' and before == Exam.Status.SCHEDULED:
        if not reason.strip():
            raise ValidationError('A rescheduling reason is required.')
        exam.results.all().delete()
        exam.status = Exam.Status.DRAFT
        audit(exam, user, 'Rescheduled', f'{before} → {exam.status}. {reason.strip()}')

    elif action == 'submit_internal' and before == Exam.Status.MARKING:
        check_complete(exam)
        exam.status = Exam.Status.INTERNAL_REVIEW
    elif action == 'review_internal' and before == Exam.Status.INTERNAL_REVIEW:
        check_complete(exam)
        exam.internal_reviewed_at = timezone.now()
        exam.internal_reviewed_by = user
        if exam.external_examiner_id or exam.external_examiner_name:
            exam.status = Exam.Status.EXTERNAL_REVIEW
        else:
            exam.status = Exam.Status.SUBMITTED
    elif action == 'review_external' and before == Exam.Status.EXTERNAL_REVIEW:
        check_complete(exam)
        exam.external_reviewed_at = timezone.now()
        exam.external_reviewed_by = user
        exam.status = Exam.Status.SUBMITTED
    else:
        raise ValidationError('That action is not available in the current exam state.')

    exam.revision += 1
    exam.save()
    return exam


class StatementResult(tuple):
    def __new__(cls, results, groups, gpa=0.0):
        obj = super().__new__(cls, (results, groups))
        obj.results = results
        obj.groups = groups
        obj.gpa = gpa
        return obj


def parse_decimal_mark(val, roll_no, field_name='mark'):
    raw = str(val).strip() if val is not None else ''
    if not raw:
        return None
    try:
        mark = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f'{roll_no}: enter a numeric {field_name}.')
    if not mark.is_finite() or mark.as_tuple().exponent < -2:
        raise ValidationError(f'{roll_no}: use finite {field_name} with up to two decimal places.')
    return mark


@transaction.atomic
def save_marks(user, exam_id, entries, revision, examiner_data=None):
    exam = Exam.objects.select_for_update().select_related('course__faculty__user').get(pk=exam_id)
    require_editor(user, exam)
    if exam.status not in EDITABLE_STATUSES:
        raise ValidationError('Marks are locked. The exam must be in marks entry or returned for correction.')
    if str(exam.revision) != str(revision):
        raise ValidationError('Another user changed this exam. Reload to avoid overwriting their work.')
    rows = {str(r.pk): r for r in exam.results.select_related('student', 'exam')}
    if not entries or not set(entries).issubset(rows):
        raise ValidationError('The marks sheet contains invalid candidates.')

    # Update examiner assignments if provided
    if examiner_data and is_admin(user):
        if 'internal_examiner' in examiner_data:
            exam.internal_examiner_id = examiner_data['internal_examiner'] or None
        if 'external_examiner' in examiner_data:
            exam.external_examiner_id = examiner_data['external_examiner'] or None
        if 'external_examiner_name' in examiner_data:
            exam.external_examiner_name = examiner_data['external_examiner_name'].strip()
        if 'examiner_remarks' in examiner_data:
            exam.examiner_remarks = examiner_data['examiner_remarks'].strip()

    changes = []
    for key, data in entries.items():
        row = rows[key]
        attendance = data.get('attendance', '').strip().upper()
        if attendance not in ('PENDING', 'PRESENT', 'ABSENT'):
            raise ValidationError(f'{row.student.roll_no}: choose valid attendance.')

        cat_val = data.get('cat_marks', None)
        exam_val = data.get('exam_marks', None)
        exam_ie_val = data.get('exam_marks_ie', None)
        exam_ee_val = data.get('exam_marks_ee', None)
        total_val = data.get('marks', None)

        cat_mark = parse_decimal_mark(cat_val, row.student.roll_no, 'CAT mark')
        exam_mark = parse_decimal_mark(exam_val, row.student.roll_no, 'exam mark')
        exam_mark_ie = parse_decimal_mark(exam_ie_val, row.student.roll_no, 'IE exam mark')
        exam_mark_ee = parse_decimal_mark(exam_ee_val, row.student.roll_no, 'EE exam mark')
        total_mark = parse_decimal_mark(total_val, row.student.roll_no, 'mark')

        if attendance == 'ABSENT' and (cat_mark is not None or exam_mark is not None or total_mark is not None):
            raise ValidationError(f'{row.student.roll_no}: absent candidates cannot receive marks.')

        # Resolve final exam mark from IE/EE: EE (moderated) takes precedence
        if exam_mark_ee is not None:
            exam_mark = exam_mark_ee
        elif exam_mark_ie is not None:
            exam_mark = exam_mark_ie

        if attendance == 'PRESENT':
            if cat_mark is not None or exam_mark is not None:
                total_mark = round((cat_mark or Decimal(0)) + (exam_mark or Decimal(0)), 2)
            elif total_mark is not None:
                c_calc = round(min(exam.cat_max_marks, total_mark * Decimal('0.3')), 2)
                e_calc = round(total_mark - c_calc, 2)
                cat_mark, exam_mark = c_calc, e_calc

        before = f'{row.attendance}/{row.cat_marks}+{row.exam_marks}={row.marks_obtained}/{row.remarks}'
        row.attendance = attendance
        row.cat_marks = cat_mark if attendance == 'PRESENT' else None
        row.exam_marks = exam_mark if attendance == 'PRESENT' else None
        row.exam_marks_ie = exam_mark_ie if attendance == 'PRESENT' else None
        row.exam_marks_ee = exam_mark_ee if attendance == 'PRESENT' else None
        row.marks_obtained = total_mark if attendance == 'PRESENT' else None
        row.remarks = data.get('remarks', '').strip()
        row.full_clean()
        changes.append(f'{row.student.roll_no}: {before} → {row.attendance}/{row.cat_marks}+{row.exam_marks}={row.marks_obtained}/{row.remarks}')

    for key in entries:
        rows[key].save(update_fields=['attendance', 'cat_marks', 'exam_marks', 'exam_marks_ie', 'exam_marks_ee', 'marks_obtained', 'remarks'])
    exam.revision += 1
    exam.save(update_fields=['revision', 'internal_examiner', 'external_examiner', 'external_examiner_name', 'examiner_remarks'])
    audit(exam, user, 'Marks saved', '\n'.join(changes))


@transaction.atomic
def create_appeal(user, result_id, reason):
    result = Result.objects.select_for_update().select_related('student', 'exam').get(pk=result_id)
    if not user.is_student or result.student.user_id != user.pk:
        raise PermissionDenied
    if result.exam.status != Exam.Status.PUBLISHED:
        raise ValidationError('Only published results may be appealed.')
    if not reason.strip() or len(reason) > 2000:
        raise ValidationError('Provide an appeal reason of 1–2000 characters.')
    if result.appeals.filter(status='OPEN').exists():
        raise ValidationError('You already have an open appeal for this result.')
    appeal = ExamAppeal.objects.create(result=result, reason=reason.strip())
    audit(result.exam, user, 'Appeal submitted', f'Appeal #{appeal.pk}: {reason.strip()}')
    return appeal


@transaction.atomic
def resolve_appeal(user, appeal_id, decision, resolution):
    if not is_admin(user):
        raise PermissionDenied
    appeal = ExamAppeal.objects.select_for_update().select_related('result__exam').get(pk=appeal_id)
    if appeal.status != 'OPEN' or decision not in ('ACCEPTED', 'REJECTED') or not resolution.strip() or len(resolution) > 2000:
        raise ValidationError('Select a decision and give a resolution for an open appeal (up to 2000 characters).')
    if decision == 'ACCEPTED':
        exam = appeal.result.exam
        if exam.status == Exam.Status.PUBLISHED:
            transition(user, exam.pk, 'reopen', f'Appeal #{appeal.pk}: {resolution}')
        elif exam.status != Exam.Status.MARKING:
            raise ValidationError('Return this exam to marks entry before accepting this appeal.')
    appeal.status, appeal.resolution = decision, resolution.strip()
    appeal.resolved_at, appeal.resolved_by = timezone.now(), user
    appeal.save()
    audit(appeal.result.exam, user, 'Appeal resolved', f'#{appeal.pk} {decision}: {resolution}')


def student_statement(student, term=None):
    results = Result.objects.filter(
        student=student, exam__status=Exam.Status.PUBLISHED
    ).select_related('exam__course', 'exam__term', 'exam__original_exam').prefetch_related('appeals')
    if term:
        results = results.filter(exam__term_id=term)
    groups = {}
    # Latest published supplementary sitting replaces its original component.
    supplements = {}
    for row in sorted(results, key=lambda r: (r.exam.date, r.exam_id)):
        if row.exam.original_exam_id:
            supplements[row.exam.original_exam_id] = row
    for row in results:
        if row.exam.original_exam_id:
            continue
        key = (row.exam.course_id, row.exam.term_id)
        group = groups.setdefault(key, {
            'course': row.exam.course, 'term': row.exam.term, 'components': [],
            'total': Decimal(0), 'weight': Decimal(0), 'kinds': set(), 'final': None,
            'grade_exam': None,
        })
        effective = supplements.get(row.exam_id, row)
        contribution = (effective.marks_obtained or Decimal(0)) * row.exam.weight / Decimal(effective.exam.max_marks)
        group['components'].append({'result': row, 'effective': effective, 'contribution': contribution})
        group['total'] += contribution
        group['weight'] += row.exam.weight
        group['kinds'].add(row.exam.kind)
        if row.exam.kind == Exam.Kind.FINAL:
            group['final'] = row.exam
            # A published supplementary sitting governs the awarded grade/GP for
            # this component too (e.g. its capped grade_bands), not just the marks.
            group['grade_exam'] = effective.exam

    complete_groups = []
    for group in groups.values():
        is_complete = (
            (group['weight'] == 100 and {Exam.Kind.CAT, Exam.Kind.FINAL}.issubset(group['kinds']))
            or (group['final'] is not None and group['weight'] == 100 and len(group['components']) == 1)
        )
        is_complete = is_complete and all(
            c['effective'].attendance == 'ABSENT' or
            (c['effective'].attendance == 'PRESENT' and c['effective'].marks_obtained is not None)
            for c in group['components']
        )
        group['complete'] = is_complete
        if is_complete and group['final']:
            grade_exam = group['grade_exam'] or group['final']
            group['grade'] = grade_exam.grade_for(float(group['total']))
            group['grade_point'] = exam_grade_point(grade_exam, group['grade'])
            group['outcome'] = 'Pass' if group['total'] >= grade_exam.pass_mark else 'Fail'
            complete_groups.append(group)
        elif is_complete and group['components']:
            # Fallback to first component's effective exam grade (honours a
            # published supplementary substitution even for a single-component course).
            grade_exam = group['components'][0]['effective'].exam
            group['grade'] = grade_exam.grade_for(float(group['total']))
            group['grade_point'] = exam_grade_point(grade_exam, group['grade'])
            group['outcome'] = 'Pass' if group['total'] >= grade_exam.pass_mark else 'Fail'
            complete_groups.append(group)
        else:
            group['grade'] = 'Incomplete'
            group['grade_point'] = 0.0
            group['outcome'] = 'Awaiting published components'

    # Compute Cumulative GPA across complete course groups
    gpa = weighted_gpa(complete_groups)
    return StatementResult(results, list(groups.values()), gpa)



def exam_grade_point(exam, grade):
    """Use the examination's saved scale, retaining legacy grade-only bands."""
    band = next((b for b in exam.grade_bands if b['grade'] == grade), {})
    return Decimal(str(band.get('gp', grade_point_for(grade))))


def weighted_gpa(groups):
    credits = sum(g['course'].credits for g in groups)
    if not credits:
        return None
    points = sum(Decimal(str(g['grade_point'])) * g['course'].credits for g in groups)
    return (points / credits).quantize(Decimal('0.01'))
