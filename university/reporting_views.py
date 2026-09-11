from university.document_views import present_pdf
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.urls import reverse

from university.models import (
    Department,
    Program,
    AcademicTerm,
    Course,
    SemesterRegistration,
    Exam,
    Result,
    AuditLog,
)
from accounts.models import StudentProfile, FacultyProfile
from university.audit_services import log_activity
from university.reporting_services import (
    REPORT_CATEGORIES,
    REPORT_REGISTRY,
    build_report_data,
    generate_report_pdf,
    generate_report_excel,
    generate_report_csv,
)


def _ensure_admin(user):
    return user.is_authenticated and (user.is_admin_role or user.is_superuser)


def _ensure_faculty(user):
    return user.is_authenticated and (user.is_faculty or user.is_admin_role or user.is_superuser)


# ==============================================================================
# 1. ADMIN REPORTING DASHBOARD
# ==============================================================================

@login_required
def admin_reports_dashboard(request):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted to academic administrators.")
        return redirect("university:dashboard")

    # Global KPI tallies for overview cards
    total_students = StudentProfile.objects.count()
    total_faculty = FacultyProfile.objects.count()
    total_programs = Program.objects.count()
    total_exams = Exam.objects.count()

    results = Result.objects.filter(marks_obtained__isnull=False)
    total_marks = results.count()
    pass_marks = results.filter(marks_obtained__gte=40).count()
    overall_pass_rate = round(pass_marks / (total_marks or 1) * 100, 1)

    # Category map
    cat_map = {c["id"]: c for c in REPORT_CATEGORIES}

    # Audience mapping for reports
    audience_map = {
        "student_register": "Registrar & Admissions",
        "student_demographics": "Institutional Planning",
        "students_by_program": "Deans & HODs",
        "senate_consolidated_sheet": "Senate & Academic Board",
        "academic_performance": "Senate & Deans",
        "grade_distribution": "Exam Board & Senate",
        "examination_results_summary": "Exam Board",
        "pass_fail_analysis": "Exam Board & Academic Affairs",
        "cat_vs_exam_analysis": "Exam Board & HODs",
        "missing_marks_audit": "Exam Board & HODs",
        "registration_summary": "Registrar & Deans",
        "unit_enrollment_counts": "Deans & HODs",
        "faculty_workload": "Deans & Academic Registrar",
        "faculty_directory": "Human Resources & Registrar",
        "timetable_by_program": "Timetable Committee & Students",
        "timetable_by_venue": "Estates & Timetable Committee",
        "user_activity_audit": "Internal Audit & IT Security",
        "recycle_bin_audit": "Compliance & Data Admin",
    }

    # Prepare enriched flat list of all reports
    reports_list = []
    for key, rep in REPORT_REGISTRY.items():
        cat_id = rep["category"]
        category_info = cat_map.get(cat_id, {
            "id": cat_id,
            "title": cat_id.capitalize(),
            "icon": "fa-file",
            "color": "#6C5CE7",
            "description": ""
        })
        rep_copy = dict(rep)
        rep_copy["category_info"] = category_info
        rep_copy["audience"] = audience_map.get(key, "General Administration")
        reports_list.append(rep_copy)

    # Group report registry by category for quick reference
    categorized_reports = []
    for cat in REPORT_CATEGORIES:
        reps = [r for r in reports_list if r["category"] == cat["id"]]
        categorized_reports.append({
            "category": cat,
            "reports": reps,
            "count": len(reps)
        })

    # Filter query parameters from GET request
    q = request.GET.get("q", "").strip().lower()
    selected_cat = request.GET.get("category", "").strip()
    selected_orientation = request.GET.get("orientation", "").strip()
    selected_audience = request.GET.get("audience", "").strip()
    sort_by = request.GET.get("sort", "default").strip()

    filtered_reports = list(reports_list)

    if selected_cat and selected_cat != "all":
        filtered_reports = [r for r in filtered_reports if r["category"] == selected_cat]

    if selected_orientation and selected_orientation != "all":
        filtered_reports = [r for r in filtered_reports if r.get("orientation") == selected_orientation]

    if selected_audience and selected_audience != "all":
        filtered_reports = [r for r in filtered_reports if selected_audience.lower() in r.get("audience", "").lower()]

    if q:
        filtered_reports = [
            r for r in filtered_reports
            if q in r["title"].lower()
            or q in r["description"].lower()
            or q in r["category_info"]["title"].lower()
            or q in r.get("audience", "").lower()
            or any(q in f.lower() for f in r.get("filters", []))
        ]

    # Sorting
    if sort_by == "name_asc":
        filtered_reports.sort(key=lambda r: r["title"])
    elif sort_by == "name_desc":
        filtered_reports.sort(key=lambda r: r["title"], reverse=True)
    elif sort_by == "category":
        filtered_reports.sort(key=lambda r: (r["category_info"]["title"], r["title"]))
    elif sort_by == "filters_count":
        filtered_reports.sort(key=lambda r: len(r.get("filters", [])), reverse=True)

    # Distinct audiences for filter dropdown
    distinct_audiences = sorted(list(set(audience_map.values())))

    # Recent report activity from AuditLog
    recent_reports = AuditLog.objects.filter(module="REPORTS").order_by("-timestamp")[:8]

    context = {
        "categories": REPORT_CATEGORIES,
        "categorized_reports": categorized_reports,
        "all_reports": reports_list,
        "filtered_reports": filtered_reports,
        "distinct_audiences": distinct_audiences,
        "total_reports_count": len(REPORT_REGISTRY),
        "total_students": total_students,
        "total_faculty": total_faculty,
        "total_programs": total_programs,
        "total_exams": total_exams,
        "overall_pass_rate": overall_pass_rate,
        "recent_reports": recent_reports,
        "selected_q": request.GET.get("q", ""),
        "selected_cat": selected_cat or "all",
        "selected_orientation": selected_orientation or "all",
        "selected_audience": selected_audience or "all",
        "selected_sort": sort_by,
    }
    return render(request, "reports/admin_reports_dashboard.html", context)


# ==============================================================================
# 2. WEB REPORT VIEWER WITH DYNAMIC FILTERS & PAGINATION
# ==============================================================================

@login_required
def report_view(request, report_key):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted to academic administrators.")
        return redirect("university:dashboard")

    if report_key not in REPORT_REGISTRY:
        raise Http404("Requested report does not exist.")

    report_meta = REPORT_REGISTRY[report_key]
    report_data = build_report_data(report_key, request.GET, user=request.user)
    if not report_data:
        raise Http404("Unable to generate report data.")

    # Server-side pagination
    all_rows = report_data.get("rows", [])
    per_page = int(request.GET.get("per_page", 25))
    paginator = Paginator(all_rows, per_page)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Log report generation activity
    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.EXPORT,
        module=AuditLog.Module.ACADEMICS,
        entity=report_meta["title"],
        description=f"Viewed report {report_meta['title']} with filters: {', '.join(report_data.get('applied_filters', [])) or 'None'}",
    )

    # Dropdowns for dynamic filter toolbar
    terms = AcademicTerm.objects.all().order_by("-start_date")
    departments = Department.objects.all().order_by("name")
    programs = Program.objects.all().order_by("name")
    courses = Course.objects.all().order_by("code")
    faculty_list = FacultyProfile.objects.select_related("user").all().order_by("user__first_name")

    context = {
        "report_key": report_key,
        "meta": report_meta,
        "report_data": report_data,
        "page_obj": page_obj,
        "total_rows_count": len(all_rows),
        "per_page": per_page,
        "terms": terms,
        "departments": departments,
        "programs": programs,
        "courses": courses,
        "faculty_list": faculty_list,
        "selected_term": request.GET.get("term", ""),
        "selected_department": request.GET.get("department", ""),
        "selected_program": request.GET.get("program", ""),
        "selected_course": request.GET.get("course", ""),
        "selected_faculty": request.GET.get("faculty", ""),
        "selected_semester": request.GET.get("semester", ""),
        "selected_gender": request.GET.get("gender", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "search_query": request.GET.get("q", ""),
    }
    return render(request, "reports/report_view.html", context)


# ==============================================================================
# 3. REPORT EXPORTS (PDF, EXCEL, CSV)
# ==============================================================================

@login_required
def export_report(request, report_key, fmt):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted to academic administrators.")
        return redirect("university:dashboard")

    if report_key not in REPORT_REGISTRY:
        raise Http404("Requested report does not exist.")

    fmt = fmt.lower()
    if fmt not in ("pdf", "excel", "csv"):
        raise Http404("Unsupported export format.")

    report_meta = REPORT_REGISTRY[report_key]
    report_data = build_report_data(report_key, request.GET, user=request.user)
    if not report_data:
        raise Http404("Unable to generate report data for export.")

    # Log export action
    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.EXPORT,
        module=AuditLog.Module.ACADEMICS,
        entity=report_meta["title"],
        description=f"Exported {report_meta['title']} ({fmt.upper()})",
    )

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M")
    base_filename = f"{report_key}_{timestamp_str}"

    if fmt == "pdf":
        pdf_bytes = generate_report_pdf(report_data)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{base_filename}.pdf"'
        return present_pdf(request, response)

    elif fmt == "excel":
        excel_bytes = generate_report_excel(report_data)
        response = HttpResponse(
            excel_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{base_filename}.xlsx"'
        return response

    elif fmt == "csv":
        csv_bytes = generate_report_csv(report_data)
        response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{base_filename}.csv"'
        return response


# ==============================================================================
# 4. FACULTY TEACHING REPORTS PORTAL
# ==============================================================================

@login_required
def faculty_reports(request):
    if not _ensure_faculty(request.user):
        messages.error(request, "Access restricted to faculty members.")
        return redirect("university:dashboard")

    faculty = getattr(request.user, "faculty_profile", None)
    if not faculty and not request.user.is_superuser:
        messages.error(request, "No faculty profile found.")
        return redirect("university:dashboard")

    # Courses assigned to this lecturer
    courses = Course.objects.filter(faculty=faculty) if faculty else Course.objects.all()[:10]
    total_students_taught = sum(c.enrolled_count for c in courses)

    context = {
        "faculty": faculty,
        "courses": courses,
        "total_courses": courses.count(),
        "total_students_taught": total_students_taught,
    }
    return render(request, "reports/faculty_reports.html", context)


# ==============================================================================
# 5. STUDENT ACADEMIC REPORTS HUB
# ==============================================================================

@login_required
def student_reports(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        messages.error(request, "Student profile required.")
        return redirect("university:dashboard")

    # Fetch student summary statistics
    results = Result.objects.filter(student=student, marks_obtained__isnull=False)
    passed_count = results.filter(marks_obtained__gte=40).count()
    failed_count = results.filter(marks_obtained__lt=40).count()

    # Registrations
    registrations = SemesterRegistration.objects.filter(student=student).order_by("-created_at")

    from university.admission_document_services import get_student_admission_documents
    doc_data = get_student_admission_documents(student)

    context = {
        "student": student,
        "results": results,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "registrations": registrations,
        "admission_letter": doc_data.get("admission_letter"),
        "attachments_count": len(doc_data.get("attachments", [])),
    }
    return render(request, "reports/student_reports.html", context)


# ==============================================================================
# 6. STUDENT ADMISSION DOCUMENTS & ATTACHMENTS
# ==============================================================================

@login_required
def student_admission_documents(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        messages.error(request, "Student profile required.")
        return redirect("university:dashboard")

    from university.admission_document_services import get_student_admission_documents
    doc_data = get_student_admission_documents(student)

    context = {
        "student": student,
        "application": doc_data["application"],
        "admission_letter": doc_data["admission_letter"],
        "historical_letters": doc_data["historical_letters"],
        "attachments": doc_data["attachments"],
        "delivery_logs": doc_data["delivery_logs"],
    }
    return render(request, "reports/student_admission_documents.html", context)


@login_required
def student_view_admission_document(request, doc_id):
    student = getattr(request.user, "student_profile", None)
    from university.models import IssuedAdmissionDocument
    doc = get_object_or_404(IssuedAdmissionDocument, pk=doc_id)

    is_owner = (
        (student and (doc.student == student or doc.application.student == student)) or
        (doc.application.email and doc.application.email.lower() == request.user.email.lower())
    )
    if not is_owner and not (request.user.is_admin_role or request.user.is_superuser):
        return HttpResponseForbidden("You are not authorized to view this document.")

    from university.admission_document_services import build_admission_letter_pdf_bytes
    if not doc.pdf_file:
        pdf_bytes = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_bytes = doc.pdf_file.read()
        except Exception:
            pdf_bytes = build_admission_letter_pdf_bytes(doc)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    response["Content-Disposition"] = f'inline; filename="{filename}"'

    return present_pdf(
        request,
        response,
        title=f"Admission Letter - {doc.document_reference}"
    )


@login_required
def student_download_admission_document(request, doc_id):
    student = getattr(request.user, "student_profile", None)
    from university.models import IssuedAdmissionDocument
    doc = get_object_or_404(IssuedAdmissionDocument, pk=doc_id)

    is_owner = (
        (student and (doc.student == student or doc.application.student == student)) or
        (doc.application.email and doc.application.email.lower() == request.user.email.lower())
    )
    if not is_owner and not (request.user.is_admin_role or request.user.is_superuser):
        return HttpResponseForbidden("You are not authorized to download this document.")

    from university.admission_document_services import build_admission_letter_pdf_bytes
    if not doc.pdf_file:
        pdf_bytes = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_bytes = doc.pdf_file.read()
        except Exception:
            pdf_bytes = build_admission_letter_pdf_bytes(doc)

    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def student_download_attachment(request, attachment_id):
    student = getattr(request.user, "student_profile", None)
    from university.models import ApplicationAttachment
    att = get_object_or_404(ApplicationAttachment, pk=attachment_id)

    is_owner = (
        (student and (att.application.student == student)) or
        (att.application.email and att.application.email.lower() == request.user.email.lower())
    )
    if not is_owner and not (request.user.is_admin_role or request.user.is_superuser):
        return HttpResponseForbidden("You are not authorized to access this document attachment.")

    if not att.is_visible_to_student and not (request.user.is_admin_role or request.user.is_superuser):
        return HttpResponseForbidden("This document attachment is currently restricted.")

    if not att.file or not os.path.exists(att.file.path):
        raise Http404("Document file does not exist on storage.")

    import mimetypes
    from django.http import FileResponse
    mime = att.mime_type or mimetypes.guess_type(att.file_name)[0] or "application/octet-stream"
    response = FileResponse(open(att.file.path, "rb"), content_type=mime)
    response["Content-Disposition"] = f'attachment; filename="{att.file_name or os.path.basename(att.file.name)}"'
    return response

