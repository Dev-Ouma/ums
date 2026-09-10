import csv
import io
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Table, TableStyle
)

from accounts.models import FacultyProfile, Role
from university.models import Department
from university.student_io import NumberedCanvas

User = get_user_model()

FACULTY_IMPORT_COLUMNS = [
    ("employee_id", "Employee ID", True),
    ("first_name", "First Name", True),
    ("last_name", "Last Name", True),
    ("email", "Email", True),
    ("phone", "Phone", False),
    ("username", "Username", False),
    ("department_code", "Department Code", False),
    ("designation", "Designation", False),
    ("specialization", "Specialization", False),
]

SAMPLE_FACULTY_ROW = {
    "employee_id": "FAC202601",
    "first_name": "Dr. Sarah",
    "last_name": "Connor",
    "email": "sarah.connor@example.com",
    "phone": "+254 722 000 111",
    "username": "sarah.connor",
    "department_code": "CSE",
    "designation": "Associate Professor",
    "specialization": "Machine Learning & Robotics",
}


# ==============================================================================
# 1. EXPORT FUNCTIONS (CSV, EXCEL, PDF)
# ==============================================================================

def export_faculty_csv(queryset):
    """Generate CSV byte string with UTF-8 BOM."""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    headers = [
        "Employee ID", "First Name", "Last Name", "Full Name", "Designation",
        "Department Code", "Department Name", "Specialization", "Assigned Courses",
        "Email", "Phone", "Joining Date"
    ]
    writer.writerow(headers)

    for fp in queryset:
        u = fp.user
        dept_code = fp.department.code if fp.department else "—"
        dept_name = fp.department.name if fp.department else "—"
        joining = fp.joining_date.strftime("%Y-%m-%d") if fp.joining_date else "—"
        
        # Course list
        courses = [c.code for c in fp.courses.all()]
        courses_str = ", ".join(courses) if courses else "0 courses"

        writer.writerow([
            fp.employee_id,
            u.first_name,
            u.last_name,
            u.display_name,
            fp.designation,
            dept_code,
            dept_name,
            fp.specialization or "General",
            courses_str,
            u.email,
            u.phone,
            joining,
        ])

    return buffer.getvalue().encode("utf-8")


def export_faculty_excel(queryset, site_name="University Management System"):
    """Generate professional styled Excel (.xlsx) file for Faculty."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Faculty"
    ws.views.sheetView[0].showGridLines = True

    primary_color = "6C5CE7"      # Faculty teal theme
    header_fill_color = "4834D4"  # Dark teal
    zebra_color = "EEF2FF"
    border_color = "CBD5E1"

    font_title = Font(name="Arial", size=15, bold=True, color="FFFFFF")
    font_sub = Font(name="Arial", size=10, italic=True, color="E0E7FF")
    font_header = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Arial", size=10, color="2B2B3A")
    font_bold_data = Font(name="Arial", size=10, bold=True, color="2B2B3A")

    thin_border = Border(
        left=Side(style="thin", color=border_color),
        right=Side(style="thin", color=border_color),
        top=Side(style="thin", color=border_color),
        bottom=Side(style="thin", color=border_color),
    )

    # 1. Title Banner
    ws.merge_cells("A1:K1")
    title_cell = ws["A1"]
    title_cell.value = f"  {site_name.upper()} — FACULTY DIRECTORY"
    title_cell.font = font_title
    title_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    title_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 36

    # 2. Subtitle / Metadata Row
    ws.merge_cells("A2:K2")
    sub_cell = ws["A2"]
    sub_cell.value = f"  Generated: {timezone.now().strftime('%d %b %Y, %H:%M')} | Total Faculty: {queryset.count()}"
    sub_cell.font = font_sub
    sub_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    sub_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[2].height = 20

    ws.row_dimensions[3].height = 8

    # 3. Column Headers
    headers = [
        "Faculty ID", "Name", "Designation", "Department", "Specialization",
        "Courses", "Email", "Phone", "Joining Date"
    ]
    header_row_idx = 4
    ws.row_dimensions[header_row_idx].height = 26

    for col_idx, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row_idx, column=col_idx, value=h_text)
        cell.font = font_header
        cell.fill = PatternFill(start_color=header_fill_color, end_color=header_fill_color, fill_type="solid")
        cell.alignment = Alignment(horizontal="center" if col_idx in [1, 6, 9] else "left", vertical="center")
        cell.border = thin_border

    # 4. Data Rows
    for row_idx, fp in enumerate(queryset, start=5):
        u = fp.user
        dept_str = f"{fp.department.name} ({fp.department.code})" if fp.department else "—"
        joining = fp.joining_date.strftime("%Y-%m-%d") if fp.joining_date else "—"
        is_even = (row_idx % 2 == 0)
        row_fill = PatternFill(start_color=zebra_color, end_color=zebra_color, fill_type="solid") if is_even else None

        courses = [c.code for c in fp.courses.all()]
        courses_str = ", ".join(courses) if courses else "0"

        values = [
            fp.employee_id,
            u.display_name,
            fp.designation,
            dept_str,
            fp.specialization or "General",
            courses_str,
            u.email,
            u.phone,
            joining,
        ]

        ws.row_dimensions[row_idx].height = 20
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_bold_data if col_idx in [1, 2] else font_data
            if row_fill:
                cell.fill = row_fill
            cell.border = thin_border
            cell.alignment = Alignment(
                horizontal="center" if col_idx in [1, 6, 9] else "left",
                vertical="center"
            )

    # 5. Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in [1, 2, 3]:
                continue
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    ws.freeze_panes = "A5"

    output = io.BytesIO()
    finish_worksheet(ws, header_row=4)
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def export_faculty_pdf(queryset, site_name="University Management System", logo_path=None, filter_text=None):
    """Generate high-quality branded PDF report for Faculty in landscape A4."""
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=45
    )

    styles = document_styles()

    title_style = ParagraphStyle(
        "FacultyReportTitle",
        fontName="Quicksand-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1E293B"),
    )
    meta_style = ParagraphStyle(
        "FacultyReportMeta",
        fontName="Quicksand-Bold",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#6C5CE7"),
        alignment=2,
    )
    th_style = ParagraphStyle(
        "FacultyTH",
        fontName="Quicksand-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
        alignment=0,
    )
    td_style = ParagraphStyle(
        "FacultyTD",
        fontName="Quicksand",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
    )
    td_bold = ParagraphStyle(
        "FacultyTDBold",
        fontName="Quicksand-Bold",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
    )
    td_center = ParagraphStyle(
        "FacultyTDCenter",
        fontName="Quicksand",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
        alignment=1,
    )

    story = []
    branding = get_branding()
    branding.update(site_name=site_name, logo_path=logo_path or branding['logo_path'])
    story.append(letterhead(doc.width, 'Faculty Directory',
        subtitle=f"Total records: {queryset.count()}", filter_text=filter_text, branding=branding))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#6C5CE7"), spaceAfter=12))

    # 2. Faculty Table
    table_headers = [
        Paragraph("Faculty ID", th_style),
        Paragraph("Faculty Name", th_style),
        Paragraph("Designation", th_style),
        Paragraph("Department", th_style),
        Paragraph("Specialization", th_style),
        Paragraph("Courses", th_style),
        Paragraph("Email Address", th_style),
        Paragraph("Phone", th_style),
    ]
    table_data = [table_headers]

    for fp in queryset:
        u = fp.user
        dept = fp.department.name if fp.department else "—"
        courses = [c.code for c in fp.courses.all()]
        courses_str = ", ".join(courses) if courses else "0"

        table_data.append([
            Paragraph(fp.employee_id, td_bold),
            Paragraph(u.display_name, td_style),
            Paragraph(fp.designation, td_style),
            Paragraph(dept, td_style),
            Paragraph(fp.specialization or "General", td_style),
            Paragraph(courses_str, td_center),
            Paragraph(u.email, td_style),
            Paragraph(u.phone or "—", td_style),
        ])

    # Total width ~770pt
    col_widths = [75, 125, 110, 115, 115, 50, 120, 60]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C5CE7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
    ]

    for r in range(1, len(table_data)):
        if r % 2 == 0:
            t_style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#EEF2FF")))

    t.setStyle(TableStyle(t_style))
    story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()


# ==============================================================================
# 2. TEMPLATE GENERATION (CSV & EXCEL)
# ==============================================================================

def generate_faculty_template_csv():
    """Generate CSV template with Row 1 headers and Row 2 prefilled sample data."""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    headers = [col[1] for col in FACULTY_IMPORT_COLUMNS]
    writer.writerow(headers)

    sample_row = [
        SAMPLE_FACULTY_ROW["employee_id"],
        SAMPLE_FACULTY_ROW["first_name"],
        SAMPLE_FACULTY_ROW["last_name"],
        SAMPLE_FACULTY_ROW["email"],
        SAMPLE_FACULTY_ROW["phone"],
        SAMPLE_FACULTY_ROW["username"],
        SAMPLE_FACULTY_ROW["department_code"],
        SAMPLE_FACULTY_ROW["designation"],
        SAMPLE_FACULTY_ROW["specialization"],
    ]
    writer.writerow(sample_row)
    return buffer.getvalue().encode("utf-8")


def generate_faculty_template_excel():
    """Generate Excel template with Row 1 headers and Row 2 prefilled sample data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Faculty Import Template"
    ws.views.sheetView[0].showGridLines = True

    header_fill = PatternFill(start_color="6C5CE7", end_color="6C5CE7", fill_type="solid")
    sample_fill = PatternFill(start_color="EEF2FF", end_color="EEF2FF", fill_type="solid")
    font_header = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    font_sample = Font(name="Arial", size=10, italic=True, color="4834D4")

    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    headers = [col[1] for col in FACULTY_IMPORT_COLUMNS]
    ws.row_dimensions[1].height = 26
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    sample_values = [
        SAMPLE_FACULTY_ROW["employee_id"],
        SAMPLE_FACULTY_ROW["first_name"],
        SAMPLE_FACULTY_ROW["last_name"],
        SAMPLE_FACULTY_ROW["email"],
        SAMPLE_FACULTY_ROW["phone"],
        SAMPLE_FACULTY_ROW["username"],
        SAMPLE_FACULTY_ROW["department_code"],
        SAMPLE_FACULTY_ROW["designation"],
        SAMPLE_FACULTY_ROW["specialization"],
    ]
    ws.row_dimensions[2].height = 22
    for col_idx, val in enumerate(sample_values, start=1):
        cell = ws.cell(row=2, column=col_idx, value=val)
        cell.font = font_sample
        cell.fill = sample_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = thin_border

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ==============================================================================
# 3. IMPORT PARSER & VALIDATOR
# ==============================================================================

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _normalize_faculty_header(name):
    """Normalize column header: 'Employee ID' -> 'employee_id'."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(name).strip().lower()).strip("_")
    alias_map = {
        "faculty_id": "employee_id",
        "staff_id": "employee_id",
        "emp_id": "employee_id",
        "id": "employee_id",
        "fname": "first_name",
        "lname": "last_name",
        "position": "designation",
        "title": "designation",
        "dept": "department_code",
        "department": "department_code",
        "dept_code": "department_code",
        "mail": "email",
        "e_mail": "email",
        "tel": "phone",
        "phone_number": "phone",
        "mobile": "phone",
        "user_name": "username",
        "specialty": "specialization",
        "subject": "specialization",
    }
    return alias_map.get(s, s)


def parse_uploaded_faculty_file(uploaded_file):
    """Read CSV or Excel rows as a list of dicts for faculty."""
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
                header_row = [_normalize_faculty_header(c) for c in r]
                continue
            row_dict = {}
            for h, val in zip(header_row, r):
                if h:
                    row_dict[h] = str(val).strip()
            rows.append(row_dict)

    elif filename.endswith((".xlsx", ".xls")):
        wb = openpyxl.load_workbook(uploaded_file, data_only=True)
        ws = wb.active
        header_row = None
        for r in ws.iter_rows(values_only=True):
            if not any(c is not None and str(c).strip() for c in r):
                continue
            if header_row is None:
                header_row = [_normalize_faculty_header(c) for c in r]
                continue
            row_dict = {}
            for h, val in zip(header_row, r):
                if h:
                    row_dict[h] = "" if val is None else str(val).strip()
            rows.append(row_dict)
    else:
        raise ValueError("Unsupported format. Please upload a .csv or .xlsx file.")

    return rows


def validate_faculty_import_rows(raw_rows):
    """
    Validate faculty rows, detect intra-file and database duplicates,
    and return structured result for admin preview before commit.
    """
    existing_eids = set(FacultyProfile.objects.values_list("employee_id", flat=True))
    existing_usernames = set(User.objects.values_list("username", flat=True))
    existing_emails = set(User.objects.values_list("email", flat=True))

    depts_by_code = {d.code.upper(): d for d in Department.objects.all() if d.code}
    depts_by_name = {d.name.strip().lower(): d for d in Department.objects.all() if d.name}

    seen_eids = set()
    seen_usernames = set()
    seen_emails = set()

    validated_items = []
    valid_count = 0
    duplicate_count = 0
    error_count = 0

    for idx, raw in enumerate(raw_rows, start=2):
        errors = []
        is_duplicate = False

        employee_id = raw.get("employee_id", "").strip()
        first_name = raw.get("first_name", "").strip()
        last_name = raw.get("last_name", "").strip()
        email = raw.get("email", "").strip()
        phone = raw.get("phone", "0000").strip() or "0000"

        username = raw.get("username", "").strip()
        if not username and email:
            username = email.split("@")[0]
        if not username:
            username = f"{first_name.lower()}.{last_name.lower()}" if first_name else f"faculty_{idx}"
        username = re.sub(r"[^a-zA-Z0-9._-]", "", username)

        # Tolerated in legacy files, never invented: a row without a password
        # gets a single-use activation link instead.
        password = raw.get("password", "").strip()
        dept_raw = raw.get("department_code", "").strip()
        designation = raw.get("designation", "").strip() or "Assistant Professor"
        specialization = raw.get("specialization", "").strip()

        if not employee_id:
            errors.append("Employee ID is required.")
        if not first_name:
            errors.append("First name is required.")
        if not last_name:
            errors.append("Last name is required.")
        if not email:
            errors.append("Email is required.")
        elif not EMAIL_REGEX.match(email):
            errors.append(f"Invalid email format '{email}'.")

        # Department resolution
        dept_obj = None
        if dept_raw:
            dept_obj = depts_by_code.get(dept_raw.upper()) or depts_by_name.get(dept_raw.lower())
            if not dept_obj:
                errors.append(f"Unknown department '{dept_raw}'.")

        # Duplicate detection
        dup_reasons = []
        if employee_id:
            if employee_id in seen_eids:
                dup_reasons.append(f"Duplicate Employee ID '{employee_id}' in this file")
            elif employee_id in existing_eids:
                dup_reasons.append(f"Employee ID '{employee_id}' already registered in database")

        if username:
            if username in seen_usernames:
                dup_reasons.append(f"Duplicate Username '{username}' in this file")
            elif username in existing_usernames:
                dup_reasons.append(f"Username '{username}' already taken in database")

        if email:
            if email in seen_emails:
                dup_reasons.append(f"Duplicate Email '{email}' in this file")
            elif email in existing_emails:
                dup_reasons.append(f"Email '{email}' already exists in database")

        has_validation_errors = bool(errors)
        if dup_reasons:
            is_duplicate = True
            errors.extend(dup_reasons)

        if employee_id:
            seen_eids.add(employee_id)
        if username:
            seen_usernames.add(username)
        if email:
            seen_emails.add(email)

        if has_validation_errors:
            status = "error"
            error_count += 1
        elif is_duplicate:
            status = "duplicate"
            duplicate_count += 1
        else:
            status = "valid"
            valid_count += 1

        item = {
            "row_num": idx,
            "employee_id": employee_id,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}".strip(),
            "email": email,
            "phone": phone,
            "username": username,
            "password": password,
            "department_id": dept_obj.id if dept_obj else None,
            "department_code": dept_obj.code if dept_obj else dept_raw,
            "department_name": dept_obj.name if dept_obj else "—",
            "designation": designation,
            "specialization": specialization,
            "status": status,
            "errors": errors,
        }
        validated_items.append(item)

    return {
        "items": validated_items,
        "total_count": len(validated_items),
        "valid_count": valid_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
    }


# ==============================================================================
# 4. BATCH EXECUTION
# ==============================================================================

def execute_faculty_import(valid_items, actor=None):
    """
    Atomically insert validated faculty records and their central accounts.

    Accounts come from the user management service, so an imported member of
    staff gets the same identity envelope as one created by hand: one account,
    an institutional email, an audit entry and an activation link rather than a
    shared starting password.
    Returns (imported_count, failed_count).
    """
    from university.identity_models import UserType
    from university.identity_services import create_user_account, provision_staff_account

    imported_count = 0
    failed_count = 0

    for item in valid_items:
        try:
            with transaction.atomic():
                if FacultyProfile.objects.filter(employee_id=item["employee_id"]).exists():
                    failed_count += 1
                    continue
                if User.objects.filter(username=item["username"]).exists():
                    failed_count += 1
                    continue

                supplied_password = item.get("password") or ""
                created = create_user_account(
                    user_type=UserType.STAFF,
                    first_name=item["first_name"],
                    last_name=item["last_name"],
                    email=item["email"],
                    username=item["username"],
                    phone=item["phone"],
                    role=Role.FACULTY,
                    password_mode="MANUAL" if supplied_password else "LINK",
                    password=supplied_password or None,
                    actor=actor,
                    notify=False,
                )
                user = created["user"]

                fp = FacultyProfile(
                    user=user,
                    employee_id=item["employee_id"],
                    department_id=item["department_id"],
                    designation=item["designation"],
                    specialization=item["specialization"],
                    joining_date=timezone.now().date(),
                )
                fp.save()
                provision_staff_account(fp, actor=actor, notify=False)
                imported_count += 1
        except Exception:
            # One bad row must not cost the whole batch.
            failed_count += 1

    return imported_count, failed_count

