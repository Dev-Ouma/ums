from xml.sax.saxutils import escape
import csv
import io
from datetime import datetime, date
from decimal import Decimal

from django.db.models import Count, Avg, Max, Min, Q, F, Sum
from django.utils import timezone
from django.http import HttpResponse

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet, sanitize_csv_value)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from university.models import (
    Department,
    Program,
    AcademicTerm,
    Course,
    SemesterRegistration,
    Enrollment,
    Attendance,
    Exam,
    Result,
    FeeInvoice,
    Payment,
    AuditLog,
    RecycleBinItem,
    ClassSchedule,
    GradingScale,
    Application,
    ApplicationAttachment,
    IssuedAdmissionDocument,
    DocumentDeliveryLog,
)
from accounts.models import User, StudentProfile, FacultyProfile
from university.audit_services import log_activity


# ==============================================================================
# 1. REPORT REGISTRY & METADATA
# ==============================================================================

REPORT_CATEGORIES = [
    {
        "id": "student",
        "title": "Student Reports",
        "icon": "fa-user-graduate",
        "color": "#2563eb",
        "bg_soft": "rgba(37, 99, 235, 0.08)",
        "description": "Student population, demographic breakdowns, registers, and status distributions."
    },
    {
        "id": "academic",
        "title": "Academic & Senate Reports",
        "icon": "fa-scroll",
        "color": "#0f766e",
        "bg_soft": "rgba(15, 118, 110, 0.08)",
        "description": "Senate consolidated marksheets, academic performance, GPA distributions, and progression."
    },
    {
        "id": "examination",
        "title": "Examination Reports",
        "icon": "fa-file-pen",
        "color": "#4338ca",
        "bg_soft": "rgba(67, 56, 202, 0.08)",
        "description": "CAT vs Exam analyses, pass/fail statistics, missing marks audits, and nominal rolls."
    },
    {
        "id": "registration",
        "title": "Registration Reports",
        "icon": "fa-clipboard-check",
        "color": "#0369a1",
        "bg_soft": "rgba(3, 105, 161, 0.08)",
        "description": "Unit enrollment demand, semester registration counts, and registered vs unregistered cohorts."
    },
    {
        "id": "faculty",
        "title": "Teaching Staff Reports",
        "icon": "fa-chalkboard-user",
        "color": "#0d9488",
        "bg_soft": "rgba(13, 148, 136, 0.08)",
        "description": "Teaching staff workloads, course assignments, and departmental directories.",
    },
    {
        "id": "timetable",
        "title": "Timetable & Venue Reports",
        "icon": "fa-calendar-week",
        "color": "#6366f1",
        "bg_soft": "rgba(99, 102, 241, 0.08)",
        "description": "Master program schedules, venue utilization rates, and lecturer timetables."
    },
    {
        "id": "administrative",
        "title": "Administrative & Compliance",
        "icon": "fa-shield-halved",
        "color": "#475569",
        "bg_soft": "rgba(71, 85, 105, 0.08)",
        "description": "User activity ledgers, audit trail forensic logs, and recycle bin deletion recovery logs."
    },
    {
        "id": "admissions",
        "title": "Admissions & Documents",
        "icon": "fa-folder-tree",
        "color": "#1e3a8a",
        "bg_soft": "rgba(30, 58, 138, 0.08)",
        "description": "Admission letter issuance, document delivery tracking, and application attachment verification."
    },
]

REPORT_REGISTRY = {
    # --- Admissions & Document Reports ---
    "admission_documents_status": {
        "key": "admission_documents_status",
        "category": "admissions",
        "title": "Admission Documents & Letter Issuance Status",
        "description": "Comprehensive audit of issued admission letters, pending letters, version controls, and transmission delivery logs.",
        "icon": "fa-file-invoice",
        "orientation": "landscape",
        "filters": ["program", "status", "q"]
    },
    "application_attachments_audit": {
        "key": "application_attachments_audit",
        "category": "admissions",
        "title": "Application Attachments & Verification Audit",
        "description": "Inspection register of applicant-submitted credentials (KCSE slips, National IDs, photos) and verification states.",
        "icon": "fa-paperclip",
        "orientation": "landscape",
        "filters": ["program", "status", "q"]
    },

    # --- Student Reports ---
    "student_register": {
        "key": "student_register",
        "category": "student",
        "title": "Student Register & Cohort Directory",
        "description": "Complete nominal register of active and admitted students with programme and semester details.",
        "icon": "fa-address-book",
        "orientation": "landscape",
        "filters": ["department", "program", "semester", "gender", "status", "q"]
    },
    "student_demographics": {
        "key": "student_demographics",
        "category": "student",
        "title": "Student Demographics & Diversity Report",
        "description": "Statistical summary of student population categorized by gender, department, and year of study.",
        "icon": "fa-chart-pie",
        "orientation": "portrait",
        "filters": ["department", "program", "gender"]
    },
    "students_by_program": {
        "key": "students_by_program",
        "category": "student",
        "title": "Programme Enrollment & Capacity Report",
        "description": "Distribution of student enrollment across academic programmes and study levels.",
        "icon": "fa-graduation-cap",
        "orientation": "portrait",
        "filters": ["department", "program"]
    },

    # --- Academic & Senate Reports ---
    "senate_consolidated_sheet": {
        "key": "senate_consolidated_sheet",
        "category": "academic",
        "title": "Senate Consolidated Marksheet",
        "description": "Official master examination board marksheet compiling CAT, Final Exam, Total, and Senate recommendation per candidate.",
        "icon": "fa-table-list",
        "orientation": "landscape",
        "filters": ["term", "program", "semester", "course"]
    },
    "academic_performance": {
        "key": "academic_performance",
        "category": "academic",
        "title": "Academic Performance & GPA Summary",
        "description": "Comparative evaluation of mean scores, highest/lowest marks, and performance across courses.",
        "icon": "fa-award",
        "orientation": "landscape",
        "filters": ["term", "department", "program", "course"]
    },
    "grade_distribution": {
        "key": "grade_distribution",
        "category": "academic",
        "title": "Grade Distribution & Bell Curve Analysis",
        "description": "Statistical count and percentage distribution of grades (A, B, C, D, F) awarded in examinations.",
        "icon": "fa-chart-column",
        "orientation": "portrait",
        "filters": ["term", "department", "program", "course"]
    },

    # --- Examination Reports ---
    "examination_results_summary": {
        "key": "examination_results_summary",
        "category": "examination",
        "title": "Examination Results & Pass/Fail Analysis",
        "description": "Comprehensive pass/fail analysis with failure rates, sitting counts, and absent candidates.",
        "icon": "fa-file-invoice",
        "orientation": "landscape",
        "filters": ["term", "department", "program", "course"]
    },
    "pass_fail_analysis": {
        "key": "pass_fail_analysis",
        "category": "examination",
        "title": "Examination Pass / Fail & Grade Risk Analysis",
        "description": "Comprehensive pass/fail analysis with candidate sitting counts, failure rates, and performance breakdown.",
        "icon": "fa-chart-pie",
        "orientation": "landscape",
        "filters": ["term", "department", "program", "course"]
    },
    "cat_vs_exam_analysis": {
        "key": "cat_vs_exam_analysis",
        "category": "examination",
        "title": "Continuous Assessment (CAT) vs Exam Analysis",
        "description": "Correlation and variance analysis between coursework assessments (30%) and final examinations (70%).",
        "icon": "fa-scale-balanced",
        "orientation": "landscape",
        "filters": ["term", "department", "course"]
    },
    "missing_marks_audit": {
        "key": "missing_marks_audit",
        "category": "examination",
        "title": "Missing Marks & Pending Submission Audit",
        "description": "Forensic exception report identifying registered students lacking submitted or approved examination marks.",
        "icon": "fa-triangle-exclamation",
        "orientation": "landscape",
        "filters": ["term", "department", "program", "course"]
    },

    # --- Registration Reports ---
    "registration_summary": {
        "key": "registration_summary",
        "category": "registration",
        "title": "Semester Registration & Cohort Summary",
        "description": "Audited tallies of registered, pending approval, and unregistered students for the current academic session.",
        "icon": "fa-clipboard-user",
        "orientation": "portrait",
        "filters": ["term", "department", "program", "semester"]
    },
    "unit_enrollment_counts": {
        "key": "unit_enrollment_counts",
        "category": "registration",
        "title": "Course Unit Enrollment Demand Report",
        "description": "Headcount of students enrolled in each academic course unit across faculties.",
        "icon": "fa-book-open",
        "orientation": "portrait",
        "filters": ["term", "department", "program"]
    },

    # --- Faculty / Teaching Staff Reports ---
    "faculty_workload": {
        "key": "faculty_workload",
        "category": "faculty",
        "title": "Teaching Staff Workload & Contact Hours",
        "description": "Audit of teaching assignments, contact hours per week, and student load per academic teaching staff member.",
        "icon": "fa-briefcase",
        "orientation": "landscape",
        "filters": ["department", "faculty"]
    },
    "faculty_directory": {
        "key": "faculty_directory",
        "category": "faculty",
        "title": "Teaching Staff Directory",
        "description": "Full teaching staff registry categorized by department, academic rank, specialization, and appointment date.",
        "icon": "fa-id-badge",
        "orientation": "landscape",
        "filters": ["department"]
    },

    # --- Timetable Reports ---
    "timetable_by_program": {
        "key": "timetable_by_program",
        "category": "timetable",
        "title": "Programme Master Timetable Schedule",
        "description": "Weekly course lecture schedule per programme and level of study with lecture venues.",
        "icon": "fa-calendar-days",
        "orientation": "landscape",
        "filters": ["department", "program", "semester"]
    },
    "timetable_by_venue": {
        "key": "timetable_by_venue",
        "category": "timetable",
        "title": "Lecture Venue Utilization & Room Booking Audit",
        "description": "Occupancy hours and weekly schedule per lecture theatre, seminar room, and computing laboratory.",
        "icon": "fa-building-columns",
        "orientation": "landscape",
        "filters": ["term"]
    },

    # --- Administrative Reports ---
    "user_activity_audit": {
        "key": "user_activity_audit",
        "category": "administrative",
        "title": "System Audit Trail & Security Activity Ledger",
        "description": "Immutable log of user operations, state changes, security actions, and administrative transactions.",
        "icon": "fa-clock-rotate-left",
        "orientation": "landscape",
        "filters": ["date_from", "date_to", "q"]
    },
    "recycle_bin_audit": {
        "key": "recycle_bin_audit",
        "category": "administrative",
        "title": "Recycle Bin & Data Retention Audit",
        "description": "Ledger of soft-deleted records, actor metadata, client IP addresses, and protected status.",
        "icon": "fa-trash-can-arrow-up",
        "orientation": "landscape",
        "filters": ["date_from", "date_to", "q"]
    }
}


# ==============================================================================
# 2. QUERY BUILDERS & DATA AGGREGATION
# ==============================================================================

def build_report_data(report_key, params, user=None):
    """
    Executes live database queries and formats the result dataset,
    KPI summary cards, and chart distribution for the specified report.
    """
    meta = REPORT_REGISTRY.get(report_key)
    if not meta:
        return None

    handler_map = {
        "student_register": _query_student_register,
        "student_demographics": _query_student_demographics,
        "students_by_program": _query_students_by_program,
        "senate_consolidated_sheet": _query_senate_consolidated_sheet,
        "academic_performance": _query_academic_performance,
        "grade_distribution": _query_grade_distribution,
        "examination_results_summary": _query_examination_results_summary,
        "pass_fail_analysis": _query_examination_results_summary,
        "cat_vs_exam_analysis": _query_cat_vs_exam_analysis,
        "missing_marks_audit": _query_missing_marks_audit,
        "registration_summary": _query_registration_summary,
        "unit_enrollment_counts": _query_unit_enrollment_counts,
        "faculty_workload": _query_faculty_workload,
        "faculty_directory": _query_faculty_directory,
        "timetable_by_program": _query_timetable_by_program,
        "timetable_by_venue": _query_timetable_by_venue,
        "user_activity_audit": _query_user_activity_audit,
        "recycle_bin_audit": _query_recycle_bin_audit,
        "admission_documents_status": _query_admission_documents_status,
        "application_attachments_audit": _query_application_attachments_audit,
    }

    handler = handler_map.get(report_key)
    if not handler:
        return None

    data = handler(params, user)
    data["key"] = report_key
    data["meta"] = meta
    data["generated_at"] = timezone.now()
    data["generated_by"] = getattr(user, "display_name", "System Administrator") if user else "System Administrator"
    return data


# ---------- Sub-queries ----------

def _query_student_register(params, user):
    qs = StudentProfile.objects.select_related("user", "program", "program__department").all()

    applied_filters = []
    if params.get("department"):
        qs = qs.filter(program__department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    if params.get("program"):
        qs = qs.filter(program_id=params["program"])
        prog = Program.objects.filter(pk=params["program"]).first()
        if prog: applied_filters.append(f"Programme: {prog.name}")

    if params.get("semester"):
        qs = qs.filter(current_semester=params["semester"])
        applied_filters.append(f"Semester: {params['semester']}")

    if params.get("gender"):
        qs = qs.filter(gender=params["gender"])
        applied_filters.append(f"Gender: {dict(StudentProfile.GENDER).get(params['gender'], params['gender'])}")

    if params.get("q"):
        q = params["q"].strip()
        qs = qs.filter(Q(roll_no__icontains=q) | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(user__email__icontains=q))
        applied_filters.append(f"Search: '{q}'")

    total_count = qs.count()
    male_count = qs.filter(gender="M").count()
    female_count = qs.filter(gender="F").count()

    columns = ["#", "Roll No", "Student Name", "Gender", "Programme", "Dept", "Sem", "Year of Study", "Email"]
    rows = []
    for idx, s in enumerate(qs, start=1):
        rows.append([
            str(idx),
            s.roll_no,
            s.user.display_name,
            s.get_gender_display(),
            s.program.code if s.program else "—",
            s.program.department.code if s.program and s.program.department else "—",
            f"Sem {s.current_semester}",
            f"Year {s.year_of_study}",
            s.user.email
        ])

    kpis = [
        {"label": "Total Students", "val": f"{total_count:,}", "icon": "fa-users", "color": "#6C5CE7"},
        {"label": "Male Students", "val": f"{male_count:,}", "icon": "fa-person", "color": "#0984e3"},
        {"label": "Female Students", "val": f"{female_count:,}", "icon": "fa-person-dress", "color": "#e84393"},
        {"label": "Programmes", "val": f"{qs.values('program').distinct().count():,}", "icon": "fa-graduation-cap", "color": "#00b894"},
    ]

    return {
        "title": "Student Register & Cohort Directory",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_student_demographics(params, user):
    qs = StudentProfile.objects.select_related("program", "program__department").all()
    applied_filters = []

    if params.get("department"):
        qs = qs.filter(program__department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    if params.get("program"):
        qs = qs.filter(program_id=params["program"])
        prog = Program.objects.filter(pk=params["program"]).first()
        if prog: applied_filters.append(f"Programme: {prog.name}")

    total = qs.count()
    dept_breakdown = qs.values("program__department__name").annotate(count=Count("id")).order_by("-count")

    columns = ["Department / Faculty", "Male", "Female", "Other", "Total Students", "Percentage (%)"]
    rows = []
    for item in dept_breakdown:
        dname = item["program__department__name"] or "Unassigned"
        sub_qs = qs.filter(program__department__name=item["program__department__name"])
        m = sub_qs.filter(gender="M").count()
        f = sub_qs.filter(gender="F").count()
        o = sub_qs.filter(gender="O").count()
        t = sub_qs.count()
        pct = round(t / total * 100, 1) if total else 0.0
        rows.append([dname, str(m), str(f), str(o), str(t), f"{pct}%"])

    kpis = [
        {"label": "Total Students", "val": f"{total:,}", "icon": "fa-users", "color": "#6C5CE7"},
        {"label": "Male Ratio", "val": f"{round(qs.filter(gender='M').count() / (total or 1) * 100, 1)}%", "icon": "fa-mars", "color": "#0984e3"},
        {"label": "Female Ratio", "val": f"{round(qs.filter(gender='F').count() / (total or 1) * 100, 1)}%", "icon": "fa-venus", "color": "#e84393"},
        {"label": "Departments", "val": f"{dept_breakdown.count():,}", "icon": "fa-building-columns", "color": "#00b894"},
    ]

    return {
        "title": "Student Demographics & Diversity Report",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_students_by_program(params, user):
    qs = Program.objects.select_related("department").annotate(
        student_count=Count("students"),
        male_count=Count("students", filter=Q(students__gender="M")),
        female_count=Count("students", filter=Q(students__gender="F"))
    ).order_by("-student_count")

    applied_filters = []
    if params.get("department"):
        qs = qs.filter(department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    columns = ["Programme Code", "Programme Title", "Department", "Male", "Female", "Enrolled", "Status"]
    rows = []
    total_enrolled = 0
    for p in qs:
        total_enrolled += p.student_count
        rows.append([
            p.code,
            p.name,
            p.department.name if p.department else "—",
            str(p.male_count),
            str(p.female_count),
            str(p.student_count),
            "Active"
        ])

    kpis = [
        {"label": "Total Programmes", "val": f"{qs.count():,}", "icon": "fa-graduation-cap", "color": "#6C5CE7"},
        {"label": "Total Enrolled", "val": f"{total_enrolled:,}", "icon": "fa-users", "color": "#00b894"},
        {"label": "Avg per Programme", "val": f"{round(total_enrolled / (qs.count() or 1), 1):,}", "icon": "fa-chart-line", "color": "#e84393"},
    ]

    return {
        "title": "Programme Enrollment & Capacity Report",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_senate_consolidated_sheet(params, user):
    """
    Senate master marksheet: lists each student with course marks, total passed, failed,
    weighted average, and Senate recommendation.
    """
    applied_filters = []
    results_qs = Result.objects.select_related("student", "student__user", "student__program", "exam", "exam__course").all()

    if params.get("term"):
        results_qs = results_qs.filter(exam__term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("program"):
        results_qs = results_qs.filter(student__program_id=params["program"])
        prog = Program.objects.filter(pk=params["program"]).first()
        if prog: applied_filters.append(f"Programme: {prog.name}")

    if params.get("course"):
        results_qs = results_qs.filter(exam__course_id=params["course"])
        c = Course.objects.filter(pk=params["course"]).first()
        if c: applied_filters.append(f"Course: {c.code} - {c.title}")

    students_map = {}
    for r in results_qs:
        sid = r.student_id
        if sid not in students_map:
            students_map[sid] = {
                "student": r.student,
                "results": [],
                "total_marks": Decimal(0),
                "count": 0,
                "passed": 0,
                "failed": 0
            }
        students_map[sid]["results"].append(r)
        if r.marks_obtained is not None:
            students_map[sid]["total_marks"] += r.marks_obtained
            students_map[sid]["count"] += 1
            if r.outcome == "Pass":
                students_map[sid]["passed"] += 1
            else:
                students_map[sid]["failed"] += 1

    columns = ["#", "Roll No", "Student Name", "Programme", "Units Taken", "Passed", "Failed", "Mean (%)", "Senate Recommendation"]
    rows = []
    pass_count = 0
    supp_count = 0
    repeat_count = 0

    for idx, (sid, data) in enumerate(students_map.items(), start=1):
        s = data["student"]
        count = data["count"]
        mean_score = round(float(data["total_marks"]) / count, 1) if count else 0.0
        failed = data["failed"]

        if failed == 0:
            rec = "PASS (Proceed to Next Level)"
            pass_count += 1
        elif failed <= 3:
            rec = f"SUPPLEMENTARY in {failed} Unit(s)"
            supp_count += 1
        else:
            rec = "REPEAT YEAR / PROBATION"
            repeat_count += 1

        rows.append([
            str(idx),
            s.roll_no,
            s.user.display_name,
            s.program.code if s.program else "—",
            str(count),
            str(data["passed"]),
            str(failed),
            f"{mean_score}%",
            rec
        ])

    kpis = [
        {"label": "Candidates Examined", "val": f"{len(students_map):,}", "icon": "fa-users", "color": "#6C5CE7"},
        {"label": "Pass & Proceed", "val": f"{pass_count:,}", "icon": "fa-circle-check", "color": "#00b894"},
        {"label": "Supplementary", "val": f"{supp_count:,}", "icon": "fa-arrows-rotate", "color": "#f0932b"},
        {"label": "Repeat / Probation", "val": f"{repeat_count:,}", "icon": "fa-circle-xmark", "color": "#e84393"},
    ]

    return {
        "title": "Senate Consolidated Marksheet",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_academic_performance(params, user):
    qs = Result.objects.select_related("exam__course", "exam__term", "student__program").filter(marks_obtained__isnull=False)
    applied_filters = []

    if params.get("term"):
        qs = qs.filter(exam__term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("department"):
        qs = qs.filter(exam__course__department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    course_stats = qs.values(
        "exam__course__code", "exam__course__title", "exam__course__department__name"
    ).annotate(
        c_count=Count("id"),
        avg_score=Avg("marks_obtained"),
        min_score=Min("marks_obtained"),
        max_score=Max("marks_obtained"),
        pass_count=Count("id", filter=Q(marks_obtained__gte=40))
    ).order_by("-c_count")

    columns = ["Course Code", "Course Title", "Department", "Enrolled", "Mean Mark", "Min", "Max", "Pass Rate (%)"]
    rows = []
    total_candidates = 0
    overall_sum = Decimal(0)

    for item in course_stats:
        cnt = item["c_count"]
        total_candidates += cnt
        avg = round(float(item["avg_score"]), 1) if item["avg_score"] else 0.0
        overall_sum += item["avg_score"] * cnt if item["avg_score"] else Decimal(0)
        p_rate = round(item["pass_count"] / cnt * 100, 1) if cnt else 0.0
        rows.append([
            item["exam__course__code"],
            item["exam__course__title"],
            item["exam__course__department__name"] or "—",
            str(cnt),
            f"{avg}",
            str(item["min_score"]),
            str(item["max_score"]),
            f"{p_rate}%"
        ])

    overall_avg = round(float(overall_sum) / (total_candidates or 1), 1)

    kpis = [
        {"label": "Courses Examined", "val": f"{course_stats.count():,}", "icon": "fa-book", "color": "#6C5CE7"},
        {"label": "Total Marks Logged", "val": f"{total_candidates:,}", "icon": "fa-clipboard-check", "color": "#0984e3"},
        {"label": "Overall Mean Score", "val": f"{overall_avg}%", "icon": "fa-chart-line", "color": "#00b894"},
    ]

    return {
        "title": "Academic Performance & GPA Summary",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_grade_distribution(params, user):
    qs = Result.objects.select_related("exam__course", "exam__term").filter(marks_obtained__isnull=False)
    applied_filters = []

    if params.get("term"):
        qs = qs.filter(exam__term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("course"):
        qs = qs.filter(exam__course_id=params["course"])
        c = Course.objects.filter(pk=params["course"]).first()
        if c: applied_filters.append(f"Course: {c.code} - {c.title}")

    total = qs.count()
    grade_a = qs.filter(marks_obtained__gte=70).count()
    grade_b = qs.filter(marks_obtained__gte=60, marks_obtained__lt=70).count()
    grade_c = qs.filter(marks_obtained__gte=50, marks_obtained__lt=60).count()
    grade_d = qs.filter(marks_obtained__gte=40, marks_obtained__lt=50).count()
    grade_f = qs.filter(marks_obtained__lt=40).count()

    columns = ["Grade Band", "Score Range (%)", "Number of Candidates", "Proportion (%)", "Academic Standing"]
    rows = [
        ["Grade A", "70% – 100%", str(grade_a), f"{round(grade_a / (total or 1) * 100, 1)}%", "Distinction / Excellent"],
        ["Grade B", "60% – 69%", str(grade_b), f"{round(grade_b / (total or 1) * 100, 1)}%", "Credit / Very Good"],
        ["Grade C", "50% – 59%", str(grade_c), f"{round(grade_c / (total or 1) * 100, 1)}%", "Good / Satisfactory"],
        ["Grade D", "40% – 49%", str(grade_d), f"{round(grade_d / (total or 1) * 100, 1)}%", "Pass / Minimum Pass"],
        ["Grade F", "0% – 39%", str(grade_f), f"{round(grade_f / (total or 1) * 100, 1)}%", "Fail / Supplementary Required"],
    ]

    pass_total = grade_a + grade_b + grade_c + grade_d
    pass_pct = round(pass_total / (total or 1) * 100, 1)

    kpis = [
        {"label": "Total Candidates", "val": f"{total:,}", "icon": "fa-users", "color": "#6C5CE7"},
        {"label": "Passed (A–D)", "val": f"{pass_total:,}", "icon": "fa-circle-check", "color": "#00b894"},
        {"label": "Failed (F)", "val": f"{grade_f:,}", "icon": "fa-circle-xmark", "color": "#e84393"},
        {"label": "Overall Pass Rate", "val": f"{pass_pct}%", "icon": "fa-percent", "color": "#0984e3"},
    ]

    return {
        "title": "Grade Distribution & Bell Curve Analysis",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_examination_results_summary(params, user):
    qs = Exam.objects.select_related("course", "term", "course__department").annotate(
        total_candidates=Count("results"),
        present_candidates=Count("results", filter=Q(results__attendance="PRESENT")),
        absent_candidates=Count("results", filter=Q(results__attendance="ABSENT")),
        passed_candidates=Count("results", filter=Q(results__marks_obtained__gte=40)),
        failed_candidates=Count("results", filter=Q(results__marks_obtained__lt=40, results__attendance="PRESENT"))
    ).order_by("-date")

    applied_filters = []
    if params.get("term"):
        qs = qs.filter(term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("department"):
        qs = qs.filter(course__department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    columns = ["Exam Title", "Course", "Date", "Registered", "Sat Exam", "Passed", "Failed", "Absent", "Pass %"]
    rows = []
    tot_reg = 0
    tot_pass = 0

    for e in qs:
        tot_reg += e.total_candidates
        tot_pass += e.passed_candidates
        sat = e.present_candidates
        pass_pct = round(e.passed_candidates / (sat or 1) * 100, 1) if sat else 0.0
        rows.append([
            e.name,
            e.course.code,
            e.date.strftime("%d %b %Y") if e.date else "—",
            str(e.total_candidates),
            str(sat),
            str(e.passed_candidates),
            str(e.failed_candidates),
            str(e.absent_candidates),
            f"{pass_pct}%"
        ])

    kpis = [
        {"label": "Examinations Scheduled", "val": f"{qs.count():,}", "icon": "fa-file-pen", "color": "#6C5CE7"},
        {"label": "Total Registrations", "val": f"{tot_reg:,}", "icon": "fa-users", "color": "#0984e3"},
        {"label": "Passed Results", "val": f"{tot_pass:,}", "icon": "fa-circle-check", "color": "#00b894"},
    ]

    return {
        "title": "Examination Results & Pass/Fail Analysis",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_cat_vs_exam_analysis(params, user):
    qs = Result.objects.select_related("exam", "exam__course", "student", "student__user").filter(
        cat_marks__isnull=False, exam_marks__isnull=False
    )
    applied_filters = []

    if params.get("term"):
        qs = qs.filter(exam__term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("course"):
        qs = qs.filter(exam__course_id=params["course"])
        c = Course.objects.filter(pk=params["course"]).first()
        if c: applied_filters.append(f"Course: {c.code} - {c.title}")

    columns = ["Roll No", "Student Name", "Course", "CAT Mark (30)", "Final Exam (70)", "Total (100)", "Variance", "Outcome"]
    rows = []

    cat_sum = Decimal(0)
    exam_sum = Decimal(0)
    count = qs.count()

    for r in qs[:200]:
        cat = r.cat_marks or Decimal(0)
        ex = r.exam_marks or Decimal(0)
        tot = cat + ex
        cat_sum += cat
        exam_sum += ex

        cat_norm = float(cat) / 30.0 * 100.0
        ex_norm = float(ex) / 70.0 * 100.0
        variance = round(ex_norm - cat_norm, 1)
        var_str = f"+{variance}%" if variance > 0 else f"{variance}%"

        rows.append([
            r.student.roll_no,
            r.student.user.display_name,
            r.exam.course.code,
            str(cat),
            str(ex),
            str(tot),
            var_str,
            r.outcome
        ])

    avg_cat = round(float(cat_sum) / (count or 1), 1)
    avg_exam = round(float(exam_sum) / (count or 1), 1)

    kpis = [
        {"label": "Candidates Analyzed", "val": f"{count:,}", "icon": "fa-users", "color": "#6C5CE7"},
        {"label": "Avg CAT (Max 30)", "val": f"{avg_cat}", "icon": "fa-pen-clip", "color": "#0984e3"},
        {"label": "Avg Exam (Max 70)", "val": f"{avg_exam}", "icon": "fa-file-lines", "color": "#00b894"},
    ]

    return {
        "title": "Continuous Assessment (CAT) vs Exam Analysis",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_missing_marks_audit(params, user):
    qs = Result.objects.select_related("exam", "exam__course", "student", "student__user").filter(
        Q(marks_obtained__isnull=True) | Q(attendance="PENDING")
    )
    applied_filters = []

    if params.get("term"):
        qs = qs.filter(exam__term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("course"):
        qs = qs.filter(exam__course_id=params["course"])
        c = Course.objects.filter(pk=params["course"]).first()
        if c: applied_filters.append(f"Course: {c.code}")

    columns = ["Roll No", "Student Name", "Course Code", "Exam Name", "Date", "Status", "Issue Flag"]
    rows = []

    for r in qs:
        issue = "Marks Not Submitted" if r.marks_obtained is None else "Attendance Not Verified"
        rows.append([
            r.student.roll_no,
            r.student.user.display_name,
            r.exam.course.code,
            r.exam.name,
            r.exam.date.strftime("%d %b %Y") if r.exam.date else "—",
            r.attendance,
            issue
        ])

    kpis = [
        {"label": "Missing Marks Exceptions", "val": f"{qs.count():,}", "icon": "fa-triangle-exclamation", "color": "#e84393"},
        {"label": "Courses Affected", "val": f"{qs.values('exam__course').distinct().count():,}", "icon": "fa-book", "color": "#f0932b"},
    ]

    return {
        "title": "Missing Marks & Pending Submission Audit",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_registration_summary(params, user):
    qs = SemesterRegistration.objects.select_related("student", "student__user", "student__program", "term").all()
    applied_filters = []

    if params.get("term"):
        qs = qs.filter(term_id=params["term"])
        term = AcademicTerm.objects.filter(pk=params["term"]).first()
        if term: applied_filters.append(f"Term: {term.name}")

    if params.get("program"):
        qs = qs.filter(student__program_id=params["program"])
        p = Program.objects.filter(pk=params["program"]).first()
        if p: applied_filters.append(f"Programme: {p.name}")

    total = qs.count()
    approved = qs.filter(status="APPROVED").count()
    submitted = qs.filter(status="SUBMITTED").count()
    draft = qs.filter(status="DRAFT").count()

    columns = ["Roll No", "Student Name", "Programme", "Semester", "Academic Term", "Registration Date", "Status"]
    rows = []
    for r in qs[:200]:
        rows.append([
            r.student.roll_no,
            r.student.user.display_name,
            r.student.program.code if r.student.program else "—",
            f"Sem {r.semester_no}",
            r.term.name if r.term else "—",
            r.created_at.strftime("%d %b %Y") if r.created_at else "—",
            r.get_status_display()
        ])

    kpis = [
        {"label": "Total Registrations", "val": f"{total:,}", "icon": "fa-clipboard-check", "color": "#6C5CE7"},
        {"label": "Approved Units", "val": f"{approved:,}", "icon": "fa-circle-check", "color": "#00b894"},
        {"label": "Pending Approval", "val": f"{submitted:,}", "icon": "fa-hourglass-half", "color": "#f0932b"},
        {"label": "Drafts", "val": f"{draft:,}", "icon": "fa-file-lines", "color": "#8d92a5"},
    ]

    return {
        "title": "Semester Registration & Cohort Summary",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_unit_enrollment_counts(params, user):
    qs = Course.objects.select_related("department", "program").annotate(
        enrolled_count=Count("enrollments")
    ).order_by("-enrolled_count")

    applied_filters = []
    if params.get("department"):
        qs = qs.filter(department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    columns = ["Course Code", "Course Title", "Department", "Credit Hours", "Enrolled Students", "Demand Level"]
    rows = []
    for c in qs:
        demand = "High" if c.enrolled_count >= 30 else "Moderate" if c.enrolled_count >= 10 else "Low"
        rows.append([
            c.code,
            c.title,
            c.department.name if c.department else "—",
            str(c.credits),
            str(c.enrolled_count),
            demand
        ])

    kpis = [
        {"label": "Total Units", "val": f"{qs.count():,}", "icon": "fa-book-open", "color": "#6C5CE7"},
        {"label": "Total Enrollments", "val": f"{sum(c.enrolled_count for c in qs):,}", "icon": "fa-users", "color": "#00b894"},
    ]

    return {
        "title": "Course Unit Enrollment Demand Report",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_faculty_workload(params, user):
    qs = FacultyProfile.objects.select_related("user", "department").annotate(
        assigned_courses=Count("courses"),
        total_students=Count("courses__enrollments")
    ).order_by("-assigned_courses")

    applied_filters = []
    if params.get("department"):
        qs = qs.filter(department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    columns = ["Staff ID", "Lecturer Name", "Department", "Designation", "Courses Assigned", "Students Taught", "Estimated Hours / Wk"]
    rows = []
    for f in qs:
        contact_hours = f.assigned_courses * 3
        rows.append([
            f.employee_id,
            f.user.display_name,
            f.department.name if f.department else "—",
            f.designation,
            str(f.assigned_courses),
            str(f.total_students),
            f"{contact_hours} hrs"
        ])

    kpis = [
        {"label": "Academic Staff", "val": f"{qs.count():,}", "icon": "fa-chalkboard-user", "color": "#6C5CE7"},
        {"label": "Courses Assigned", "val": f"{sum(f.assigned_courses for f in qs):,}", "icon": "fa-book", "color": "#00b894"},
    ]

    return {
        "title": "Teaching Staff Workload & Contact Hours",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_faculty_directory(params, user):
    qs = FacultyProfile.objects.select_related("user", "department").all().order_by("department__name", "user__first_name")
    applied_filters = []

    if params.get("department"):
        qs = qs.filter(department_id=params["department"])
        dept = Department.objects.filter(pk=params["department"]).first()
        if dept: applied_filters.append(f"Department: {dept.name}")

    columns = ["Staff ID", "Lecturer Name", "Department", "Designation", "Specialization", "Email", "Phone"]
    rows = []
    for f in qs:
        rows.append([
            f.employee_id,
            f.user.display_name,
            f.department.name if f.department else "—",
            f.designation,
            f.specialization or "General",
            f.user.email,
            f.user.phone or "—"
        ])

    kpis = [
        {"label": "Total Teaching Staff", "val": f"{qs.count():,}", "icon": "fa-id-badge", "color": "#6C5CE7"},
        {"label": "Departments", "val": f"{qs.values('department').distinct().count():,}", "icon": "fa-building-columns", "color": "#00b894"},
    ]

    return {
        "title": "Teaching Staff Directory",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_timetable_by_program(params, user):
    qs = ClassSchedule.objects.select_related("course", "course__program", "room", "course__faculty__user").all()
    applied_filters = []

    if params.get("program"):
        qs = qs.filter(course__program_id=params["program"])
        p = Program.objects.filter(pk=params["program"]).first()
        if p: applied_filters.append(f"Programme: {p.name}")

    columns = ["Day", "Time Slot", "Course Code", "Course Title", "Venue", "Instructor", "Session"]
    rows = []
    for s in qs:
        time_slot = f"{s.start_time.strftime('%H:%M')} – {s.end_time.strftime('%H:%M')}"
        rows.append([
            s.get_day_display(),
            time_slot,
            s.course.code,
            s.course.title,
            s.room.name if s.room else "TBA",
            s.course.faculty.user.display_name if (s.course.faculty and s.course.faculty.user) else "TBA",
            s.get_session_type_display()
        ])

    kpis = [
        {"label": "Scheduled Slots", "val": f"{qs.count():,}", "icon": "fa-calendar-week", "color": "#6C5CE7"},
    ]

    return {
        "title": "Programme Master Timetable Schedule",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_timetable_by_venue(params, user):
    qs = ClassSchedule.objects.select_related("course", "room").filter(room__isnull=False)
    applied_filters = []

    venue_stats = qs.values("room__name", "room__capacity").annotate(
        slots_count=Count("id")
    ).order_by("-slots_count")

    columns = ["Venue Name", "Room Capacity", "Scheduled Slots / Wk", "Total Hours / Wk", "Utilization Status"]
    rows = []
    for v in venue_stats:
        hrs = v["slots_count"] * 2
        status = "Heavy Usage" if hrs >= 20 else "Normal Usage" if hrs >= 8 else "Light Usage"
        rows.append([
            v["room__name"],
            str(v["room__capacity"] or 0),
            str(v["slots_count"]),
            f"{hrs} hrs",
            status
        ])

    kpis = [
        {"label": "Active Venues", "val": f"{venue_stats.count():,}", "icon": "fa-building-columns", "color": "#6C5CE7"},
    ]

    return {
        "title": "Lecture Venue Utilization & Room Booking Audit",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_user_activity_audit(params, user):
    qs = AuditLog.objects.all().order_by("-timestamp")
    applied_filters = []

    if params.get("date_from"):
        qs = qs.filter(timestamp__date__gte=params["date_from"])
        applied_filters.append(f"From: {params['date_from']}")
    if params.get("date_to"):
        qs = qs.filter(timestamp__date__lte=params["date_to"])
        applied_filters.append(f"To: {params['date_to']}")
    if params.get("q"):
        q = params["q"].strip()
        qs = qs.filter(Q(user_display__icontains=q) | Q(module__icontains=q) | Q(description__icontains=q))
        applied_filters.append(f"Search: '{q}'")

    columns = ["Timestamp", "Actor", "Role", "Module", "Action", "IP Address", "Details"]
    rows = []
    for log in qs[:250]:
        rows.append([
            log.timestamp.strftime("%Y-%m-%d %H:%M"),
            log.user_display or "System",
            log.user_role or "—",
            log.module,
            log.action,
            log.ip_address or "—",
            (log.description or "")[:60]
        ])

    kpis = [
        {"label": "Total Logged Operations", "val": f"{qs.count():,}", "icon": "fa-clock-rotate-left", "color": "#6C5CE7"},
        {"label": "Unique Actors", "val": f"{qs.values('user_display').distinct().count():,}", "icon": "fa-user-shield", "color": "#00b894"},
    ]

    return {
        "title": "System Audit Trail & Security Activity Ledger",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_recycle_bin_audit(params, user):
    qs = RecycleBinItem.objects.all().order_by("-deleted_at")
    applied_filters = []

    if params.get("date_from"):
        qs = qs.filter(deleted_at__date__gte=params["date_from"])
        applied_filters.append(f"From: {params['date_from']}")
    if params.get("date_to"):
        qs = qs.filter(deleted_at__date__lte=params["date_to"])
        applied_filters.append(f"To: {params['date_to']}")
    if params.get("q"):
        q = params["q"].strip()
        qs = qs.filter(Q(object_repr__icontains=q) | Q(module__icontains=q))
        applied_filters.append(f"Search: '{q}'")

    columns = ["Deleted At", "Module", "Object Description", "Deleted By", "Status", "Protected", "IP Address"]
    rows = []
    for item in qs[:250]:
        del_by = item.deleted_by.display_name if (item.deleted_by and hasattr(item.deleted_by, "display_name")) else "System"
        rows.append([
            item.deleted_at.strftime("%Y-%m-%d %H:%M"),
            item.module,
            item.object_repr[:50],
            del_by,
            "Restored" if item.is_restored else "Deleted",
            "Yes" if item.is_protected else "No",
            item.ip_address or "—"
        ])

    kpis = [
        {"label": "Recycle Bin Records", "val": f"{qs.count():,}", "icon": "fa-trash-can-arrow-up", "color": "#e84393"},
        {"label": "Protected Records", "val": f"{qs.filter(is_protected=True).count():,}", "icon": "fa-shield", "color": "#0984e3"},
    ]

    return {
        "title": "Recycle Bin & Data Retention Audit",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_admission_documents_status(params, user):
    qs = Application.objects.select_related("program", "intake", "student").prefetch_related("issued_documents", "issued_documents__delivery_logs").all().order_by("-created_at")

    applied_filters = []
    if params.get("program"):
        qs = qs.filter(program_id=params["program"])
        prog = Program.objects.filter(pk=params["program"]).first()
        if prog: applied_filters.append(f"Programme: {prog.code}")

    if params.get("status"):
        qs = qs.filter(status=params["status"])
        applied_filters.append(f"Status: {params['status']}")

    if params.get("q"):
        q = params["q"].strip()
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(application_number__icontains=q) | Q(admitted_reg_no__icontains=q) | Q(email__icontains=q))
        applied_filters.append(f"Search: '{q}'")

    total_apps = qs.count()
    total_issued = 0
    total_pending = 0
    total_resent = 0

    columns = ["#", "App Ref", "Applicant Name", "Programme", "Intake", "Status", "Letter Status", "Letter Ref", "Version", "Reporting Date", "Deliveries"]
    rows = []
    for idx, app in enumerate(qs, start=1):
        doc = app.issued_documents.filter(is_current_version=True).first()
        if doc and doc.status != "REVOKED":
            total_issued += 1
            letter_status = "Issued"
            letter_ref = doc.document_reference
            ver_str = f"v{doc.version}"
            rep_date = doc.reporting_date.strftime("%d %b %Y") if doc.reporting_date else "—"
            del_count = doc.delivery_logs.count()
            if del_count > 0:
                total_resent += del_count
            deliv_str = f"{del_count} sent" if del_count else "Not sent"
        elif doc and doc.status == "REVOKED":
            letter_status = "Revoked"
            letter_ref = doc.document_reference
            ver_str = f"v{doc.version}"
            rep_date = "—"
            deliv_str = "—"
        else:
            total_pending += 1
            letter_status = "Pending"
            letter_ref = "—"
            ver_str = "—"
            rep_date = app.reporting_date.strftime("%d %b %Y") if app.reporting_date else "—"
            deliv_str = "—"

        rows.append([
            str(idx),
            app.application_number,
            app.full_name,
            app.program.code if app.program else "—",
            app.intake.name if app.intake else "Regular",
            app.get_status_display(),
            letter_status,
            letter_ref,
            ver_str,
            rep_date,
            deliv_str,
        ])

    kpis = [
        {"label": "Total Applications", "val": f"{total_apps:,}", "icon": "fa-users-rectangle", "color": "#1e3a8a"},
        {"label": "Letters Issued", "val": f"{total_issued:,}", "icon": "fa-file-circle-check", "color": "#00b894"},
        {"label": "Letters Pending", "val": f"{total_pending:,}", "icon": "fa-clock", "color": "#f39c12"},
        {"label": "Dispatches Logged", "val": f"{total_resent:,}", "icon": "fa-paper-plane", "color": "#6c5ce7"},
    ]

    return {
        "title": "Admission Documents & Letter Issuance Status",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


def _query_application_attachments_audit(params, user):
    qs = ApplicationAttachment.objects.select_related("application", "application__program", "verified_by").all().order_by("-uploaded_at")

    applied_filters = []
    if params.get("program"):
        qs = qs.filter(application__program_id=params["program"])
        prog = Program.objects.filter(pk=params["program"]).first()
        if prog: applied_filters.append(f"Programme: {prog.code}")

    if params.get("status"):
        qs = qs.filter(verification_status=params["status"])
        applied_filters.append(f"Verification: {params['status']}")

    if params.get("q"):
        q = params["q"].strip()
        qs = qs.filter(Q(application__first_name__icontains=q) | Q(application__last_name__icontains=q) | Q(application__application_number__icontains=q) | Q(name__icontains=q))
        applied_filters.append(f"Search: '{q}'")

    total_files = qs.count()
    verified_count = qs.filter(verification_status="VERIFIED").count()
    rejected_count = qs.filter(verification_status="REJECTED").count()
    pending_count = qs.filter(verification_status="PENDING").count()

    columns = ["#", "App Ref", "Applicant Name", "Programme", "Document Title", "Category", "File Size", "Uploaded At", "Status", "Verified By"]
    rows = []
    for idx, att in enumerate(qs, start=1):
        rows.append([
            str(idx),
            att.application.application_number,
            att.application.full_name,
            att.application.program.code if att.application.program else "—",
            att.name,
            att.get_document_type_display(),
            f"{round(att.file_size / 1024, 1)} KB" if att.file_size else "—",
            att.uploaded_at.strftime("%d %b %Y"),
            att.get_verification_status_display(),
            att.verified_by.display_name if (att.verified_by and hasattr(att.verified_by, "display_name")) else (att.verified_by.username if att.verified_by else "—"),
        ])

    kpis = [
        {"label": "Uploaded Files", "val": f"{total_files:,}", "icon": "fa-paperclip", "color": "#1e3a8a"},
        {"label": "Verified & Approved", "val": f"{verified_count:,}", "icon": "fa-circle-check", "color": "#00b894"},
        {"label": "Pending Verification", "val": f"{pending_count:,}", "icon": "fa-clock", "color": "#f39c12"},
        {"label": "Rejected Files", "val": f"{rejected_count:,}", "icon": "fa-circle-xmark", "color": "#e74c3c"},
    ]

    return {
        "title": "Application Attachments & Verification Audit",
        "applied_filters": applied_filters,
        "kpis": kpis,
        "columns": columns,
        "rows": rows,
    }


# ==============================================================================
# 3. EXPORT ENGINES: PDF, EXCEL, CSV
# ==============================================================================

NumberedCanvas = PageNumberCanvas


def generate_report_pdf(report_data):
    """
    Generates an official ReportLab PDF with university letterhead,
    active filter metadata, summary KPI metrics, and structured tables.
    """
    buffer = io.BytesIO()
    orientation = report_data.get("meta", {}).get("orientation", "portrait")
    pagesize = landscape(A4) if orientation == "landscape" else portrait(A4)

    doc = ReportDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=45
    )

    styles = document_styles()
    meta_style = ParagraphStyle(
        "ReportMeta",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#718096")
    )
    table_hdr_style = ParagraphStyle(
        "TableHdr",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=9.5,
        textColor=colors.white,
        fontName="Quicksand-Bold"
    )
    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#2D3748")
    )

    elements = []

    avail_width = doc.width
    elements.append(letterhead(doc.width, report_data['title'],
        generated_at=report_data['generated_at'], generated_by=report_data['generated_by']))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#6C5CE7"), spaceAfter=10))

    if report_data.get("applied_filters"):
        filter_str = "  |  ".join(report_data["applied_filters"])
        filter_para = Paragraph(f"<b>Applied Filters:</b> {escape(filter_str)}", meta_style)
        elements.append(filter_para)
        elements.append(Spacer(1, 8))

    cols = report_data.get("columns", [])
    raw_rows = report_data.get("rows", [])

    if cols and raw_rows:
        num_cols = len(cols)
        col_width = avail_width / num_cols

        table_data = [[Paragraph(escape(str(c)), table_hdr_style) for c in cols]]
        for r in raw_rows:
            table_data.append([Paragraph(escape(str(val)), cell_style) for val in r])

        t = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)
        t_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2D2A4A")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ]
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor("#F7FAFC")))

        t.setStyle(TableStyle(t_style))
        elements.append(t)
    else:
        elements.append(Paragraph("No records found matching the specified filter criteria.", meta_style))

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Records: {len(raw_rows)} · Generated by {escape(str(report_data['generated_by']))}", meta_style))

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


def generate_report_excel(report_data):
    """
    Generates an OpenPyXL formatted spreadsheet (.xlsx) with branded header
    banner, auto-column sizing, styled headers, and data rows.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = report_data.get("key", "Report")[:31]

    brand_fill = PatternFill(start_color="2D2A4A", end_color="2D2A4A", fill_type="solid")
    header_fill = PatternFill(start_color="6C5CE7", end_color="6C5CE7", fill_type="solid")
    alt_fill = PatternFill(start_color="F4F5FB", end_color="F4F5FB", fill_type="solid")
    border_side = Side(style="thin", color="E2E8F0")
    thin_border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)

    last_col = get_column_letter(max(1, len(report_data.get("columns", []))))
    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = get_branding()["site_name"].upper() + " — OFFICIAL REPORT"
    ws["A1"].font = Font(name="Quicksand", size=13, bold=True, color="FFFFFF")
    ws["A1"].fill = brand_fill
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = report_data.get("title", "Report").upper()
    ws["A2"].font = Font(name="Quicksand", size=11, bold=True, color="2D2A4A")
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 22

    gen_meta = f"Generated: {report_data['generated_at'].strftime('%Y-%m-%d %H:%M')} | User: {report_data['generated_by']}"
    if report_data.get("applied_filters"):
        gen_meta += f" | Filters: {', '.join(report_data['applied_filters'])}"
    ws.merge_cells(f"A3:{last_col}3")
    ws["A3"] = gen_meta
    ws["A3"].font = Font(name="Quicksand", size=9, italic=True, color="718096")
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[3].height = 18

    cols = report_data.get("columns", [])
    raw_rows = report_data.get("rows", [])
    start_row = 5

    for c_idx, col_name in enumerate(cols, start=1):
        cell = ws.cell(row=start_row, column=c_idx, value=col_name)
        cell.font = Font(name="Quicksand", size=10, bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[start_row].height = 24

    for r_idx, row_data in enumerate(raw_rows, start=start_row + 1):
        is_alt = (r_idx % 2 == 0)
        for c_idx, val in enumerate(row_data, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name="Quicksand", size=9.5)
            cell.alignment = Alignment(horizontal="left", vertical="center")
            cell.border = thin_border
            if is_alt:
                cell.fill = alt_fill
        ws.row_dimensions[r_idx].height = 19

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    buf = io.BytesIO()
    finish_worksheet(ws, header_row=5)
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def generate_report_csv(report_data):
    """
    Generates a streaming CSV response with UTF-8 BOM encoding and formula escaping.
    """
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)

    writer.writerow(report_data.get("columns", []))
    for row in report_data.get("rows", []):
        writer.writerow([sanitize_csv_value(val) for val in row])

    return output.getvalue().encode('utf-8')
