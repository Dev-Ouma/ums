from university.document_views import present_pdf
from datetime import date, datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import FacultyProfile, Role, StudentProfile
from university.attachment_services import (
    apply_for_attachment, approve_attachment_application,
    assign_academic_supervisor, generate_attachment_intro_letter_pdf,
    generate_attachment_logbook_pdf, record_logbook_entry,
    reject_attachment_application, review_logbook_entry,
    submit_attachment_assessment
)
from university.upload_security import validate_uploaded_file
from university.models import (
    AcademicTerm, AttachmentAssessment, AttachmentLogbookEntry,
    AttachmentPlacement
)

User = get_user_model()


def _is_admin(user):
    return user.is_authenticated and (user.is_admin_role or user.is_superuser or user.is_staff)


def _is_faculty(user):
    return user.is_authenticated and (user.is_faculty or hasattr(user, "faculty_profile"))


# ==============================================================================
# STUDENT PORTAL & LOGBOOK
# ==============================================================================

@login_required
def student_attachment_portal(request):
    """Student industrial attachment dashboard and logbook management."""
    try:
        student = request.user.student_profile
    except (AttributeError, StudentProfile.DoesNotExist):
        messages.error(request, "Only registered students can access the Industrial Attachment portal.")
        return redirect("university:dashboard")

    placement = AttachmentPlacement.objects.filter(student=student).first()
    entries = []
    next_week = 1

    if placement:
        entries = placement.logbook_entries.all().order_by("week_number")
        if entries.exists():
            next_week = entries.last().week_number + 1

    current_term = AcademicTerm.objects.filter(is_current=True).first()

    context = {
        "student": student,
        "placement": placement,
        "entries": entries,
        "next_week": next_week,
        "current_term": current_term,
    }
    return render(request, "attachments/student_portal.html", context)


@login_required
def student_attachment_apply(request):
    """Handle student attachment placement submission."""
    if request.method != "POST":
        return redirect("university:student_attachment_portal")

    try:
        student = request.user.student_profile
    except (AttributeError, StudentProfile.DoesNotExist):
        raise PermissionDenied("Student profile required.")

    company_name = request.POST.get("company_name", "").strip()
    company_branch = request.POST.get("company_branch_location", "").strip() or "Headquarters"
    company_address = request.POST.get("company_address", "").strip()
    company_sup_name = request.POST.get("company_supervisor_name", "").strip()
    company_sup_email = request.POST.get("company_supervisor_email", "").strip()
    company_sup_phone = request.POST.get("company_supervisor_phone", "").strip()
    department_or_unit = request.POST.get("department_or_unit", "").strip() or "IT / Operations"
    
    start_date_str = request.POST.get("start_date", "").strip()
    end_date_str = request.POST.get("end_date", "").strip()
    offer_letter = request.FILES.get("offer_letter")

    try:
        validate_uploaded_file(
            offer_letter,
            extensions={".pdf", ".docx"},
            mime_types={"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        )
    except ValidationError as error:
        messages.error(request, str(error.message if hasattr(error, "message") else error))
        return redirect("university:student_attachment_portal")

    if not (company_name and company_sup_name and company_sup_phone and start_date_str and end_date_str):
        messages.error(request, "Please fill in all required company and date details.")
        return redirect("university:student_attachment_portal")

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        messages.error(request, "Invalid date format provided.")
        return redirect("university:student_attachment_portal")

    current_term = AcademicTerm.objects.filter(is_current=True).first()

    try:
        placement = apply_for_attachment(
            student_profile=student,
            company_name=company_name,
            company_branch_location=company_branch,
            company_address=company_address,
            company_supervisor_name=company_sup_name,
            company_supervisor_email=company_sup_email,
            company_supervisor_phone=company_sup_phone,
            department_or_unit=department_or_unit,
            start_date=start_date,
            end_date=end_date,
            offer_letter=offer_letter,
            term=current_term,
            request=request
        )
        messages.success(request, f"Attachment application for {company_name} submitted successfully! Ref: {placement.intro_letter_reference}")
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, "message") else e))

    return redirect("university:student_attachment_portal")


@login_required
def student_attachment_logbook_submit(request, pk):
    """Student submits a weekly logbook entry."""
    if request.method != "POST":
        return redirect("university:student_attachment_portal")

    placement = get_object_or_404(AttachmentPlacement, id=pk)
    if not (_is_admin(request.user) or placement.student.user_id == request.user.id):
        raise PermissionDenied("You can only submit logbook entries for your own placement.")

    try:
        week_num = int(request.POST.get("week_number", 1))
        df_str = request.POST.get("date_from", "")
        dt_str = request.POST.get("date_to", "")
        tasks = request.POST.get("activities_summary", "").strip()
        skills = request.POST.get("skills_acquired", "").strip()
        challenges = request.POST.get("challenges_encountered", "").strip()

        date_from = datetime.strptime(df_str, "%Y-%m-%d").date()
        date_to = datetime.strptime(dt_str, "%Y-%m-%d").date()

        if not (tasks and skills):
            messages.error(request, "Please enter tasks performed and skills acquired.")
            return redirect("university:student_attachment_portal")

        record_logbook_entry(
            placement=placement,
            week_number=week_num,
            date_from=date_from,
            date_to=date_to,
            activities_summary=tasks,
            skills_acquired=skills,
            challenges_encountered=challenges,
            company_supervisor_signed=True
        )
        messages.success(request, f"Week {week_num} logbook entry recorded successfully!")
    except Exception as e:
        messages.error(request, f"Error saving logbook entry: {e}")

    return redirect("university:student_attachment_portal")


@login_required
def attachment_intro_letter_pdf(request, pk):
    """Download official University Recommendation Letter for Industrial Attachment (PDF)."""
    placement = get_object_or_404(AttachmentPlacement, id=pk)
    if not (_is_admin(request.user) or placement.student.user_id == request.user.id):
        raise PermissionDenied("Unauthorized access to recommendation letter.")

    pdf_bytes = generate_attachment_intro_letter_pdf(placement)
    clean_roll = placement.student.roll_no.replace("/", "_")
    filename = f"Attachment_Intro_Letter_{clean_roll}.pdf"

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return present_pdf(request, response)


@login_required
def attachment_logbook_pdf(request, pk):
    """Download compiled Logbook & Assessment Report (PDF)."""
    placement = get_object_or_404(AttachmentPlacement, id=pk)
    if not (_is_admin(request.user) or _is_faculty(request.user) or placement.student.user_id == request.user.id):
        raise PermissionDenied("Unauthorized access to attachment logbook.")

    pdf_bytes = generate_attachment_logbook_pdf(placement)
    clean_roll = placement.student.roll_no.replace("/", "_")
    filename = f"Attachment_Logbook_{clean_roll}.pdf"

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return present_pdf(request, response)


# ==============================================================================
# FACULTY SUPERVISOR DASHBOARD & GRADING
# ==============================================================================

@login_required
def faculty_attachment_dashboard(request):
    """Faculty supervisor dashboard to monitor attachés, review logbooks, and grade."""
    if not (_is_faculty(request.user) or _is_admin(request.user)):
        raise PermissionDenied("Faculty access required.")

    faculty_profile = getattr(request.user, "faculty_profile", None)

    if faculty_profile:
        placements = AttachmentPlacement.objects.filter(academic_supervisor=faculty_profile).select_related("student__user", "student__program")
    else:
        # Staff viewing all supervised placements
        placements = AttachmentPlacement.objects.filter(academic_supervisor__isnull=False).select_related("student__user", "student__program")

    stats = {
        "total": placements.count(),
        "in_progress": placements.filter(status=AttachmentPlacement.Status.IN_PROGRESS).count(),
        "completed": placements.filter(status=AttachmentPlacement.Status.COMPLETED).count(),
    }

    context = {
        "placements": placements,
        "stats": stats,
        "is_assessor": faculty_profile is not None,
    }
    return render(request, "attachments/faculty_dashboard.html", context)


@login_required
def faculty_attachment_review_log(request, pk):
    """Faculty reviews student weekly logbook entry."""
    if request.method != "POST":
        return redirect("university:faculty_attachment_dashboard")

    entry = get_object_or_404(AttachmentLogbookEntry, id=pk)
    placement = entry.attachment

    if not (_is_admin(request.user) or (placement.academic_supervisor and placement.academic_supervisor.user_id == request.user.id)):
        raise PermissionDenied("Only the assigned academic supervisor may review this logbook.")

    feedback = request.POST.get("feedback", "").strip()
    review_logbook_entry(entry.id, request.user, feedback)
    messages.success(request, f"Feedback recorded for Week {entry.week_number} logbook entry.")
    return redirect("university:faculty_attachment_dashboard")


@login_required
def faculty_attachment_grade(request, pk):
    """Faculty assessor submits 5-part rubric evaluation."""
    if request.method != "POST":
        return redirect("university:faculty_attachment_dashboard")

    placement = get_object_or_404(AttachmentPlacement, id=pk)
    faculty_profile = getattr(request.user, "faculty_profile", None)

    if not (_is_admin(request.user) or (placement.academic_supervisor and placement.academic_supervisor.user_id == request.user.id)):
        raise PermissionDenied("Only the assigned supervisor may assess this placement.")

    if not faculty_profile and _is_admin(request.user):
        faculty_profile = placement.academic_supervisor

    if not faculty_profile:
        messages.error(request, "Academic supervisor required to record assessment.")
        return redirect("university:faculty_attachment_dashboard")

    try:
        org_suit = Decimal(request.POST.get("org_suitability", "8.0"))
        att_score = Decimal(request.POST.get("attendance_score", "12.0"))
        tech_score = Decimal(request.POST.get("technical_score", "28.0"))
        log_score = Decimal(request.POST.get("logbook_score", "16.0"))
        pres_score = Decimal(request.POST.get("presentation_score", "16.0"))
        comments = request.POST.get("assessor_comments", "").strip()
        ind_comments = request.POST.get("industry_comments", "").strip()

        assessment = submit_attachment_assessment(
            placement_id=placement.id,
            assessor_faculty=faculty_profile,
            org_suitability=org_suit,
            attendance_score=att_score,
            technical_score=tech_score,
            logbook_score=log_score,
            presentation_score=pres_score,
            assessor_comments=comments,
            industry_comments=ind_comments,
            request=request
        )
        messages.success(request, f"Assessment recorded: Total Score {assessment.total_score}% (Grade {assessment.grade}). Placement marked COMPLETED.")
    except Exception as e:
        messages.error(request, f"Error recording assessment: {e}")

    return redirect("university:faculty_attachment_dashboard")


# ==============================================================================
# ADMINISTRATOR ATTACHMENT DESK
# ==============================================================================

@login_required
def admin_attachment_dashboard(request):
    """Central administrative desk for industrial attachment coordination."""
    if not _is_admin(request.user):
        raise PermissionDenied("Administrator access required.")

    placements = AttachmentPlacement.objects.all().select_related(
        "student__user", "student__program", "academic_supervisor__user"
    )

    q = request.GET.get("q", "").strip()
    if q:
        placements = placements.filter(
            Q(student__roll_no__icontains=q) |
            Q(student__user__first_name__icontains=q) |
            Q(student__user__last_name__icontains=q) |
            Q(company_name__icontains=q) |
            Q(intro_letter_reference__icontains=q)
        )

    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        placements = placements.filter(status=status_filter)

    faculty_list = FacultyProfile.objects.all().select_related("user", "department")

    stats = {
        "total": AttachmentPlacement.objects.count(),
        "submitted": AttachmentPlacement.objects.filter(status=AttachmentPlacement.Status.SUBMITTED).count(),
        "approved": AttachmentPlacement.objects.filter(status=AttachmentPlacement.Status.APPROVED).count(),
        "in_progress": AttachmentPlacement.objects.filter(status=AttachmentPlacement.Status.IN_PROGRESS).count(),
        "completed": AttachmentPlacement.objects.filter(status=AttachmentPlacement.Status.COMPLETED).count(),
    }

    context = {
        "placements": placements,
        "faculty_list": faculty_list,
        "stats": stats,
        "q": q,
        "selected_status": status_filter,
        "statuses": AttachmentPlacement.Status.choices,
    }
    return render(request, "attachments/admin_dashboard.html", context)


@login_required
def admin_attachment_action(request, pk):
    """Admin actions: approve, reject, or assign supervisor."""
    if request.method != "POST" or not _is_admin(request.user):
        raise PermissionDenied("Admin authorization required.")

    placement = get_object_or_404(AttachmentPlacement, id=pk)
    action = request.POST.get("action", "")

    if action == "approve":
        approve_attachment_application(placement.id, request.user, request=request)
        messages.success(request, f"Attachment application for {placement.student.roll_no} approved!")
    elif action == "reject":
        reason = request.POST.get("reason", "").strip()
        reject_attachment_application(placement.id, request.user, reason=reason, request=request)
        messages.warning(request, f"Attachment application for {placement.student.roll_no} rejected.")
    elif action == "assign_supervisor":
        fac_id = request.POST.get("faculty_id", "")
        faculty = get_object_or_404(FacultyProfile, id=fac_id)
        assign_academic_supervisor(placement.id, faculty, request.user, request=request)
        messages.success(request, f"Assigned {faculty.user.get_full_name()} as supervisor for {placement.student.roll_no}.")

    return redirect("university:admin_attachment_dashboard")
