from university.document_views import present_pdf
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import FacultyProfile, Role, StudentProfile

from . import ai, course_io, faculty_io, fee_io, program_io, services, student_io, timetable_io
from .decorators import role_required
from .document_design import get_branding
from .financial_services import (
    check_financial_clearance, generate_fee_receipt_pdf, generate_student_statement_pdf
)
from .audit_services import log_activity
from .recycle_bin_services import move_to_recycle_bin
from .forms import (
    AssignmentForm, ClassScheduleForm, CourseForm, DepartmentForm, EventForm,
    ExamForm, FacultyForm, FeeInvoiceForm, FeeStructureForm, ProgramForm, StudentForm,
)
from .models import (
    AcademicTerm, Assignment, Attendance, ClassSchedule, Course, Department,
    Enrollment, Event, Exam, ExamRoom, FeeInvoice, FeeStructure, Notice, Payment,
    Program, Result, Submission, School, AuditLog,
)

DAY_LABELS_MAP = dict(ClassSchedule.Day.choices)


# ==========================================================================
# PUBLIC SITE
# ==========================================================================
def home(request):
    try:
        from cms.models import Page
        from cms import services as cms_services
        cms_home = cms_services.get_page(Page.RESERVED_HOME, request.user)
        if cms_home:
            return cms_services.render_page(request, cms_home)
    except Exception:
        pass

    ctx = {
        "stats": {
            "students": StudentProfile.objects.count(),
            "faculty": FacultyProfile.objects.count(),
            "courses": Course.objects.count(),
            "departments": Department.objects.count(),
        },
        "departments": Department.objects.all()[:6],
        "featured_courses": Course.objects.select_related("department")[:6],
        "events": Event.objects.filter(date__gte=date.today())[:3],
    }
    return render(request, "public/home.html", ctx)


def about(request):
    return render(request, "public/about.html", {
        "departments": Department.objects.all(),
    })


def contact(request):
    if request.method == "POST":
        messages.success(request, "Thanks for reaching out! Our team will reply to your "
                                  "example.com inbox shortly.")
        return redirect("university:contact")
    return render(request, "public/contact.html")


def courses_public(request):
    q = request.GET.get("q", "").strip()
    courses = Course.objects.select_related("department", "faculty__user")
    if q:
        courses = courses.filter(Q(title__icontains=q) | Q(code__icontains=q))
    return render(request, "public/courses.html", {
        "courses": courses, "departments": Department.objects.all(), "q": q,
    })


def public_status(request):
    """
    Public system status page displaying operational health of institution services.
    """
    from university.module_models import SystemModule, ModuleStatus
    from university.control_services import current_status

    state = current_status()

    service_defs = [
        ("students", "Student Portal & Biodata", "fa-user-graduate", "Self-service biodata, profile, and student requests"),
        ("admissions", "Online Admissions & Applications", "fa-file-signature", "Applicant submission, documents, and admission decisions"),
        ("academics", "Academic Programmes & Curriculum", "fa-book-open", "Department courses, syllabi, and semester configurations"),
        ("examinations", "Examinations & Grading", "fa-file-pen", "Exam cards, continuous assessments, and grade sheets"),
        ("finance", "Tuition Fees & Payments", "fa-credit-card", "Invoicing, online statements, and payment receipts"),
        ("timetable", "Class Timetables & Schedules", "fa-calendar-week", "Room allocations, class slots, and lecturer schedules"),
        ("accommodation", "Hostel & Accommodation", "fa-hotel", "Hostel room allocations and check-out clearances"),
        ("library", "Library Catalog & Services", "fa-book", "Book loans, search catalog, and e-resources"),
    ]

    modules_map = {m.code: m for m in SystemModule.objects.filter(code__in=[s[0] for s in service_defs])}

    services = []
    has_incident = state.get("maintenance") or state.get("lockdown")
    for code, title, icon, desc in service_defs:
        mod = modules_map.get(code)
        if has_incident and not state.get("read_only"):
            status_label = "Under Maintenance" if state.get("maintenance") else "Restricted"
            status_class = "warning" if state.get("maintenance") else "danger"
            badge_icon = "fa-screwdriver-wrench" if state.get("maintenance") else "fa-ban"
        elif mod:
            if mod.status == ModuleStatus.ENABLED:
                status_label = "Operational"
                status_class = "success"
                badge_icon = "fa-circle-check"
            elif mod.status == ModuleStatus.MAINTENANCE:
                status_label = "Maintenance"
                status_class = "warning"
                badge_icon = "fa-screwdriver-wrench"
            elif mod.status == ModuleStatus.COMING_SOON:
                status_label = "Coming Soon"
                status_class = "info"
                badge_icon = "fa-rocket"
            else:
                status_label = "Service Offline"
                status_class = "secondary"
                badge_icon = "fa-circle-pause"
        else:
            status_label = "Operational"
            status_class = "success"
            badge_icon = "fa-circle-check"

        services.append({
            "code": code,
            "title": title,
            "icon": icon,
            "desc": desc,
            "status_label": status_label,
            "status_class": status_class,
            "badge_icon": badge_icon,
            "notice": mod.status_message if mod and mod.status != ModuleStatus.ENABLED else "",
        })

    return render(request, "public/status.html", {
        "state": state,
        "services": services,
    })


# ==========================================================================
# DASHBOARD ROUTER
# ==========================================================================
@login_required
def dashboard(request):
    user = request.user
    if user.is_admin_role or user.is_superuser:
        return render(request, "dashboard/admin_dashboard.html", services.admin_dashboard())
    if user.is_faculty:
        fp = get_object_or_404(FacultyProfile, user=user)
        ctx = services.faculty_dashboard(fp)
        ctx["faculty"] = fp
        return render(request, "dashboard/faculty_dashboard.html", ctx)
    sp = get_object_or_404(StudentProfile, user=user)
    ctx = services.student_dashboard(sp)
    ctx["student"] = sp
    ctx["prediction"] = ai.student_prediction(sp)
    return render(request, "dashboard/student_dashboard.html", ctx)


@login_required
def api_academic_performance(request):
    if not (request.user.is_admin_role or request.user.is_superuser or request.user.is_faculty):
        return JsonResponse({"error": "Forbidden"}, status=403)
    term_id = request.GET.get("term")
    dept_id = request.GET.get("department")
    prog_id = request.GET.get("program")
    sem = request.GET.get("semester")
    data = services.academic_performance_data(
        term_id=term_id or None,
        department_id=dept_id or None,
        program_id=prog_id or None,
        semester=sem or None,
    )
    return JsonResponse(data)


# ==========================================================================
# Generic form / delete helpers (rendered with reusable templates)
# ==========================================================================
def _render_form(request, form, title, subtitle="", icon="fa-pen-to-square", back=None):
    return render(request, "dashboard/form_page.html", {
        "form": form, "form_title": title, "form_subtitle": subtitle,
        "form_icon": icon, "back_url": reverse(back) if back else None,
    })


def _confirm_delete(request, obj, label, back):
    if request.method == "POST":
        name = str(obj)
        move_to_recycle_bin(obj, user=request.user, request=request)
        messages.success(request, f"Moved {label} '{name}' to Recycle Bin.")
        return redirect(back)
    return render(request, "dashboard/confirm_delete.html", {
        "object": obj, "label": label, "back_url": reverse(back),
    })


def _pdf_disposition(request, filename):
    """Inline by default (for iframe preview); attachment only when explicitly downloading."""
    kind = "attachment" if request.GET.get("download") == "1" else "inline"
    return f'{kind}; filename="{filename}"'


def _render_pdf_viewer(request, title, subtitle, pdf_export_url_name, pdf_export_args=(), back_url=None,
                       icon="fa-file-pdf"):
    """Render a viewer page whose embedded iframe loads the real generated PDF —
    the same 'preview before download' pattern used by the transcript module,
    so the preview can never drift from the actual downloadable file."""
    base = reverse(pdf_export_url_name, args=pdf_export_args)
    qs = request.GET.urlencode()
    pdf_url = f"{base}?{qs}" if qs else base
    sep = "&" if qs else "?"
    download_url = f"{pdf_url}{sep}download=1"
    return render(request, "dashboard/pdf_viewer.html", {
        "title": title, "subtitle": subtitle, "pdf_url": pdf_url,
        "download_url": download_url, "back_url": back_url, "icon": icon,
    })


# ==========================================================================
# ADMIN AREA — STUDENTS
# ==========================================================================
ALLOWED_STUDENT_PAGE_SIZES = [10, 25, 50, 100]


def _get_filtered_students_queryset(request):
    """Common filtering and sorting logic for students list, exports, and preview."""
    q = request.GET.get("q", "").strip()
    students_qs = StudentProfile.objects.select_related("user", "program")
    if q:
        students_qs = students_qs.filter(
            Q(roll_no__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__email__icontains=q) |
            Q(program__code__icontains=q) |
            Q(program__name__icontains=q)
        )

    sort_by = request.GET.get("sort", "").strip()
    order = request.GET.get("order", "asc").strip().lower()
    sort_fields = {
        "roll_no": "roll_no",
        "name": "user__first_name",
        "program": "program__code",
        "semester": "current_semester",
        "email": "user__email",
    }
    if sort_by in sort_fields:
        prefix = "-" if order == "desc" else ""
        students_qs = students_qs.order_by(f"{prefix}{sort_fields[sort_by]}", "id")
    else:
        students_qs = students_qs.order_by("roll_no")

    return students_qs, q, sort_by, order


@role_required(Role.ADMIN)
def admin_students(request):
    students_qs, q, sort_by, order = _get_filtered_students_queryset(request)
    total_count = students_qs.count()

    # Rows per page handling: 10, 25, 50, 100, All (default: 25)
    per_page_param = request.GET.get("per_page", "25").strip().lower()
    if per_page_param == "all":
        per_page = max(total_count, 1)
        selected_per_page = "all"
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in ALLOWED_STUDENT_PAGE_SIZES:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        selected_per_page = str(per_page)

    paginator = Paginator(students_qs, per_page)
    page_number = request.GET.get("page", 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_range = (
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
        if paginator.num_pages > 1
        else []
    )

    return render(
        request,
        "dashboard/admin_students.html",
        {
            "students": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "page_range": page_range,
            "q": q,
            "per_page": selected_per_page,
            "sort_by": sort_by,
            "order": order,
            "total_count": total_count,
        },
    )


@role_required(Role.ADMIN)
def student_export(request, fmt):
    """Export students list in CSV, Excel, or PDF matching current filters and sorting."""
    fmt = fmt.lower().strip()
    students_qs, q, sort_by, order = _get_filtered_students_queryset(request)

    branding = get_branding()
    site_name = branding["site_name"]
    logo_path = branding["logo_path"]

    timestamp = timezone.now().strftime("%Y%m%d_%H%M")

    if fmt == "csv":
        data = student_io.export_students_csv(students_qs)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="students_{timestamp}.csv"'
        return resp

    elif fmt in ["excel", "xlsx", "xls"]:
        data = student_io.export_students_excel(students_qs, site_name=site_name)
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="students_{timestamp}.xlsx"'
        return resp

    elif fmt == "pdf":
        filter_text = f"Search: '{q}'" if q else None
        data = student_io.export_students_pdf(
            students_qs,
            site_name=site_name,
            logo_path=logo_path,
            filter_text=filter_text,
        )
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"students_{timestamp}.pdf")
        return present_pdf(request, resp)

    else:
        raise Http404(f"Unsupported export format: {fmt}")


@role_required(Role.ADMIN)
def student_preview(request):
    """Load the real generated student directory PDF in-browser before downloading."""
    return _render_pdf_viewer(request, "Student Directory Preview",
                              "Preview matches the downloadable PDF exactly.",
                              "university:student_export", ["pdf"],
                              back_url=reverse("university:admin_students"), icon="fa-user-graduate")


@role_required(Role.ADMIN)
def student_import_template(request, fmt):
    """Download sample student import template in CSV or Excel format."""
    fmt = fmt.lower().strip()
    if fmt == "csv":
        data = student_io.generate_import_template_csv()
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="sample_student_import.csv"'
        return resp
    elif fmt in ["excel", "xlsx", "xls"]:
        data = student_io.generate_import_template_excel()
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = 'attachment; filename="sample_student_import.xlsx"'
        return resp
    else:
        raise Http404(f"Unsupported template format: {fmt}")


@role_required(Role.ADMIN)
def student_import(request):
    """Multi-step bulk import workflow with validation preview and atomic commit."""
    preview_data = None

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "preview":
            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                messages.error(request, "Please choose a CSV or Excel file to upload.")
                return render(request, "dashboard/student_import.html", {"preview_data": None})

            try:
                raw_rows = student_io.parse_uploaded_file(uploaded_file)
                if not raw_rows:
                    messages.error(request, "The uploaded file does not contain any data rows.")
                    return render(request, "dashboard/student_import.html", {"preview_data": None})

                validation_result = student_io.validate_import_rows(raw_rows)
                request.session["pending_student_import"] = validation_result
                preview_data = validation_result
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
                return render(request, "dashboard/student_import.html", {"preview_data": None})

        elif action == "confirm":
            session_data = request.session.get("pending_student_import")
            if not session_data or not session_data.get("items"):
                messages.error(request, "No pending import session found. Please upload your file again.")
                return redirect("university:student_import")

            valid_items = [item for item in session_data["items"] if item["status"] == "valid"]
            duplicates_count = session_data.get("duplicate_count", 0)
            errors_count = session_data.get("error_count", 0)

            if not valid_items:
                messages.error(request, "There are no valid records to import.")
                return render(request, "dashboard/student_import.html", {"preview_data": session_data})

            imported_count, failed_count = student_io.execute_student_import(valid_items)
            total_failed = errors_count + failed_count

            # Clear session
            request.session.pop("pending_student_import", None)

            # Format summary message as requested:
            # "Successfully imported: 95 | Failed: 5 | Duplicates: 2"
            summary_msg = (
                f"Successfully imported: {imported_count} | "
                f"Failed: {total_failed} | Duplicates: {duplicates_count}"
            )
            messages.success(request, summary_msg)
            return redirect("university:admin_students")

        elif action == "cancel":
            request.session.pop("pending_student_import", None)
            messages.info(request, "Import cancelled.")
            return redirect("university:student_import")

    # If preview data is in session
    if preview_data is None and request.method == "GET":
        preview_data = request.session.get("pending_student_import")

    return render(
        request,
        "dashboard/student_import.html",
        {
            "preview_data": preview_data,
            "sample_row": student_io.SAMPLE_STUDENT_ROW,
            "columns": student_io.IMPORT_COLUMNS,
        },
    )


@role_required(Role.ADMIN)
def student_detail(request, pk):
    student = get_object_or_404(StudentProfile.objects.select_related("user", "program"), pk=pk)
    ctx = services.student_dashboard(student)
    ctx["student"] = student
    ctx["prediction"] = ai.student_prediction(student)
    ctx["recommendations"] = ai.study_recommendations(student)
    return render(request, "dashboard/student_detail.html", ctx)


@role_required(Role.ADMIN)
def student_create(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sp = form.save()
        messages.success(request, f"Student {sp.user.display_name} added.")
        return redirect("university:student_detail", pk=sp.pk)
    return _render_form(request, form, "Add Student", "Create a new student account and profile",
                        "fa-user-plus", "university:admin_students")


@role_required(Role.ADMIN)
def student_edit(request, pk):
    sp = get_object_or_404(StudentProfile, pk=pk)
    form = StudentForm(request.POST or None, instance=sp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Student updated.")
        return redirect("university:student_detail", pk=sp.pk)
    return _render_form(request, form, "Edit Student", sp.user.display_name,
                        "fa-user-pen", "university:admin_students")


@role_required(Role.ADMIN)
def student_delete(request, pk):
    sp = get_object_or_404(StudentProfile, pk=pk)
    if request.method == "POST":
        name = sp.user.display_name
        move_to_recycle_bin(sp, user=request.user, request=request)
        messages.success(request, f"Moved student '{name}' to Recycle Bin.")
        return redirect("university:admin_students")
    return render(request, "dashboard/confirm_delete.html", {
        "object": sp, "label": "student", "back_url": reverse("university:admin_students"),
    })


# ==========================================================================
# ADMIN AREA — FACULTY
# ==========================================================================
ALLOWED_FACULTY_PAGE_SIZES = [10, 25, 50, 100]


def _get_filtered_faculty_queryset(request):
    """Common filtering, search, and sorting logic for faculty list, exports, and preview."""
    q = request.GET.get("q", "").strip()
    dept_id = request.GET.get("department", "").strip()

    faculty_qs = FacultyProfile.objects.select_related("user", "department").prefetch_related("courses").annotate(
        n_courses=Count("courses", distinct=True)
    )

    if q:
        faculty_qs = faculty_qs.filter(
            Q(employee_id__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__email__icontains=q) |
            Q(department__name__icontains=q) |
            Q(department__code__icontains=q) |
            Q(designation__icontains=q) |
            Q(specialization__icontains=q)
        )

    if dept_id:
        faculty_qs = faculty_qs.filter(department_id=dept_id)

    sort_by = request.GET.get("sort", "").strip()
    order = request.GET.get("order", "asc").strip().lower()
    sort_fields = {
        "employee_id": "employee_id",
        "name": "user__first_name",
        "department": "department__name",
        "designation": "designation",
        "email": "user__email",
        "courses": "n_courses",
    }
    if sort_by in sort_fields:
        prefix = "-" if order == "desc" else ""
        faculty_qs = faculty_qs.order_by(f"{prefix}{sort_fields[sort_by]}", "id")
    else:
        faculty_qs = faculty_qs.order_by("employee_id")

    return faculty_qs, q, dept_id, sort_by, order


@role_required(Role.ADMIN)
def admin_faculty(request):
    faculty_qs, q, dept_id, sort_by, order = _get_filtered_faculty_queryset(request)
    total_count = faculty_qs.count()

    per_page_param = request.GET.get("per_page", "25").strip().lower()
    if per_page_param == "all":
        per_page = max(total_count, 1)
        selected_per_page = "all"
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in ALLOWED_FACULTY_PAGE_SIZES:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        selected_per_page = str(per_page)

    paginator = Paginator(faculty_qs, per_page)
    page_number = request.GET.get("page", 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_range = (
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
        if paginator.num_pages > 1
        else []
    )

    departments = Department.objects.all().order_by("name")

    return render(
        request,
        "dashboard/admin_faculty.html",
        {
            "faculty": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "page_range": page_range,
            "q": q,
            "selected_department": dept_id,
            "departments": departments,
            "per_page": selected_per_page,
            "sort_by": sort_by,
            "order": order,
            "total_count": total_count,
        },
    )


@role_required(Role.ADMIN)
def faculty_export(request, fmt):
    """Export faculty in CSV, Excel, or PDF with scope=filtered (default) or scope=all."""
    fmt = fmt.lower().strip()
    scope = request.GET.get("scope", "filtered").strip().lower()

    if scope == "all":
        faculty_qs = FacultyProfile.objects.select_related("user", "department").prefetch_related("courses").annotate(
            n_courses=Count("courses", distinct=True)
        ).order_by("employee_id")
        filter_text = "All Faculty"
    else:
        faculty_qs, q, dept_id, sort_by, order = _get_filtered_faculty_queryset(request)
        filter_parts = []
        if q:
            filter_parts.append(f"Search: '{q}'")
        if dept_id:
            dept = Department.objects.filter(pk=dept_id).first()
            if dept:
                filter_parts.append(f"Dept: {dept.code}")
        filter_text = " · ".join(filter_parts) if filter_parts else None

    branding = get_branding()
    site_name = branding["site_name"]
    logo_path = branding["logo_path"]

    timestamp = timezone.now().strftime("%Y%m%d_%H%M")

    if fmt == "csv":
        data = faculty_io.export_faculty_csv(faculty_qs)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="faculty_{timestamp}.csv"'
        return resp

    elif fmt in ["excel", "xlsx", "xls"]:
        data = faculty_io.export_faculty_excel(faculty_qs, site_name=site_name)
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="faculty_{timestamp}.xlsx"'
        return resp

    elif fmt == "pdf":
        data = faculty_io.export_faculty_pdf(
            faculty_qs,
            site_name=site_name,
            logo_path=logo_path,
            filter_text=filter_text,
        )
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"faculty_{timestamp}.pdf")
        return present_pdf(request, resp)

    else:
        raise Http404(f"Unsupported export format: {fmt}")


@role_required(Role.ADMIN)
def faculty_preview(request):
    """Load the real generated faculty directory PDF in-browser before downloading."""
    return _render_pdf_viewer(request, "Faculty Directory Preview",
                              "Preview matches the downloadable PDF exactly.",
                              "university:faculty_export", ["pdf"],
                              back_url=reverse("university:admin_faculty"), icon="fa-chalkboard-user")


@role_required(Role.ADMIN)
def faculty_import_template(request, fmt):
    """Download sample faculty import template in CSV or Excel format."""
    fmt = fmt.lower().strip()
    if fmt == "csv":
        data = faculty_io.generate_faculty_template_csv()
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="sample_faculty_import.csv"'
        return resp
    elif fmt in ["excel", "xlsx", "xls"]:
        data = faculty_io.generate_faculty_template_excel()
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = 'attachment; filename="sample_faculty_import.xlsx"'
        return resp
    else:
        raise Http404(f"Unsupported template format: {fmt}")


@role_required(Role.ADMIN)
def faculty_import(request):
    """Multi-step bulk faculty import with validation preview and atomic commit."""
    preview_data = None

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "preview":
            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                messages.error(request, "Please choose a CSV or Excel file to upload.")
                return render(request, "dashboard/faculty_import.html", {"preview_data": None})

            try:
                raw_rows = faculty_io.parse_uploaded_faculty_file(uploaded_file)
                if not raw_rows:
                    messages.error(request, "The uploaded file does not contain any data rows.")
                    return render(request, "dashboard/faculty_import.html", {"preview_data": None})

                validation_result = faculty_io.validate_faculty_import_rows(raw_rows)
                request.session["pending_faculty_import"] = validation_result
                preview_data = validation_result
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
                return render(request, "dashboard/faculty_import.html", {"preview_data": None})

        elif action == "confirm":
            session_data = request.session.get("pending_faculty_import")
            if not session_data or not session_data.get("items"):
                messages.error(request, "No pending import session found. Please upload your file again.")
                return redirect("university:faculty_import")

            valid_items = [item for item in session_data["items"] if item["status"] == "valid"]
            duplicates_count = session_data.get("duplicate_count", 0)
            errors_count = session_data.get("error_count", 0)

            if not valid_items:
                messages.error(request, "There are no valid records to import.")
                return render(request, "dashboard/faculty_import.html", {"preview_data": session_data})

            imported_count, failed_count = faculty_io.execute_faculty_import(valid_items)
            total_failed = errors_count + failed_count

            request.session.pop("pending_faculty_import", None)

            # Summary message format:
            # "Successfully imported: 95 | Failed: 5 | Duplicates: 2"
            summary_msg = (
                f"Successfully imported: {imported_count} | "
                f"Failed: {total_failed} | Duplicates: {duplicates_count}"
            )
            messages.success(request, summary_msg)
            return redirect("university:admin_faculty")

        elif action == "cancel":
            request.session.pop("pending_faculty_import", None)
            messages.info(request, "Faculty import cancelled.")
            return redirect("university:faculty_import")

    if preview_data is None and request.method == "GET":
        preview_data = request.session.get("pending_faculty_import")

    return render(
        request,
        "dashboard/faculty_import.html",
        {
            "preview_data": preview_data,
            "sample_row": faculty_io.SAMPLE_FACULTY_ROW,
            "columns": faculty_io.FACULTY_IMPORT_COLUMNS,
        },
    )


@role_required(Role.ADMIN)
def faculty_detail(request, pk):
    fp = get_object_or_404(FacultyProfile.objects.select_related("user", "department"), pk=pk)
    return render(request, "dashboard/faculty_detail.html",
                  {"faculty": fp, "courses": fp.courses.all()})


@role_required(Role.ADMIN)
def faculty_create(request):
    form = FacultyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fp = form.save()
        messages.success(request, f"Faculty {fp.user.display_name} added.")
        return redirect("university:faculty_detail", pk=fp.pk)
    return _render_form(request, form, "Add Faculty", "Create a new faculty account and profile",
                        "fa-user-plus", "university:admin_faculty")


@role_required(Role.ADMIN)
def faculty_edit(request, pk):
    fp = get_object_or_404(FacultyProfile, pk=pk)
    form = FacultyForm(request.POST or None, instance=fp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Faculty updated.")
        return redirect("university:faculty_detail", pk=fp.pk)
    return _render_form(request, form, "Edit Faculty", fp.user.display_name,
                        "fa-user-pen", "university:admin_faculty")


@role_required(Role.ADMIN)
def faculty_delete(request, pk):
    fp = get_object_or_404(FacultyProfile, pk=pk)
    if request.method == "POST":
        name = fp.user.display_name
        move_to_recycle_bin(fp, user=request.user, request=request)
        messages.success(request, f"Moved faculty '{name}' to Recycle Bin.")
        return redirect("university:admin_faculty")
    return render(request, "dashboard/confirm_delete.html", {
        "object": fp, "label": "faculty", "back_url": reverse("university:admin_faculty"),
    })


# ==========================================================================
# ADMIN AREA — DEPARTMENTS & PROGRAMS
# ==========================================================================
@role_required(Role.ADMIN)
def admin_departments(request):
    departments = Department.objects.annotate(
        n_courses=Count("courses", distinct=True),
        n_programs=Count("programs", distinct=True))
    return render(request, "dashboard/admin_departments.html", {"departments": departments})


@login_required
def department_detail(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    return render(request, "dashboard/department_detail.html", {
        "dept": dept, "courses": dept.courses.all(), "programs": dept.programs.all(),
    })


@role_required(Role.ADMIN)
def department_create(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        dept = form.save()
        messages.success(request, f"Department {dept.name} created.")
        return redirect("university:department_detail", pk=dept.pk)
    return _render_form(request, form, "Add Department", "", "fa-building-columns",
                        "university:admin_departments")


@role_required(Role.ADMIN)
def department_edit(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    form = DepartmentForm(request.POST or None, instance=dept)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect("university:department_detail", pk=dept.pk)
    return _render_form(request, form, "Edit Department", dept.name, "fa-pen",
                        "university:admin_departments")


@role_required(Role.ADMIN)
def department_delete(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    return _confirm_delete(request, dept, "department", "university:admin_departments")


# ==========================================================================
# PROGRAMMES (ADMIN CRUD, FILTERING, DIRECTORY & IMPORT/EXPORT)
# ==========================================================================
ALLOWED_PROGRAM_PAGE_SIZES = [10, 25, 50, 100]


def _get_filtered_programs_queryset(request):
    """Build filtered and sorted queryset for programmes."""
    q = request.GET.get("q", "").strip()
    school_id = request.GET.get("school", "").strip()
    dept_id = request.GET.get("department", "").strip()
    level_filter = request.GET.get("level", "").strip()
    mode_filter = request.GET.get("study_mode", "").strip()
    status_filter = request.GET.get("status", "").strip()
    sort_by = request.GET.get("sort", "").strip().lower()
    order = request.GET.get("order", "asc").strip().lower()

    programs = Program.objects.select_related("department__school").annotate(
        student_count=Count("students", distinct=True),
        course_count=Count("courses", distinct=True)
    )

    if q:
        programs = programs.filter(
            Q(code__icontains=q) |
            Q(name__icontains=q) |
            Q(award_title__icontains=q) |
            Q(department__code__icontains=q) |
            Q(department__name__icontains=q) |
            Q(department__school__name__icontains=q)
        )

    if school_id and school_id.isdigit():
        programs = programs.filter(department__school_id=school_id)

    if dept_id and dept_id.isdigit():
        programs = programs.filter(department_id=dept_id)

    if level_filter:
        programs = programs.filter(level=level_filter)

    if mode_filter:
        programs = programs.filter(study_mode=mode_filter)

    if status_filter:
        programs = programs.filter(status=status_filter)

    # Sorting
    valid_sorts = {
        "code": "code",
        "name": "name",
        "department": "department__name",
        "school": "department__school__name",
        "level": "level",
        "students": "student_count",
        "courses": "course_count",
        "status": "status",
        "created": "created_at",
    }
    sort_field = valid_sorts.get(sort_by, "name")
    if order == "desc":
        sort_field = f"-{sort_field}"

    return programs.order_by(sort_field)


@role_required(Role.ADMIN)
def admin_programs(request):
    """Admin directory for Academic Programmes with search, filtering, sorting, pagination."""
    programs_qs = _get_filtered_programs_queryset(request)

    # Stats counters
    all_programs = Program.objects.all()
    stats = {
        "total": all_programs.count(),
        "active": all_programs.filter(status=Program.Status.ACTIVE).count(),
        "students": StudentProfile.objects.filter(program__isnull=False).count(),
        "courses": Course.objects.filter(program__isnull=False).count(),
    }

    # Pagination
    try:
        page_size = int(request.GET.get("page_size", 25))
        if page_size not in ALLOWED_PROGRAM_PAGE_SIZES:
            page_size = 25
    except ValueError:
        page_size = 25

    paginator = Paginator(programs_qs, page_size)
    page = paginator.get_page(request.GET.get("page"))

    schools = School.objects.all().order_by("name")
    departments = Department.objects.select_related("school").order_by("name")

    return render(request, "dashboard/admin_programs.html", {
        "programs": page,
        "stats": stats,
        "schools": schools,
        "departments": departments,
        "levels": Program.LEVELS,
        "study_modes": Program.StudyMode.choices,
        "statuses": Program.Status.choices,
        "selected_school": request.GET.get("school", ""),
        "selected_dept": request.GET.get("department", ""),
        "selected_level": request.GET.get("level", ""),
        "selected_mode": request.GET.get("study_mode", ""),
        "selected_status": request.GET.get("status", ""),
        "q": request.GET.get("q", ""),
        "sort": request.GET.get("sort", ""),
        "order": request.GET.get("order", "asc"),
        "page_size": page_size,
        "allowed_page_sizes": ALLOWED_PROGRAM_PAGE_SIZES,
    })


@login_required
def program_detail(request, pk):
    """Detailed view for a single academic programme."""
    p = get_object_or_404(
        Program.objects.select_related("department__school").annotate(
            student_count=Count("students", distinct=True),
            course_count=Count("courses", distinct=True)
        ),
        pk=pk
    )

    courses = p.courses.select_related("department", "faculty__user").order_by("semester_no", "code")
    students = p.students.select_related("user").order_by("roll_no")[:50]
    fee_structures = p.fee_structures.select_related("term").order_by("-term__start_date")
    recent_applications = p.applications.select_related("intake").order_by("-created_at")[:20]

    return render(request, "dashboard/program_detail.html", {
        "program": p,
        "courses": courses,
        "students": students,
        "fee_structures": fee_structures,
        "applications": recent_applications,
        "is_admin": getattr(request.user, "role", None) == Role.ADMIN or getattr(request.user, "is_superuser", False),
    })


@role_required(Role.ADMIN)
def program_create(request):
    form = ProgramForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.CREATE,
            module=AuditLog.Module.PROGRAMMES,
            entity="Program",
            entity_id=p.id,
            description=f"Created programme {p.code} — {p.name}",
            new_state={"code": p.code, "name": p.name, "department_id": p.department_id},
        )
        messages.success(request, f"Programme '{p.name}' ({p.code}) created successfully.")
        return redirect("university:program_detail", pk=p.pk)
    return _render_form(request, form, "Add Programme", "Register a new degree, diploma, or certificate programme", "fa-graduation-cap",
                        "university:admin_programs")


@role_required(Role.ADMIN)
def program_edit(request, pk):
    p = get_object_or_404(Program, pk=pk)
    form = ProgramForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.PROGRAMMES,
            entity="Program",
            entity_id=p.id,
            description=f"Updated programme {p.code} — {p.name}",
            new_state={"code": p.code, "name": p.name},
        )
        messages.success(request, f"Programme '{p.name}' updated successfully.")
        return redirect("university:program_detail", pk=p.pk)
    return _render_form(request, form, "Edit Programme", p.name, "fa-pen",
                        "university:admin_programs")


@role_required(Role.ADMIN)
def program_delete(request, pk):
    p = get_object_or_404(Program, pk=pk)
    if request.method == "POST":
        name = str(p)
        move_to_recycle_bin(p, user=request.user, request=request)
        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.DELETE,
            module=AuditLog.Module.PROGRAMMES,
            entity="Program",
            entity_id=p.id,
            description=f"Deleted programme '{name}' to Recycle Bin.",
            new_state={"code": p.code, "name": p.name},
        )
        messages.success(request, f"Moved programme '{name}' to Recycle Bin.")
        return redirect("university:admin_programs")
    return _confirm_delete(request, p, "programme", "university:admin_programs")


@role_required(Role.ADMIN)
def program_export(request, fmt):
    programs = _get_filtered_programs_queryset(request)
    fmt = fmt.lower().strip()
    filename_base = f"Programmes_Export_{timezone.now().strftime('%Y%m%d_%H%M%S')}"

    if fmt == "csv":
        data = program_io.export_program_csv(programs)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
        return resp
    elif fmt in ["excel", "xlsx"]:
        data = program_io.export_program_excel(programs)
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.xlsx"'
        return resp
    elif fmt == "pdf":
        data = program_io.export_program_pdf(programs)
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"{filename_base}.pdf")
        return present_pdf(request, resp, "Programmes Directory")
    else:
        raise Http404("Unsupported export format.")


@role_required(Role.ADMIN)
def program_preview(request):
    programs = _get_filtered_programs_queryset(request)
    return _render_pdf_viewer(
        request,
        title="Programmes Directory",
        subtitle=f"Previewing {programs.count()} programme records",
        pdf_export_url_name="university:program_export",
        pdf_export_args=["pdf"],
        back_url=reverse("university:admin_programs"),
        icon="fa-graduation-cap"
    )


@role_required(Role.ADMIN)
def program_import(request):
    preview_data = None
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ["upload", "preview"]:
            file = request.FILES.get("file")
            if not file:
                messages.error(request, "Please select a file to import.")
            else:
                preview_data = program_io.parse_and_validate_program_import_file(file)
                if "error" in preview_data:
                    messages.error(request, preview_data["error"])
                    preview_data = None
                else:
                    request.session["pending_program_import"] = preview_data
        elif action == "commit":
            stored_data = request.session.pop("pending_program_import", None)
            if not stored_data or not stored_data.get("valid_rows"):
                messages.error(request, "No valid programme records to commit.")
                return redirect("university:program_import")
            imported_count = program_io.commit_program_import(stored_data["valid_rows"], user=request.user)
            messages.success(request, f"Successfully imported {imported_count} programmes.")
            return redirect("university:admin_programs")
        elif action == "cancel":
            request.session.pop("pending_program_import", None)
            messages.info(request, "Import cancelled.")
            return redirect("university:program_import")

    if preview_data is None and request.method == "GET":
        preview_data = request.session.get("pending_program_import")

    return render(request, "dashboard/program_import.html", {
        "preview_data": preview_data,
        "sample_row": program_io.SAMPLE_PROGRAM_ROW,
        "columns": program_io.PROGRAM_IMPORT_COLUMNS,
    })


@role_required(Role.ADMIN)
def program_import_template(request, fmt):
    fmt = fmt.lower().strip()
    data = program_io.generate_program_import_template(fmt=fmt)
    if fmt == "excel":
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="Programmes_Import_Template.xlsx"'
        return resp
    resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="Programmes_Import_Template.csv"'
    return resp


@login_required
def api_academic_hierarchy(request):
    """JSON API endpoint for dependent cascading dropdowns: School -> Department -> Programme."""
    school_id = request.GET.get("school_id")
    dept_id = request.GET.get("department_id")

    if dept_id and dept_id.isdigit():
        programs = list(Program.objects.filter(department_id=dept_id, status=Program.Status.ACTIVE).values("id", "code", "name", "level"))
        return JsonResponse({"programs": programs})

    if school_id and school_id.isdigit():
        departments = list(Department.objects.filter(school_id=school_id).values("id", "code", "name"))
        return JsonResponse({"departments": departments})

    schools = list(School.objects.all().values("id", "code", "name"))
    departments = list(Department.objects.all().values("id", "code", "name", "school_id"))
    programs = list(Program.objects.filter(status=Program.Status.ACTIVE).values("id", "code", "name", "department_id", "level"))
    return JsonResponse({
        "schools": schools,
        "departments": departments,
        "programs": programs,
    })


# ==========================================================================
# COURSES  (admin CRUD + enrollment management)
# ==========================================================================
ALLOWED_COURSE_PAGE_SIZES = [10, 25, 50, 100]


def _get_filtered_courses_queryset(request):
    """Build filtered and sorted queryset for courses."""
    q = request.GET.get("q", "").strip()
    dept_id = request.GET.get("department", "").strip()
    prog_id = request.GET.get("program", "").strip()
    status_filter = request.GET.get("status", "").strip()
    sort_by = request.GET.get("sort", "").strip().lower()
    order = request.GET.get("order", "asc").strip().lower()

    courses = Course.objects.select_related("department", "program", "faculty__user").annotate(
        n=Count("enrollments", distinct=True)
    )

    if q:
        courses = courses.filter(
            Q(title__icontains=q)
            | Q(code__icontains=q)
            | Q(department__name__icontains=q)
            | Q(department__code__icontains=q)
            | Q(faculty__user__first_name__icontains=q)
            | Q(faculty__user__last_name__icontains=q)
            | Q(description__icontains=q)
        )

    if dept_id:
        courses = courses.filter(department_id=dept_id)

    if prog_id:
        courses = courses.filter(program_id=prog_id)

    if status_filter:
        courses = courses.filter(status__iexact=status_filter)

    sort_fields = {
        "code": "code",
        "title": "title",
        "department": "department__name",
        "credits": "credits",
        "semester": "semester_no",
        "enrolled": "n",
        "status": "status",
    }
    if sort_by in sort_fields:
        prefix = "-" if order == "desc" else ""
        courses = courses.order_by(f"{prefix}{sort_fields[sort_by]}", "id")
    else:
        courses = courses.order_by("code")

    return courses, q, dept_id, prog_id, status_filter, sort_by, order


@login_required
def admin_courses(request):
    courses_qs, q, dept_id, prog_id, status_filter, sort_by, order = _get_filtered_courses_queryset(request)
    total_count = courses_qs.count()

    per_page_param = request.GET.get("per_page", "25").strip().lower()
    if per_page_param == "all":
        per_page = max(total_count, 1)
        selected_per_page = "all"
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in ALLOWED_COURSE_PAGE_SIZES:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        selected_per_page = str(per_page)

    paginator = Paginator(courses_qs, per_page)
    page_number = request.GET.get("page", 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_range = (
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
        if paginator.num_pages > 1
        else []
    )

    departments = Department.objects.all().order_by("name")
    programs = Program.objects.select_related("department").all().order_by("name")
    statuses = [Course.STATUS_ACTIVE, Course.STATUS_ARCHIVED, Course.STATUS_UPCOMING]

    return render(
        request,
        "dashboard/admin_courses.html",
        {
            "courses": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "page_range": page_range,
            "q": q,
            "selected_department": dept_id,
            "selected_program": prog_id,
            "selected_status": status_filter,
            "departments": departments,
            "programs": programs,
            "statuses": statuses,
            "per_page": selected_per_page,
            "sort_by": sort_by,
            "order": order,
            "total_count": total_count,
        },
    )


@role_required(Role.ADMIN)
def course_export(request, fmt):
    """Export courses in CSV, Excel, or PDF with scope=filtered (default) or scope=all."""
    fmt = fmt.lower().strip()
    scope = request.GET.get("scope", "filtered").strip().lower()

    if scope == "all":
        courses_qs = Course.objects.select_related("department", "program", "faculty__user").annotate(
            n=Count("enrollments", distinct=True)
        ).order_by("code")
        filter_text = "All Courses"
    else:
        courses_qs, q, dept_id, prog_id, status_filter, sort_by, order = _get_filtered_courses_queryset(request)
        filter_parts = []
        if q:
            filter_parts.append(f"Search: '{q}'")
        if dept_id:
            dept = Department.objects.filter(pk=dept_id).first()
            if dept:
                filter_parts.append(f"Dept: {dept.code}")
        if prog_id:
            prog = Program.objects.filter(pk=prog_id).first()
            if prog:
                filter_parts.append(f"Program: {prog.code}")
        if status_filter:
            filter_parts.append(f"Status: {status_filter}")
        filter_text = " · ".join(filter_parts) if filter_parts else None

    branding = get_branding()
    site_name = branding["site_name"]
    logo_path = branding["logo_path"]

    timestamp = timezone.now().strftime("%Y%m%d_%H%M")

    if fmt == "csv":
        data = course_io.export_course_csv(courses_qs)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="courses_{timestamp}.csv"'
        return resp

    elif fmt in ["excel", "xlsx", "xls"]:
        data = course_io.export_course_excel(courses_qs, site_name=site_name)
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="courses_{timestamp}.xlsx"'
        return resp

    elif fmt == "pdf":
        data = course_io.export_course_pdf(
            courses_qs,
            site_name=site_name,
            logo_path=logo_path,
            filter_text=filter_text,
        )
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"courses_{timestamp}.pdf")
        return present_pdf(request, resp)

    else:
        raise Http404(f"Unsupported export format: {fmt}")


@role_required(Role.ADMIN)
def course_preview(request):
    """Load the real generated course catalog PDF in-browser before downloading."""
    return _render_pdf_viewer(request, "Course Catalog Preview",
                              "Preview matches the downloadable PDF exactly.",
                              "university:course_export", ["pdf"],
                              back_url=reverse("university:admin_courses"), icon="fa-book")


@role_required(Role.ADMIN)
def course_import_template(request, fmt):
    """Download sample course import template in CSV or Excel format."""
    fmt = fmt.lower().strip()
    if fmt == "csv":
        data = course_io.generate_course_template_csv()
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="sample_course_import.csv"'
        return resp
    elif fmt in ["excel", "xlsx", "xls"]:
        data = course_io.generate_course_template_excel()
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = 'attachment; filename="sample_course_import.xlsx"'
        return resp
    else:
        raise Http404(f"Unsupported template format: {fmt}")


@role_required(Role.ADMIN)
def course_import(request):
    """Multi-step bulk course import with validation preview and atomic commit."""
    preview_data = None

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "preview":
            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                messages.error(request, "Please choose a CSV or Excel file to upload.")
                return render(request, "dashboard/course_import.html", {"preview_data": None})

            try:
                raw_rows = course_io.parse_uploaded_course_file(uploaded_file)
                if not raw_rows:
                    messages.error(request, "The uploaded file does not contain any data rows.")
                    return render(request, "dashboard/course_import.html", {"preview_data": None})

                validation_result = course_io.validate_course_import_rows(raw_rows)
                request.session["pending_course_import"] = validation_result
                preview_data = validation_result
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
                return render(request, "dashboard/course_import.html", {"preview_data": None})

        elif action == "confirm":
            session_data = request.session.get("pending_course_import")
            if not session_data or not session_data.get("items"):
                messages.error(request, "No pending import session found. Please upload your file again.")
                return redirect("university:course_import")

            valid_items = [item for item in session_data["items"] if item["status"] == "valid"]
            duplicates_count = session_data.get("duplicate_count", 0)
            errors_count = session_data.get("error_count", 0)

            if not valid_items:
                messages.error(request, "There are no valid records to import.")
                return render(request, "dashboard/course_import.html", {"preview_data": session_data})

            imported_count, failed_count = course_io.execute_course_import(valid_items)
            total_failed = errors_count + failed_count

            request.session.pop("pending_course_import", None)

            # Summary message format:
            # "Successfully imported: 95 | Failed: 5 | Duplicates: 2"
            summary_msg = (
                f"Successfully imported: {imported_count} | "
                f"Failed: {total_failed} | "
                f"Duplicates: {duplicates_count}"
            )
            messages.success(request, summary_msg)
            return redirect("university:admin_courses")

        elif action == "cancel":
            request.session.pop("pending_course_import", None)
            messages.info(request, "Course import was cancelled.")
            return redirect("university:course_import")

    elif "pending_course_import" in request.session:
        preview_data = request.session["pending_course_import"]

    return render(
        request,
        "dashboard/course_import.html",
        {
            "preview_data": preview_data,
            "sample_row": course_io.SAMPLE_COURSE_ROW,
            "columns": course_io.COURSE_IMPORT_COLUMNS,
        },
    )


@login_required
def course_detail(request, pk):
    course = get_object_or_404(Course.objects.select_related(
        "department", "faculty__user"), pk=pk)
    roster = Enrollment.objects.filter(course=course).select_related("student__user")
    enrolled_ids = roster.values_list("student_id", flat=True)
    available = StudentProfile.objects.exclude(pk__in=enrolled_ids).select_related("user")
    return render(request, "dashboard/course_detail.html", {
        "course": course, "roster": roster,
        "assignments": course.assignments.all(), "exams": course.exams.all(),
        "available_students": available,
    })


@role_required(Role.ADMIN)
def course_create(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        messages.success(request, f"Course {c.code} created.")
        return redirect("university:course_detail", pk=c.pk)
    return _render_form(request, form, "Add Course", "", "fa-book",
                        "university:admin_courses")


@role_required(Role.ADMIN)
def course_edit(request, pk):
    c = get_object_or_404(Course, pk=pk)
    form = CourseForm(request.POST or None, instance=c)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Course updated.")
        return redirect("university:course_detail", pk=c.pk)
    return _render_form(request, form, "Edit Course", c.title, "fa-pen",
                        "university:admin_courses")


@role_required(Role.ADMIN)
def course_delete(request, pk):
    c = get_object_or_404(Course, pk=pk)
    return _confirm_delete(request, c, "course", "university:admin_courses")


@role_required(Role.ADMIN)
@require_POST
def course_enroll(request, pk):
    course = get_object_or_404(Course, pk=pk)
    student_id = request.POST.get("student")
    if student_id:
        sp = get_object_or_404(StudentProfile, pk=student_id)
        Enrollment.objects.get_or_create(student=sp, course=course,
                                          defaults={"status": Enrollment.ACTIVE})
        messages.success(request, f"Enrolled {sp.user.display_name} in {course.code}.")
    return redirect("university:course_detail", pk=course.pk)


@role_required(Role.ADMIN)
@require_POST
def enrollment_remove(request, pk):
    enr = get_object_or_404(Enrollment, pk=pk)
    course_pk = enr.course_id
    name = enr.student.user.display_name
    enr.delete()
    messages.info(request, f"Removed {name} from the course.")
    return redirect("university:course_detail", pk=course_pk)


# ==========================================================================
# FEES  (admin)
# ==========================================================================
ALLOWED_FEE_PAGE_SIZES = [10, 25, 50, 100]


def _get_filtered_fees_queryset(request):
    """Build filtered and sorted queryset for fee invoices."""
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()
    term_id = request.GET.get("term", "").strip()
    sort_by = request.GET.get("sort", "").strip().lower()
    order = request.GET.get("order", "desc").strip().lower()

    invoices = FeeInvoice.objects.select_related("student__user", "term").annotate(
        calculated_balance=F("amount") - F("amount_paid")
    )

    if q:
        invoices = invoices.filter(
            Q(title__icontains=q)
            | Q(student__roll_no__icontains=q)
            | Q(student__user__first_name__icontains=q)
            | Q(student__user__last_name__icontains=q)
            | Q(student__user__email__icontains=q)
        )

    if status_filter == "PAID":
        invoices = invoices.filter(amount_paid__gte=F("amount"))
    elif status_filter == "PARTIAL":
        invoices = invoices.filter(amount_paid__gt=0, amount_paid__lt=F("amount"))
    elif status_filter == "UNPAID":
        invoices = invoices.filter(amount_paid=0)

    if term_id:
        invoices = invoices.filter(term_id=term_id)

    sort_fields = {
        "invoice": "id",
        "student": "student__user__first_name",
        "title": "title",
        "amount": "amount",
        "paid": "amount_paid",
        "balance": "calculated_balance",
        "due_date": "due_date",
        "issued_on": "issued_on",
    }
    if sort_by in sort_fields:
        prefix = "-" if order == "desc" else ""
        invoices = invoices.order_by(f"{prefix}{sort_fields[sort_by]}", "-id")
    else:
        invoices = invoices.order_by("-issued_on", "-id")

    return invoices, q, status_filter, term_id, sort_by, order


@role_required(Role.ADMIN)
def admin_fees(request):
    invoices_qs, q, status_filter, term_id, sort_by, order = _get_filtered_fees_queryset(request)
    total_count = invoices_qs.count()

    per_page_param = request.GET.get("per_page", "25").strip().lower()
    if per_page_param == "all":
        per_page = max(total_count, 1)
        selected_per_page = "all"
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in ALLOWED_FEE_PAGE_SIZES:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        selected_per_page = str(per_page)

    paginator = Paginator(invoices_qs, per_page)
    page_number = request.GET.get("page", 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_range = (
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
        if paginator.num_pages > 1
        else []
    )

    all_invoices = FeeInvoice.objects.all()
    terms = AcademicTerm.objects.all().order_by("-start_date")
    statuses = ["PAID", "PARTIAL", "UNPAID"]

    ctx = {
        "invoices": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "page_range": page_range,
        "total_count": total_count,
        "q": q,
        "selected_status": status_filter,
        "selected_term": term_id,
        "terms": terms,
        "statuses": statuses,
        "per_page": selected_per_page,
        "sort_by": sort_by,
        "order": order,
        "collected": services.total_fees_collected(),
        "billed": services.total_fees_billed(),
        "unpaid": sum(float(i.balance) for i in all_invoices),
    }
    return render(request, "dashboard/admin_fees.html", ctx)


@role_required(Role.ADMIN)
def fee_export(request, fmt):
    """Export fee invoices in CSV, Excel, or PDF with scope=filtered (default) or scope=all."""
    fmt = fmt.lower().strip()
    scope = request.GET.get("scope", "filtered").strip().lower()

    if scope == "all":
        invoices_qs = FeeInvoice.objects.select_related("student__user", "term").order_by("-issued_on", "-id")
        filter_text = "All Invoices"
    else:
        invoices_qs, q, status_filter, term_id, sort_by, order = _get_filtered_fees_queryset(request)
        filter_parts = []
        if q:
            filter_parts.append(f"Search: '{q}'")
        if status_filter:
            filter_parts.append(f"Status: {status_filter}")
        if term_id:
            term = AcademicTerm.objects.filter(pk=term_id).first()
            if term:
                filter_parts.append(f"Term: {term.name}")
        filter_text = " · ".join(filter_parts) if filter_parts else None

    branding = get_branding()
    site_name = branding["site_name"]
    logo_path = branding["logo_path"]

    timestamp = timezone.now().strftime("%Y%m%d_%H%M")

    if fmt == "csv":
        data = fee_io.export_fee_csv(invoices_qs)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="fee_report_{timestamp}.csv"'
        return resp

    elif fmt in ["excel", "xlsx", "xls"]:
        data = fee_io.export_fee_excel(invoices_qs, site_name=site_name)
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="fee_report_{timestamp}.xlsx"'
        return resp

    elif fmt == "pdf":
        data = fee_io.export_fee_pdf(
            invoices_qs,
            site_name=site_name,
            logo_path=logo_path,
            filter_text=filter_text,
        )
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"fee_report_{timestamp}.pdf")
        return present_pdf(request, resp)

    else:
        raise Http404(f"Unsupported export format: {fmt}")


@role_required(Role.ADMIN)
def fee_preview(request):
    """Load the real generated fee invoices report PDF in-browser before downloading."""
    return _render_pdf_viewer(request, "Fee Invoices Report Preview",
                              "Preview matches the downloadable PDF exactly.",
                              "university:fee_export", ["pdf"],
                              back_url=reverse("university:admin_fees"), icon="fa-wallet")


@role_required(Role.ADMIN)
def fee_create(request):
    form = FeeInvoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Invoice created.")
        return redirect("university:admin_fees")
    return _render_form(request, form, "Create Invoice", "", "fa-file-invoice",
                        "university:admin_fees")


@role_required(Role.ADMIN)
@require_POST
def record_payment(request, pk):
    invoice = get_object_or_404(FeeInvoice, pk=pk)
    try:
        amount = Decimal(request.POST.get("amount", "0"))
    except (InvalidOperation, TypeError):
        amount = Decimal("0")
    if amount > 0:
        amount = min(amount, invoice.balance)
        invoice.amount_paid += amount
        invoice.save()
        Payment.objects.create(invoice=invoice, amount=amount, method="Front-desk",
                               reference=f"TXN-{timezone.now().strftime('%H%M%S')}")
        messages.success(request, f"Recorded KES {amount:,.0f} against {invoice.title}.")
    return redirect("university:admin_fees")


@role_required(Role.ADMIN)
def admin_fee_structures(request):
    """Admin: Directory of program fee schedules per year/semester."""
    structures = FeeStructure.objects.select_related("program", "term").order_by("program__name", "year_of_study", "semester")
    return render(request, "dashboard/admin_fee_structures.html", {
        "structures": structures,
    })


@role_required(Role.ADMIN)
def fee_structure_create(request):
    """Admin: Define fee schedule for a program and study year/term."""
    form = FeeStructureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fs = form.save()
        messages.success(request, f"Fee structure for {fs.program.code} Year {fs.year_of_study} Sem {fs.semester} created.")
        return redirect("university:admin_fee_structures")
    return _render_form(request, form, "Create Fee Structure", "Define tuition and statutory fee components",
                        "fa-scale-balanced", "university:admin_fee_structures")


@role_required(Role.ADMIN)
def fee_structure_edit(request, pk):
    """Admin: Edit fee schedule amounts."""
    fs = get_object_or_404(FeeStructure, pk=pk)
    form = FeeStructureForm(request.POST or None, instance=fs)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Fee structure updated.")
        return redirect("university:admin_fee_structures")
    return _render_form(request, form, f"Edit Fee Structure: {fs.program.code}",
                        f"Year {fs.year_of_study} Semester {fs.semester}",
                        "fa-scale-balanced", "university:admin_fee_structures")


@role_required(Role.ADMIN)
@require_POST
def fee_structure_delete(request, pk):
    """Admin: Remove fee structure."""
    fs = get_object_or_404(FeeStructure, pk=pk)
    move_to_recycle_bin(fs, user=request.user, request=request)
    messages.info(request, "Fee structure moved to Recycle Bin.")
    return redirect("university:admin_fee_structures")


@login_required
def fee_receipt_pdf(request, pk):
    """Download official University Payment Receipt PDF."""
    payment = get_object_or_404(Payment.objects.select_related("invoice__student__user", "invoice__term"), pk=pk)
    # Security: student can only download own receipts, admin can download all
    if request.user.role == Role.STUDENT and payment.invoice.student.user != request.user:
        raise Http404("Receipt not found.")

    pdf_bytes = generate_fee_receipt_pdf(payment)
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Receipt_REC-{payment.id:06d}.pdf"'
    return present_pdf(request, resp)


@login_required
def student_fee_statement(request):
    """Student / Admin: View interactive ledger statement of account."""
    if request.user.role == Role.STUDENT:
        sp = get_object_or_404(StudentProfile, user=request.user)
    else:
        student_id = request.GET.get("student_id")
        sp = get_object_or_404(StudentProfile, pk=student_id) if student_id else None
        if not sp:
            messages.warning(request, "Please select a student to view financial statement.")
            return redirect("university:admin_fees")

    clearance = check_financial_clearance(sp)
    invoices = FeeInvoice.objects.filter(student=sp).order_by("issued_on", "id")
    payments = Payment.objects.filter(invoice__student=sp).select_related("invoice").order_by("paid_on", "id")

    return render(request, "dashboard/student_fee_statement.html", {
        "student": sp,
        "clearance": clearance,
        "invoices": invoices,
        "payments": payments,
    })


@login_required
def student_fee_statement_pdf(request):
    """Download official Student Statement of Account PDF."""
    if request.user.role == Role.STUDENT:
        sp = get_object_or_404(StudentProfile, user=request.user)
    else:
        student_id = request.GET.get("student_id")
        sp = get_object_or_404(StudentProfile, pk=student_id) if student_id else None
        if not sp:
            raise Http404("Student not specified.")

    pdf_bytes = generate_student_statement_pdf(sp)
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="Statement_{sp.roll_no}.pdf"'
    return present_pdf(request, resp)


# ==========================================================================
# TIMETABLE  (weekly class scheduling)
# ==========================================================================
def _selected_term(request):
    term_id = request.GET.get("term")
    if term_id:
        term = AcademicTerm.objects.filter(pk=term_id).first()
        if term:
            return term
    return AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()


def _order_by_weekday(schedules):
    """Order a ClassSchedule queryset Monday->Saturday instead of the model's alphabetical default."""
    order = Case(*[When(day=code, then=i) for i, (code, _) in enumerate(ClassSchedule.Day.choices)],
                output_field=IntegerField())
    return schedules.annotate(_weekday_order=order).order_by("_weekday_order", "start_time")


def _timetable_grid(schedules):
    by_day = {code: [] for code, _ in ClassSchedule.Day.choices}
    for s in schedules:
        by_day.setdefault(s.day, []).append(s)
    return [{"code": code, "label": label, "sessions": by_day.get(code, [])}
            for code, label in ClassSchedule.Day.choices]


def _get_filtered_schedule_queryset(request):
    """Build the filtered timetable queryset shared by the grid, export and preview views."""
    term = _selected_term(request)
    semester = request.GET.get("semester", "").strip()
    dept_id = request.GET.get("department", "").strip()
    prog_id = request.GET.get("program", "").strip()
    course_id = request.GET.get("course", "").strip()
    faculty_id = request.GET.get("faculty", "").strip()
    day = request.GET.get("day", "").strip()
    room_id = request.GET.get("room", "").strip()
    status_filter = request.GET.get("status", "").strip()

    schedules = ClassSchedule.objects.select_related(
        "course", "course__department", "course__program", "course__faculty__user", "room", "term")
    schedules = schedules.filter(term=term) if term else schedules.none()

    if semester:
        schedules = schedules.filter(course__semester_no=semester)
    if dept_id:
        schedules = schedules.filter(course__department_id=dept_id)
    if prog_id:
        schedules = schedules.filter(course__program_id=prog_id)
    if course_id:
        schedules = schedules.filter(course_id=course_id)
    if faculty_id:
        schedules = schedules.filter(course__faculty_id=faculty_id)
    if day:
        schedules = schedules.filter(day=day)
    if room_id:
        schedules = schedules.filter(room_id=room_id)
    if status_filter:
        schedules = schedules.filter(status=status_filter)

    filters = {"semester": semester, "department": dept_id, "program": prog_id, "course": course_id,
              "faculty": faculty_id, "day": day, "room": room_id, "status": status_filter}
    return schedules, term, filters


def _timetable_filter_options():
    return {
        "departments": Department.objects.all(),
        "programs": Program.objects.select_related("department").all(),
        "courses": Course.objects.select_related("department").order_by("code"),
        "faculty_list": FacultyProfile.objects.select_related("user").order_by("user__first_name"),
        "rooms": ExamRoom.objects.filter(active=True),
        "day_choices": ClassSchedule.Day.choices,
        "semester_choices": [1, 2, 3],
    }


@role_required(Role.ADMIN)
def admin_timetable(request):
    schedules, term, filters = _get_filtered_schedule_queryset(request)
    ctx = {
        "grid": _timetable_grid(schedules), "term": term, "terms": AcademicTerm.objects.all(),
        "filters": filters, "total_count": schedules.count(),
    }
    ctx.update(_timetable_filter_options())
    return render(request, "dashboard/admin_timetable.html", ctx)


@role_required(Role.ADMIN)
def class_schedule_create(request):
    form = ClassScheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            schedule = form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Scheduled {schedule.course.code} on "
                                     f"{schedule.get_day_display()}.")
            return redirect("university:admin_timetable")
    return _render_form(request, form, "Schedule a Class", "", "fa-calendar-plus",
                        "university:admin_timetable")


@role_required(Role.ADMIN)
def class_schedule_edit(request, pk):
    schedule = get_object_or_404(ClassSchedule, pk=pk)
    form = ClassScheduleForm(request.POST or None, instance=schedule)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Class schedule updated.")
            return redirect("university:admin_timetable")
    return _render_form(request, form, "Edit Class Schedule", str(schedule), "fa-pen",
                        "university:admin_timetable")


@role_required(Role.ADMIN)
def class_schedule_delete(request, pk):
    schedule = get_object_or_404(ClassSchedule, pk=pk)
    return _confirm_delete(request, schedule, "class schedule", "university:admin_timetable")


@role_required(Role.ADMIN)
@require_POST
def class_schedule_publish(request, pk):
    schedule = get_object_or_404(ClassSchedule, pk=pk)
    schedule.status = (ClassSchedule.Status.DRAFT if schedule.status == ClassSchedule.Status.PUBLISHED
                       else ClassSchedule.Status.PUBLISHED)
    schedule.save(update_fields=["status"])
    messages.success(request, f"{schedule} is now {schedule.get_status_display().lower()}.")
    referer = request.META.get("HTTP_REFERER")
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect("university:admin_timetable")


@role_required(Role.ADMIN)
def timetable_export(request, fmt):
    """Export the timetable in CSV, Excel, or PDF, respecting active filters (scope=all ignores them)."""
    fmt = fmt.lower().strip()
    scope = request.GET.get("scope", "filtered").strip().lower()

    if scope == "all":
        schedules = ClassSchedule.objects.select_related(
            "course", "course__department", "course__program", "course__faculty__user", "room", "term")
        filter_text = "All Timetable Entries"
    else:
        schedules, term, filters = _get_filtered_schedule_queryset(request)
        parts = [f"Term: {term.name}"] if term else []
        if filters["department"]:
            dept = Department.objects.filter(pk=filters["department"]).first()
            if dept:
                parts.append(f"Dept: {dept.code}")
        if filters["day"]:
            parts.append(f"Day: {DAY_LABELS_MAP.get(filters['day'], filters['day'])}")
        filter_text = " · ".join(parts) if parts else None

    branding = get_branding()
    site_name = branding["site_name"]
    logo_path = branding["logo_path"]
    timestamp = timezone.now().strftime("%Y%m%d_%H%M")
    schedules = _order_by_weekday(schedules)

    if fmt == "csv":
        data = timetable_io.export_timetable_csv(schedules)
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="timetable_{timestamp}.csv"'
        return resp
    elif fmt in ("excel", "xlsx", "xls"):
        data = timetable_io.export_timetable_excel(schedules, site_name=site_name)
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="timetable_{timestamp}.xlsx"'
        return resp
    elif fmt == "pdf":
        data = timetable_io.export_timetable_pdf(schedules, site_name=site_name, logo_path=logo_path,
                                                 filter_text=filter_text)
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = _pdf_disposition(request, f"timetable_{timestamp}.pdf")
        return present_pdf(request, resp)
    raise Http404(f"Unsupported export format: {fmt}")


@role_required(Role.ADMIN)
def timetable_preview(request):
    """Load the real generated timetable PDF in-browser before downloading."""
    return _render_pdf_viewer(request, "Timetable Preview",
                              "Preview matches the downloadable PDF exactly.",
                              "university:timetable_export", ["pdf"],
                              back_url=reverse("university:admin_timetable"), icon="fa-calendar-week")


@role_required(Role.ADMIN)
def timetable_import_template(request, fmt):
    fmt = fmt.lower().strip()
    if fmt == "csv":
        data = timetable_io.generate_timetable_template_csv()
        resp = HttpResponse(data, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="sample_timetable_import.csv"'
        return resp
    elif fmt in ("excel", "xlsx", "xls"):
        data = timetable_io.generate_timetable_template_excel()
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="sample_timetable_import.xlsx"'
        return resp
    raise Http404(f"Unsupported template format: {fmt}")


@role_required(Role.ADMIN)
def timetable_import(request):
    """Multi-step bulk timetable import: upload -> validate & preview -> confirm or cancel."""
    preview_data = None

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "preview":
            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                messages.error(request, "Please choose a CSV or Excel file to upload.")
                return render(request, "dashboard/timetable_import.html", {"preview_data": None})
            try:
                raw_rows = timetable_io.parse_uploaded_timetable_file(uploaded_file)
                if not raw_rows:
                    messages.error(request, "The uploaded file does not contain any data rows.")
                    return render(request, "dashboard/timetable_import.html", {"preview_data": None})
                validation_result = timetable_io.validate_timetable_import_rows(raw_rows)
                request.session["pending_timetable_import"] = validation_result
                preview_data = validation_result
            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")
                return render(request, "dashboard/timetable_import.html", {"preview_data": None})

        elif action == "confirm":
            session_data = request.session.get("pending_timetable_import")
            if not session_data or not session_data.get("items"):
                messages.error(request, "No pending import session found. Please upload your file again.")
                return redirect("university:timetable_import")

            valid_items = [item for item in session_data["items"] if item["status"] == "valid"]
            duplicates_count = session_data.get("duplicate_count", 0)
            conflicts_count = session_data.get("conflict_count", 0)
            errors_count = session_data.get("error_count", 0)

            if not valid_items:
                messages.error(request, "There are no valid records to import.")
                return render(request, "dashboard/timetable_import.html", {"preview_data": session_data})

            publish_status = (ClassSchedule.Status.PUBLISHED if request.POST.get("publish") == "1"
                             else ClassSchedule.Status.DRAFT)
            imported_count, failed_count = timetable_io.execute_timetable_import(
                valid_items, status=publish_status)
            total_failed = errors_count + failed_count

            request.session.pop("pending_timetable_import", None)
            summary_msg = (f"Successfully imported: {imported_count} | Failed: {total_failed} | "
                          f"Duplicates: {duplicates_count} | Conflicts: {conflicts_count}")
            messages.success(request, summary_msg)
            return redirect("university:admin_timetable")

        elif action == "cancel":
            request.session.pop("pending_timetable_import", None)
            messages.info(request, "Timetable import was cancelled.")
            return redirect("university:timetable_import")

    elif "pending_timetable_import" in request.session:
        preview_data = request.session["pending_timetable_import"]

    return render(request, "dashboard/timetable_import.html", {
        "preview_data": preview_data, "sample_row": timetable_io.SAMPLE_TIMETABLE_ROW,
        "columns": timetable_io.TIMETABLE_IMPORT_COLUMNS,
    })


@role_required(Role.FACULTY)
def faculty_timetable(request):
    fp = get_object_or_404(FacultyProfile, user=request.user)
    term = _selected_term(request)
    schedules = ClassSchedule.objects.filter(
        course__faculty=fp, status=ClassSchedule.Status.PUBLISHED).select_related("course", "room")
    schedules = schedules.filter(term=term) if term else schedules.none()
    return render(request, "dashboard/faculty_timetable.html", {
        "grid": _timetable_grid(schedules), "term": term,
        "terms": AcademicTerm.objects.all(),
    })


@role_required(Role.STUDENT)
def student_timetable(request):
    sp = get_object_or_404(StudentProfile, user=request.user)
    term = _selected_term(request)
    course_ids = Enrollment.objects.filter(student=sp, status=Enrollment.ACTIVE
                                           ).values_list("course_id", flat=True)
    schedules = ClassSchedule.objects.filter(
        course_id__in=course_ids, status=ClassSchedule.Status.PUBLISHED).select_related(
        "course", "course__faculty__user", "room")
    schedules = schedules.filter(term=term) if term else schedules.none()
    return render(request, "dashboard/student_timetable.html", {
        "grid": _timetable_grid(schedules), "term": term,
        "terms": AcademicTerm.objects.all(),
    })


# ==========================================================================
# NOTICES & EVENTS  (notices are viewable by everyone)
# ==========================================================================
@login_required
def notices(request):
    from .control_services import active_messages, permitted
    from .models import MessageDelivery
    can_post = permitted(request.user, 'messages.create')
    if request.method == 'POST':
        from .control_views import message_edit
        return message_edit(request)
    entries = active_messages(request.user, include_archived=True)
    receipts = {d.message_id: d for d in MessageDelivery.objects.filter(recipient=request.user, method='IN_APP')}
    selected = request.GET.get('filter', 'unread')
    result = []
    for n in entries:
        d = receipts.get(n.pk)
        n.is_read = bool(d and d.read_at)
        n.is_archived = bool(d and d.archived_at)
        if selected == 'archived' and not n.is_archived: continue
        if selected != 'archived' and n.is_archived: continue
        if selected == 'unread' and n.is_read: continue
        if selected == 'read' and not n.is_read: continue
        if selected == 'important' and n.priority not in {'HIGH','CRITICAL'}: continue
        result.append(n)
    from django.core.paginator import Paginator
    return render(request, 'dashboard/notices.html', {'notices': Paginator(result,20).get_page(request.GET.get('page')), 'can_post': can_post, 'selected_filter': selected})


@login_required
@require_POST
def notice_delete(request, pk):
    from .control_views import message_action
    return message_action(request, pk, 'delete')


@login_required

def events(request):
    return render(request, "dashboard/events.html",
                  {"events": Event.objects.all(),
                   "can_manage": request.user.is_admin_role or request.user.is_superuser})


@role_required(Role.ADMIN)
def event_create(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Event created.")
        return redirect("university:events")
    return _render_form(request, form, "Add Event", "", "fa-calendar-plus",
                        "university:events")


@role_required(Role.ADMIN)
def event_edit(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    form = EventForm(request.POST or None, instance=ev)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Event updated.")
        return redirect("university:events")
    return _render_form(request, form, "Edit Event", ev.title, "fa-pen",
                        "university:events")


@role_required(Role.ADMIN)
def event_delete(request, pk):
    ev = get_object_or_404(Event, pk=pk)
    return _confirm_delete(request, ev, "event", "university:events")


# ==========================================================================
# FACULTY AREA — courses, attendance (day-wise), assignments, grading
# ==========================================================================
@role_required(Role.FACULTY)
def faculty_courses(request):
    fp = get_object_or_404(FacultyProfile, user=request.user)
    courses = Course.objects.filter(faculty=fp).annotate(n=Count("enrollments"))
    return render(request, "dashboard/faculty_courses.html", {"courses": courses})


@role_required(Role.FACULTY, Role.ADMIN)
def faculty_attendance(request, pk):
    """Mark / edit attendance for a chosen date (defaults to today)."""
    if request.user.is_admin_role or request.user.is_superuser:
        course = get_object_or_404(Course, pk=pk)
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        course = get_object_or_404(Course, pk=pk, faculty=fp)
    roster = Enrollment.objects.filter(course=course, status=Enrollment.ACTIVE
                                       ).select_related("student__user")
    sel = request.GET.get("date") or request.POST.get("date")
    try:
        day = date.fromisoformat(sel) if sel else timezone.now().date()
    except ValueError:
        day = timezone.now().date()
    if request.method == "POST":
        marked = 0
        for e in roster:
            status = request.POST.get(f"status_{e.id}")
            if status in dict(Attendance.STATUS):
                Attendance.objects.update_or_create(
                    enrollment=e, date=day, defaults={"status": status})
                marked += 1
        messages.success(request, f"Attendance saved for {marked} student(s) on {day}.")
        return redirect(f"{request.path}?date={day.isoformat()}")
    existing = {a.enrollment_id: a.status for a in
                Attendance.objects.filter(enrollment__in=roster, date=day)}
    return render(request, "dashboard/faculty_attendance.html", {
        "course": course, "roster": roster, "existing": existing, "today": day,
    })


@role_required(Role.FACULTY, Role.ADMIN)
def faculty_attendance_history(request, pk):
    """Day-wise attendance register: dates x students matrix."""
    if request.user.is_admin_role or request.user.is_superuser:
        course = get_object_or_404(Course, pk=pk)
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        course = get_object_or_404(Course, pk=pk, faculty=fp)
    roster = list(Enrollment.objects.filter(course=course, status=Enrollment.ACTIVE
                                            ).select_related("student__user"))
    records = Attendance.objects.filter(enrollment__course=course)
    dates = sorted({r.date for r in records}, reverse=True)
    lookup = {(r.enrollment_id, r.date): r.status for r in records}
    rows = []
    for e in roster:
        cells = [{"date": d, "status": lookup.get((e.id, d))} for d in dates]
        present = sum(1 for c in cells if c["status"] in (Attendance.PRESENT, Attendance.LATE))
        total = sum(1 for c in cells if c["status"])
        rows.append({"enrollment": e, "cells": cells,
                     "pct": round(present / total * 100, 1) if total else 0})
    return render(request, "dashboard/faculty_attendance_history.html", {
        "course": course, "dates": dates, "rows": rows,
    })


@role_required(Role.FACULTY, Role.ADMIN)
def faculty_assignments(request):
    if request.user.is_admin_role or request.user.is_superuser:
        assignments = Assignment.objects.select_related("course").annotate(
            n_sub=Count("submissions"))
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        assignments = Assignment.objects.filter(course__faculty=fp).select_related(
            "course").annotate(n_sub=Count("submissions"))
    return render(request, "dashboard/faculty_assignments.html", {"assignments": assignments})


@role_required(Role.FACULTY, Role.ADMIN)
def assignment_create(request):
    fp = None
    if not (request.user.is_admin_role or request.user.is_superuser):
        fp = get_object_or_404(FacultyProfile, user=request.user)
    form = AssignmentForm(request.POST or None, faculty=fp)
    if request.method == "POST" and form.is_valid():
        a = form.save()
        messages.success(request, f"Assignment '{a.title}' created.")
        return redirect("university:faculty_grade", pk=a.pk)
    return _render_form(request, form, "New Assignment", "", "fa-file-circle-plus",
                        "university:faculty_assignments")


@role_required(Role.FACULTY, Role.ADMIN)
def assignment_edit(request, pk):
    fp = None
    if request.user.is_admin_role or request.user.is_superuser:
        a = get_object_or_404(Assignment, pk=pk)
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        a = get_object_or_404(Assignment, pk=pk, course__faculty=fp)
    form = AssignmentForm(request.POST or None, instance=a, faculty=fp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Assignment updated.")
        return redirect("university:faculty_grade", pk=a.pk)
    return _render_form(request, form, "Edit Assignment", a.title, "fa-pen",
                        "university:faculty_assignments")


@role_required(Role.FACULTY, Role.ADMIN)
def assignment_delete(request, pk):
    if request.user.is_admin_role or request.user.is_superuser:
        a = get_object_or_404(Assignment, pk=pk)
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        a = get_object_or_404(Assignment, pk=pk, course__faculty=fp)
    return _confirm_delete(request, a, "assignment", "university:faculty_assignments")


@login_required
def assignment_detail(request, pk):
    assignment = get_object_or_404(Assignment.objects.select_related("course"), pk=pk)
    ctx = {"assignment": assignment}
    if request.user.is_student and hasattr(request.user, "student_profile"):
        sp = request.user.student_profile
        ctx["submission"] = Submission.objects.filter(assignment=assignment, student=sp).first()
        ctx["enrolled"] = Enrollment.objects.filter(student=sp, course=assignment.course).exists()
    else:
        ctx["submissions"] = Submission.objects.filter(
            assignment=assignment).select_related("student__user")
    return render(request, "dashboard/assignment_detail.html", ctx)


# Compatibility routes use the same scoped, audited examination workflow.
@role_required(Role.ADMIN, Role.FACULTY)
def exam_create(request):
    from .examination_views import edit
    return edit(request)


@role_required(Role.ADMIN, Role.FACULTY)
def exam_edit(request, pk):
    from .examination_views import edit
    return edit(request, pk)


@role_required(Role.ADMIN, Role.FACULTY)
def exam_delete(request, pk):
    from .examination_views import staff_exam
    staff_exam(request, pk)
    messages.info(request, "Use the examination overview to cancel a sitting. Examination records are retained for audit.")
    return redirect("examinations:detail", pk=pk)


@role_required(Role.FACULTY, Role.ADMIN)
def faculty_grade(request, pk):
    if request.user.is_admin_role or request.user.is_superuser:
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        fp = get_object_or_404(FacultyProfile, user=request.user)
        assignment = get_object_or_404(Assignment, pk=pk, course__faculty=fp)
    subs = Submission.objects.filter(assignment=assignment).select_related("student__user")
    if request.method == "POST":
        for s in subs:
            raw = request.POST.get(f"marks_{s.id}", "").strip()
            if raw:
                try:
                    s.marks = min(int(raw), assignment.max_marks)
                    s.feedback = request.POST.get(f"feedback_{s.id}", "")
                    s.status = Submission.GRADED
                    s.save()
                except ValueError:
                    pass
        messages.success(request, "Grades updated.")
        return redirect("university:faculty_grade", pk=assignment.pk)
    return render(request, "dashboard/faculty_grade.html",
                  {"assignment": assignment, "subs": subs})


# ==========================================================================
# STUDENT AREA
# ==========================================================================
@role_required(Role.STUDENT)
def student_courses(request):
    sp = get_object_or_404(StudentProfile, user=request.user)
    enrollments = Enrollment.objects.filter(student=sp).select_related(
        "course__department", "course__faculty__user")
    return render(request, "dashboard/student_courses.html", {"enrollments": enrollments})


@role_required(Role.STUDENT)
def student_attendance(request):
    sp = get_object_or_404(StudentProfile, user=request.user)
    rows = []
    for e in Enrollment.objects.filter(student=sp).select_related("course"):
        qs = Attendance.objects.filter(enrollment=e)
        t = qs.count()
        p = qs.filter(status__in=[Attendance.PRESENT, Attendance.LATE]).count()
        rows.append({"course": e.course, "total": t, "present": p,
                     "pct": round(p / t * 100, 1) if t else 0})
    log = Attendance.objects.filter(enrollment__student=sp).select_related(
        "enrollment__course").order_by("-date")[:60]
    return render(request, "dashboard/student_attendance.html", {"rows": rows, "log": log})


@role_required(Role.STUDENT)
def student_results(request):
    return redirect("examinations:statement")


@role_required(Role.STUDENT)
def student_assignments(request):
    sp = get_object_or_404(StudentProfile, user=request.user)
    course_ids = Enrollment.objects.filter(student=sp).values_list("course_id", flat=True)
    assignments = Assignment.objects.filter(course_id__in=course_ids).select_related("course")
    subs = {s.assignment_id: s for s in Submission.objects.filter(student=sp)}
    data = [{"assignment": a, "submission": subs.get(a.id)} for a in assignments]
    return render(request, "dashboard/student_assignments.html", {"data": data})


@role_required(Role.STUDENT)
@require_POST
def submit_assignment(request, pk):
    sp = get_object_or_404(StudentProfile, user=request.user)
    assignment = get_object_or_404(Assignment, pk=pk)
    content = request.POST.get("content", "").strip()
    Submission.objects.update_or_create(
        assignment=assignment, student=sp,
        defaults={"content": content, "submitted_on": timezone.now(),
                  "status": Submission.SUBMITTED})
    messages.success(request, f"Submitted '{assignment.title}'.")
    return redirect("university:student_assignments")


@role_required(Role.STUDENT)
def student_fees(request):
    sp = get_object_or_404(StudentProfile, user=request.user)

    if request.method == "POST":
        invoice_id = request.POST.get("invoice_id")
        amount_str = request.POST.get("amount", "0")
        method = request.POST.get("method", "M-Pesa")
        reference = request.POST.get("reference", "").strip() or f"MPESA-{timezone.now().strftime('%H%M%S')}"

        invoice = get_object_or_404(FeeInvoice, pk=invoice_id, student=sp)
        try:
            amt = Decimal(amount_str)
        except Exception:
            amt = Decimal("0.00")

        if amt > 0:
            amt = min(amt, invoice.balance)
            invoice.amount_paid += amt
            invoice.save()
            pmt = Payment.objects.create(invoice=invoice, amount=amt, method=method, reference=reference)
            messages.success(request, f"Payment of KES {amt:,.2f} recorded successfully (Ref: {reference}). Receipt REC-{pmt.id:06d} generated.")
        else:
            messages.error(request, "Please enter a valid payment amount.")
        return redirect("university:student_fees")

    invoices = FeeInvoice.objects.filter(student=sp).order_by("-issued_on")
    payments = Payment.objects.filter(invoice__student=sp).select_related("invoice").order_by("-paid_on")
    clearance = check_financial_clearance(sp)

    return render(request, "dashboard/student_fees.html", {
        "student": sp,
        "invoices": invoices,
        "payments": payments,
        "clearance": clearance,
        "stats": services.student_stats(sp),
    })


# ==========================================================================
# AI FEATURES (no external API key)
# ==========================================================================
@login_required
def ai_assistant(request):
    return render(request, "dashboard/ai_assistant.html",
                  {"suggestions": ai._default_suggestions(request.user)})


@login_required
@require_POST
def ai_reply(request):
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload = {}
    message = payload.get("message", "")
    return JsonResponse(ai.assistant_reply(message, request.user))


@login_required
def ai_insights(request):
    user = request.user
    if user.is_student and hasattr(user, "student_profile"):
        sp = user.student_profile
        return render(request, "dashboard/ai_insights_student.html", {
            "prediction": ai.student_prediction(sp),
            "recommendations": ai.study_recommendations(sp),
            "stats": services.student_stats(sp),
        })
    return render(request, "dashboard/ai_insights_staff.html", {
        "at_risk": ai.at_risk_students(12),
        "top_courses": services.popular_courses(5),
    })
