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

    context = {
        # Student
        "student_name": application.full_name,
        "first_name": application.first_name,
        "last_name": application.last_name,
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
        "university_address": branding.get("address", "P.O. Box 90100 - 00100, Nairobi, Kenya"),
        "university_email": branding.get("admissions_email", "admissions@ums.ac.ke"),
        "university_phone": branding.get("phone", "+254 (0) 20 123 4567"),
        "portal_url": branding.get("portal_url", "https://portal.ums.ac.ke"),
        "verification_url": f"{ver_base.rstrip('/')}/{doc_ref.replace('/', '-')}",

        # Finance
        "tuition_fee": f"KES {tuition_val:,.2f}",
        "total_fees": f"KES {total_fee_val:,.2f}",
        "bank_name": "Confirm with the Finance Office",
        "bank_account": "Published institutional account",
        "bank_branch": "See current fee notice",
        "mpesa_paybill": "Published institutional paybill",

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
        name="Standard University Admission Letter",
        document_type=AdmissionDocumentTemplate.DocumentType.ADMISSION_LETTER,
        is_active=True,
        is_default=True,
        version=1,
        header_title="OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)",
        salutation_template="Dear {{student_name}},",
        subject_template="LETTER OF OFFER: ADMISSION TO {{programme_name}} ({{programme_code}})",
        body_template=(
            "I am pleased to inform you that following your application, the University Admissions Board has offered you "
            "admission into the <b>{{programme_name}}</b> in the <b>{{faculty_name}}</b> for the <b>{{intake}}</b> "
            "commencing in <b>{{academic_year}} ({{semester}})</b>.\n\n"
            "You are required to report to the Main Campus for orientation, document verification, and formal registration on "
            "<b>{{reporting_date}}</b> at 8:00 AM. Failure to report within two weeks of the scheduled date without prior "
            "written approval from the Registrar will result in the forfeiture of this offer."
        ),
        terms_and_conditions=(
            "1. This offer of provisional admission is subject to physical verification of your original KCSE / High School certificates, National ID card / passport, and birth certificate.\n"
            "2. All registered students are bound by the University Charter, Statutes, Rules and Regulations governing Student Conduct and Discipline.\n"
            "3. At least 75% of first-semester fees must be paid prior to biometric registration and unit enrolment.\n"
            "4. The university reserves the right to withdraw this offer at any time should any submitted documents or academic qualifications be found fraudulent or falsified."
        ),
        fee_schedule_instructions=(
            "Tuition and statutory fees must be deposited directly to the University Bank Account:\n"
            "• Depository Bank: {{bank_name}}\n"
            "• Account Number: {{bank_account}} ({{bank_branch}})\n"
            "• M-Pesa Paybill: {{mpesa_paybill}} (Account: {{application_number}})\n"
            "• Estimated First Semester Total: {{total_fees}} (Tuition: {{tuition_fee}})\n"
            "Cheques and cash payments at campus counters are strictly not accepted."
        ),
        signatory_name="Dr. Margaret Omolo, PhD",
        signatory_title="Registrar, Academic & Student Affairs",
        verification_base_url="https://ums.ac.ke/verify-admission/",
    )
    return tmpl


def build_admission_letter_pdf_bytes(issued_document):
    """
    Renders the official university admission letter as a PDF byte stream
    using ReportLab and ReportDocTemplate.
    """
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=36,
        bottomMargin=36,
    )

    styles = document_styles()
    primary_color = colors.HexColor("#25356B")
    accent_color = colors.HexColor("#D6A84F")
    dark_gray = colors.HexColor("#1f2937")
    muted_gray = colors.HexColor("#4b5563")

    title_style = ParagraphStyle(
        "LetterTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=16,
        leading=20,
        textColor=primary_color,
        alignment=1,  # Centered
    )
    subtitle_style = ParagraphStyle(
        "LetterSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=muted_gray,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "HeadingStyle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=12,
        leading=16,
        textColor=primary_color,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9.5,
        leading=14,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "LetterBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9.5,
        leading=14,
        textColor=dark_gray,
    )

    story = []
    context = issued_document.rendered_context or {}
    tmpl = issued_document.template

    # 1. Header & Logo: a restrained registry letterhead with a clear offer marker.
    logo_path = get_branding()["logo_path"]
    if logo_path and os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=46, height=46)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    header_title = tmpl.header_title if tmpl else "OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)"
    story.append(Paragraph(escape(context.get("university_name", "UNIVERSITY MANAGEMENT SYSTEM").upper()), title_style))
    story.append(Paragraph(escape(header_title), subtitle_style))
    story.append(Paragraph(
        f"{escape(context.get('university_address', 'P.O. Box 90100 - 00100, Nairobi, Kenya'))} · "
        f"{escape(context.get('university_email', 'admissions@ums.ac.ke'))} · "
        f"{escape(context.get('university_phone', '+254 20 123 4567'))}",
        subtitle_style
    ))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=2.2, color=accent_color, spaceAfter=5))
    story.append(HRFlowable(width="100%", thickness=0.7, color=primary_color, spaceAfter=12))

    # 2. Reference, Reg No, Date Table
    ref_table_data = [
        [
            Paragraph(f"<b>Ref No:</b> {escape(issued_document.document_reference)}", body_style),
            Paragraph(f"<b>Date:</b> {escape(context.get('issue_date', ''))}", ParagraphStyle("RightDate", parent=body_style, alignment=2))
        ],
        [
            Paragraph(f"<b>Student Reg No:</b> <b>{escape(context.get('registration_number', ''))}</b>", body_style),
            Paragraph(f"<b>Version:</b> v{issued_document.version} &nbsp;|&nbsp; <b>Status:</b> PROVISIONAL ADMISSION",
                      ParagraphStyle("RightStatus", parent=body_bold, alignment=2, textColor=colors.HexColor("#047857")))
        ],
    ]
    ref_table = Table(ref_table_data, colWidths=[310, 205])
    ref_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(ref_table)
    story.append(Spacer(1, 10))

    # 3. Applicant Bio Block
    bio_text = (
        f"<b>{escape(context.get('student_name', ''))}</b><br/>"
        f"National ID / Passport: {escape(context.get('national_id', ''))}<br/>"
        f"Email: {escape(context.get('email', ''))} | Tel: {escape(context.get('phone', ''))}<br/>"
        f"{escape(context.get('address', ''))}"
    )
    bio_table = Table([[Paragraph("<b>APPLICANT</b><br/>" + bio_text, body_style)]], colWidths=[515])
    bio_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F8FC")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D7DEEB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(bio_table)
    story.append(Spacer(1, 10))

    # 4. Salutation & Subject Line
    salutation = render_template_text(tmpl.salutation_template if tmpl else "Dear {{student_name}},", context)
    story.append(Paragraph(f"<b>{salutation}</b>", body_style))
    story.append(Spacer(1, 6))

    subject = render_template_text(tmpl.subject_template if tmpl else "LETTER OF OFFER: ADMISSION TO {{programme_name}}", context)
    story.append(Paragraph(f"<u>{subject.upper()}</u>", heading_style))
    story.append(Spacer(1, 8))

    # 5. Body Content
    body_raw = tmpl.body_template if tmpl else (
        "I am pleased to inform you that following your application, the University Admissions Board has offered you "
        "admission into the <b>{{programme_name}}</b> in the <b>{{faculty_name}}</b> for the <b>{{intake}}</b> "
        "commencing in <b>{{academic_year}} ({{semester}})</b>.\n\n"
        "You are required to report to the Main Campus for orientation, document verification, and formal registration on "
        "<b>{{reporting_date}}</b> at 8:00 AM. Failure to report within two weeks of the scheduled date without prior "
        "written approval from the Registrar will result in the forfeiture of this offer."
    )
    body_rendered = render_template_text(body_raw, context)
    for paragraph in body_rendered.split("\n\n"):
        clean_p = paragraph.strip().replace("\n", "<br/>")
        if clean_p:
            story.append(Paragraph(clean_p, body_style))
            story.append(Spacer(1, 6))

    # 6. Fee Quotation Box
    fee_box_data = [
        [Paragraph("<b>Estimated Year 1 Semester 1 Fee Schedule</b>", body_bold), ""],
        [Paragraph("Tuition Fee:", body_style), Paragraph(context.get("tuition_fee", "KES 45,000.00"), body_style)],
        [Paragraph("Other approved university charges:", body_style),
         Paragraph("Confirm current schedule", body_style)],
        [Paragraph("<b>Total First Semester Fees:</b>", body_bold),
         Paragraph(f"<b>{context.get('total_fees', 'KES 55,500.00')}</b>", body_bold)],
    ]
    fee_table = Table(fee_box_data, colWidths=[380, 135])
    fee_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(fee_table)
    story.append(Spacer(1, 8))

    # 7. Institution-controlled payment guidance. Keep this separate from the
    # estimate table so finance instructions can change without redesigning
    # the official letter layout.
    fee_instructions_raw = getattr(tmpl, "fee_schedule_instructions", "") if tmpl else ""
    if fee_instructions_raw:
        fee_instructions = render_template_text(fee_instructions_raw, context)
        story.append(Paragraph("<b>Payment &amp; Finance Guidance</b>", body_bold))
        for paragraph in fee_instructions.split("\n\n"):
            clean_p = paragraph.strip().replace("\n", "<br/>")
            if clean_p:
                story.append(Paragraph(clean_p, ParagraphStyle("FinanceGuidance", parent=body_style, fontSize=8.5, leading=12)))
        story.append(Spacer(1, 6))

    # 8. Terms & Conditions
    terms_raw = tmpl.terms_and_conditions if tmpl else ""
    if terms_raw:
        terms_rendered = render_template_text(terms_raw, context)
        story.append(Paragraph("<b>Important Conditions & Guidelines:</b>", body_bold))
        for line in terms_rendered.split("\n"):
            line_s = line.strip()
            if line_s:
                story.append(Paragraph(line_s, ParagraphStyle("Terms", parent=body_style, fontSize=8.5, leading=12)))
        story.append(Spacer(1, 6))

    # 9. Signatory & Official Verification Block
    sig_name = context.get("signatory_name", "Dr. Margaret Omolo, PhD")
    sig_title = context.get("signatory_title", "Registrar, Academic & Student Affairs")
    ver_url = context.get("verification_url", "https://ums.ac.ke/verify-admission/")

    signature_cell = Paragraph("Yours sincerely,<br/><br/><br/>" + f"<b>{escape(sig_name)}</b><br/>{escape(sig_title)}", body_style)
    if tmpl and tmpl.signatory_signature and getattr(tmpl.signatory_signature, "path", None):
        try:
            signature_cell = [RLImage(tmpl.signatory_signature.path, width=110, height=34), Paragraph(f"<b>{escape(sig_name)}</b><br/>{escape(sig_title)}", body_style)]
        except (OSError, ValueError):
            pass
    seal_cell = Paragraph("OFFICIAL UNIVERSITY SEAL", ParagraphStyle("Seal", parent=body_style, alignment=1, textColor=colors.HexColor("#9ca3af")))
    if tmpl and tmpl.official_seal and getattr(tmpl.official_seal, "path", None):
        try:
            seal_cell = RLImage(tmpl.official_seal.path, width=74, height=74)
        except (OSError, ValueError):
            pass
    sign_data = [
        [
            signature_cell,
            seal_cell
        ],
        [
            Paragraph(f"<b>{escape(context.get('university_name', 'University Management System'))}</b>", body_style),
            Paragraph(f"Verify authenticity online:<br/><b>{escape(ver_url)}</b>", ParagraphStyle("VerBlock", parent=body_style, fontSize=8, leading=11, alignment=1, textColor=muted_gray))
        ]
    ]
    sign_table = Table(sign_data, colWidths=[310, 205])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(sign_table)

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
