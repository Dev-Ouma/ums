"""
Evaluation Views — Course & Lecturer QA Survey
==============================================
Portals:
  Student:  List pending courses, submit evaluation form, view submitted
  Faculty:  View anonymised analytics for own courses
  Admin:    Overview dashboard, per-course drill-down, window management, export
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from .audit_services import log_activity
from .evaluation_services import (
    get_evaluation_window,
    get_student_pending_evaluations,
    get_student_submitted_evaluations,
    get_admin_overview,
    get_course_analytics,
    get_faculty_course_analytics,
    is_window_open,
    make_submission_token,
    submit_evaluation,
    generate_evaluation_excel,
    generate_evaluation_pdf,
)
from .examination_services import is_admin
from .models import (
    AcademicTerm, AuditLog, Course, CourseEvaluation, EvaluationWindow,
)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _get_student(request):
    sp = getattr(request.user, "student_profile", None)
    if not sp:
        raise PermissionDenied("Only registered students can submit evaluations.")
    return sp


def _get_faculty(request):
    fp = getattr(request.user, "faculty_profile", None)
    if not fp:
        raise PermissionDenied("Only faculty members can access this portal.")
    return fp


# ---------------------------------------------------------------------------
# STUDENT PORTAL
# ---------------------------------------------------------------------------

@login_required
def student_evaluation_portal(request):
    """Student landing page: pending + submitted evaluations."""
    sp = _get_student(request)
    active_term = AcademicTerm.objects.filter(is_current=True).first()
    window = get_evaluation_window(active_term)
    open_window = is_window_open(active_term)

    all_terms = AcademicTerm.objects.all()

    pending = get_student_pending_evaluations(sp, term=active_term) if open_window else []
    submitted = get_student_submitted_evaluations(sp, term=active_term)

    context = {
        "student": sp,
        "active_term": active_term,
        "window": window,
        "open_window": open_window,
        "pending": pending,
        "submitted": submitted,
        "all_terms": all_terms,
    }
    return render(request, "evaluations/student_portal.html", context)


@login_required
def student_evaluation_submit(request, course_id):
    """Student submits evaluation for a specific course."""
    sp = _get_student(request)
    course = get_object_or_404(Course, pk=course_id)
    active_term = AcademicTerm.objects.filter(is_current=True).first()

    if not active_term:
        messages.error(request, "No active academic term found.")
        return redirect("university:student_evaluation_portal")

    if not is_window_open(active_term):
        messages.warning(request, "The evaluation window is currently closed.")
        return redirect("university:student_evaluation_portal")

    # Check already submitted
    token = make_submission_token(sp.pk, course.pk, active_term.pk)
    if CourseEvaluation.objects.filter(submission_token=token).exists():
        messages.info(request, "You have already evaluated this course.")
        return redirect("university:student_evaluation_portal")

    if request.method == "POST":
        fields = ["teaching_quality", "course_content", "assessment_fairness",
                  "resources_adequacy", "overall_satisfaction"]
        errors = {}
        data = {}
        for field in fields:
            val = request.POST.get(field)
            if not val or not val.isdigit() or int(val) not in range(1, 6):
                errors[field] = "Please provide a rating between 1 and 5."
            else:
                data[field] = int(val)
        data["strengths"] = request.POST.get("strengths", "").strip()[:2000]
        data["suggestions"] = request.POST.get("suggestions", "").strip()[:2000]

        if not errors:
            ev, err = submit_evaluation(sp, course, active_term, data)
            if err:
                messages.error(request, err)
            else:
                log_activity(
                    request=request,
                    action=AuditLog.Action.CREATE,
                    module=AuditLog.Module.ACADEMICS,
                    entity="CourseEvaluation",
                    entity_id=ev.pk,
                    description=f"Student submitted evaluation for {course.code} — Term: {active_term.name}",
                    new_state={"course": course.code, "term": active_term.name, "avg": ev.average_score},
                )
                messages.success(
                    request,
                    f"✅ Thank you! Your evaluation for {course.code} has been submitted anonymously."
                )
                return redirect("university:student_evaluation_portal")
        else:
            context = {
                "course": course,
                "term": active_term,
                "errors": errors,
                "post_data": request.POST,
            }
            return render(request, "evaluations/student_submit.html", context)

    context = {"course": course, "term": active_term}
    return render(request, "evaluations/student_submit.html", context)


# ---------------------------------------------------------------------------
# FACULTY PORTAL
# ---------------------------------------------------------------------------

@login_required
def faculty_evaluation_dashboard(request):
    """Faculty sees anonymised analytics for their own courses."""
    fp = _get_faculty(request)
    active_term = AcademicTerm.objects.filter(is_current=True).first()
    all_terms = AcademicTerm.objects.all()

    selected_term_id = request.GET.get("term")
    term = None
    if selected_term_id:
        term = AcademicTerm.objects.filter(pk=selected_term_id).first()

    analytics = get_faculty_course_analytics(fp, term=term)

    context = {
        "faculty": fp,
        "active_term": active_term,
        "selected_term": term,
        "all_terms": all_terms,
        "analytics": analytics,
    }
    return render(request, "evaluations/faculty_dashboard.html", context)


# ---------------------------------------------------------------------------
# ADMIN PORTAL
# ---------------------------------------------------------------------------

@login_required
def admin_evaluation_dashboard(request):
    """Admin overview: all courses, averages, response counts."""
    if not is_admin(request.user):
        raise PermissionDenied

    all_terms = AcademicTerm.objects.all()
    selected_term_id = request.GET.get("term")
    active_term = AcademicTerm.objects.filter(is_current=True).first()
    term = None
    if selected_term_id:
        term = AcademicTerm.objects.filter(pk=selected_term_id).first()

    overview = get_admin_overview(term=term)
    window = get_evaluation_window(active_term)
    total_responses = CourseEvaluation.objects.count()
    term_responses = CourseEvaluation.objects.filter(term=active_term).count() if active_term else 0

    context = {
        "overview": overview,
        "all_terms": all_terms,
        "selected_term": term,
        "active_term": active_term,
        "window": window,
        "total_responses": total_responses,
        "term_responses": term_responses,
    }
    return render(request, "evaluations/admin_dashboard.html", context)


@login_required
def admin_evaluation_course_detail(request, course_id):
    """Admin drill-down: full analytics for one course."""
    if not is_admin(request.user):
        raise PermissionDenied

    course = get_object_or_404(Course, pk=course_id)
    all_terms = AcademicTerm.objects.all()
    selected_term_id = request.GET.get("term")
    term = AcademicTerm.objects.filter(pk=selected_term_id).first() if selected_term_id else None

    analytics = get_course_analytics(course, term=term)

    context = {
        "course": course,
        "analytics": analytics,
        "all_terms": all_terms,
        "selected_term": term,
    }
    return render(request, "evaluations/admin_course_detail.html", context)


@login_required
@require_POST
def admin_evaluation_window_toggle(request):
    """Toggle the evaluation window open/closed for the current term."""
    if not is_admin(request.user):
        raise PermissionDenied

    active_term = AcademicTerm.objects.filter(is_current=True).first()
    if not active_term:
        messages.error(request, "No active term found. Please configure an academic term first.")
        return redirect("university:admin_evaluation_dashboard")

    window, _ = EvaluationWindow.objects.get_or_create(term=active_term)
    window.is_open = not window.is_open
    window.save(update_fields=["is_open"])

    state = "opened" if window.is_open else "closed"
    log_activity(
        request=request,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="EvaluationWindow",
        entity_id=window.pk,
        description=f"Evaluation window {state} for {active_term.name}",
        new_state={"is_open": window.is_open, "term": active_term.name},
    )
    messages.success(request, f"Evaluation window {state} for {active_term.name}.")
    return redirect("university:admin_evaluation_dashboard")


@login_required
def admin_evaluation_export(request, fmt):
    """Export evaluation data as Excel or PDF."""
    if not is_admin(request.user):
        raise PermissionDenied

    term_id = request.GET.get("term")
    term = AcademicTerm.objects.filter(pk=term_id).first() if term_id else None
    term_label = term.name.replace(" ", "_") if term else "All_Terms"

    if fmt == "xlsx":
        buf = generate_evaluation_excel(term=term)
        resp = HttpResponse(
            buf.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp["Content-Disposition"] = f'attachment; filename="evaluation_report_{term_label}.xlsx"'
        return resp

    elif fmt == "pdf":
        buf = generate_evaluation_pdf(term=term)
        resp = HttpResponse(buf.read(), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="evaluation_report_{term_label}.pdf"'
        return resp

    raise Http404(f"Unknown export format: {fmt}")
