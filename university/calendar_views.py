import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from university.decorators import role_required
from university.models import AcademicYear, AcademicTerm, Program, SystemSetting, AuditLog
from university.forms import AcademicYearForm, SemesterForm
from university.recycle_bin_services import move_to_recycle_bin
from university.audit_services import log_activity
from university.settings_services import get_setting, set_setting
from university.academic_calendar_services import (
    get_current_academic_year, get_current_semester, get_active_academic_context,
    set_current_academic_year, set_current_semester,
    publish_academic_year, unpublish_academic_year,
    close_academic_year, reopen_academic_year,
    publish_semester, unpublish_semester,
    close_semester, reopen_semester,
    seed_default_academic_calendar,
)
from university.student_numbering_services import (
    get_numbering_config, preview_student_registration_number, render_pattern, build_numbering_context
)


@role_required(Role.ADMIN)
def admin_academic_years(request):
    """
    Central Academic Year Directory view.
    Displays all academic years, status indicators, active semester counts,
    and single-current switch controls.
    """
    # Ensure default calendar exists
    if not AcademicYear.objects.exists():
        seed_default_academic_calendar()

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    years_qs = AcademicYear.objects.annotate(
        total_semesters=Count("semesters", distinct=True)
    ).order_by("-start_date")

    if q:
        years_qs = years_qs.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q) | Q(reference_no__icontains=q)
        )

    if status_filter:
        years_qs = years_qs.filter(status=status_filter)

    paginator = Paginator(years_qs, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    current_year = get_current_academic_year()
    current_sem = get_current_semester()
    active_ctx = get_active_academic_context()

    context = {
        "page_obj": page_obj,
        "academic_years": page_obj.object_list,
        "q": q,
        "status_filter": status_filter,
        "statuses": AcademicYear.Status.choices,
        "current_year": current_year,
        "current_semester": current_sem,
        "active_context": active_ctx,
        "total_count": AcademicYear.objects.count(),
        "published_count": AcademicYear.objects.filter(status__in=[AcademicYear.Status.PUBLISHED, AcademicYear.Status.CURRENT]).count(),
        "form": AcademicYearForm(),
        "numbering_config": get_numbering_config(),
        "sample_number": preview_student_registration_number(),
    }
    return render(request, "dashboard/admin_academic_years.html", context)


@role_required(Role.ADMIN)
def academic_year_detail(request, pk):
    """
    Detailed Academic Year view with complete Semester / Session management,
    registration and examination windows, and student numbering configuration.
    """
    ay = get_object_or_404(AcademicYear, pk=pk)
    semesters = ay.semesters.annotate(
        registrations_count=Count("academic_registrations", distinct=True),
        exams_count=Count("exam", distinct=True),
    ).order_by("semester_number", "start_date")

    semester_form = SemesterForm(initial={
        "academic_year": ay,
        "start_date": ay.start_date,
        "end_date": ay.end_date,
    })

    context = {
        "academic_year": ay,
        "semesters": semesters,
        "semester_form": semester_form,
        "numbering_config": get_numbering_config(),
        "sample_number": preview_student_registration_number(academic_year=ay),
        "active_context": get_active_academic_context(),
    }
    return render(request, "dashboard/academic_year_detail.html", context)


@role_required(Role.ADMIN)
def academic_year_create(request):
    """Create a new Academic Year."""
    if request.method == "POST":
        form = AcademicYearForm(request.POST)
        if form.is_valid():
            ay = form.save(commit=False)
            ay.created_by = request.user
            ay.save()

            if ay.is_current:
                set_current_academic_year(ay.pk, user=request.user, request=request)

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.CREATE,
                module=AuditLog.Module.CALENDAR,
                entity="AcademicYear",
                entity_id=ay.id,
                description=f"Created Academic Year '{ay.name}' ({ay.code}).",
                new_state={"name": ay.name, "start_date": str(ay.start_date), "end_date": str(ay.end_date)},
            )
            messages.success(request, f"Academic Year '{ay.name}' created successfully.")
            return redirect("university:academic_year_detail", pk=ay.pk)
        else:
            messages.error(request, "Please correct the errors in the Academic Year form.")
    else:
        form = AcademicYearForm()

    return render(request, "dashboard/form_page.html", {
        "form": form,
        "form_title": "Add Academic Year",
        "form_subtitle": "Define a new university calendar year",
        "form_icon": "fa-calendar-days",
        "back_url": reverse("university:admin_academic_years"),
    })


@role_required(Role.ADMIN)
def academic_year_edit(request, pk):
    """Edit an existing Academic Year."""
    ay = get_object_or_404(AcademicYear, pk=pk)
    if request.method == "POST":
        form = AcademicYearForm(request.POST, instance=ay)
        if form.is_valid():
            ay = form.save()
            if ay.is_current:
                set_current_academic_year(ay.pk, user=request.user, request=request)

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.UPDATE,
                module=AuditLog.Module.CALENDAR,
                entity="AcademicYear",
                entity_id=ay.id,
                description=f"Updated Academic Year '{ay.name}'.",
                new_state={"name": ay.name, "status": ay.status, "is_current": ay.is_current},
            )
            messages.success(request, f"Academic Year '{ay.name}' updated successfully.")
            return redirect("university:academic_year_detail", pk=ay.pk)
        else:
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = AcademicYearForm(instance=ay)

    return render(request, "dashboard/form_page.html", {
        "form": form,
        "form_title": "Edit Academic Year",
        "form_subtitle": ay.name,
        "form_icon": "fa-pen-to-square",
        "back_url": reverse("university:admin_academic_years"),
    })


@role_required(Role.ADMIN)
def academic_year_delete(request, pk):
    """Safe soft-deletion of an Academic Year to Recycle Bin."""
    ay = get_object_or_404(AcademicYear, pk=pk)
    if ay.is_current:
        messages.error(request, "Cannot delete the CURRENT academic year. Set another year as current first.")
        return redirect("university:admin_academic_years")

    # Check for active dependencies
    has_registrations = ay.semesters.filter(academic_registrations__isnull=False).exists()
    if has_registrations:
        messages.warning(request, f"Academic Year '{ay.name}' has recorded student registrations. Moving to Recycle Bin.")

    if request.method == "POST":
        name = str(ay)
        move_to_recycle_bin(ay, user=request.user, request=request)
        messages.success(request, f"Academic Year '{name}' moved to Recycle Bin.")
        return redirect("university:admin_academic_years")

    return render(request, "dashboard/confirm_delete.html", {
        "object": ay,
        "label": "Academic Year",
        "back_url": reverse("university:admin_academic_years"),
    })


@role_required(Role.ADMIN)
@require_POST
def academic_year_action(request, pk, action):
    """Lifecycle state machine dispatcher for Academic Year."""
    ay = get_object_or_404(AcademicYear, pk=pk)
    try:
        if action == "set_current":
            set_current_academic_year(ay.pk, user=request.user, request=request)
            messages.success(request, f"Academic Year '{ay.name}' is now the CURRENT academic year.")
        elif action == "publish":
            publish_academic_year(ay.pk, user=request.user, request=request)
            messages.success(request, f"Academic Year '{ay.name}' published successfully.")
        elif action == "unpublish":
            unpublish_academic_year(ay.pk, user=request.user, request=request)
            messages.info(request, f"Academic Year '{ay.name}' reverted to DRAFT.")
        elif action == "close":
            close_academic_year(ay.pk, user=request.user, request=request)
            messages.warning(request, f"Academic Year '{ay.name}' has been CLOSED.")
        elif action == "reopen":
            reopen_academic_year(ay.pk, user=request.user, request=request)
            messages.success(request, f"Academic Year '{ay.name}' has been REOPENED.")
        else:
            messages.error(request, f"Unknown action '{action}'.")
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, "message") else e))

    return redirect(request.POST.get("next") or reverse("university:academic_year_detail", args=[ay.pk]))


# ==============================================================================
# SEMESTER MANAGEMENT
# ==============================================================================

@role_required(Role.ADMIN)
def semester_create(request, year_id):
    """Add a semester under a specific Academic Year."""
    ay = get_object_or_404(AcademicYear, pk=year_id)
    if request.method == "POST":
        form = SemesterForm(request.POST)
        if form.is_valid():
            sem = form.save(commit=False)
            sem.academic_year = ay
            sem.created_by = request.user
            sem.save()

            if sem.is_current:
                set_current_semester(sem.pk, user=request.user, request=request)

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.CREATE,
                module=AuditLog.Module.CALENDAR,
                entity="AcademicTerm",
                entity_id=sem.id,
                description=f"Created Semester '{sem.name}' in Academic Year '{ay.name}'.",
                new_state={"name": sem.name, "start_date": str(sem.start_date), "end_date": str(sem.end_date)},
            )
            messages.success(request, f"Semester '{sem.name}' created successfully.")
            return redirect("university:academic_year_detail", pk=ay.pk)
        else:
            messages.error(request, "Please check the dates and details entered for the semester.")
    else:
        form = SemesterForm(initial={"academic_year": ay, "start_date": ay.start_date, "end_date": ay.end_date})

    return render(request, "dashboard/form_page.html", {
        "form": form,
        "form_title": "Add Semester / Session",
        "form_subtitle": f"Under Academic Year {ay.name}",
        "form_icon": "fa-clock",
        "back_url": reverse("university:academic_year_detail", args=[ay.pk]),
    })


@role_required(Role.ADMIN)
def semester_edit(request, pk):
    """Edit a Semester / AcademicTerm."""
    sem = get_object_or_404(AcademicTerm, pk=pk)
    ay = sem.academic_year
    back_url = reverse("university:academic_year_detail", args=[ay.pk]) if ay else reverse("university:admin_academic_years")

    if request.method == "POST":
        form = SemesterForm(request.POST, instance=sem)
        if form.is_valid():
            sem = form.save()
            if sem.is_current:
                set_current_semester(sem.pk, user=request.user, request=request)

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.UPDATE,
                module=AuditLog.Module.CALENDAR,
                entity="AcademicTerm",
                entity_id=sem.id,
                description=f"Updated Semester '{sem.name}'.",
                new_state={"name": sem.name, "status": sem.status, "is_current": sem.is_current},
            )
            messages.success(request, f"Semester '{sem.name}' updated successfully.")
            return redirect(back_url)
        else:
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = SemesterForm(instance=sem)

    return render(request, "dashboard/form_page.html", {
        "form": form,
        "form_title": "Edit Semester",
        "form_subtitle": sem.name,
        "form_icon": "fa-pen-to-square",
        "back_url": back_url,
    })


@role_required(Role.ADMIN)
def semester_delete(request, pk):
    """Safe soft-deletion of a Semester to Recycle Bin."""
    sem = get_object_or_404(AcademicTerm, pk=pk)
    ay = sem.academic_year
    back_url = reverse("university:academic_year_detail", args=[ay.pk]) if ay else reverse("university:admin_academic_years")

    if sem.is_current:
        messages.error(request, "Cannot delete the CURRENT semester. Set another semester as current first.")
        return redirect(back_url)

    if request.method == "POST":
        name = str(sem)
        move_to_recycle_bin(sem, user=request.user, request=request)
        messages.success(request, f"Semester '{name}' moved to Recycle Bin.")
        return redirect(back_url)

    return render(request, "dashboard/confirm_delete.html", {
        "object": sem,
        "label": "Semester / Term",
        "back_url": back_url,
    })


@role_required(Role.ADMIN)
@require_POST
def semester_action(request, pk, action):
    """Lifecycle state machine dispatcher for Semester."""
    sem = get_object_or_404(AcademicTerm, pk=pk)
    ay = sem.academic_year
    back_url = reverse("university:academic_year_detail", args=[ay.pk]) if ay else reverse("university:admin_academic_years")

    try:
        if action == "set_current":
            set_current_semester(sem.pk, user=request.user, request=request)
            messages.success(request, f"Semester '{sem.name}' is now the CURRENT active semester.")
        elif action == "publish":
            publish_semester(sem.pk, user=request.user, request=request)
            messages.success(request, f"Semester '{sem.name}' published successfully.")
        elif action == "unpublish":
            unpublish_semester(sem.pk, user=request.user, request=request)
            messages.info(request, f"Semester '{sem.name}' reverted to DRAFT.")
        elif action == "close":
            close_semester(sem.pk, user=request.user, request=request)
            messages.warning(request, f"Semester '{sem.name}' has been CLOSED.")
        elif action == "reopen":
            reopen_semester(sem.pk, user=request.user, request=request)
            messages.success(request, f"Semester '{sem.name}' has been REOPENED.")
        else:
            messages.error(request, f"Unknown action '{action}'.")
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, "message") else e))

    return redirect(request.POST.get("next") or back_url)


# ==============================================================================
# STUDENT NUMBERING CONFIGURATION & LIVE AJAX PREVIEW
# ==============================================================================

@role_required(Role.ADMIN)
def api_numbering_preview(request):
    """AJAX endpoint for testing custom registration number templates."""
    pattern = request.GET.get("pattern", "").strip() or get_numbering_config()["pattern"]
    program_id = request.GET.get("program_id")
    ay_id = request.GET.get("academic_year_id")

    prog = Program.objects.filter(pk=program_id).first() if program_id else Program.objects.first()
    ay = AcademicYear.objects.filter(pk=ay_id).first() if ay_id else get_current_academic_year()

    try:
        sample = preview_student_registration_number(pattern=pattern, program=prog, academic_year=ay)
        return JsonResponse({"preview": sample, "status": "ok"})
    except Exception as e:
        return JsonResponse({"error": str(e), "status": "error"}, status=400)


@role_required(Role.ADMIN)
@require_POST
def admin_save_numbering_config(request):
    """Save updated student numbering format from the Academic Calendar hub."""
    pattern = request.POST.get("pattern", "").strip()
    prefix = request.POST.get("prefix", "").strip()
    padding = request.POST.get("padding", "4").strip()
    separator = request.POST.get("separator", "/").strip()

    if pattern:
        set_setting("student_id_format_pattern", pattern, user=request.user, request=request)
    if prefix:
        set_setting("student_id_prefix", prefix, user=request.user, request=request)
    if padding:
        set_setting("student_id_seq_padding", padding, user=request.user, request=request)
    if separator:
        set_setting("student_id_separator", separator, user=request.user, request=request)

    messages.success(request, "Student registration numbering configuration updated.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("university:admin_academic_years"))
