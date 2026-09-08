import csv
import io
import re

from django.db import transaction
from django.utils import timezone

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Table, TableStyle
)

from accounts.models import FacultyProfile
from university.models import Course, Department, Program
from university.student_io import NumberedCanvas


COURSE_IMPORT_COLUMNS = [
    ("code", "Course Code", True),
    ("title", "Course Title", True),
    ("department_code", "Department Code", True),
    ("program_code", "Program Code", False),
    ("credits", "Credits", False),
    ("semester_no", "Semester", False),
    ("status", "Status", False),
    ("faculty_id", "Faculty ID", False),
    ("description", "Description", False),
]

SAMPLE_COURSE_ROW = {
    "code": "CS901",
    "title": "Introduction to Quantum Computing",
    "department_code": "CSE",
    "program_code": "BT-CSE",
    "credits": 4,
    "semester_no": 1,
    "status": "Active",
    "faculty_id": "FAC1000",
    "description": "Fundamental concepts of quantum circuits, qubits, and quantum algorithms.",
}


# ==============================================================================
# 1. EXPORT FUNCTIONS (CSV, EXCEL, PDF)
# ==============================================================================

def export_course_csv(queryset):
    """Generate CSV byte string with UTF-8 BOM for courses."""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    headers = [
        "Course Code", "Course Title", "Department Code", "Department Name",
        "Program Code", "Program Name", "Credits", "Level", "Semester",
        "Assigned Faculty", "Status", "Enrolled Students", "Description"
    ]
    writer.writerow(headers)

    for c in queryset:
        dept_code = c.department.code if c.department else "—"
        dept_name = c.department.name if c.department else "—"
        prog_code = c.program.code if c.program else "—"
        prog_name = c.program.name if c.program else "—"
        faculty_name = c.faculty.user.display_name if c.faculty else "TBA"
        enrolled = getattr(c, "n", c.enrolled_count)

        writer.writerow([
            c.code,
            c.title,
            dept_code,
            dept_name,
            prog_code,
            prog_name,
            c.credits,
            c.level_display,
            f"Semester {c.semester_no}",
            faculty_name,
            c.status,
            enrolled,
            c.description or "—",
        ])

    return buffer.getvalue().encode("utf-8-sig")


def export_course_excel(queryset, site_name="University Management System"):
    """Generate professionally styled Excel (.xlsx) file for Courses."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Courses"
    ws.views.sheetView[0].showGridLines = True

    primary_color = "4F46E5"      # Royal Indigo
    header_fill_color = "4338CA"  # Deep Indigo
    zebra_color = "EEF2FF"        # Light Indigo tint
    border_color = "CBD5E1"

    font_title = Font(name="Arial", size=15, bold=True, color="FFFFFF")
    font_sub = Font(name="Arial", size=10, italic=True, color="E0E7FF")
    font_header = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Arial", size=10, color="1E293B")
    font_bold_data = Font(name="Arial", size=10, bold=True, color="1E293B")

    thin_border = Border(
        left=Side(style="thin", color=border_color),
        right=Side(style="thin", color=border_color),
        top=Side(style="thin", color=border_color),
        bottom=Side(style="thin", color=border_color),
    )

    # 1. Title Banner
    ws.merge_cells("A1:K1")
    title_cell = ws["A1"]
    title_cell.value = f"  {site_name.upper()} — COURSE CATALOG"
    title_cell.font = font_title
    title_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    title_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 36

    # 2. Subtitle / Metadata Row
    ws.merge_cells("A2:K2")
    sub_cell = ws["A2"]
    sub_cell.value = f"  Generated: {timezone.now().strftime('%d %b %Y, %H:%M')} | Total Courses: {queryset.count()}"
    sub_cell.font = font_sub
    sub_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    sub_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[2].height = 20

    ws.row_dimensions[3].height = 8

    # 3. Column Headers
    headers = [
        "Course Code", "Course Title", "Department", "Program", "Credits",
        "Level", "Semester", "Assigned Faculty", "Status", "Enrolled", "Description"
    ]
    header_row_idx = 4
    ws.row_dimensions[header_row_idx].height = 28

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row_idx, column=col_num)
        cell.value = header
        cell.font = font_header
        cell.fill = PatternFill(start_color=header_fill_color, end_color=header_fill_color, fill_type="solid")
        if header in ["Credits", "Semester", "Status", "Enrolled"]:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = thin_border

    # 4. Data Rows
    for row_idx, c in enumerate(queryset, start=5):
        ws.row_dimensions[row_idx].height = 22
        fill_color = zebra_color if row_idx % 2 == 0 else "FFFFFF"
        row_fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")

        dept_code = c.department.code if c.department else "—"
        prog_code = c.program.code if c.program else "—"
        faculty_name = c.faculty.user.display_name if c.faculty else "TBA"
        enrolled = getattr(c, "n", c.enrolled_count)

        values = [
            (c.code, font_bold_data, "left"),
            (c.title, font_data, "left"),
            (dept_code, font_data, "center"),
            (prog_code, font_data, "center"),
            (c.credits, font_data, "center"),
            (c.level_display, font_data, "left"),
            (f"Sem {c.semester_no}", font_data, "center"),
            (faculty_name, font_data, "left"),
            (c.status, font_data, "center"),
            (enrolled, font_data, "center"),
            (c.description or "—", font_data, "left"),
        ]

        for col_idx, (val, font, align_h) in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = val
            cell.font = font
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal=align_h, vertical="center")
            cell.border = thin_border

    # 5. Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in [1, 2, 3]:
                continue
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

    ws.column_dimensions["A"].width = 14  # Course Code
    ws.column_dimensions["B"].width = 30  # Course Title
    ws.column_dimensions["K"].width = 35  # Description

    # Freeze header rows
    ws.freeze_panes = "A5"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def export_course_pdf(queryset, site_name="University Management System", logo_path=None, filter_text=None):
    """Generate high-quality branded PDF report for Courses in landscape A4."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Typography styles with Quicksand fallback
    title_style = ParagraphStyle(
        "CourseReportTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#1E293B"),
    )
    meta_style = ParagraphStyle(
        "CourseReportMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#64748B"),
        alignment=2,  # Right align
    )
    th_style = ParagraphStyle(
        "CourseTH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=0,
    )
    td_style = ParagraphStyle(
        "CourseTD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    td_bold = ParagraphStyle(
        "CourseTDBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4F46E5"),
    )
    td_center = ParagraphStyle(
        "CourseTDCenter",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
        alignment=1,
    )

    story = []

    # 1. Header Banner
    now_str = timezone.now().strftime("%d %b %Y, %H:%M")
    filter_desc = f"<b>Filter:</b> {filter_text}<br/>" if filter_text else ""
    info_html = f"<b>Generated:</b> {now_str}<br/><b>Total Courses:</b> {queryset.count()}<br/>{filter_desc}"

    if logo_path:
        try:
            logo_flowable = RLImage(logo_path, width=46, height=46)
            header_table = Table(
                [[logo_flowable, Paragraph(f"<b>COURSE CATALOG & REGISTRY</b><br/><font color='#64748B' size='8'>{site_name}</font>", title_style), Paragraph(info_html, meta_style)]],
                colWidths=[54, 450, 260],
            )
        except Exception:
            header_table = Table(
                [[Paragraph(f"<b>{site_name.upper()}</b><br/><font size='12' color='#4F46E5'>Official Course Catalog</font>", title_style), Paragraph(info_html, meta_style)]],
                colWidths=[504, 260],
            )
    else:
        header_table = Table(
            [[Paragraph(f"<b>{site_name.upper()}</b><br/><font size='12' color='#4F46E5'>Official Course Catalog</font>", title_style), Paragraph(info_html, meta_style)]],
            colWidths=[504, 260],
        )

    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#4F46E5"), spaceAfter=12))

    # 2. Courses Table
    headers = [
        Paragraph("<b>Code</b>", th_style),
        Paragraph("<b>Course Title</b>", th_style),
        Paragraph("<b>Dept</b>", th_style),
        Paragraph("<b>Program</b>", th_style),
        Paragraph("<b>Credits</b>", th_style),
        Paragraph("<b>Sem / Level</b>", th_style),
        Paragraph("<b>Assigned Faculty</b>", th_style),
        Paragraph("<b>Status</b>", th_style),
        Paragraph("<b>Enrolled</b>", th_style),
    ]

    col_widths = [65, 180, 55, 65, 45, 85, 135, 65, 70]
    data = [headers]

    for c in queryset:
        dept_code = c.department.code if c.department else "—"
        prog_code = c.program.code if c.program else "—"
        faculty_name = c.faculty.user.display_name if c.faculty else "TBA"
        enrolled = getattr(c, "n", c.enrolled_count)

        status_color = "#10B981" if c.status == "Active" else "#64748B"
        status_html = f"<font color='{status_color}'><b>{c.status}</b></font>"

        row = [
            Paragraph(c.code, td_bold),
            Paragraph(c.title, td_style),
            Paragraph(dept_code, td_center),
            Paragraph(prog_code, td_center),
            Paragraph(str(c.credits), td_center),
            Paragraph(f"Sem {c.semester_no}<br/><font size='7' color='#64748B'>{c.level_display}</font>", td_style),
            Paragraph(faculty_name, td_style),
            Paragraph(status_html, td_center),
            Paragraph(f"{enrolled} students", td_center),
        ]
        data.append(row)

    if len(data) == 1:
        empty_msg = Paragraph("<i>No courses match the active search/filter criteria.</i>", td_style)
        data.append([empty_msg] + [""] * (len(headers) - 1))

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4F46E5")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]))

    story.append(t)
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


# ==============================================================================
# 2. TEMPLATE GENERATION (CSV & EXCEL)
# ==============================================================================

def generate_course_template_csv():
    """Generate a sample CSV template with headers (Row 1) and realistic course data (Row 2)."""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    headers = [col[1] for col in COURSE_IMPORT_COLUMNS]
    writer.writerow(headers)

    sample_values = [
        SAMPLE_COURSE_ROW["code"],
        SAMPLE_COURSE_ROW["title"],
        SAMPLE_COURSE_ROW["department_code"],
        SAMPLE_COURSE_ROW["program_code"],
        SAMPLE_COURSE_ROW["credits"],
        SAMPLE_COURSE_ROW["semester_no"],
        SAMPLE_COURSE_ROW["status"],
        SAMPLE_COURSE_ROW["faculty_id"],
        SAMPLE_COURSE_ROW["description"],
    ]
    writer.writerow(sample_values)

    return buffer.getvalue().encode("utf-8-sig")


def generate_course_template_excel():
    """Generate a pre-formatted Excel template with headers and sample course data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Course Import Template"
    ws.views.sheetView[0].showGridLines = True

    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    sample_font = Font(name="Arial", size=10, color="334155")
    sample_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    headers = [col[1] for col in COURSE_IMPORT_COLUMNS]
    ws.append(headers)
    ws.row_dimensions[1].height = 26

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    sample_values = [
        SAMPLE_COURSE_ROW["code"],
        SAMPLE_COURSE_ROW["title"],
        SAMPLE_COURSE_ROW["department_code"],
        SAMPLE_COURSE_ROW["program_code"],
        SAMPLE_COURSE_ROW["credits"],
        SAMPLE_COURSE_ROW["semester_no"],
        SAMPLE_COURSE_ROW["status"],
        SAMPLE_COURSE_ROW["faculty_id"],
        SAMPLE_COURSE_ROW["description"],
    ]
    ws.append(sample_values)
    ws.row_dimensions[2].height = 22

    for col_idx in range(1, len(sample_values) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.font = sample_font
        cell.fill = sample_fill
        cell.alignment = Alignment(vertical="center")
        cell.border = border

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ==============================================================================
# 3. PARSING, VALIDATION & BULK IMPORT ENGINE
# ==============================================================================

def _normalize_course_header(header_name):
    """Normalize user spreadsheet column headers to standard internal keys."""
    if not header_name:
        return ""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(header_name).strip().lower()).strip("_")

    alias_map = {
        "course_code": "code",
        "code": "code",
        "course_id": "code",
        "subject_code": "code",
        "course_title": "title",
        "title": "title",
        "course_name": "title",
        "name": "title",
        "department_code": "department_code",
        "department": "department_code",
        "dept_code": "department_code",
        "dept": "department_code",
        "program_code": "program_code",
        "program": "program_code",
        "programme_code": "program_code",
        "programme": "program_code",
        "credits": "credits",
        "credit": "credits",
        "credit_hours": "credits",
        "units": "credits",
        "semester": "semester_no",
        "semester_no": "semester_no",
        "sem": "semester_no",
        "term": "semester_no",
        "status": "status",
        "course_status": "status",
        "faculty": "faculty_id",
        "faculty_id": "faculty_id",
        "lecturer": "faculty_id",
        "instructor": "faculty_id",
        "assigned_faculty": "faculty_id",
        "teacher": "faculty_id",
        "description": "description",
        "desc": "description",
        "course_description": "description",
    }
    return alias_map.get(s, s)


def parse_uploaded_course_file(uploaded_file):
    """Read CSV or Excel rows as a list of dicts for courses."""
    filename = getattr(uploaded_file, "name", "") or "upload.csv"
    filename = filename.lower()
    rows = []

    if filename.endswith(".csv"):
        raw_bytes = uploaded_file.read()
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1")

        reader = csv.reader(io.StringIO(text))
        header_row = None
        for r in reader:
            if not any(str(c).strip() for c in r):
                continue
            if header_row is None:
                header_row = [_normalize_course_header(c) for c in r]
                continue

            row_dict = {}
            for idx, col_key in enumerate(header_row):
                val = r[idx].strip() if idx < len(r) else ""
                row_dict[col_key] = val
            rows.append(row_dict)

    elif filename.endswith((".xlsx", ".xls")):
        wb = openpyxl.load_workbook(uploaded_file, data_only=True)
        ws = wb.active
        header_row = None
        for r in ws.iter_rows(values_only=True):
            if not any(r):
                continue
            if header_row is None:
                header_row = [_normalize_course_header(c) for c in r]
                continue

            row_dict = {}
            for idx, col_key in enumerate(header_row):
                val = str(r[idx]).strip() if idx < len(r) and r[idx] is not None else ""
                row_dict[col_key] = val
            rows.append(row_dict)

    return rows


def validate_course_import_rows(raw_rows):
    """
    Validate parsed course rows before importing into database.
    Checks:
    - Required fields: code, title, department_code
    - Department validity (matches code or name)
    - Program validity (matches code or name)
    - Faculty assignment (matches employee_id, username, or display_name)
    - Credits range: 1-10
    - Semester range: 1-12
    - Duplicate course code in file or database
    """
    # Pre-cache departments, programs, and faculty
    depts_by_code = {d.code.upper(): d for d in Department.objects.all()}
    depts_by_name = {d.name.lower(): d for d in Department.objects.all()}

    progs_by_code = {p.code.upper(): p for p in Program.objects.select_related("department").all()}
    progs_by_name = {p.name.lower(): p for p in Program.objects.select_related("department").all()}

    faculty_by_eid = {f.employee_id.upper(): f for f in FacultyProfile.objects.select_related("user").all()}
    faculty_by_username = {f.user.username.lower(): f for f in FacultyProfile.objects.select_related("user").all()}

    existing_codes = set(Course.objects.values_list("code", flat=True))
    seen_codes = set()

    validated_items = []
    valid_count = 0
    duplicate_count = 0
    error_count = 0

    for idx, raw in enumerate(raw_rows, start=2):
        errors = []
        is_duplicate = False

        code = raw.get("code", "").strip().upper()
        title = raw.get("title", "").strip()
        dept_raw = raw.get("department_code", "").strip()
        prog_raw = raw.get("program_code", "").strip()
        credits_raw = raw.get("credits", "4").strip() or "4"
        semester_raw = raw.get("semester_no", "1").strip() or "1"
        status_raw = raw.get("status", "Active").strip() or "Active"
        faculty_raw = raw.get("faculty_id", "").strip()
        description = raw.get("description", "").strip()

        # Required fields validation
        if not code:
            errors.append("Course Code is required.")
        if not title:
            errors.append("Course Title is required.")
        if not dept_raw:
            errors.append("Department Code is required.")

        # Department resolution
        dept_obj = None
        if dept_raw:
            dept_obj = depts_by_code.get(dept_raw.upper()) or depts_by_name.get(dept_raw.lower())
            if not dept_obj:
                errors.append(f"Unknown department '{dept_raw}'.")

        # Program resolution
        prog_obj = None
        if prog_raw:
            prog_obj = progs_by_code.get(prog_raw.upper()) or progs_by_name.get(prog_raw.lower())
            if not prog_obj:
                errors.append(f"Unknown program '{prog_raw}'.")

        # Faculty resolution
        faculty_obj = None
        if faculty_raw:
            faculty_obj = faculty_by_eid.get(faculty_raw.upper()) or faculty_by_username.get(faculty_raw.lower())
            if not faculty_obj:
                # Try partial match on display_name
                matched = [f for f in faculty_by_eid.values() if faculty_raw.lower() in f.user.display_name.lower()]
                if matched:
                    faculty_obj = matched[0]
                else:
                    errors.append(f"Unknown faculty member '{faculty_raw}'.")

        # Credits validation
        credits_val = 4
        try:
            credits_val = int(credits_raw)
            if not (1 <= credits_val <= 10):
                errors.append(f"Credits must be between 1 and 10 (got {credits_val}).")
        except ValueError:
            errors.append(f"Invalid credits number '{credits_raw}'.")

        # Semester validation
        semester_val = 1
        try:
            semester_val = int(semester_raw)
            if not (1 <= semester_val <= 12):
                errors.append(f"Semester must be between 1 and 12 (got {semester_val}).")
        except ValueError:
            errors.append(f"Invalid semester number '{semester_raw}'.")

        # Status validation
        normalized_status = "Active"
        if status_raw.lower() in ["archived", "archive", "inactive"]:
            normalized_status = "Archived"
        elif status_raw.lower() in ["upcoming", "pending", "draft"]:
            normalized_status = "Upcoming"
        elif status_raw.lower() in ["active", "published"]:
            normalized_status = "Active"
        else:
            errors.append(f"Invalid status '{status_raw}'. Allowed: Active, Archived, Upcoming.")

        # Duplicate checking
        dup_reasons = []
        if code:
            if code in seen_codes:
                dup_reasons.append(f"Duplicate Course Code '{code}' in this file")
            elif code in existing_codes:
                dup_reasons.append(f"Course Code '{code}' already exists in database")

        has_validation_errors = bool(errors)
        if dup_reasons:
            is_duplicate = True
            errors.extend(dup_reasons)

        if code:
            seen_codes.add(code)

        if has_validation_errors:
            status = "error"
            error_count += 1
        elif is_duplicate:
            status = "duplicate"
            duplicate_count += 1
        else:
            status = "valid"
            valid_count += 1

        validated_items.append({
            "row_num": idx,
            "status": status,
            "code": code,
            "title": title,
            "department_code": dept_obj.code if dept_obj else dept_raw,
            "department_id": dept_obj.id if dept_obj else None,
            "program_code": prog_obj.code if prog_obj else (prog_raw or None),
            "program_id": prog_obj.id if prog_obj else None,
            "faculty_name": faculty_obj.user.display_name if faculty_obj else (faculty_raw or "TBA"),
            "faculty_id": faculty_obj.id if faculty_obj else None,
            "credits": credits_val,
            "semester_no": semester_val,
            "course_status": normalized_status,
            "description": description,
            "errors": errors,
        })

    return {
        "items": validated_items,
        "total_count": len(raw_rows),
        "valid_count": valid_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
    }


def execute_course_import(valid_items):
    """Atomically commit valid course items to the database."""
    imported_count = 0
    failed_count = 0

    with transaction.atomic():
        for item in valid_items:
            try:
                dept_id = item.get("department_id")
                if not dept_id:
                    failed_count += 1
                    continue

                prog_id = item.get("program_id")
                faculty_id = item.get("faculty_id")

                Course.objects.update_or_create(
                    code=item["code"],
                    defaults={
                        "title": item["title"],
                        "department_id": dept_id,
                        "program_id": prog_id,
                        "faculty_id": faculty_id,
                        "credits": item.get("credits", 4),
                        "semester_no": item.get("semester_no", 1),
                        "status": item.get("course_status", "Active"),
                        "description": item.get("description", ""),
                    }
                )
                imported_count += 1
            except Exception:
                failed_count += 1

    return imported_count, failed_count
