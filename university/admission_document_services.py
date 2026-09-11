import io
import os
import re
from datetime import timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, Spacer, Table, TableStyle
)

from university.document_design import (
    ReportDocTemplate, document_styles, get_branding
)
from university.models import (
    AcademicTerm, AcademicYear, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue,
    AdmissionDocumentTemplate, AuditLog, DocumentDeliveryLog,
    FeeStructure, IssuedAdmissionDocument
)
from university.audit_services import log_activity
from university.academic_calendar_services import get_current_academic_year, get_current_semester
from university.student_numbering_services import generate_student_registration_number

User = get_user_model()


def build_dynamic_fields_catalog():
    """
    Returns a structured dictionary of all available template placeholders
    categorized by domain, including active custom application fields.
    """
    catalog = {
        "Student": [
            ("student_name", "Full applicant / student name (e.g. John Kamau Mwangi)"),
            ("first_name", "First / Given name"),
            ("last_name", "Surname / Family name"),
            ("email", "Applicant / Student email address"),
            ("phone", "Applicant / Student phone number"),
            ("national_id", "National ID / Passport Number"),
            ("date_of_birth", "Date of Birth (formatted)"),
            ("gender", "Gender (Male / Female / Other)"),
            ("address", "Postal and physical address"),
        ],
        "Application": [
            ("application_number", "Unique application code (e.g. APP-2026-0001)"),
            ("intake", "Intake name (e.g. September 2026 Academic Intake)"),
            ("secondary_school", "Secondary / High school attended"),
            ("kcse_index_number", "KCSE index number"),
            ("kcse_mean_grade", "KCSE mean grade (e.g. B+)"),
            ("kcse_year", "KCSE examination year"),
            ("application_date", "Date application was submitted"),
        ],
        "Admission": [
            ("admission_number", "Official student admission / registration number"),
            ("registration_number", "Formal assigned student roll/registration number"),
            ("admission_date", "Formal admission offer date"),
            ("reporting_date", "Scheduled student reporting / orientation date"),
            ("document_reference", "Institutional document reference number (e.g. UMS/ADM/2026/001)"),
            ("issue_date", "Official date of document generation / issuance"),
            ("version", "Document version number (e.g. 1, 2)"),
        ],
        "Programme": [
            ("programme_name", "Full programme name (e.g. Bachelor of Science in Computer Science)"),
            ("programme_code", "Programme code (e.g. BCS, BCT)"),
            ("award_title", "Award title (e.g. Bachelor of Science)"),
            ("level", "Academic level (Undergraduate, Postgraduate, Diploma, Certificate)"),
            ("duration_years", "Prescribed study duration in years"),
        ],
        "Department & Faculty": [
            ("department_name", "Academic department name"),
            ("department_code", "Department short code"),
            ("faculty_name", "Faculty / School name"),
            ("faculty_code", "Faculty / School code"),
        ],
        "Academic Year & Semester": [
            ("academic_year", "Academic Year (e.g. 2026/2027)"),
            ("semester", "Academic Semester / Term (e.g. Semester 1)"),
            ("semester_number", "Semester sequence number (e.g. 1)"),
        ],
        "Institution": [
            ("university_name", "Official university name"),
            ("university_address", "University physical and postal address"),
            ("university_email", "Admissions email address"),
            ("university_phone", "Admissions inquiries phone number"),
            ("portal_url", "Student web portal address"),
            ("verification_url", "Admission verification link"),
        ],
        "Finance": [
            ("tuition_fee", "First semester tuition fees (e.g. KES 45,000.00)"),
            ("total_fees", "Total estimated fees for first semester (e.g. KES 55,500.00)"),
            ("bank_name", "University depository bank (e.g. Absa Bank Kenya)"),
            ("bank_account", "University fee collection account number"),
            ("bank_branch", "Depository branch"),
            ("mpesa_paybill", "M-Pesa paybill business number"),
        ],
        "Document & Authority": [
            ("signatory_name", "Authorized officer name"),
            ("signatory_title", "Authorized officer official designation"),
        ]
    }

    # Dynamically inject active custom application fields
    custom_fields = ApplicationCustomField.objects.filter(is_active=True).order_by("display_order")
    if custom_fields.exists():
        catalog["Custom Application Fields"] = [
            (cf.name, f"{cf.label} (Custom application field)") for cf in custom_fields
        ]

    return catalog


def build_admission_document_context(application, document=None, template=None, custom_overrides=None):
    """
    Extracts live database values and produces a full key-value context dictionary
    for document placeholders.
    """
    branding = get_branding()
    active_ay = get_current_academic_year()
    active_term = get_current_semester()

    # Academic Year resolution
    if document and document.academic_year:
        ay_str = document.academic_year.name
    elif application.intake and application.intake.academic_year:
        ay_str = application.intake.academic_year.name
    elif active_ay:
        ay_str = active_ay.name
    else:
        ay_str = "Academic year to be confirmed"

    # Semester resolution
    if document and document.semester:
        sem_str = document.semester.name
        sem_no = str(getattr(document.semester, "semester_number", 1))
    elif active_term:
        sem_str = active_term.name
        sem_no = str(getattr(active_term, "semester_number", 1))
    else:
        sem_str = "Semester to be confirmed"
        sem_no = ""

    # Programme resolution
    prog = application.program
    dept = prog.department if prog else None
    fac = dept.school if dept else None

    # Registration number
    if application.student and application.student.roll_no:
        reg_no = application.student.roll_no
    elif application.admitted_reg_no:
        reg_no = application.admitted_reg_no
    else:
        reg_no = generate_student_registration_number(program=prog, intake=application.intake)

    # Dates
    rep_date = (document.reporting_date if (document and document.reporting_date)
                else (application.reporting_date or (timezone.now().date() + timedelta(days=21))))
    reporting_str = rep_date.strftime("%A, %d %B %Y")
    issue_dt = (document.issue_date if (document and document.issue_date) else timezone.now().date())
    issue_str = issue_dt.strftime("%d %B %Y")
    dob_str = application.date_of_birth.strftime("%d %B %Y") if application.date_of_birth else "N/A"
    app_date_str = application.created_at.strftime("%d %B %Y") if application.created_at else issue_str

    # Fee estimates
    fee_struct = FeeStructure.objects.filter(program=prog, year_of_study=1, semester=1).first()
    tuition_val = fee_struct.tuition_fee if fee_struct else Decimal("45000.00")
    total_fee_val = fee_struct.total_fee if fee_struct else Decimal("55500.00")

    # Document reference
    doc_ref = document.document_reference if document else f"UMS/ADM/{issue_dt.year}/{application.application_number.split('-')[-1]}"

    # Signatory details
    sig_name = template.signatory_name if template else "Dr. Margaret Omolo, PhD"
    sig_title = template.signatory_title if template else "Registrar, Academic & Student Affairs"
    ver_base = template.verification_base_url if template else "https://ums.ac.ke/verify-admission/"

    salutation_title = "Ms." if application.gender == "FEMALE" else ("Mr." if application.gender == "MALE" else "")
    student_title_name = f"{salutation_title} {application.last_name.upper()}".strip() if salutation_title else application.full_name.upper()

    context = {
        # Student
        "student_name": application.full_name,
        "first_name": application.first_name,
        "last_name": application.last_name,
        "title": salutation_title,
        "title_name": student_title_name,
        "applicant_title_name": f"{salutation_title} {application.full_name}".strip() if salutation_title else application.full_name,
        "email": application.email,
        "phone": application.phone,
        "national_id": application.national_id,
        "date_of_birth": dob_str,
        "gender": application.get_gender_display() if hasattr(application, "get_gender_display") else application.gender,
        "address": application.address or "Nairobi, Kenya",

        # Application
        "application_number": application.application_number,
        "intake": application.intake.name if application.intake else "Intake to be confirmed",
        "secondary_school": application.secondary_school or "Not provided",
        "kcse_index_number": application.kcse_index_number or "N/A",
        "kcse_mean_grade": application.kcse_mean_grade or "Not provided",
        "kcse_year": str(application.kcse_year) if application.kcse_year else "Not provided",
        "application_date": app_date_str,

        # Admission
        "admission_number": reg_no,
        "registration_number": reg_no,
        "admission_date": issue_str,
        "reporting_date": reporting_str,
        "document_reference": doc_ref,
        "issue_date": issue_str,
        "version": str(document.version if document else 1),

        # Programme
        "programme_name": prog.name if prog else "Undergraduate Degree Programme",
        "programme_code": prog.code if prog else "UG",
        "award_title": prog.award_title if (prog and hasattr(prog, "award_title") and prog.award_title) else "Bachelor Degree",
        "level": prog.get_level_display() if (prog and hasattr(prog, "get_level_display")) else "Undergraduate",
        "duration_years": str(getattr(prog, "duration_years", 4)),

        # Department & Faculty
        "department_name": dept.name if dept else "Academic Department",
        "department_code": dept.code if dept else "DEPT",
        "faculty_name": fac.name if fac else "Faculty of Academic Studies",
        "faculty_code": fac.code if fac else "FAC",

        # Academic Period
        "academic_year": ay_str,
        "semester": sem_str,
        "semester_number": sem_no,

        # Institution
        "university_name": branding.get("site_name", "University Management System"),
        "university_address": branding.get("address", "P.O. Box 90100 - 00100, GPO, Nairobi, Kenya"),
        "university_email": branding.get("admissions_email", "admissions@ums.ac.ke"),
        "university_phone": branding.get("phone", "+254 (0) 20 123 4567"),
        "admissions_desk_phone": "+254 (0) 20 123 4567 / +254 700 000 000",
        "portal_url": branding.get("portal_url", "https://portal.ums.ac.ke"),
        "verification_url": f"{ver_base.rstrip('/')}/{doc_ref.replace('/', '-')}",

        # Finance
        "tuition_fee": f"KES {tuition_val:,.2f}",
        "total_fees": f"KES {total_fee_val:,.2f}",
        "finance_email": "finance@ums.ac.ke",
        "bank_name": "Absa Bank Kenya PLC",
        "bank_account": "03-094-8002145",
        "bank_branch": "University Way Branch",
        "mpesa_paybill": "222111",

        # Signatory
        "signatory_name": sig_name,
        "signatory_title": sig_title,
    }

    # Load custom application values
    custom_values = ApplicationCustomFieldValue.objects.filter(application=application).select_related("field")
    for cv in custom_values:
        context[cv.field.name] = cv.value

    # Apply manual overrides if provided
    if custom_overrides and isinstance(custom_overrides, dict):
        context.update(custom_overrides)

    return context


def render_template_text(template_string, context):
    """
    Substitutes {{placeholder}} tokens with values from context.
    Safely handles missing keys by leaving or clearing them.
    """
    if not template_string:
        return ""

    def replace_token(match):
        token = match.group(1).strip()
        val = context.get(token)
        if val is not None:
            return str(val)
        return ""

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", replace_token, template_string)


def get_or_create_default_template(program=None, academic_year=None):
    """
    Returns the most specific active AdmissionDocumentTemplate for the given
    program/academic year, or falls back to the universal default template.
    """
    # 1. Look for program + academic year specific template
    if program and academic_year:
        tmpl = AdmissionDocumentTemplate.objects.filter(
            program=program, academic_year=academic_year, is_active=True
        ).first()
        if tmpl:
            return tmpl

    # 2. Look for program-specific template
    if program:
        tmpl = AdmissionDocumentTemplate.objects.filter(
            program=program, academic_year__isnull=True, is_active=True
        ).first()
        if tmpl:
            return tmpl

    # 3. Look for academic-year default template
    if academic_year:
        tmpl = AdmissionDocumentTemplate.objects.filter(
            academic_year=academic_year, program__isnull=True, is_active=True
        ).first()
        if tmpl:
            return tmpl

    # 4. Universal default
    tmpl = AdmissionDocumentTemplate.objects.filter(
        is_default=True, is_active=True
    ).first()
    if tmpl:
        return tmpl

    # 5. First active template
    tmpl = AdmissionDocumentTemplate.objects.filter(is_active=True).first()
    if tmpl:
        return tmpl

    # 6. Bootstrap standard default template
    tmpl = AdmissionDocumentTemplate.objects.create(
        name="Official Undergraduate Admission Offer",
        document_type=AdmissionDocumentTemplate.DocumentType.ADMISSION_LETTER,
        is_active=True,
        is_default=True,
        version=1,
        header_title="Office of the Deputy Vice-Chancellor<br/>(Academic Affairs)",
        salutation_template="Dear {{title_name}}, Admission Number: {{registration_number}}",
        subject_template="RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR",
        body_template=(
            "Following your application for admission to {{university_name}}, I wish to congratulate you on this achievement. "
            "You have been admitted on the basis of your qualifications, which are subject to verification by the University. "
            "When reporting, you will be required to present original and copies of the following:\n\n"
            "1. KCSE Certificate or Result Slip\n"
            "2. Birth Certificate\n"
            "3. National Identity Card or Passport\n"
            "4. Two coloured passport-size photographs\n"
            "5. Proof of payment of tuition fees"
        ),
        fee_schedule_instructions=(
            "TUITION FEES\n"
            "You will pay {{tuition_fee}} as tuition fee in a Semester (Estimated total first-semester university charges: {{total_fees}}). "
            "For more information, please contact the Finance Office at {{finance_email}} or Tel: {{university_phone}}.\n\n"
            "FEE PAYMENT\n"
            "You are required to follow the instructions below to pay the tuition fee:\n"
            "1. While logged in to the students portal ({{portal_url}}), navigate to the \"STUDENT PAYMENT INSTRUCTIONS\" section at the bottom of the page.\n"
            "2. Click on \"Fee Payment\" / \"M-Pesa Payment\" and follow the prompts.\n"
            "   (M-Pesa Paybill: {{mpesa_paybill}}, Account: {{registration_number}} | Bank: {{bank_name}}, Account: {{bank_account}})\n"
            "3. Ensure you obtain an official electronic receipt upon payment."
        ),
        terms_and_conditions=(
            "COMMENCEMENT DATE\n"
            "The programme will commence on {{reporting_date}}. You are, therefore, expected to report and complete your registration on this date.\n\n"
            "OTHER IMPORTANT INFORMATION\n"
            "i. Admission to the University does not guarantee accommodation in the Halls of Residence. Students not allocated university accommodation will be required to make private arrangements.\n"
            "ii. This admission offer is subject to your adherence to the University's Rules and Regulations.\n"
            "iii. In case of any queries, please contact the Admissions Office at {{university_email}} or Tel: {{university_phone}}."
        ),
        signatory_name="DR. MARGARET OMOLO, PhD",
        signatory_title="ACADEMIC REGISTRAR",
        verification_base_url="https://ums.ac.ke/verify-admission/",
    )
    return tmpl


def build_admission_letter_pdf_bytes(issued_document):
    """
    Renders the official university admission letter as a PDF byte stream
    using ReportLab and ReportDocTemplate, following authentic Kenyan university
    stationery standards (inspired by premier national universities like UoN).
    """
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=26,
        bottomMargin=26,
    )

    styles = document_styles()
    primary_color = colors.HexColor("#1A2B4C")
    dark_gray = colors.HexColor("#1F2937")
    muted_gray = colors.HexColor("#4B5563")

    title_style = ParagraphStyle(
        "LetterInstTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=14.5,
        leading=17,
        textColor=primary_color,
        alignment=1,  # Centered
        spaceAfter=1.5,
    )
    office_style = ParagraphStyle(
        "LetterOfficeTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=10,
        leading=13,
        textColor=primary_color,
        alignment=1,  # Centered
        spaceAfter=1.5,
    )
    confidential_style = ParagraphStyle(
        "LetterConfidential",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#374151"),
        alignment=1,  # Centered
        spaceAfter=3,
    )
    contact_left = ParagraphStyle(
        "HeaderContactLeft",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=7.8,
        leading=10.5,
        textColor=muted_gray,
        alignment=0,
    )
    contact_right = ParagraphStyle(
        "HeaderContactRight",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=7.8,
        leading=10.5,
        textColor=muted_gray,
        alignment=2,
    )
    ref_left = ParagraphStyle(
        "RefLeft",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.8,
        leading=12,
        textColor=dark_gray,
        alignment=0,
    )
    ref_right = ParagraphStyle(
        "RefRight",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.8,
        leading=12,
        textColor=dark_gray,
        alignment=2,
    )
    salutation_style = ParagraphStyle(
        "LetterSalutation",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9.2,
        leading=12.5,
        textColor=dark_gray,
        spaceAfter=4,
    )
    subject_style = ParagraphStyle(
        "LetterSubject",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9.5,
        leading=12.5,
        textColor=primary_color,
        spaceAfter=5,
    )
    section_head_style = ParagraphStyle(
        "SectionHead",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=12,
        textColor=primary_color,
        spaceBefore=4.5,
        spaceAfter=1.5,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.6,
        leading=11.8,
        textColor=dark_gray,
        spaceAfter=2,
    )
    list_item_style = ParagraphStyle(
        "LetterListItem",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.6,
        leading=11.5,
        textColor=dark_gray,
        leftIndent=16,
        spaceAfter=1.5,
    )
    closing_style = ParagraphStyle(
        "LetterClosing",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.6,
        leading=11.8,
        textColor=dark_gray,
        spaceBefore=4,
        spaceAfter=2,
        keepWithNext=True,
    )

    story = []
    context = issued_document.rendered_context or {}
    tmpl = issued_document.template

    # 1. Official Crest
    crest_path = os.path.join(settings.BASE_DIR, "static", "img", "ums-crest.jpg")
    if not os.path.exists(crest_path):
        crest_path = get_branding()["logo_path"]
    if crest_path and os.path.exists(crest_path):
        try:
            img = RLImage(crest_path, width=42, height=42)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 2))
        except Exception:
            pass

    # 2. Institution Name & Office
    univ_name = context.get("university_name", "UNIVERSITY MANAGEMENT SYSTEM").upper()
    story.append(Paragraph(escape(univ_name), title_style))

    hdr_title = tmpl.header_title if (tmpl and tmpl.header_title) else "Office of the Deputy Vice-Chancellor<br/>(Academic Affairs)"
    story.append(Paragraph(hdr_title.replace("\n", "<br/>"), office_style))
    story.append(Paragraph("<b>CONFIDENTIAL</b>", confidential_style))

    # 3. Two-Column Institutional Contact Header (Classic Kenyan University Letterhead)
    header_left_html = (
        '<b>Telegram:</b> "VARSITY" NAIROBI<br/>'
        f'<b>Telephone:</b> {escape(context.get("university_phone", "+254-020-1234567"))}<br/>'
        f'<b>Admissions Desk:</b> {escape(context.get("admissions_desk_phone", "+254 700 000 000"))}'
    )
    header_right_html = (
        f'{escape(context.get("university_address", "P.O. Box 90100 - 00100, GPO, Nairobi, Kenya"))}<br/>'
        f'<b>Email:</b> {escape(context.get("university_email", "admissions@ums.ac.ke"))}<br/>'
        '<b>Website:</b> www.ums.ac.ke'
    )
    contact_table = Table(
        [[Paragraph(header_left_html, contact_left), Paragraph(header_right_html, contact_right)]],
        colWidths=[255, 256]
    )
    contact_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(contact_table)
    story.append(Spacer(1, 2))
    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#1A2B4C"), spaceAfter=5))

    # 4. Reference & Date
    ref_table_data = [
        [
            Paragraph(f"<b>Our Ref:</b> {escape(issued_document.document_reference)}", ref_left),
            Paragraph(f"<b>Date:</b> {escape(context.get('issue_date', ''))}", ref_right),
        ]
    ]
    ref_table = Table(ref_table_data, colWidths=[260, 251])
    ref_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(ref_table)
    story.append(Spacer(1, 5))

    # 5. Salutation with Inline Admission Number
    salutation_template = tmpl.salutation_template if (tmpl and tmpl.salutation_template) else "Dear {{title_name}}, Admission Number: {{registration_number}}"
    if "Admission Number" not in salutation_template:
        salutation_template = f"{salutation_template.rstrip(',')} , Admission Number: {{{{registration_number}}}}"
    salutation_rendered = render_template_text(salutation_template, context)
    story.append(Paragraph(f"<b>{salutation_rendered}</b>", salutation_style))
    story.append(Spacer(1, 2))

    # 6. Subject Line
    subject_raw = tmpl.subject_template if (tmpl and tmpl.subject_template) else "RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR"
    if not subject_raw.upper().startswith("RE:"):
        subject_raw = f"RE: {subject_raw}"
    subject_rendered = render_template_text(subject_raw, context)
    story.append(Paragraph(f"<b><u>{subject_rendered.upper()}</u></b>", subject_style))
    story.append(Spacer(1, 4))

    # 7. Body Paragraphs (Opening & Checklist)
    body_raw = tmpl.body_template if (tmpl and tmpl.body_template) else ""
    if body_raw:
        body_rendered = render_template_text(body_raw, context)
        for block in body_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for line in lines:
                if re.match(r"^\d+\.\s+", line):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 2))

    # 8. Tuition Fees & Fee Payment
    fee_raw = tmpl.fee_schedule_instructions if (tmpl and tmpl.fee_schedule_instructions) else ""
    if fee_raw:
        fee_rendered = render_template_text(fee_raw, context)
        for block in fee_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for i, line in enumerate(lines):
                if i == 0 and ("TUITION FEES" in line.upper() or "FEE PAYMENT" in line.upper()):
                    story.append(Paragraph(f"<b>{line}</b>", section_head_style))
                elif re.match(r"^\d+\.\s+", line) or line.startswith("•") or line.startswith("-"):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 2))

    # 9. Commencement Date & Other Important Information
    terms_raw = tmpl.terms_and_conditions if (tmpl and tmpl.terms_and_conditions) else ""
    if terms_raw:
        terms_rendered = render_template_text(terms_raw, context)
        for block in terms_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for i, line in enumerate(lines):
                if i == 0 and ("COMMENCEMENT" in line.upper() or "IMPORTANT INFORMATION" in line.upper() or "CONDITIONS" in line.upper()):
                    story.append(Paragraph(f"<b>{line}</b>", section_head_style))
                elif re.match(r"^(?:[ivx]+|[a-z]|\d+)\.\s+", line, re.IGNORECASE) or line.startswith("•") or line.startswith("-"):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 2))

    # 10. Closing & Sign-off Block
    story.append(Paragraph(
        f"We look forward to welcoming you to {escape(context.get('university_name', 'University Management System'))} and supporting you on your academic journey.",
        closing_style
    ))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Yours faithfully,", closing_style))
    story.append(Spacer(1, 1))

    # Signature Graphic & Name Block
    sig_img_path = os.path.join(settings.BASE_DIR, "static", "img", "registrar-signature.jpg")
    if tmpl and tmpl.signatory_signature and getattr(tmpl.signatory_signature, "path", None) and os.path.exists(tmpl.signatory_signature.path):
        sig_img_path = tmpl.signatory_signature.path

    if sig_img_path and os.path.exists(sig_img_path):
        try:
            sig_rl = RLImage(sig_img_path, width=95, height=30)
            sig_rl.hAlign = "LEFT"
            story.append(sig_rl)
            story.append(Spacer(1, 1))
        except Exception:
            story.append(Spacer(1, 16))
    else:
        story.append(Spacer(1, 16))

    sig_name = context.get("signatory_name", "DR. MARGARET OMOLO, PhD").upper()
    sig_title = context.get("signatory_title", "ACADEMIC REGISTRAR").upper()

    sig_table = Table(
        [[
            Paragraph(f"<b>{escape(sig_name)}</b><br/><b>{escape(sig_title)}</b>", ParagraphStyle("SigText", parent=body_style, fontSize=8.6, leading=11.5)),
            Paragraph(f"Official Registry Verification:<br/><b>{escape(context.get('verification_url', 'https://ums.ac.ke/verify-admission/'))}</b>", ParagraphStyle("VerFoot", parent=body_style, fontSize=7.2, leading=9.5, alignment=2, textColor=muted_gray))
        ]],
        colWidths=[270, 241]
    )
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(sig_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


@transaction.atomic
def generate_admission_document(application, template=None, user=None, custom_overrides=None, reason="", issue_as_new_version=False):
    """
    Generates or regenerates an official IssuedAdmissionDocument for an Application.
    Supports versioning (v1, v2), superseding older versions, dynamic context snapshotting,
    PDF file rendering, and audit logging.
    """
    active_ay = get_current_academic_year()
    active_term = get_current_semester()

    if not template:
        template = get_or_create_default_template(
            program=application.program,
            academic_year=application.intake.academic_year if application.intake else active_ay
        )

    # Check for existing documents
    existing_docs = IssuedAdmissionDocument.objects.filter(
        application=application,
        document_type=template.document_type
    ).order_by("-version")

    current_doc = existing_docs.filter(is_current_version=True).first()

    if current_doc and not issue_as_new_version:
        # If already exists and no new version requested, return current doc
        return current_doc

    # Calculate version number
    if existing_docs.exists():
        new_version = existing_docs.first().version + 1
        # Mark all prior documents as SUPERSEDED if they were CURRENT
        existing_docs.filter(status=IssuedAdmissionDocument.Status.CURRENT).update(
            status=IssuedAdmissionDocument.Status.SUPERSEDED,
            is_current_version=False
        )
    else:
        new_version = 1

    # Generate document reference: UMS/ADM/2026/0001
    year_prefix = active_ay.name.split("/")[0] if active_ay else timezone.now().year
    app_seq = application.application_number.split("-")[-1]
    doc_ref = f"UMS/ADM/{year_prefix}/{app_seq}"
    if new_version > 1:
        doc_ref = f"{doc_ref}/V{new_version}"

    # Create the IssuedAdmissionDocument record
    doc_record = IssuedAdmissionDocument(
        application=application,
        student=application.student,
        template=template,
        document_type=template.document_type,
        document_reference=doc_ref,
        version=new_version,
        status=IssuedAdmissionDocument.Status.CURRENT,
        issue_date=timezone.now().date(),
        reporting_date=application.reporting_date or (timezone.now().date() + timedelta(days=21)),
        academic_year=application.intake.academic_year if application.intake else active_ay,
        semester=active_term,
        generated_by=user,
        generated_at=timezone.now(),
        change_reason=reason or ("Initial generation" if new_version == 1 else f"Regenerated version {new_version}"),
        is_current_version=True,
        is_visible_to_student=True,
    )

    # Build dynamic context snapshot
    context = build_admission_document_context(
        application=application,
        document=doc_record,
        template=template,
        custom_overrides=custom_overrides
    )
    doc_record.rendered_context = context
    doc_record.rendered_content = render_template_text(template.body_template, context)

    # Generate the PDF byte stream
    pdf_bytes = build_admission_letter_pdf_bytes(doc_record)
    filename = f"admission_letter_{application.application_number}_v{new_version}.pdf"
    doc_record.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    doc_record.save()

    # If application has assigned reg no, sync it
    if not application.admitted_reg_no and context.get("registration_number"):
        application.admitted_reg_no = context.get("registration_number")
        application.save(update_fields=["admitted_reg_no"])

    # Audit Trail
    action = AuditLog.Action.GENERATE_DOCUMENT if new_version == 1 else AuditLog.Action.REGENERATE_DOCUMENT
    log_activity(
        user=user,
        action=action,
        module=AuditLog.Module.ADMISSIONS,
        entity="IssuedAdmissionDocument",
        entity_id=doc_record.id,
        description=f"{'Generated' if new_version == 1 else 'Regenerated'} Admission Letter (v{new_version}, Ref: {doc_ref}) for applicant {application.application_number} ({application.full_name}). Reason: {doc_record.change_reason}",
        new_state={
            "reference": doc_ref,
            "version": new_version,
            "application_number": application.application_number,
            "student_reg_no": context.get("registration_number"),
            "program": application.program.code if application.program else "",
        }
    )

    return doc_record


def resend_admission_document(document, delivery_method, recipient, subject, message, user=None, request=None):
    """
    Dispatches the admission document via Email, SMS, or Portal notification,
    and logs the result in DocumentDeliveryLog and AuditLog.
    """
    if document.status == IssuedAdmissionDocument.Status.REVOKED:
        raise ValueError("Cannot resend a revoked or voided admission document.")

    ip = None
    if request:
        # Forwarded headers are untrusted until a trusted proxy boundary is
        # explicitly configured; do not let callers spoof delivery metadata.
        ip = request.META.get("REMOTE_ADDR", "")

    status = DocumentDeliveryLog.Status.SENT
    failure_msg = ""

    if delivery_method == DocumentDeliveryLog.Method.EMAIL:
        try:
            email = EmailMessage(
                subject=subject,
                body=message,
                from_email=settings.DEFAULT_FROM_EMAIL if hasattr(settings, "DEFAULT_FROM_EMAIL") else "admissions@ums.ac.ke",
                to=[recipient],
            )
            # Attach PDF if available
            if document.pdf_file and os.path.exists(document.pdf_file.path):
                email.attach_file(document.pdf_file.path)
            elif document.pdf_file:
                # Read from storage
                pdf_content = document.pdf_file.read()
                email.attach(f"Admission_Letter_{document.document_reference.replace('/', '_')}.pdf", pdf_content, "application/pdf")
            email.send(fail_silently=False)
            status = DocumentDeliveryLog.Status.DELIVERED
        except Exception as e:
            status = DocumentDeliveryLog.Status.FAILED
            failure_msg = str(e)
    elif delivery_method == DocumentDeliveryLog.Method.SMS:
        # SMS delivery integration
        status = DocumentDeliveryLog.Status.SENT
    elif delivery_method == DocumentDeliveryLog.Method.PORTAL_NOTICE:
        status = DocumentDeliveryLog.Status.DELIVERED

    # Create delivery log
    log_entry = DocumentDeliveryLog.objects.create(
        document=document,
        delivery_method=delivery_method,
        recipient=recipient,
        subject=subject,
        message_body=message,
        sent_by=user,
        sent_at=timezone.now(),
        status=status,
        failure_reason=failure_msg,
        ip_address=ip,
    )

    # Audit Trail
    log_activity(
        user=user,
        action=AuditLog.Action.RESEND_DOCUMENT,
        module=AuditLog.Module.ADMISSIONS,
        entity="IssuedAdmissionDocument",
        entity_id=document.id,
        description=f"Resent admission document {document.document_reference} (v{document.version}) to {recipient} via {delivery_method}. Status: {status}.",
        new_state={
            "recipient": recipient,
            "method": delivery_method,
            "status": status,
            "failure_reason": failure_msg,
        }
    )

    return log_entry


def revoke_admission_document(document, user=None, reason=""):
    """
    Revokes / voids an issued admission document.
    """
    document.status = IssuedAdmissionDocument.Status.REVOKED
    document.is_current_version = False
    document.revoked_by = user
    document.revoked_at = timezone.now()
    document.revocation_reason = reason
    document.save()

    log_activity(
        user=user,
        action=AuditLog.Action.REVOKE_DOCUMENT,
        module=AuditLog.Module.ADMISSIONS,
        entity="IssuedAdmissionDocument",
        entity_id=document.id,
        description=f"Revoked admission document {document.document_reference} (v{document.version}) for {document.application.full_name}. Reason: {reason}",
        new_state={
            "reference": document.document_reference,
            "revocation_reason": reason,
        }
    )
    return document


def get_student_admission_documents(student_profile):
    """
    Retrieves all admission documents, version history, and verified application
    attachments for a student profile.
    """
    application = getattr(student_profile, "admission_application", None)
    if not application:
        # Check by student roll_no or email match
        application = Application.objects.filter(student=student_profile).first()
        if not application and student_profile.user and student_profile.user.email:
            application = Application.objects.filter(email=student_profile.user.email).first()

    current_letter = None
    historical_letters = []
    attachments = []
    delivery_logs = []

    if application:
        current_letter = application.issued_documents.filter(
            is_current_version=True,
            is_visible_to_student=True
        ).exclude(status=IssuedAdmissionDocument.Status.REVOKED).first()

        historical_letters = application.issued_documents.filter(
            is_visible_to_student=True
        ).exclude(id=current_letter.id if current_letter else None).order_by("-version")

        attachments = application.attachments.filter(is_visible_to_student=True).order_by("-uploaded_at")

        if current_letter:
            delivery_logs = current_letter.delivery_logs.all().order_by("-sent_at")

    return {
        "application": application,
        "admission_letter": current_letter,
        "historical_letters": historical_letters,
        "attachments": attachments,
        "delivery_logs": delivery_logs,
    }
