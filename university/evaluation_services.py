from xml.sax.saxutils import escape
"""
Evaluation Services — Course & Lecturer QA Survey
=================================================
Provides business logic for:
  - Generating per-student, per-course anonymous submission tokens
  - Eligibility checking (open window, enrolled, fee-cleared, not yet submitted)
  - Submitting evaluations
  - Aggregate analytics per course/lecturer/term (for admin & faculty dashboards)
  - Excel/PDF report generation
"""
import hashlib
import io
from decimal import Decimal

from django.db.models import Avg, Count
from django.utils import timezone

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

from .models import (
    AcademicTerm, Course, CourseEvaluation, Enrollment, EvaluationWindow,
    SemesterRegistration,
)


# ---------------------------------------------------------------------------
# ANONYMOUS TOKEN
# ---------------------------------------------------------------------------

def make_submission_token(student_id: int, course_id: int, term_id: int) -> str:
    """
    Return a 64-char hex SHA-256 of the compound key.
    The token is stored; the student identity is NOT stored.
    """
    raw = f"{student_id}:{course_id}:{term_id}:eval-qa-ums"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# ELIGIBILITY
# ---------------------------------------------------------------------------

def get_evaluation_window(term=None):
    """Return the EvaluationWindow for the given term (or current term)."""
    if term is None:
        term = AcademicTerm.objects.filter(is_current=True).first()
    if term is None:
        return None
    window, _ = EvaluationWindow.objects.get_or_create(term=term)
    return window


def is_window_open(term=None) -> bool:
    """Check whether the evaluation window is currently open."""
    window = get_evaluation_window(term)
    if window is None or not window.is_open:
        return False
    now = timezone.now()
    if window.opens_at and now < window.opens_at:
        return False
    if window.closes_at and now > window.closes_at:
        return False
    return True


def get_student_pending_evaluations(student_profile, term=None):
    """
    Return list of dicts for courses the student is enrolled in for the term
    that have NOT yet been evaluated.
    """
    if term is None:
        term = AcademicTerm.objects.filter(is_current=True).first()
    if not term:
        return []

    # All active enrollments for the term
    enrollments = Enrollment.objects.filter(
        student=student_profile,
        term=term,
    ).exclude(status=Enrollment.DROPPED).select_related("course", "course__faculty")

    pending = []
    for enroll in enrollments:
        token = make_submission_token(student_profile.pk, enroll.course.pk, term.pk)
        already_submitted = CourseEvaluation.objects.filter(submission_token=token).exists()
        if not already_submitted:
            pending.append({
                "course": enroll.course,
                "term": term,
                "token": token,
                "enrollment": enroll,
            })
    return pending


def get_student_submitted_evaluations(student_profile, term=None):
    """
    Return list of dicts for courses the student has ALREADY evaluated.
    """
    if term is None:
        term = AcademicTerm.objects.filter(is_current=True).first()
    if not term:
        return []

    enrollments = Enrollment.objects.filter(
        student=student_profile,
        term=term,
    ).exclude(status=Enrollment.DROPPED).select_related("course")

    submitted = []
    for enroll in enrollments:
        token = make_submission_token(student_profile.pk, enroll.course.pk, term.pk)
        try:
            ev = CourseEvaluation.objects.get(submission_token=token)
            submitted.append({
                "course": enroll.course,
                "term": term,
                "evaluation": ev,
            })
        except CourseEvaluation.DoesNotExist:
            pass
    return submitted


# ---------------------------------------------------------------------------
# SUBMISSION
# ---------------------------------------------------------------------------

def submit_evaluation(student_profile, course, term, cleaned_data: dict):
    """
    Validate and persist a CourseEvaluation. Returns (evaluation, error_msg).
    error_msg is None on success.
    """
    token = make_submission_token(student_profile.pk, course.pk, term.pk)

    # Duplicate guard
    if CourseEvaluation.objects.filter(submission_token=token).exists():
        return None, "You have already submitted an evaluation for this course."

    # Must be enrolled
    enrolled = Enrollment.objects.filter(
        student=student_profile, course=course, term=term
    ).exclude(status=Enrollment.DROPPED).exists()
    if not enrolled:
        return None, "You are not enrolled in this course for the current term."

    ev = CourseEvaluation.objects.create(
        course=course,
        term=term,
        submission_token=token,
        teaching_quality=cleaned_data["teaching_quality"],
        course_content=cleaned_data["course_content"],
        assessment_fairness=cleaned_data["assessment_fairness"],
        resources_adequacy=cleaned_data["resources_adequacy"],
        overall_satisfaction=cleaned_data["overall_satisfaction"],
        strengths=cleaned_data.get("strengths", ""),
        suggestions=cleaned_data.get("suggestions", ""),
    )
    return ev, None


# ---------------------------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------------------------

DIMENSIONS = [
    ("teaching_quality", "Teaching Quality"),
    ("course_content", "Course Content"),
    ("assessment_fairness", "Assessment Fairness"),
    ("resources_adequacy", "Resources Adequacy"),
    ("overall_satisfaction", "Overall Satisfaction"),
]


def get_course_analytics(course, term=None):
    """Aggregate analytics for a single course in a term."""
    qs = CourseEvaluation.objects.filter(course=course)
    if term:
        qs = qs.filter(term=term)

    total = qs.count()
    if total == 0:
        return {"total": 0, "average": None, "dimensions": [], "comments": []}

    agg = qs.aggregate(
        avg_teaching=Avg("teaching_quality"),
        avg_content=Avg("course_content"),
        avg_fairness=Avg("assessment_fairness"),
        avg_resources=Avg("resources_adequacy"),
        avg_overall=Avg("overall_satisfaction"),
    )

    overall_avg = round(
        (agg["avg_teaching"] + agg["avg_content"] + agg["avg_fairness"] +
         agg["avg_resources"] + agg["avg_overall"]) / 5, 2
    )

    dimensions = [
        {"key": "teaching_quality", "label": "Teaching Quality", "avg": round(agg["avg_teaching"] or 0, 2)},
        {"key": "course_content", "label": "Course Content", "avg": round(agg["avg_content"] or 0, 2)},
        {"key": "assessment_fairness", "label": "Assessment Fairness", "avg": round(agg["avg_fairness"] or 0, 2)},
        {"key": "resources_adequacy", "label": "Resources Adequacy", "avg": round(agg["avg_resources"] or 0, 2)},
        {"key": "overall_satisfaction", "label": "Overall Satisfaction", "avg": round(agg["avg_overall"] or 0, 2)},
    ]

    # Rating distribution for overall_satisfaction
    distribution = {}
    for i in range(1, 6):
        distribution[i] = qs.filter(overall_satisfaction=i).count()

    comments = list(
        qs.exclude(strengths="", suggestions="")
          .values("strengths", "suggestions", "submitted_at")
          .order_by("-submitted_at")[:50]
    )

    return {
        "total": total,
        "average": overall_avg,
        "dimensions": dimensions,
        "distribution": distribution,
        "comments": comments,
    }


def get_admin_overview(term=None):
    """
    Admin-level overview: per-course average scores and response counts for a term.
    Returns a list of dicts sorted by average score descending.
    """
    qs = CourseEvaluation.objects.all()
    if term:
        qs = qs.filter(term=term)

    course_ids = qs.values_list("course_id", flat=True).distinct()
    rows = []
    for cid in course_ids:
        course_qs = qs.filter(course_id=cid)
        count = course_qs.count()
        agg = course_qs.aggregate(
            avg_tq=Avg("teaching_quality"),
            avg_cc=Avg("course_content"),
            avg_af=Avg("assessment_fairness"),
            avg_ra=Avg("resources_adequacy"),
            avg_os=Avg("overall_satisfaction"),
        )
        avgs = [v for v in agg.values() if v is not None]
        overall = round(sum(avgs) / len(avgs), 2) if avgs else 0
        course = Course.objects.select_related("faculty").get(pk=cid)
        rows.append({
            "course": course,
            "count": count,
            "overall": overall,
            "avg_teaching": round(agg["avg_tq"] or 0, 2),
            "avg_content": round(agg["avg_cc"] or 0, 2),
            "avg_fairness": round(agg["avg_af"] or 0, 2),
            "avg_resources": round(agg["avg_ra"] or 0, 2),
            "avg_overall": round(agg["avg_os"] or 0, 2),
        })
    rows.sort(key=lambda r: r["overall"], reverse=True)
    return rows


def get_faculty_course_analytics(faculty_profile, term=None):
    """Return analytics for all courses taught by a faculty member."""
    courses = Course.objects.filter(faculty=faculty_profile)
    results = []
    for course in courses:
        data = get_course_analytics(course, term=term)
        if data["total"] > 0:
            results.append({"course": course, **data})
    results.sort(key=lambda r: r.get("average") or 0, reverse=True)
    return results


# ---------------------------------------------------------------------------
# REPORT GENERATION — Excel
# ---------------------------------------------------------------------------

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill("solid", fgColor="1A1A2E")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def _header_row(ws, cells, row_num):
    for col, val in enumerate(cells, 1):
        c = ws.cell(row=row_num, column=col, value=val)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = Alignment(horizontal="center")
        c.border = _BORDER


def generate_evaluation_excel(term=None):
    """Generate an Excel workbook with evaluation data for the given term."""
    rows = get_admin_overview(term=term)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Evaluation Summary"

    # Title
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    term_label = term.name if term else "All Terms"
    title_cell.value = f"{get_branding()['site_name']} — Course Evaluation Summary — {term_label}"
    title_cell.font = Font(size=14, bold=True)
    title_cell.alignment = Alignment(horizontal="center")

    headers = ["Course Code", "Course Title", "Lecturer", "Responses",
               "Teaching Quality", "Course Content", "Assessment Fairness",
               "Resources Adequacy", "Overall Satisfaction", "Composite Score"]
    _header_row(ws, headers, 2)
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 25
    for col_letter in ["D", "E", "F", "G", "H", "I", "J"]:
        ws.column_dimensions[col_letter].width = 18

    for r_idx, row in enumerate(rows, 3):
        course = row["course"]
        lecturer = str(course.faculty) if course.faculty else "N/A"
        values = [
            course.code, course.title, lecturer, row["count"],
            row["avg_teaching"], row["avg_content"], row["avg_fairness"],
            row["avg_resources"], row["avg_overall"], row["overall"],
        ]
        for c_idx, val in enumerate(values, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.border = _BORDER
            if c_idx >= 5:
                cell.alignment = Alignment(horizontal="center")
                # Color-code by score
                score = float(val) if val else 0
                if score >= 4.0:
                    cell.fill = PatternFill("solid", fgColor="D4EFDF")  # green tint
                elif score >= 3.0:
                    cell.fill = PatternFill("solid", fgColor="FDEBD0")  # orange tint
                else:
                    cell.fill = PatternFill("solid", fgColor="FADBD8")  # red tint

    buf = io.BytesIO()
    finish_worksheet(ws, header_row=2)
    wb.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# REPORT GENERATION — PDF
# ---------------------------------------------------------------------------

def generate_evaluation_pdf(term=None):
    """Generate a PDF summary of evaluation data for the given term."""
    buf = io.BytesIO()
    doc = ReportDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=30, rightMargin=30,
                            topMargin=30, bottomMargin=30)
    styles = document_styles()
    story = []

    term_label = term.name if term else "All Terms"
    story.append(letterhead(doc.width, "Course Evaluation Report", subtitle=term_label))
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C5CE7")))
    story.append(Spacer(1, 8))

    rows = get_admin_overview(term=term)
    if not rows:
        story.append(Paragraph("No evaluation data available for this period.", styles["Normal"]))
    else:
        table_data = [["Course", "Lecturer", "Resp.", "Teaching", "Content",
                       "Fairness", "Resources", "Overall", "Score"]]
        for row in rows:
            course = row["course"]
            lecturer = str(course.faculty) if course.faculty else "N/A"
            table_data.append([
                f"{course.code}\n{course.title}",
                lecturer,
                str(row["count"]),
                str(row["avg_teaching"]),
                str(row["avg_content"]),
                str(row["avg_fairness"]),
                str(row["avg_resources"]),
                str(row["avg_overall"]),
                str(row["overall"]),
            ])

        cell_style = ParagraphStyle('EvaluationCell', parent=styles['Normal'], fontSize=8, leading=11)
        head_style = ParagraphStyle('EvaluationHead', parent=cell_style, fontName='Quicksand-Bold', textColor=colors.white)
        table_data = [[Paragraph(escape(str(value)).replace('\n', '<br/>'), head_style if i == 0 else cell_style)
                       for value in row] for i, row in enumerate(table_data)]
        tbl = Table(table_data, colWidths=[doc.width * .22, doc.width * .18] + [doc.width * .60 / 7] * 7, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1A2E")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Quicksand-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"Generated: {timezone.now().strftime('%d %b %Y %H:%M')}",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    ))

    doc.build(story)
    buf.seek(0)
    return buf
