"""Business logic for the Student Requests module (Deferment, Withdrawal, Sick Leave)."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import StudentProfile
from .audit_services import log_activity
from .models import AcademicTerm, AuditLog, SemesterRegistration, StudentRequest


def submit_request(student, request_type, reason, start_date=None, end_date=None,
                   supporting_document=None, http_request=None):
    if student.status != StudentProfile.Status.ACTIVE:
        raise ValidationError(
            f"You cannot submit a new request while your status is "
            f"'{student.get_status_display()}'. Contact the Academic Registry for assistance.")
    if StudentRequest.objects.filter(student=student, status=StudentRequest.Status.PENDING).exists():
        raise ValidationError(
            "You already have a pending request. Please wait for it to be reviewed before submitting another.")

    req = StudentRequest.objects.create(
        student=student, request_type=request_type, reason=reason,
        start_date=start_date, end_date=end_date, supporting_document=supporting_document,
    )
    log_activity(
        request=http_request, user=student.user, action=AuditLog.Action.CREATE, module=AuditLog.Module.ACADEMICS,
        entity="StudentRequest", entity_id=req.pk,
        description=f"{student.roll_no} submitted a {req.get_request_type_display()} request.",
        new_state={"request_type": req.request_type, "status": req.status},
    )
    return req


def mark_under_review(reviewer, req, http_request=None):
    if req.status != StudentRequest.Status.PENDING:
        raise ValidationError("Only pending requests can be moved to review.")
    req.status = StudentRequest.Status.UNDER_REVIEW
    req.reviewed_by = reviewer
    req.save(update_fields=["status", "reviewed_by"])
    log_activity(
        request=http_request, user=reviewer, action=AuditLog.Action.UPDATE, module=AuditLog.Module.ACADEMICS,
        entity="StudentRequest", entity_id=req.pk,
        description=f"{req.student.roll_no}'s {req.get_request_type_display()} request moved to Under Review.",
    )
    return req


def decide_request(reviewer, req, decision, comments="", http_request=None):
    if req.status not in (StudentRequest.Status.PENDING, StudentRequest.Status.UNDER_REVIEW):
        raise ValidationError("This request has already been decided.")
    if decision not in (StudentRequest.Status.APPROVED, StudentRequest.Status.REJECTED):
        raise ValidationError("Invalid decision.")

    previous_status = req.student.status
    req.status = decision
    req.review_comments = comments
    req.reviewed_by = reviewer
    req.decided_at = timezone.now()
    req.save()

    if decision == StudentRequest.Status.APPROVED:
        student = req.student
        status_map = {
            StudentRequest.Type.DEFERMENT: StudentProfile.Status.DEFERRED,
            StudentRequest.Type.WITHDRAWAL: StudentProfile.Status.WITHDRAWN,
            StudentRequest.Type.SICK_LEAVE: StudentProfile.Status.ON_LEAVE,
        }
        student.status = status_map[req.request_type]
        student.save(update_fields=["status"])

    log_activity(
        request=http_request, user=reviewer, action=AuditLog.Action.UPDATE, module=AuditLog.Module.ACADEMICS,
        entity="StudentRequest", entity_id=req.pk,
        description=f"{req.student.roll_no}'s {req.get_request_type_display()} request was {req.get_status_display().lower()}.",
        previous_state={"student_status": previous_status}, new_state={"student_status": req.student.status},
    )
    return req


def resume_studies(student, http_request=None):
    """Self-service / admin-triggered reactivation after Deferment or Sick Leave.
    Preserves all historical academic records — only the status flips. Ensures the
    student has a SemesterRegistration for whichever academic term is currently
    active, per Admin Academic Year/Semester Setup (the term may have changed
    while the student was away)."""
    if student.status not in (StudentProfile.Status.DEFERRED, StudentProfile.Status.ON_LEAVE):
        raise ValidationError("Only deferred or on-leave students can resume studies.")
    previous_status = student.status
    student.status = StudentProfile.Status.ACTIVE
    student.save(update_fields=["status"])

    active_term = AcademicTerm.objects.filter(is_current=True).first() or \
        AcademicTerm.objects.order_by("-start_date").first()
    if active_term:
        SemesterRegistration.objects.get_or_create(
            student=student, term=active_term,
            defaults={
                "semester_no": student.current_semester,
                "academic_year": f"{active_term.start_date.year}/{active_term.end_date.year}",
                "status": SemesterRegistration.DRAFT,
            }
        )

    log_activity(
        request=http_request, user=student.user, action=AuditLog.Action.UPDATE, module=AuditLog.Module.ACADEMICS,
        entity="StudentProfile", entity_id=student.pk,
        description=f"{student.roll_no} resumed studies (was {previous_status}); "
                    f"assigned to {active_term.name if active_term else 'no active term'}.",
        previous_state={"status": previous_status}, new_state={"status": student.status},
    )
    return student


def sync_leave_expiry(student):
    """Auto-return a student from sick leave once their approved leave period has ended.
    Deferment/withdrawal never auto-expire — those require an explicit resume/appeal."""
    if student.status != StudentProfile.Status.ON_LEAVE:
        return student
    latest = StudentRequest.objects.filter(
        student=student, request_type=StudentRequest.Type.SICK_LEAVE,
        status=StudentRequest.Status.APPROVED, end_date__isnull=False,
    ).order_by("-decided_at").first()
    if latest and latest.end_date < timezone.localdate():
        student.status = StudentProfile.Status.ACTIVE
        student.save(update_fields=["status"])
        log_activity(
            action=AuditLog.Action.UPDATE, module=AuditLog.Module.ACADEMICS,
            entity="StudentProfile", entity_id=student.pk,
            description=f"{student.roll_no}'s approved sick leave period ended; status auto-reset to Active.",
            previous_state={"status": StudentProfile.Status.ON_LEAVE}, new_state={"status": student.status},
        )
    return student
