from university.document_views import present_pdf
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from university.graduation_services import (
    approve_senate_graduation, audit_graduation_eligibility,
    generate_clearance_certificate_pdf, generate_degree_certificate_pdf,
    initiate_student_clearance, process_department_clearance
)
from university.models import (
    DepartmentClearance, GraduationApplication, GraduationCeremony
)


def _admin_required(view_func):
    """Ensure user is an active Administrator or Staff member."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        user_role = getattr(request.user, "role", "")
        if not (request.user.is_staff or request.user.is_superuser or user_role in (Role.ADMIN, "ADMIN")):
            messages.error(request, "Access restricted. Administrator privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ==============================================================================
# STUDENT GRADUATION & CLEARANCE VIEWS
# ==============================================================================

@login_required
def student_graduation_status(request):
    """Student portal for graduation eligibility, clearance tracker, and certificates."""
    try:
        sp = request.user.student_profile
    except Exception:
        messages.error(request, "Only enrolled students can access graduation clearance.")
        return redirect("university:dashboard")

    application = GraduationApplication.objects.filter(student=sp).prefetch_related("clearances").first()
    audit_data = audit_graduation_eligibility(sp)

    context = {
        "student": sp,
        "application": application,
        "audit": audit_data,
        "ceremony": GraduationCeremony.objects.filter(status=GraduationCeremony.Status.PLANNED).first(),
    }
    return render(request, "graduation/student_portal.html", context)


@login_required
@require_POST
def student_apply_clearance(request):
    """Student initiates multi-department exit clearance."""
    try:
        sp = request.user.student_profile
    except Exception:
        messages.error(request, "Only enrolled students can initiate graduation clearance.")
        return redirect("university:dashboard")

    ceremony_id = request.POST.get("ceremony_id")
    ceremony = GraduationCeremony.objects.filter(pk=ceremony_id).first() if ceremony_id else None

    app, audit = initiate_student_clearance(sp, ceremony=ceremony, request=request)
    messages.success(request, "Graduation and exit clearance successfully initiated. Your 5 departmental clearance stations are now active.")
    return redirect("university:student_graduation")


@login_required
def student_degree_certificate_pdf(request):
    """Download official Degree Certificate PDF."""
    app = None
    app_id = request.GET.get("app_id") or request.GET.get("id")
    student_id = request.GET.get("student_id")
    roll_no = request.GET.get("roll_no")

    is_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")

    if is_staff and (app_id or student_id or roll_no):
        if app_id:
            app = GraduationApplication.objects.filter(pk=app_id).select_related("student__user", "student__program", "ceremony").first()
        elif student_id:
            app = GraduationApplication.objects.filter(student_id=student_id).select_related("student__user", "student__program", "ceremony").first()
        elif roll_no:
            app = GraduationApplication.objects.filter(student__roll_no__iexact=roll_no).select_related("student__user", "student__program", "ceremony").first()

    if not app:
        try:
            sp = request.user.student_profile
            app = GraduationApplication.objects.filter(student=sp).select_related("student__user", "student__program", "ceremony").first()
        except Exception:
            if is_staff:
                app = GraduationApplication.objects.select_related("student__user", "student__program", "ceremony").filter(
                    status__in=[GraduationApplication.Status.CLEARED, GraduationApplication.Status.SENATE_APPROVED, GraduationApplication.Status.GRADUATED]
                ).order_by("-id").first() or GraduationApplication.objects.select_related("student__user", "student__program", "ceremony").order_by("-id").first()

    if not app:
        raise Http404("Graduation application not found.")

    sp = app.student

    if app.status not in [GraduationApplication.Status.CLEARED, GraduationApplication.Status.SENATE_APPROVED, GraduationApplication.Status.GRADUATED]:
        messages.warning(request, "Degree certificate will become available once full clearance and Senate approval are completed.")
        return redirect("university:student_graduation")

    pdf_bytes = generate_degree_certificate_pdf(app)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    clean_roll = sp.roll_no.replace("/", "_")
    response["Content-Disposition"] = f'inline; filename="Degree_Certificate_{clean_roll}.pdf"'
    return present_pdf(request, response)


@login_required
def student_clearance_certificate_pdf(request):
    """Download official Certificate of University Clearance PDF."""
    app = None
    app_id = request.GET.get("app_id") or request.GET.get("id")
    student_id = request.GET.get("student_id")
    roll_no = request.GET.get("roll_no")

    is_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")

    if is_staff and (app_id or student_id or roll_no):
        if app_id:
            app = GraduationApplication.objects.filter(pk=app_id).select_related("student__user", "student__program", "ceremony").first()
        elif student_id:
            app = GraduationApplication.objects.filter(student_id=student_id).select_related("student__user", "student__program", "ceremony").first()
        elif roll_no:
            app = GraduationApplication.objects.filter(student__roll_no__iexact=roll_no).select_related("student__user", "student__program", "ceremony").first()

    if not app:
        try:
            sp = request.user.student_profile
            app = GraduationApplication.objects.filter(student=sp).select_related("student__user", "student__program", "ceremony").first()
        except Exception:
            if is_staff:
                app = GraduationApplication.objects.select_related("student__user", "student__program", "ceremony").filter(
                    status__in=[GraduationApplication.Status.CLEARED, GraduationApplication.Status.SENATE_APPROVED, GraduationApplication.Status.GRADUATED]
                ).order_by("-id").first() or GraduationApplication.objects.select_related("student__user", "student__program", "ceremony").order_by("-id").first()

    if not app:
        raise Http404("Clearance record not found.")

    sp = app.student
    pdf_bytes = generate_clearance_certificate_pdf(app)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    clean_roll = sp.roll_no.replace("/", "_")
    response["Content-Disposition"] = f'inline; filename="Clearance_Certificate_{clean_roll}.pdf"'
    return present_pdf(request, response)


# ==============================================================================
# ADMIN GRADUATION & CLEARANCE VIEWS
# ==============================================================================

@login_required
@_admin_required
def admin_graduation_dashboard(request):
    """Administrative Graduation & Degree Conferment Hub."""
    ceremonies = GraduationCeremony.objects.all().order_by("-ceremony_date")
    applications = GraduationApplication.objects.select_related(
        "student__user", "student__program", "ceremony"
    ).prefetch_related("clearances__cleared_by").all()

    # Metrics
    total_apps = applications.count()
    cleared_count = applications.filter(status=GraduationApplication.Status.CLEARED).count()
    senate_approved_count = applications.filter(status=GraduationApplication.Status.SENATE_APPROVED).count()
    hold_count = applications.filter(status=GraduationApplication.Status.REJECTED).count()

    # Filtering
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    ceremony_filter = request.GET.get("ceremony", "").strip()

    if q:
        applications = applications.filter(
            Q(student__roll_no__icontains=q) |
            Q(student__user__first_name__icontains=q) |
            Q(student__user__last_name__icontains=q) |
            Q(certificate_serial__icontains=q)
        )
    if status_filter:
        applications = applications.filter(status=status_filter)
    if ceremony_filter:
        applications = applications.filter(ceremony_id=ceremony_filter)

    paginator = Paginator(applications, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "ceremonies": ceremonies,
        "page_obj": page_obj,
        "total_apps": total_apps,
        "cleared_count": cleared_count,
        "senate_approved_count": senate_approved_count,
        "hold_count": hold_count,
        "q": q,
        "selected_status": status_filter,
        "selected_ceremony": ceremony_filter,
        "statuses": GraduationApplication.Status.choices,
    }
    return render(request, "graduation/admin_dashboard.html", context)


@login_required
@_admin_required
def admin_clearance_queue(request, department):
    """Department-specific clearance verification desk."""
    dept_upper = department.upper()
    clearances = DepartmentClearance.objects.filter(department=dept_upper).select_related(
        "application__student__user", "application__student__program", "cleared_by"
    ).order_by("status", "-application__applied_at")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if q:
        clearances = clearances.filter(
            Q(application__student__roll_no__icontains=q) |
            Q(application__student__user__first_name__icontains=q) |
            Q(application__student__user__last_name__icontains=q)
        )
    if status_filter:
        clearances = clearances.filter(status=status_filter)

    context = {
        "department": dept_upper,
        "dept_display": dict(DepartmentClearance.DepartmentType.choices).get(dept_upper, dept_upper),
        "clearances": clearances,
        "q": q,
        "selected_status": status_filter,
        "statuses": DepartmentClearance.ClearanceStatus.choices,
    }
    return render(request, "graduation/clearance_queue.html", context)


@login_required
@_admin_required
@require_POST
def admin_clearance_action(request, pk):
    """Sign-off or reject a student's departmental clearance."""
    action = request.POST.get("action", "").upper()
    remarks = request.POST.get("remarks", "").strip()

    status_target = DepartmentClearance.ClearanceStatus.CLEARED if action == "CLEAR" else DepartmentClearance.ClearanceStatus.REJECTED
    dc = process_department_clearance(pk, status_target, user=request.user, remarks=remarks, request=request)

    messages.success(request, f"Updated {dc.get_department_display()} clearance for {dc.application.student.roll_no} to {dc.status}.")
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("university:admin_clearance_queue", department=dc.department.lower())


@login_required
@_admin_required
@require_POST
def admin_senate_approve(request, pk):
    """Senate degree conferment confirmation."""
    app = approve_senate_graduation(pk, user=request.user, request=request)
    messages.success(request, f"Conferred degree approval for {app.student.roll_no} - {app.get_classification_display()}.")
    return redirect("university:admin_graduation_dashboard")


@login_required
@_admin_required
def admin_ceremony_create(request):
    """Schedule a new Graduation Ceremony."""
    if request.method == "POST":
        title = request.POST.get("title")
        ay = request.POST.get("academic_year", "2025/2026")
        c_date = request.POST.get("ceremony_date")
        venue = request.POST.get("venue", "Main University Pavilion")
        guest = request.POST.get("chief_guest", "")

        c = GraduationCeremony.objects.create(
            title=title, academic_year=ay, ceremony_date=c_date, venue=venue, chief_guest=guest
        )
        messages.success(request, f"Scheduled Graduation Ceremony '{c.title}'.")
        return redirect("university:admin_graduation_dashboard")
    return redirect("university:admin_graduation_dashboard")
