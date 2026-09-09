from university.document_views import present_pdf
"""Academics views for Student Unit Registration, Provisional & Academic Transcripts,
and Admin Registration & Academic Management.
"""
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from .examination_operations import generate_exam_card_pdf, generate_nominal_roll_pdf
from .examination_services import is_admin
from .financial_services import check_financial_clearance, get_or_create_semester_invoice
from .models import (
    AcademicTerm, Course, Department, Enrollment, Exam, FeeInvoice,
    Program, Result, SemesterRegistration, SupplementaryExamRegistration,
    DocumentReleaseControl, AuditLog
)
from .transcript_views import document as render_transcript_document
from .document_access_services import check_document_access
from .audit_services import get_client_ip, detect_device_type
from .academic_calendar_services import get_current_academic_year, get_current_semester, get_active_academic_context


def _get_student(request):
    """Retrieve StudentProfile for the logged-in student, or raise PermissionDenied."""
    sp = getattr(request.user, "student_profile", None)
    if not sp:
        raise PermissionDenied("Only registered students can access student academic services.")
    return sp


# ==============================================================================
# STUDENT ACADEMICS SUBMODULES
# ==============================================================================

@login_required
def student_register_units(request):
    """Student Unit Registration submodule.
    Allows viewing available units, adding/removing units for the active term,
    enforcing credit limits and deadlines, and submitting registration for approval.
    """
    sp = _get_student(request)

    if not sp.is_active_student:
        messages.error(request, f"Unit registration is unavailable while your status is "
                                f"'{sp.get_status_display()}'. Visit Student Requests for details "
                                f"or to resume your studies.")
        return render(request, "academics/student_register.html", {
            "student": sp, "term": None, "registration": None, "enrolled_units": [], "available_courses": [],
            "blocked": True,
        })

    today = timezone.now().date()

    # Authoritative active academic calendar context
    active_ay = get_current_academic_year()
    active_term = get_current_semester()
    if not active_term:
        messages.warning(request, "No active academic terms have been published. Please contact the Academic Registry.")
        return render(request, "academics/student_register.html", {
            "student": sp, "term": None, "registration": None, "enrolled_units": [], "available_courses": []
        })

    # Check academic period status: must be PUBLISHED or CURRENT
    from .models import AcademicYear
    if active_term.status in [AcademicYear.Status.DRAFT, AcademicYear.Status.CLOSED, AcademicYear.Status.ARCHIVED]:
        messages.warning(request, f"Unit registration is not active: {active_term.name} is currently {active_term.get_status_display().upper()}.")
        return render(request, "academics/student_register.html", {
            "student": sp, "term": active_term, "registration": None, "enrolled_units": [], "available_courses": [],
            "blocked": True,
        })

    # Get or create semester registration
    ay_str = active_ay.name if active_ay else f"{active_term.start_date.year}/{active_term.end_date.year}"
    registration, created = SemesterRegistration.objects.get_or_create(
        student=sp,
        term=active_term,
        defaults={
            "semester_no": sp.current_semester,
            "academic_year": ay_str,
            "status": SemesterRegistration.DRAFT,
        }
    )

    # Check registration dates & editable status
    is_window_open = active_term.is_registration_open
    is_deadline_passed = not is_window_open
    is_editable = registration.status in [SemesterRegistration.DRAFT, SemesterRegistration.REJECTED] and is_window_open

    if request.method == "POST":
        action = request.POST.get("action")

        if not is_editable and action in ["add_unit", "drop_unit", "submit_registration"]:
            messages.error(request, "Registration cannot be modified in its current status or after the deadline.")
            return redirect("university:student_register_units")

        if action == "add_unit":
            course_id = request.POST.get("course_id")
            course = get_object_or_404(Course, pk=course_id)

            # Check duplicate
            if Enrollment.objects.filter(student=sp, course=course).exclude(status=Enrollment.DROPPED).exists():
                messages.warning(request, f"You are already registered or enrolled in {course.code}.")
                return redirect("university:student_register_units")

            # Check credit limit (max 24 credits per semester)
            current_credits = sum(e.course.credits for e in registration.enrollments.exclude(status=Enrollment.DROPPED))
            if current_credits + course.credits > 24:
                messages.error(request, f"Adding {course.code} ({course.credits} units) exceeds the maximum limit of 24 credits per semester.")
                return redirect("university:student_register_units")

            with transaction.atomic():
                enrollment, _ = Enrollment.objects.update_or_create(
                    student=sp,
                    course=course,
                    defaults={
                        "term": active_term,
                        "registration": registration,
                        "status": Enrollment.DRAFT,
                        "enrolled_on": today,
                    }
                )
                registration.recalculate_credits()

            messages.success(request, f"Successfully added {course.code} — {course.title} ({course.credits} credits).")
            return redirect("university:student_register_units")

        elif action == "drop_unit":
            enrollment_id = request.POST.get("enrollment_id")
            enrollment = get_object_or_404(Enrollment, pk=enrollment_id, student=sp, registration=registration)
            course_code = enrollment.course.code

            with transaction.atomic():
                enrollment.status = Enrollment.DROPPED
                enrollment.save(update_fields=["status"])
                registration.recalculate_credits()

            messages.info(request, f"Removed {course_code} from your registration.")
            return redirect("university:student_register_units")

        elif action == "submit_registration":
            enrolled_count = registration.enrollments.exclude(status=Enrollment.DROPPED).count()
            current_credits = sum(e.course.credits for e in registration.enrollments.exclude(status=Enrollment.DROPPED))

            if enrolled_count == 0:
                messages.error(request, "Please register at least one course unit before submitting.")
                return redirect("university:student_register_units")

            if current_credits < 12:
                messages.warning(request, f"Note: You have registered {current_credits} credits, which is below the standard minimum of 12 credits.")

            with transaction.atomic():
                registration.status = SemesterRegistration.SUBMITTED
                registration.submitted_at = timezone.now()
                registration.recalculate_credits(save=False)
                registration.save()
                # Update child enrollments to SUBMITTED
                registration.enrollments.exclude(status=Enrollment.DROPPED).update(status=Enrollment.SUBMITTED)

            messages.success(request, "Your unit registration has been formally submitted for administrative approval.")
            return redirect("university:student_register_units")

    # Registered units
    enrolled_units = registration.enrollments.exclude(status=Enrollment.DROPPED).select_related(
        "course__department", "course__faculty__user"
    )
    total_registered_credits = sum(e.course.credits for e in enrolled_units)

    # Exclude already registered courses
    registered_course_ids = enrolled_units.values_list("course_id", flat=True)

    # Available units for student's program and semester
    available_courses_qs = Course.objects.filter(status=Course.STATUS_ACTIVE).exclude(id__in=registered_course_ids)
    if sp.program_id:
        # Prioritize courses in the student's program or department
        program_courses = available_courses_qs.filter(program=sp.program).select_related("department", "faculty__user")
        other_courses = available_courses_qs.filter(department=sp.program.department).exclude(program=sp.program).select_related("department", "faculty__user")
        available_courses = list(program_courses) + list(other_courses)
    else:
        available_courses = list(available_courses_qs.select_related("department", "faculty__user")[:30])

    return render(request, "academics/student_register.html", {
        "student": sp,
        "term": active_term,
        "registration": registration,
        "enrolled_units": enrolled_units,
        "total_credits": total_registered_credits,
        "available_courses": available_courses,
        "is_editable": is_editable,
        "is_deadline_passed": is_deadline_passed,
    })


@login_required
def student_provisional_transcript_view(request):
    """Student Provisional Transcript submodule."""
    sp = _get_student(request)
    return render_transcript_document(request, student_id=sp.pk, kind="provisional")


@login_required
def student_academic_transcript_view(request):
    """Student Academic Transcript submodule."""
    sp = _get_student(request)
    return render_transcript_document(request, student_id=sp.pk, kind="academic")


# ==============================================================================
# ADMIN ACADEMICS SUBMODULES
# ==============================================================================

@login_required
def admin_unit_registrations(request):
    """Admin Unit Registration Management submodule.
    Monitor, filter, search, and approve student unit registrations by semester/program.
    """
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied("Only administrative staff can manage unit registrations.")

    query = request.GET.get("q", "").strip()
    program_id = request.GET.get("program", "")
    department_id = request.GET.get("department", "")
    term_id = request.GET.get("term", "")
    status_filter = request.GET.get("status", "")

    registrations_qs = SemesterRegistration.objects.select_related(
        "student__user", "student__program__department", "term"
    ).prefetch_related("enrollments__course")

    if query:
        registrations_qs = registrations_qs.filter(
            Q(student__roll_no__icontains=query) |
            Q(student__user__first_name__icontains=query) |
            Q(student__user__last_name__icontains=query) |
            Q(student__user__email__icontains=query)
        )
    if program_id.isdigit():
        registrations_qs = registrations_qs.filter(student__program_id=program_id)
    if department_id.isdigit():
        registrations_qs = registrations_qs.filter(student__program__department_id=department_id)
    if term_id.isdigit():
        registrations_qs = registrations_qs.filter(term_id=term_id)
    if status_filter:
        registrations_qs = registrations_qs.filter(status=status_filter)

    # Batch action
    if request.method == "POST":
        action = request.POST.get("action")
        selected_ids = request.POST.getlist("selected_ids")
        if action == "bulk_approve" and selected_ids:
            with transaction.atomic():
                updated = SemesterRegistration.objects.filter(
                    id__in=selected_ids, status=SemesterRegistration.SUBMITTED
                ).update(
                    status=SemesterRegistration.APPROVED,
                    approved_at=timezone.now(),
                    approved_by=request.user
                )
                # Also activate the enrollments
                Enrollment.objects.filter(
                    registration_id__in=selected_ids, status=Enrollment.SUBMITTED
                ).update(status=Enrollment.ACTIVE)
            messages.success(request, f"Approved {updated} pending unit registration(s).")
            return redirect("university:admin_unit_registrations")

    # Exports
    export_fmt = request.GET.get("export")
    if export_fmt in ["csv", "xlsx"]:
        import csv
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="student_unit_registrations.csv"'
        writer = csv.writer(response)
        writer.writerow(["Reg No", "Student Name", "Program", "Term", "Semester", "Credits", "Status", "Units"])
        for reg in registrations_qs:
            courses_str = ", ".join(e.course.code for e in reg.enrollments.exclude(status=Enrollment.DROPPED))
            writer.writerow([
                reg.student.roll_no,
                reg.student.user.display_name,
                reg.student.program.name if reg.student.program else "",
                reg.term.name,
                f"Sem {reg.semester_no}",
                reg.total_credits,
                reg.get_status_display(),
                courses_str
            ])
        return response

    # Metrics
    total_count = registrations_qs.count()
    pending_count = SemesterRegistration.objects.filter(status=SemesterRegistration.SUBMITTED).count()
    approved_count = SemesterRegistration.objects.filter(status=SemesterRegistration.APPROVED).count()
    active_terms = AcademicTerm.objects.all()

    page = Paginator(registrations_qs, 25).get_page(request.GET.get("page"))

    return render(request, "academics/admin_registrations.html", {
        "page": page,
        "total_count": total_count,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "terms": active_terms,
        "programs": Program.objects.all(),
        "departments": Department.objects.all(),
        "q": query,
        "selected_term": term_id,
        "selected_program": program_id,
        "selected_department": department_id,
        "selected_status": status_filter,
    })


@login_required
def admin_unit_registration_detail(request, pk):
    """Admin view and management of an individual student's unit registration."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied("Only administrative staff can manage unit registrations.")

    registration = get_object_or_404(
        SemesterRegistration.objects.select_related(
            "student__user", "student__program__department", "term", "approved_by"
        ),
        pk=pk
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "approve":
            with transaction.atomic():
                registration.status = SemesterRegistration.APPROVED
                registration.approved_at = timezone.now()
                registration.approved_by = request.user
                registration.admin_remarks = request.POST.get("admin_remarks", "").strip()
                registration.save()
                # Set child enrollments to ACTIVE
                registration.enrollments.exclude(status=Enrollment.DROPPED).update(status=Enrollment.ACTIVE)
                # Automatically generate semester invoice from fee structure
                get_or_create_semester_invoice(registration.student, registration.term)
            messages.success(request, f"Registration for {registration.student.roll_no} has been APPROVED and semester fee invoice generated.")
            return redirect("university:admin_unit_registration_detail", pk=registration.pk)

        elif action == "reject":
            remarks = request.POST.get("admin_remarks", "").strip()
            with transaction.atomic():
                registration.status = SemesterRegistration.REJECTED
                registration.admin_remarks = remarks
                registration.save()
                registration.enrollments.exclude(status=Enrollment.DROPPED).update(status=Enrollment.DRAFT)
            messages.warning(request, f"Registration for {registration.student.roll_no} marked as REJECTED.")
            return redirect("university:admin_unit_registration_detail", pk=registration.pk)

        elif action == "add_unit":
            course_id = request.POST.get("course_id")
            if course_id:
                course = get_object_or_404(Course, pk=course_id)
                with transaction.atomic():
                    Enrollment.objects.update_or_create(
                        student=registration.student,
                        course=course,
                        defaults={
                            "term": registration.term,
                            "registration": registration,
                            "status": Enrollment.ACTIVE if registration.status == SemesterRegistration.APPROVED else Enrollment.DRAFT,
                            "enrolled_on": timezone.localdate(),
                        }
                    )
                    registration.recalculate_credits()
                messages.success(request, f"Added unit {course.code} to {registration.student.roll_no}'s registration.")
            return redirect("university:admin_unit_registration_detail", pk=registration.pk)

        elif action == "remove_unit":
            enrollment_id = request.POST.get("enrollment_id")
            enr = get_object_or_404(Enrollment, pk=enrollment_id, registration=registration)
            code = enr.course.code
            with transaction.atomic():
                enr.delete()
                registration.recalculate_credits()
            messages.info(request, f"Removed unit {code} from registration.")
            return redirect("university:admin_unit_registration_detail", pk=registration.pk)

    enrolled_units = registration.enrollments.select_related("course__department", "course__faculty__user")
    enrolled_course_ids = enrolled_units.values_list("course_id", flat=True)
    available_courses = Course.objects.filter(status=Course.STATUS_ACTIVE).exclude(id__in=enrolled_course_ids)

    return render(request, "academics/admin_registration_detail.html", {
        "registration": registration,
        "enrolled_units": enrolled_units,
        "available_courses": available_courses,
    })


@login_required
def admin_provisional_transcripts(request):
    """Admin Provisional Transcripts directory & generator."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied

    query = request.GET.get("q", "").strip()
    term_id = request.GET.get("term", "")

    students = StudentProfile.objects.select_related("user", "program__department")
    if query:
        students = students.filter(
            Q(roll_no__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__email__icontains=query)
        )

    terms = AcademicTerm.objects.all()
    page = Paginator(students, 25).get_page(request.GET.get("page"))

    return render(request, "academics/admin_transcripts_list.html", {
        "page": page,
        "q": query,
        "terms": terms,
        "selected_term": term_id,
        "kind": "provisional",
        "title": "Provisional Transcripts Management",
        "subtitle": "Generate and review official term-by-term provisional transcripts for registered students.",
    })


@login_required
def admin_academic_transcripts(request):
    """Admin Official Academic Transcripts directory & generator."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied

    query = request.GET.get("q", "").strip()
    students = StudentProfile.objects.select_related("user", "program__department")
    if query:
        students = students.filter(
            Q(roll_no__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__email__icontains=query)
        )

    page = Paginator(students, 25).get_page(request.GET.get("page"))

    return render(request, "academics/admin_transcripts_list.html", {
        "page": page,
        "q": query,
        "kind": "academic",
        "title": "Official Academic Transcripts Management",
        "subtitle": "Generate and certify comprehensive multi-year permanent academic transcripts.",
    })


@login_required
def admin_academics_dashboard(request):
    """Admin Academics overview hub redirecting to registrations."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied
    return redirect("university:admin_unit_registrations")


# ==============================================================================
# STUDENT EXAM CARD & EXAMINATION OPERATIONS
# ==============================================================================

@login_required
def student_exam_card(request):
    """Student portal: View Examination Card with live financial clearance verification."""
    sp = _get_student(request)
    active_term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()

    # Access control & release window check
    allowed, reason, control, is_bypass = check_document_access(
        sp, DocumentReleaseControl.DocumentType.EXAM_CARD, term=active_term, user=request.user
    )
    if not allowed:
        return render(request, "documents/locked.html", {
            "student": sp,
            "title": "Examination Card Unavailable",
            "reason": reason,
            "control": control,
        }, status=403)

    clearance = check_financial_clearance(sp, term=active_term)
    enrollments_qs = Enrollment.objects.filter(student=sp, status__in=[Enrollment.ACTIVE, "ENROLLED"]).select_related("course")
    if active_term:
        term_enr = enrollments_qs.filter(term=active_term)
        if term_enr.exists():
            enrollments_qs = term_enr

    courses = [e.course for e in enrollments_qs]
    if not courses and sp.program:
        courses = list(sp.program.courses.filter(is_active=True)[:6])

    # Attach exam dates if scheduled
    courses_with_exams = []
    for c in courses:
        ex = Exam.objects.filter(course=c).first()
        courses_with_exams.append({"course": c, "exam": ex})

    return render(request, "academics/exam_card.html", {
        "student": sp,
        "term": active_term,
        "clearance": clearance,
        "courses_with_exams": courses_with_exams,
    })


@login_required
def student_exam_card_pdf(request):
    """Download official printable Examination Card (PDF) with verification seal."""
    sp = _get_student(request)
    active_term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()

    # Access control & release window check
    allowed, reason, control, is_bypass = check_document_access(
        sp, DocumentReleaseControl.DocumentType.EXAM_CARD, term=active_term, user=request.user
    )
    if not allowed:
        return render(request, "documents/locked.html", {
            "student": sp,
            "title": "Examination Card Unavailable",
            "reason": reason,
            "control": control,
        }, status=403)

    ref_code = f"EXAM-CARD/{active_term.name.replace(' ', '') if active_term else 'SESSION'}/{sp.roll_no}"
    verify_url = request.build_absolute_uri(reverse("university:verify_document", kwargs={"reference_no": ref_code}))
    client_ip = get_client_ip(request)
    tracking_info = f"Generated for {request.user.username} · IP: {client_ip} · {timezone.now().strftime('%d %b %Y %H:%M')}"

    pdf_data = generate_exam_card_pdf(sp, term=active_term, verify_url=verify_url, tracking_info=tracking_info)

    try:
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.EXPORT,
            module=AuditLog.Module.ACADEMICS,
            ip_address=client_ip,
            device=detect_device_type(request.META.get("HTTP_USER_AGENT", "")),
            details=f"Exported Exam Card for {sp.roll_no} (Term: {active_term.name if active_term else 'General'}) - Bypass: {is_bypass}"
        )
    except Exception:
        pass

    response = HttpResponse(pdf_data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="Exam_Card_{sp.roll_no}.pdf"'
    return present_pdf(request, response)


# ==============================================================================
# SUPPLEMENTARY & SPECIAL EXAMS WORKFLOW
# ==============================================================================

@login_required
def student_supplementary(request):
    """Student portal: View eligible failed/re-sit courses and apply for Supplementary/Special Exams."""
    sp = _get_student(request)

    # Eligible courses: Grade 'F' or failing results
    all_results = Result.objects.filter(student=sp).select_related("exam__course", "exam__term")
    failing_results = [r for r in all_results if r.grade == "F" or (r.marks_obtained is not None and r.marks_obtained < 40)]

    # Already applied
    existing_regs = SupplementaryExamRegistration.objects.filter(student=sp).select_related("course", "term", "fee_invoice")

    return render(request, "academics/supplementary.html", {
        "student": sp,
        "failing_results": failing_results,
        "existing_regs": existing_regs,
    })


@login_required
@require_POST
def student_supplementary_apply(request, course_id):
    """Submit application to sit supplementary or special exam for a course."""
    sp = _get_student(request)
    course = get_object_or_404(Course, pk=course_id)
    exam_type = request.POST.get("exam_type", SupplementaryExamRegistration.ExamType.SUPPLEMENTARY)
    reason = request.POST.get("reason", "").strip()

    active_term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()

    reg, created = SupplementaryExamRegistration.objects.get_or_create(
        student=sp,
        course=course,
        exam_type=exam_type,
        defaults={
            "term": active_term,
            "reason": reason,
            "status": SupplementaryExamRegistration.Status.PENDING,
        }
    )

    if created:
        messages.success(request, f"Application for {course.code} {reg.get_exam_type_display()} submitted.")
    else:
        messages.info(request, f"You have already applied for {course.code} {reg.get_exam_type_display()}.")

    return redirect("university:student_supplementary")


@login_required
def admin_supplementary_list(request):
    """Admin: Overview of all Supplementary and Special Exam applications."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied

    qs = SupplementaryExamRegistration.objects.select_related("student__user", "course", "term", "fee_invoice").order_by("-created_at")

    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    type_filter = request.GET.get("type", "")
    if type_filter:
        qs = qs.filter(exam_type=type_filter)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "academics/admin_supplementary.html", {
        "page_obj": page_obj,
        "statuses": SupplementaryExamRegistration.Status.choices,
        "types": SupplementaryExamRegistration.ExamType.choices,
        "selected_status": status_filter,
        "selected_type": type_filter,
    })


@login_required
@require_POST
def admin_supplementary_decision(request, pk):
    """Admin: Approve or reject supplementary exam registration and auto-bill fee."""
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied

    reg = get_object_or_404(SupplementaryExamRegistration, pk=pk)
    action = request.POST.get("action")

    if action == "approve":
        reg.status = SupplementaryExamRegistration.Status.APPROVED
        # Auto-bill supplementary exam fee (e.g. KES 1,000)
        from datetime import timedelta
        from decimal import Decimal
        inv, _ = FeeInvoice.objects.get_or_create(
            student=reg.student,
            term=reg.term,
            title=f"Supplementary Exam Fee: {reg.course.code}",
            defaults={
                "amount": Decimal("1000.00"),
                "amount_paid": Decimal("0.00"),
                "due_date": timezone.now().date() + timedelta(days=14),
            }
        )
        reg.fee_invoice = inv
        reg.save()
        messages.success(request, f"Approved supplementary exam for {reg.student.roll_no} - {reg.course.code}. Invoice KES 1,000 issued.")

    elif action == "reject":
        reg.status = SupplementaryExamRegistration.Status.REJECTED
        reg.save()
        messages.warning(request, f"Rejected application for {reg.student.roll_no} - {reg.course.code}.")

    return redirect("university:admin_supplementary_list")


# ==============================================================================
# EXAMINATION NOMINAL ROLLS (ROOM ATTENDANCE SIGNING SHEETS)
# ==============================================================================

@login_required
def admin_exam_nominal_rolls(request):
    """Admin / Faculty: Directory of scheduled examinations with printable nominal rolls."""
    if not (is_admin(request.user) or request.user.is_faculty or request.user.is_superuser):
        raise PermissionDenied

    exams = Exam.objects.select_related("course__department", "term", "room").order_by("date", "start_time")
    return render(request, "academics/admin_nominal_rolls.html", {
        "exams": exams,
    })


@login_required
def admin_exam_nominal_roll_pdf(request, exam_id):
    """Download official Examination Room Nominal Roll / Sign-in sheet PDF."""
    if not (is_admin(request.user) or request.user.is_faculty or request.user.is_superuser):
        raise PermissionDenied

    exam = get_object_or_404(Exam.objects.select_related("course", "room", "term"), pk=exam_id)
    pdf_data = generate_nominal_roll_pdf(exam)
    response = HttpResponse(pdf_data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Nominal_Roll_{exam.course.code}_{exam.pk}.pdf"'
    return present_pdf(request, response)


# ==============================================================================
# STUDENT PROGRESSIVE REPORTS (WEB VIEW & MULTI-FORMAT EXPORTS)
# ==============================================================================

@login_required
def admin_progressive_reports(request):
    """Admin Progressive Reports directory view."""
    if not (is_admin(request.user) or request.user.is_faculty or request.user.is_superuser):
        raise PermissionDenied

    query = request.GET.get("q", "").strip()
    dept_id = request.GET.get("dept", "")
    prog_id = request.GET.get("program", "")

    students = StudentProfile.objects.select_related("user", "program__department")

    if query:
        students = students.filter(
            Q(roll_no__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__email__icontains=query)
        )
    if dept_id and str(dept_id).lower() not in ("all", "", "none"):
        students = students.filter(program__department_id=dept_id)
    if prog_id and str(prog_id).lower() not in ("all", "", "none"):
        students = students.filter(program_id=prog_id)

    departments = Department.objects.all().order_by("name")
    programs = Program.objects.all().order_by("name")
    page = Paginator(students, 25).get_page(request.GET.get("page"))

    return render(request, "academics/admin_progressive_reports_list.html", {
        "page": page,
        "q": query,
        "selected_dept": dept_id,
        "selected_program": prog_id,
        "departments": departments,
        "programs": programs,
        "title": "Student Progressive Reports",
    })


@login_required
def progressive_report_detail_view(request, student_id):
    """Interactive web view of a student's full Progressive Report."""
    student = get_object_or_404(StudentProfile.objects.select_related("user", "program__department"), pk=student_id)

    # Permission check: admin, faculty, or student viewing their own profile
    if not (is_admin(request.user) or request.user.is_faculty or request.user.is_superuser or (hasattr(request.user, "student_profile") and request.user.student_profile.pk == student.pk)):
        raise PermissionDenied("You do not have permission to view this student's progressive report.")

    # Access control & release window check
    allowed, reason, control, is_bypass = check_document_access(
        student, DocumentReleaseControl.DocumentType.PROGRESS_REPORT, user=request.user
    )
    if not allowed:
        return render(request, "documents/locked.html", {
            "student": student,
            "title": "Progressive Academic Report Unavailable",
            "reason": reason,
            "control": control,
        }, status=403)

    from .progressive_report_io import build_progressive_report_context
    ctx = build_progressive_report_context(student)

    # Build template-friendly context with the keys the template expects
    template_ctx = {
        "student": student,
        "is_admin": is_admin(request.user) or request.user.is_faculty or request.user.is_superuser,
        "report_ctx": {
            "cgpa": ctx.get("cgpa", 0.0),
            "total_passed_credits": ctx.get("earned_credits", 0),
            "total_attempted_credits": ctx.get("attempted_credits", 0),
            "passed_courses_count": ctx.get("total_courses_completed", 0),
            "total_courses_count": ctx.get("attempted_credits", 0),  # fallback
            "terms_data": [],
            "generated_at": ctx.get("issued_date", ""),
        },
    }

    # Count total courses across all semesters
    all_courses_count = 0
    for sem in ctx.get("semesters", []):
        all_courses_count += len(sem.get("courses", []))
        template_ctx["report_ctx"]["terms_data"].append({
            "term_name": sem.get("term_name", ""),
            "credits_attempted": sem.get("credits_attempted", 0),
            "credits_earned": sem.get("credits_completed", 0),
            "gpa": sem.get("term_gpa", "N/A"),
            "courses": [
                {
                    "code": c.get("code", ""),
                    "title": c.get("title", ""),
                    "credits": c.get("credits", 0),
                    "cat_score": c.get("ca_mark") if c.get("ca_mark") != "—" else None,
                    "exam_score": c.get("exam_mark") if c.get("exam_mark") != "—" else None,
                    "total_score": c.get("total_pct", 0),
                    "grade": c.get("grade", "N/A"),
                    "gp": c.get("grade_point", 0),
                    "is_pass": c.get("outcome", "").upper() in ["PASS", "PASSED"],
                }
                for c in sem.get("courses", [])
            ],
        })
    template_ctx["report_ctx"]["total_courses_count"] = all_courses_count

    return render(request, "academics/progressive_report_detail.html", template_ctx)


@login_required
def progressive_report_export_view(request, student_id, fmt):
    """Export endpoint for student Progressive Report in PDF, Excel (.xlsx/.xls), or CSV."""
    student = get_object_or_404(StudentProfile.objects.select_related("user", "program__department"), pk=student_id)

    if not (is_admin(request.user) or request.user.is_faculty or request.user.is_superuser or (hasattr(request.user, "student_profile") and request.user.student_profile.pk == student.pk)):
        raise PermissionDenied("You do not have permission to export this progressive report.")

    # Access control & release window check
    allowed, reason, control, is_bypass = check_document_access(
        student, DocumentReleaseControl.DocumentType.PROGRESS_REPORT, user=request.user
    )
    if not allowed:
        return render(request, "documents/locked.html", {
            "student": student,
            "title": "Progressive Academic Report Unavailable",
            "reason": reason,
            "control": control,
        }, status=403)

    from .progressive_report_io import (
        build_progressive_report_context,
        export_progressive_report_pdf,
        export_progressive_report_excel,
        export_progressive_report_csv,
    )

    ctx = build_progressive_report_context(student)
    fmt = fmt.lower().strip()
    filename_base = f"Progressive_Report_{student.roll_no}"

    if fmt == "pdf":
        client_ip = get_client_ip(request)
        tracking_info = f"Generated for {request.user.username} · IP: {client_ip} · {timezone.now().strftime('%d %b %Y %H:%M')}"
        verify_ref = f"UMS/PROG/{student.roll_no}"
        verify_url = request.build_absolute_uri(reverse("university:verify_document", kwargs={"reference_no": verify_ref}))
        pdf_data = export_progressive_report_pdf(student, ctx, tracking_info=tracking_info, verify_url=verify_url)
        resp = HttpResponse(pdf_data, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.pdf"'
        return present_pdf(request, resp)

    elif fmt in ["excel", "xlsx", "xls"]:
        excel_data = export_progressive_report_excel(student, ctx)
        resp = HttpResponse(
            excel_data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.xlsx"'
        return resp

    elif fmt == "csv":
        csv_data = export_progressive_report_csv(student, ctx)
        resp = HttpResponse(csv_data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
        return resp

    else:
        raise Http404("Unsupported export format.")


@login_required
def student_progressive_report_view(request):
    """Student portal shortcut to view their own Progressive Report."""
    sp = _get_student(request)
    return progressive_report_detail_view(request, student_id=sp.pk)


@login_required
def admin_document_controls(request):
    """
    Academic Registry Document Lifecycle & Access Control Dashboard.
    Allows Registry Officers to configure release windows (open/lock dates),
    fee clearance thresholds, and master lock toggles per document type and term.
    """
    if not (is_admin(request.user) or request.user.is_superuser):
        raise PermissionDenied("Only administrative staff can manage document release controls.")

    terms = AcademicTerm.objects.all().order_by("-start_date")
    term_id = request.GET.get("term", "")
    active_term = None
    if term_id.isdigit():
        active_term = AcademicTerm.objects.filter(pk=term_id).first()

    doc_types = DocumentReleaseControl.DocumentType.choices
    controls_map = {}
    existing_controls = DocumentReleaseControl.objects.filter(term=active_term)
    for c in existing_controls:
        controls_map[c.document_type] = c

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_controls":
            with transaction.atomic():
                for code, label in doc_types:
                    ctrl = controls_map.get(code)
                    if not ctrl:
                        ctrl = DocumentReleaseControl(term=active_term, document_type=code)

                    ctrl.is_open = request.POST.get(f"is_open_{code}") == "on"

                    open_dt = request.POST.get(f"open_date_{code}", "").strip()
                    lock_dt = request.POST.get(f"lock_date_{code}", "").strip()
                    if open_dt:
                        try:
                            ctrl.open_date = timezone.datetime.fromisoformat(open_dt)
                            if timezone.is_naive(ctrl.open_date):
                                ctrl.open_date = timezone.make_aware(ctrl.open_date)
                        except Exception:
                            pass
                    else:
                        ctrl.open_date = None

                    if lock_dt:
                        try:
                            ctrl.lock_date = timezone.datetime.fromisoformat(lock_dt)
                            if timezone.is_naive(ctrl.lock_date):
                                ctrl.lock_date = timezone.make_aware(ctrl.lock_date)
                        except Exception:
                            pass
                    else:
                        ctrl.lock_date = None

                    ctrl.require_financial_clearance = request.POST.get(f"require_fee_{code}") == "on"
                    max_fee = request.POST.get(f"max_fee_{code}", "0.00").strip()
                    try:
                        ctrl.max_allowed_fee_balance = Decimal(max_fee)
                    except Exception:
                        ctrl.max_allowed_fee_balance = Decimal("0.00")

                    ctrl.require_senate_approval = request.POST.get(f"require_senate_{code}") == "on"
                    ctrl.notes = request.POST.get(f"notes_{code}", "").strip()
                    ctrl.save()

            messages.success(request, f"Document release controls for {'Universal Policy' if not active_term else active_term.name} updated successfully.")
            return redirect(f"{reverse('university:admin_document_controls')}{'?term=' + str(active_term.pk) if active_term else ''}")

    display_rows = []
    for code, label in doc_types:
        ctrl = controls_map.get(code)
        display_rows.append({
            "code": code,
            "label": label,
            "control": ctrl,
            "is_open": ctrl.is_open if ctrl else True,
            "open_date_iso": ctrl.open_date.strftime("%Y-%m-%dT%H:%M") if ctrl and ctrl.open_date else "",
            "lock_date_iso": ctrl.lock_date.strftime("%Y-%m-%dT%H:%M") if ctrl and ctrl.lock_date else "",
            "require_fee": ctrl.require_financial_clearance if ctrl else False,
            "max_fee": ctrl.max_allowed_fee_balance if ctrl else Decimal("0.00"),
            "require_senate": ctrl.require_senate_approval if ctrl else False,
            "notes": ctrl.notes if ctrl else "",
        })

    return render(request, "academics/admin_document_controls.html", {
        "terms": terms,
        "active_term": active_term,
        "selected_term_id": term_id,
        "display_rows": display_rows,
    })




