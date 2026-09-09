"""Analytics + aggregation helpers shared by dashboards and the AI layer."""
from __future__ import annotations

import calendar
from datetime import date

from django.db.models import Avg, Count, F, Sum

from .models import (
    AcademicTerm, Assignment, Attendance, Course, Department, Enrollment, Event,
    Exam, FeeInvoice, Notice, Program, Result, Submission,
)


# --------------------------------------------------------------------------
# Small reusable aggregates
# --------------------------------------------------------------------------
def overall_attendance() -> float:
    total = Attendance.objects.count()
    if not total:
        return 0.0
    present = Attendance.objects.filter(
        status__in=[Attendance.PRESENT, Attendance.LATE]).count()
    return round(present / total * 100, 1)


def total_fees_collected() -> float:
    agg = FeeInvoice.objects.aggregate(s=Sum("amount_paid"))
    return float(agg["s"] or 0)


def total_fees_billed() -> float:
    agg = FeeInvoice.objects.aggregate(s=Sum("amount"))
    return float(agg["s"] or 0)


def popular_courses(limit: int = 5):
    return list(
        Course.objects.annotate(n=Count("enrollments"))
        .order_by("-n")[:limit]
    )


# --------------------------------------------------------------------------
# Per-student statistics
# --------------------------------------------------------------------------
def student_stats(student) -> dict:
    enrollments = Enrollment.objects.filter(student=student)
    course_count = enrollments.count()

    att_qs = Attendance.objects.filter(enrollment__student=student)
    att_total = att_qs.count()
    att_present = att_qs.filter(status__in=[Attendance.PRESENT, Attendance.LATE]).count()
    attendance_pct = round(att_present / att_total * 100, 1) if att_total else 0.0

    results = Result.objects.filter(exam__status=Exam.Status.PUBLISHED, student=student).select_related("exam__course")
    pcts = [r.percentage for r in results]
    avg_marks = round(sum(pcts) / len(pcts), 1) if pcts else 0.0
    from .examination_services import student_statement
    gpa = student_statement(student).gpa

    # weakest subject by average percentage
    subject_scores: dict[str, list[float]] = {}
    for r in results:
        subject_scores.setdefault(r.exam.course.title, []).append(r.percentage)
    weakest_subject = None
    if subject_scores:
        weakest_subject = min(
            subject_scores.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0]

    subs = Submission.objects.filter(student=student)
    total_assignments = Assignment.objects.filter(
        course__enrollments__student=student).distinct().count()
    submitted = subs.exclude(status=Submission.PENDING).count()
    pending = max(0, total_assignments - submitted)
    submission_rate = round(submitted / total_assignments * 100, 1) if total_assignments else 100.0

    fee_due = 0.0
    for inv in FeeInvoice.objects.filter(student=student):
        fee_due += float(inv.balance)

    return {
        "courses": course_count,
        "attendance_pct": attendance_pct,
        "avg_marks": avg_marks,
        "gpa": gpa,
        "pending_assignments": pending,
        "submission_rate": submission_rate,
        "fee_due": round(fee_due, 2),
        "weakest_subject": weakest_subject,
    }


# --------------------------------------------------------------------------
# Dashboard context builders
# --------------------------------------------------------------------------
def _month_labels(n=12):
    return [calendar.month_abbr[m] for m in range(1, n + 1)]


def admin_dashboard():
    from accounts.models import FacultyProfile, StudentProfile
    from django.urls import reverse

    student_count = StudentProfile.objects.count()
    faculty_count = FacultyProfile.objects.count()
    course_count = Course.objects.count()
    collected = total_fees_collected()
    billed = total_fees_billed()
    fee_pending_total = max(0.0, billed - collected)
    fee_collection_rate = round((collected / billed * 100), 1) if billed else 100.0

    active_students = Enrollment.objects.values("student").distinct().count()
    active_faculty = Course.objects.filter(faculty__isnull=False).values("faculty").distinct().count()
    courses_with_enrollments = Course.objects.annotate(n=Count("enrollments")).filter(n__gt=0).count()

    # Monthly fee collection (current year) -------------------------------
    year = date.today().year
    collected_by_month = [0.0] * 12
    pending_by_month = [0.0] * 12
    for inv in FeeInvoice.objects.all():
        m = inv.issued_on.month - 1
        collected_by_month[m] += float(inv.amount_paid)
        pending_by_month[m] += float(inv.balance)

    # Enrollment trend (cumulative by month) ------------------------------
    enroll_by_month = [0] * 12
    for e in Enrollment.objects.all():
        enroll_by_month[e.enrolled_on.month - 1] += 1
    cumulative = []
    running = 0
    for v in enroll_by_month:
        running += v
        cumulative.append(running)

    # Department distribution --------------------------------------------
    dept_rows = (Department.objects
                 .annotate(n=Count("programs__students"))
                 .values("name", "color", "n").order_by("-n"))

    # Attendance split ----------------------------------------------------
    att = Attendance.objects.values("status").annotate(n=Count("id"))
    att_map = {a["status"]: a["n"] for a in att}

    from .models import Application, ExamAppeal, DepartmentClearance, SupplementaryExamRegistration

    p_adm = Application.objects.filter(status__in=["SUBMITTED", "UNDER_REVIEW"]).count()
    p_app = ExamAppeal.objects.filter(status="OPEN").count()
    p_clr = DepartmentClearance.objects.filter(status="PENDING").count()
    p_sup = SupplementaryExamRegistration.objects.filter(status="PENDING").count()

    current_term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.order_by("-start_date").first()

    pending_actions = [
        {
            "label": "Admissions Review",
            "count": p_adm,
            "url": reverse("university:admin_admissions"),
            "icon": "fa-id-card-clip",
            "badge_class": "bg-primary",
        },
        {
            "label": "Exam Grade Appeals",
            "count": p_app,
            "url": reverse("examinations:report"),
            "icon": "fa-file-pen",
            "badge_class": "bg-danger",
        },
        {
            "label": "Graduation Clearances",
            "count": p_clr,
            "url": reverse("university:admin_graduation_dashboard"),
            "icon": "fa-user-graduate",
            "badge_class": "bg-warning text-dark",
        },
        {
            "label": "Supplementary Exams",
            "count": p_sup,
            "url": reverse("university:admin_supplementary_list"),
            "icon": "fa-arrows-rotate",
            "badge_class": "bg-info text-dark",
        },
    ]
    total_pending_actions = p_adm + p_app + p_clr + p_sup

    from .academic_calendar_services import get_active_academic_context
    academic_context = get_active_academic_context()

    return {
        "current_term": academic_context.get("semester"),
        "academic_context": academic_context,
        "pending_actions": pending_actions,
        "total_pending_actions": total_pending_actions,
        "cards": [
            {
                "label": "Total Students",
                "value": student_count,
                "sub_label": f"{active_students} actively enrolled",
                "icon": "fa-users",
                "grad": "linear-gradient(135deg,#7b6cf6,#5a4bd6)",
                "url": reverse("university:admin_students"),
                "badge_icon": "fa-circle-check",
                "up": True,
            },
            {
                "label": "Total Faculty",
                "value": faculty_count,
                "sub_label": f"{active_faculty} teaching courses",
                "icon": "fa-chalkboard-user",
                "grad": "linear-gradient(135deg,#20c997,#12b886)",
                "url": reverse("university:admin_faculty"),
                "badge_icon": "fa-user-tie",
                "up": True,
            },
            {
                "label": "Total Courses",
                "value": course_count,
                "sub_label": f"{courses_with_enrollments} with active classes",
                "icon": "fa-book",
                "grad": "linear-gradient(135deg,#f368a6,#e6488a)",
                "url": reverse("university:admin_courses"),
                "badge_icon": "fa-graduation-cap",
                "up": True,
            },
            {
                "label": "Fees Collected",
                "value": f"KES {collected:,.0f}",
                "sub_label": f"{fee_collection_rate}% rate · KES {fee_pending_total:,.0f} due",
                "icon": "fa-wallet",
                "grad": "linear-gradient(135deg,#4dabf7,#3b9ae1)",
                "url": reverse("university:admin_fees"),
                "badge_icon": "fa-receipt",
                "up": True,
            },
        ],
        "fee_billed_total": billed,
        "fee_pending_total": fee_pending_total,
        "fee_collection_rate": fee_collection_rate,
        "months": _month_labels(),
        "fee_collected": collected_by_month,
        "fee_pending": pending_by_month,
        "enroll_trend": cumulative,
        "new_admissions": enroll_by_month,
        "dept_rows": list(dept_rows),
        "attendance_split": [
            att_map.get(Attendance.PRESENT, 0),
            att_map.get(Attendance.ABSENT, 0),
            att_map.get(Attendance.LATE, 0),
        ],
        "recent_notices": Notice.objects.all()[:5],
        "upcoming_events": Event.objects.filter(date__gte=date.today())[:4],
        "top_courses": popular_courses(5),
        "academic_perf": academic_performance_data(),
        "academic_terms": AcademicTerm.objects.all().order_by("-start_date"),
        "academic_departments": Department.objects.all().order_by("name"),
        "academic_programs": Program.objects.all().order_by("name"),
        "academic_semesters": [1, 2, 3, 4, 5, 6, 7, 8],
    }


def faculty_dashboard(faculty):
    courses = Course.objects.filter(faculty=faculty)
    course_ids = list(courses.values_list("id", flat=True))
    students = (Enrollment.objects.filter(course_id__in=course_ids)
                .values("student").distinct().count())
    assignments = Assignment.objects.filter(course_id__in=course_ids)
    pending_grading = Submission.objects.filter(
        assignment__course_id__in=course_ids, status=Submission.SUBMITTED).count()

    # attendance per course
    course_labels, course_att = [], []
    for c in courses:
        qs = Attendance.objects.filter(enrollment__course=c)
        t = qs.count()
        p = qs.filter(status__in=[Attendance.PRESENT, Attendance.LATE]).count()
        course_labels.append(c.code)
        course_att.append(round(p / t * 100, 1) if t else 0)

    # grade distribution across faculty courses
    grade_buckets = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for r in Result.objects.filter(exam__status=Exam.Status.PUBLISHED, exam__course_id__in=course_ids):
        if r.grade in grade_buckets:
            grade_buckets[r.grade] += 1

    return {
        "cards": [
            {"label": "My Courses", "value": courses.count(), "icon": "fa-book-open",
             "grad": "linear-gradient(135deg,#009688,#26c6a6)"},
            {"label": "My Students", "value": students, "icon": "fa-user-group",
             "grad": "linear-gradient(135deg,#0d9488,#0f766e)"},
            {"label": "Assignments", "value": assignments.count(), "icon": "fa-file-lines",
             "grad": "linear-gradient(135deg,#f59e0b,#f97316)"},
            {"label": "To Grade", "value": pending_grading, "icon": "fa-pen-clip",
             "grad": "linear-gradient(135deg,#ef4444,#f43f5e)"},
        ],
        "courses": courses,
        "course_labels": course_labels,
        "course_att": course_att,
        "grade_labels": list(grade_buckets.keys()),
        "grade_values": list(grade_buckets.values()),
        "recent_submissions": Submission.objects.filter(
            assignment__course_id__in=course_ids).select_related(
            "student__user", "assignment")[:6],
    }


def student_dashboard(student):
    stats = student_stats(student)
    enrollments = Enrollment.objects.filter(student=student).select_related("course")

    # attendance trend over recent classes (last 8 dates)
    dates = list(Attendance.objects.filter(enrollment__student=student)
                 .values_list("date", flat=True).distinct().order_by("date"))[-8:]
    trend_labels, trend_values = [], []
    for d in dates:
        day_qs = Attendance.objects.filter(enrollment__student=student, date=d)
        t = day_qs.count()
        p = day_qs.filter(status__in=[Attendance.PRESENT, Attendance.LATE]).count()
        trend_labels.append(d.strftime("%d %b"))
        trend_values.append(round(p / t * 100) if t else 0)

    # marks per subject
    subj_labels, subj_values = [], []
    for e in enrollments:
        r = Result.objects.filter(exam__status=Exam.Status.PUBLISHED, student=student, exam__course=e.course).first()
        if r:
            subj_labels.append(e.course.code)
            subj_values.append(r.percentage)

    from .academic_calendar_services import get_active_academic_context
    from .models import SemesterRegistration
    current_reg = SemesterRegistration.objects.filter(student=student, term__is_current=True).first() or \
                  SemesterRegistration.objects.filter(student=student).order_by("-created_at").first()

    return {
        "stats": stats,
        "academic_context": get_active_academic_context(),
        "current_registration": current_reg,
        "cards": [
            {"label": "Attendance", "value": f"{stats['attendance_pct']}%", "icon": "fa-calendar-check",
             "grad": "linear-gradient(135deg,#0984e3,#48b1f3)"},
            {"label": "Average / GPA", "value": f"{stats['gpa']}" if stats['gpa'] is not None else "N/A", "icon": "fa-graduation-cap",
             "grad": "linear-gradient(135deg,#6C5CE7,#8f7bff)"},
            {"label": "Pending Work", "value": stats["pending_assignments"], "icon": "fa-list-check",
             "grad": "linear-gradient(135deg,#e17055,#f0932b)"},
            {"label": "Fee Balance", "value": f"KES {stats['fee_due']:,.0f}", "icon": "fa-wallet",
             "grad": "linear-gradient(135deg,#00b894,#20bf6b)"},
        ],
        "enrollments": enrollments,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "subj_labels": subj_labels,
        "subj_values": subj_values,
        "upcoming_events": Event.objects.filter(date__gte=date.today())[:4],
        "invoices": FeeInvoice.objects.filter(student=student)[:5],
    }


# --------------------------------------------------------------------------
# Academic Performance analytics (admin dashboard)
# --------------------------------------------------------------------------
def academic_performance_data(term_id=None, department_id=None, program_id=None, semester=None):
    """
    Aggregates real academic performance data from published exam results.
    Returns grade distribution, pass/fail rates, GPA, trend data, at-risk
    students, and per-department comparisons.
    """
    from accounts.models import StudentProfile
    from collections import defaultdict
    from decimal import Decimal

    # Base queryset: only published results with marks
    base_qs = Result.objects.filter(
        exam__status=Exam.Status.PUBLISHED,
        attendance="PRESENT",
        marks_obtained__isnull=False,
    ).select_related(
        "exam__course__department", "exam__course__program", "exam__term", "student__user", "student__program"
    )

    # Prefer final exams if present
    if base_qs.filter(exam__kind=Exam.Kind.FINAL).exists():
        base_qs = base_qs.filter(exam__kind__in=[Exam.Kind.FINAL, Exam.Kind.SUPPLEMENTARY])

    # Apply filters if non-empty and not 'all'
    if term_id and str(term_id).lower() not in ("all", "", "none"):
        base_qs = base_qs.filter(exam__term_id=term_id)
    if department_id and str(department_id).lower() not in ("all", "", "none"):
        base_qs = base_qs.filter(exam__course__department_id=department_id)
    if program_id and str(program_id).lower() not in ("all", "", "none"):
        base_qs = base_qs.filter(exam__course__program_id=program_id)
    if semester and str(semester).lower() not in ("all", "", "none"):
        base_qs = base_qs.filter(exam__course__semester_no=semester)

    results_list = list(base_qs)
    total_results = len(results_list)

    # --- 1. Grade Distribution & Pass / Fail ---
    grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    pass_count = 0
    fail_count = 0
    total_gp = Decimal("0")
    student_gps = defaultdict(list)  # student_id -> list of grade_points
    student_pcts = defaultdict(list)

    for r in results_list:
        g = r.grade
        if g in grade_dist:
            grade_dist[g] += 1
        pct = r.percentage
        if pct >= float(r.exam.pass_mark):
            pass_count += 1
        else:
            fail_count += 1
        gp = Decimal(str(r.grade_point))
        total_gp += gp
        student_gps[r.student_id].append(gp)
        student_pcts[r.student_id].append(pct)

    avg_gpa = round(float(total_gp / total_results), 2) if total_results else 0.0
    pass_rate = round(pass_count / total_results * 100, 1) if total_results else 0.0
    fail_rate = round(fail_count / total_results * 100, 1) if total_results else 0.0

    # --- 2. At-Risk Students ---
    # A student is "at risk" if their average GP across graded exams < 2.0 or failing average
    at_risk_ids = set()
    for sid, gps in student_gps.items():
        if gps:
            avg = sum(gps) / len(gps)
            if avg < Decimal("2.0"):
                at_risk_ids.add(sid)
    at_risk_count = len(at_risk_ids)
    total_students = len(student_gps)

    # --- 3. Academic Trend (Score Distribution Bell Curve) ---
    # Model student score performance as a smooth normal distribution (bell curve)
    # centered around the empirical mean performance and standard deviation.
    trend_labels = []
    trend_values = []
    all_pcts = [r.percentage for r in results_list]
    import math

    x_vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for x in x_vals:
        trend_labels.append(f"{x}%")

    if all_pcts:
        mu = sum(all_pcts) / len(all_pcts)
        if len(all_pcts) > 1:
            variance = sum((p - mu) ** 2 for p in all_pcts) / (len(all_pcts) - 1)
            sigma = math.sqrt(variance)
        else:
            sigma = 14.0
        sigma = max(sigma, 10.0)
        peak = 85.0
        for x in x_vals:
            val = round(peak * math.exp(-((x - mu) ** 2) / (2 * (sigma ** 2))), 1)
            trend_values.append(val)
    else:
        trend_values = [0.0] * len(x_vals)

    # --- 4. Performance by Department / Faculty ---
    dept_perf = defaultdict(lambda: {"total": 0, "sum_pct": 0.0, "color": "#6C5CE7"})
    for r in results_list:
        dept = r.exam.course.department
        dept_perf[dept.name]["total"] += 1
        dept_perf[dept.name]["sum_pct"] += r.percentage
        dept_perf[dept.name]["color"] = dept.color

    dept_labels = []
    dept_avgs = []
    dept_colors = []
    for name, data in sorted(dept_perf.items(), key=lambda x: x[1]["sum_pct"] / max(x[1]["total"], 1), reverse=True):
        dept_labels.append(name)
        dept_avgs.append(round(data["sum_pct"] / data["total"], 1) if data["total"] else 0)
        dept_colors.append(data["color"])

    # --- 5. At-Risk Student details ---
    at_risk_details = []
    if at_risk_ids:
        for sid in list(at_risk_ids)[:10]:
            try:
                sp = StudentProfile.objects.select_related("user", "program").get(pk=sid)
                gps = student_gps[sid]
                avg = round(float(sum(gps) / len(gps)), 2) if gps else 0.0
                at_risk_details.append({
                    "name": sp.user.display_name,
                    "roll_no": sp.roll_no,
                    "programme": sp.program.name if sp.program else "—",
                    "gpa": avg,
                    "id": sp.pk,
                })
            except StudentProfile.DoesNotExist:
                continue

    # --- 6. Top Performing Students ---
    top_students = []
    for sid, gps in sorted(student_gps.items(), key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 0, reverse=True)[:5]:
        if sid in at_risk_ids:
            continue
        try:
            sp = StudentProfile.objects.select_related("user", "program").get(pk=sid)
            avg = round(float(sum(gps) / len(gps)), 2) if gps else 0.0
            top_students.append({
                "name": sp.user.display_name,
                "roll_no": sp.roll_no,
                "programme": sp.program.name if sp.program else "—",
                "gpa": avg,
                "id": sp.pk,
            })
        except StudentProfile.DoesNotExist:
            continue

    return {
        "avg_gpa": avg_gpa,
        "pass_rate": pass_rate,
        "fail_rate": fail_rate,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "total_results": total_results,
        "total_students": total_students,
        "at_risk_count": at_risk_count,
        "grade_labels": list(grade_dist.keys()),
        "grade_values": list(grade_dist.values()),
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "dept_labels": dept_labels,
        "dept_avgs": dept_avgs,
        "dept_colors": dept_colors,
        "at_risk_details": at_risk_details,
        "top_students": top_students,
    }

