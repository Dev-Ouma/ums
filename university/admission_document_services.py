import hashlib
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
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

from django.core.exceptions import PermissionDenied
from university.document_design import (
    ReportDocTemplate, document_styles, get_branding, make_qr_code_flowable
)
from university.models import (
    AcademicTerm, AcademicYear, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue,
    AdmissionDocumentTemplate, AuditLog, DocumentDeliveryLog,
    DocumentSignatureConfig, FeeStructure, IssuedAdmissionDocument
)
from university.audit_services import log_activity
from university.academic_calendar_services import get_current_academic_year, get_current_semester
from university.student_numbering_services import generate_student_registration_number

User = get_user_model()


class SignatureRequiredError(Exception):
    """Raised when an active signature is required by institutional policy but missing."""
    pass


class SignatureAuthorizationError(PermissionDenied):
    """Raised when an unauthorized user attempts to sign an official document."""
    pass


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

    # Signatory details from document or template
    if document and document.signatory_name:
        sig_name = document.signatory_name
        sig_title = document.signatory_title or (template.signatory_title if template else "Registrar, Academic Affairs")
        sig_office = document.signatory_office or "Directorate of Academic Affairs"
        sig_ver = str(document.signature_version or 1)
    elif document and document.signatory:
        sig_name = document.signatory.get_full_name() or document.signatory.username
        sig_title = getattr(document.signatory, "user_signature", None) and document.signatory.user_signature.title or (template.signatory_title if template else "Registrar, Academic Affairs")
        sig_office = getattr(document.signatory, "user_signature", None) and document.signatory.user_signature.department_or_office or "Directorate of Academic Affairs"
        sig_ver = str(getattr(document.signatory, "user_signature", None) and document.signatory.user_signature.version or 1)
    elif template:
        sig_name = template.signatory_name or "Dr. Margaret Omolo, PhD"
        sig_title = template.signatory_title or "Registrar, Academic Affairs"
        sig_office = "Directorate of Academic Affairs"
        sig_ver = "1"
    else:
        sig_name = "Dr. Margaret Omolo, PhD"
        sig_title = "Registrar, Academic Affairs"
        sig_office = "Directorate of Academic Affairs"
        sig_ver = "1"

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
        "acceptance_deadline": (rep_date + timedelta(days=14)).strftime("%A, %d %B %Y"),
        "document_reference": doc_ref,
        "issue_date": issue_str,
        "version": str(document.version if document else 1),

        # Programme & Study Details
        "programme_name": prog.name if prog else "Undergraduate Degree Programme",
        "programme_code": prog.code if prog else "UG",
        "award_title": prog.award_title if (prog and hasattr(prog, "award_title") and prog.award_title) else "Bachelor Degree",
        "level": prog.get_level_display() if (prog and hasattr(prog, "get_level_display")) else "Undergraduate",
        "duration_years": str(getattr(prog, "duration_years", 4)),
        "study_mode": getattr(application, "study_mode", None) or "Full-Time (Regular)",
        "campus": getattr(application, "campus", None) or "Main Campus",

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

        # Central Signatory
        "signatory_name": sig_name,
        "signatory_title": sig_title,
        "signatory_office": sig_office,
        "signature_version": sig_ver,
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
            "You will pay {{tuition_fee}} as tuition fee in a Semester. "
            "For more information, please contact the Finance Office at {{finance_email}} or Tel: {{university_phone}}\n\n"
            "FEE PAYMENT\n"
            "You are required to follow the instructions below to pay the tuition fee:\n"
            "1. While logged in to the students portal, navigate to the \"STUDENT PAYMENT INSTRUCTIONS\" section at the bottom of the page.\n"
            "2. Click on \"Fee Payment\" / \"M-Pesa Payment\" and follow the prompts."
        ),
        terms_and_conditions=(
            "COMMENCEMENT DATE\n"
            "The programme will commence on {{reporting_date}}. You are, therefore, expected to report and complete your registration on this date.\n\n"
            "OTHER IMPORTANT INFORMATION\n"
            "i. Admission to the University does not guarantee accommodation in the Halls of Residence. Students not allocated university accommodation will be required to make private arrangements.\n"
            "ii. This admission offer is subject to your adherence to the University's Rules and Regulations.\n"
            "iii. In case of any queries, please contact the Admissions Office at {{university_email}} or Tel: {{university_phone}}"
        ),
        signatory_name="DR. MARGARET OMOLO, PhD",
        signatory_title="ACADEMIC REGISTRAR",
        verification_base_url="https://ums.ac.ke/verify-admission/",
    )
    return tmpl


def compute_admission_document_checksum(issued_document=None, context=None):
    """
    Computes a cryptographic SHA-256 digest for an admission letter to guarantee non-repudiation.
    """
    context = context or {}
    doc_ref = getattr(issued_document, "document_reference", "") or context.get("document_reference", "")
    reg_no = context.get("registration_number", context.get("admission_number", ""))
    prog_code = context.get("programme_code", "UG")
    issue_date = str(context.get("issue_date", ""))
    secret = getattr(settings, "SECRET_KEY", "ums-secret-key")
    raw = f"{doc_ref}:{reg_no}:{prog_code}:{issue_date}:{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_admission_letter_pdf_bytes(issued_document):
    """
    Renders the official university admission letter as a clean, authentic
    1-page PDF byte stream matching standard Kenyan university stationery
    (inspired by premier national universities like UoN).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=20,
        bottomMargin=20,
    )

    styles = getSampleStyleSheet()
    primary_color = colors.black

    title_style = ParagraphStyle(
        "LetterInstTitle",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=12.5,
        leading=14.5,
        textColor=primary_color,
        alignment=1,  # Centered
        spaceAfter=1,
    )
    office_style = ParagraphStyle(
        "LetterOfficeTitle",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=9.5,
        leading=11.5,
        textColor=primary_color,
        alignment=1,  # Centered
        spaceAfter=1,
    )
    confidential_style = ParagraphStyle(
        "LetterConfidential",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=10.5,
        textColor=primary_color,
        alignment=1,  # Centered
        spaceAfter=4,
    )
    contact_left = ParagraphStyle(
        "HeaderContactLeft",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=7.5,
        leading=9.5,
        textColor=primary_color,
        alignment=0,
    )
    contact_right = ParagraphStyle(
        "HeaderContactRight",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=7.5,
        leading=9.5,
        textColor=primary_color,
        alignment=2,
    )
    ref_style = ParagraphStyle(
        "RefLeft",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=11,
        textColor=primary_color,
        alignment=0,
        spaceAfter=2,
    )
    salutation_style = ParagraphStyle(
        "LetterSalutation",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=11,
        textColor=primary_color,
        spaceBefore=4,
        spaceAfter=3,
    )
    subject_style = ParagraphStyle(
        "LetterSubject",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=8.5,
        leading=11,
        textColor=primary_color,
        spaceAfter=4,
    )
    section_head_style = ParagraphStyle(
        "SectionHead",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=8.5,
        leading=11,
        textColor=primary_color,
        spaceBefore=4,
        spaceAfter=1,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=10.8,
        textColor=primary_color,
        spaceAfter=2,
    )
    list_item_style = ParagraphStyle(
        "LetterListItem",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=10.8,
        textColor=primary_color,
        leftIndent=16,
        spaceAfter=1,
    )
    closing_style = ParagraphStyle(
        "LetterClosing",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=11,
        textColor=primary_color,
        spaceBefore=3,
        spaceAfter=2,
        keepWithNext=True,
    )
    sig_style = ParagraphStyle(
        "SigText",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=8.5,
        leading=10.5,
        textColor=primary_color,
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
            img = RLImage(crest_path, width=34, height=34)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 1))
        except Exception:
            pass

    # 2. Institution Name & Office
    univ_name = context.get("university_name", "UNIVERSITY MANAGEMENT SYSTEM").upper()
    story.append(Paragraph(escape(univ_name), title_style))

    hdr_title = tmpl.header_title if (tmpl and tmpl.header_title) else "Office of the Deputy Vice-Chancellor<br/>(Academic Affairs)"
    story.append(Paragraph(hdr_title.replace("\n", "<br/>"), office_style))
    story.append(Paragraph("CONFIDENTIAL", confidential_style))

    # 3. Two-Column Institutional Contact Header
    header_left_html = (
        'Telegram: "VARSITY" NAIROBI<br/>'
        f'Telephone: {escape(context.get("university_phone", "254-020-3318262"))}<br/>'
        'TELEX: 28520 Varsity KE<br/>'
        'Fax: 254-020-214325'
    )
    header_right_html = (
        f'{escape(context.get("university_address", "P.O. Box 30197 - 00100, GPO"))}<br/>'
        'NAIROBI, KENYA<br/>'
        f'Email: {escape(context.get("university_email", "reg-academics@ums.ac.ke"))}<br/>'
        'Website: www.ums.ac.ke'
    )
    contact_table = Table(
        [[Paragraph(header_left_html, contact_left), Paragraph(header_right_html, contact_right)]],
        colWidths=[260, 263]
    )
    contact_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(contact_table)
    story.append(Spacer(1, 4))

    # 4. Reference & Date
    story.append(Paragraph(f"Your Ref: <b>{escape(issued_document.document_reference)}</b>", ref_style))
    story.append(Paragraph(f"Date: <b>{escape(context.get('issue_date', ''))}</b>", ref_style))
    story.append(Spacer(1, 2))

    # 5. Salutation with Inline Admission Number
    salutation_template = tmpl.salutation_template if (tmpl and tmpl.salutation_template) else "Dear {{title_name}}, Admission Number: {{registration_number}}"
    if "Admission Number" not in salutation_template:
        salutation_template = f"{salutation_template.rstrip(',')} , Admission Number: {{{{registration_number}}}}"
    salutation_rendered = render_template_text(salutation_template, context)
    story.append(Paragraph(salutation_rendered, salutation_style))

    # 6. Subject Line
    subject_raw = tmpl.subject_template if (tmpl and tmpl.subject_template) else "RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR"
    if not subject_raw.upper().startswith("RE:"):
        subject_raw = f"RE: {subject_raw}"
    subject_rendered = render_template_text(subject_raw, context)
    story.append(Paragraph(f"<u><b>{subject_rendered}</b></u>", subject_style))

    # 7. Body Paragraphs (Opening & Checklist)
    body_raw = tmpl.body_template if (tmpl and tmpl.body_template) else ""
    if body_raw:
        body_rendered = render_template_text(body_raw, context)
        for block in body_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for line in lines:
                if re.match(r"^(?:<[^>]+>)*\s*\d+\.\s*", line):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 1))

    # 8. Tuition Fees & Fee Payment
    fee_raw = tmpl.fee_schedule_instructions if (tmpl and tmpl.fee_schedule_instructions) else ""
    if fee_raw:
        fee_rendered = render_template_text(fee_raw, context)
        for block in fee_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for i, line in enumerate(lines):
                if i == 0 and ("TUITION FEES" in line.upper() or "FEE PAYMENT" in line.upper() or "SCHEDULE" in line.upper()):
                    story.append(Paragraph(line, section_head_style))
                elif re.match(r"^(?:<[^>]+>)*\s*\d+\.\s*", line) or line.startswith("•") or line.startswith("-"):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 1))

    # 9. Commencement Date & Other Important Information
    terms_raw = tmpl.terms_and_conditions if (tmpl and tmpl.terms_and_conditions) else ""
    if terms_raw:
        terms_rendered = render_template_text(terms_raw, context)
        for block in terms_rendered.split("\n\n"):
            lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
            for i, line in enumerate(lines):
                if i == 0 and ("COMMENCEMENT" in line.upper() or "IMPORTANT INFORMATION" in line.upper() or "CONDITIONS" in line.upper() or "REPORTING" in line.upper()):
                    story.append(Paragraph(line, section_head_style))
                elif re.match(r"^(?:<[^>]+>)*\s*(?:[ivx]+|[a-z]|\d+)\.\s*", line, re.IGNORECASE) or line.startswith("•") or line.startswith("-"):
                    story.append(Paragraph(line, list_item_style))
                else:
                    story.append(Paragraph(line, body_style))
    # 10. Closing & Sign-off Block
    story.append(Paragraph(
        f"We look forward to welcoming you to {escape(context.get('university_name', 'University Management System'))} and supporting you on your academic journey.",
        closing_style
    ))
    story.append(Spacer(1, 2))

    # 11. Official Signatures and Bottom-Left Security Verification Badge
    sig_img_path = None
    sig_name = (issued_document.signatory_name if (issued_document and issued_document.signatory_name) else None) or context.get("signatory_name", "DR. MARGARET OMOLO, PhD")
    sig_title = (issued_document.signatory_title if (issued_document and issued_document.signatory_title) else None) or context.get("signatory_title", "ACADEMIC REGISTRAR")

    # Priority 1: Historical snapshot
    if issued_document and issued_document.signature_snapshot and hasattr(issued_document.signature_snapshot, "path") and os.path.exists(issued_document.signature_snapshot.path):
        sig_img_path = issued_document.signature_snapshot.path
    # Priority 2: Primary signatory active User profile signature
    elif issued_document and issued_document.signatory:
        from accounts.models import UserSignature
        user_sig = UserSignature.objects.filter(user=issued_document.signatory, status=UserSignature.Status.ACTIVE).first()
        if user_sig and user_sig.signature_image and hasattr(user_sig.signature_image, "path") and os.path.exists(user_sig.signature_image.path):
            sig_img_path = user_sig.signature_image.path
            sig_name = issued_document.signatory.get_full_name()
            if user_sig.title:
                sig_title = user_sig.title
    # Priority 3: Fallback to template/static image
    elif tmpl and tmpl.signatory_signature and getattr(tmpl.signatory_signature, "path", None) and os.path.exists(tmpl.signatory_signature.path):
        sig_img_path = tmpl.signatory_signature.path
    else:
        static_sig = os.path.join(settings.BASE_DIR, "static", "img", "registrar-signature.jpg")
        if os.path.exists(static_sig):
            sig_img_path = static_sig

    # Checksum & Verification Badge Metadata
    doc_ref = (issued_document.document_reference if issued_document else None) or context.get("document_reference", "UMS/ADM/SAMPLE/001")
    doc_hash = compute_admission_document_checksum(issued_document, context)
    checksum_short = f"{doc_hash[:6]}...{doc_hash[-4:]}"
    issued_ts = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
    ver_url = context.get("verification_url") or f"https://ums.ac.ke/verify/document/{doc_ref.replace('/', '-')}"

    qr_flowable = make_qr_code_flowable(ver_url, size=46)

    badge_meta_style = ParagraphStyle(
        "BadgeMetaText",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#334155"),
    )

    badge_meta_html = (
        '<font color="#059669">●</font> <b><font face="Times-Bold" size="7.5" color="#0f172a">SECURE VERIFICATION</font></b><br/>'
        f'<font color="#64748b">Doc Ref:</font> <b>{escape(doc_ref)}</b><br/>'
        f'<font color="#64748b">Checksum:</font> {checksum_short} (SHA256)<br/>'
        f'<font color="#64748b">Issued:</font> {issued_ts}<br/>'
        '<font size="5.8" color="#94a3b8">Scan to authenticate original record.</font>'
    )

    badge_inner_table = Table(
        [[qr_flowable, Paragraph(badge_meta_html, badge_meta_style)]],
        colWidths=[50, 195]
    )
    badge_inner_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 2),
        ("RIGHTPADDING", (0, 0), (0, 0), 2),
        ("LEFTPADDING", (1, 0), (1, 0), 2),
        ("RIGHTPADDING", (1, 0), (1, 0), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))

    # Signatory column on the right
    sig_cell_elements = []
    sig_cell_elements.append(Paragraph("Yours faithfully,", closing_style))
    if sig_img_path and os.path.exists(sig_img_path):
        try:
            sig_rl = RLImage(sig_img_path, width=72, height=18)
            sig_rl.hAlign = "LEFT"
            sig_cell_elements.append(sig_rl)
        except Exception:
            sig_cell_elements.append(Spacer(1, 8))
    else:
        sig_cell_elements.append(Spacer(1, 8))

    sig_cell_elements.append(Paragraph(f"<u><b>{escape(sig_name.upper())}</b></u>", sig_style))
    sig_cell_elements.append(Paragraph(f"<b>{escape(sig_title.upper())}</b>", sig_style))

    footer_table = Table(
        [[badge_inner_table, sig_cell_elements]],
        colWidths=[255, 268]
    )
    footer_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(footer_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


@transaction.atomic
def generate_admission_document(
    application,
    template=None,
    user=None,
    custom_overrides=None,
    reason="",
    issue_as_new_version=False,
    signatory_user=None,
    co_signatory_user=None,
):
    """
    Generates or regenerates an official IssuedAdmissionDocument for an Application.
    Enforces central DocumentSignatureConfig permissions and active signature requirements.
    Preserves immutable signature snapshots to prevent historical document alteration.
    """
    active_ay = get_current_academic_year()
    active_term = get_current_semester()

    if not template:
        template = get_or_create_default_template(
            program=application.program,
            academic_year=application.intake.academic_year if application.intake else active_ay
        )

    # 1. Document Signature Policy Validation
    from accounts.signature_services import get_user_active_signature

    sig_config = DocumentSignatureConfig.objects.filter(
        document_type=DocumentSignatureConfig.DocumentType.ADMISSION_LETTER,
        is_active=True
    ).first()

    # Determine signatory user
    if signatory_user is None:
        if user and sig_config and sig_config.is_user_authorized(user):
            signatory_user = user
        elif sig_config:
            allowed_roles = sig_config.get_authorized_roles_list()
            signatory_user = User.objects.filter(
                role__in=allowed_roles,
                user_signature__status="ACTIVE"
            ).exclude(user_signature__signature_image="").first()
            if not signatory_user:
                signatory_user = User.objects.filter(role__in=allowed_roles).first()

    # Verify signatory permissions (Backend RBAC Enforcement)
    if signatory_user and sig_config:
        if not sig_config.is_user_authorized(signatory_user):
            raise SignatureAuthorizationError(
                f"Selected signatory '{signatory_user.get_full_name() or signatory_user.username}' is not authorized to sign Admission Letters."
            )

    # Verify active signature availability
    sig_profile = get_user_active_signature(signatory_user) if signatory_user else None
    if sig_config and sig_config.is_signature_required:
        if not sig_profile or not sig_profile.signature_image:
            signatory_name = signatory_user.get_full_name() if signatory_user else "the selected signatory"
            raise SignatureRequiredError(
                f"An active signature has not been configured for {signatory_name}. An official digital signature is required before this document can be issued."
            )

    # Co-signatory validation if provided
    co_sig_profile = None
    if co_signatory_user:
        co_sig_profile = get_user_active_signature(co_signatory_user)

    # Check for existing documents
    existing_docs = IssuedAdmissionDocument.objects.filter(
        application=application,
        document_type=template.document_type
    ).order_by("-version")

    current_doc = existing_docs.filter(is_current_version=True).first()

    if current_doc and not issue_as_new_version:
        return current_doc

    # Calculate version number
    if existing_docs.exists():
        new_version = existing_docs.first().version + 1
        existing_docs.filter(status=IssuedAdmissionDocument.Status.CURRENT).update(
            status=IssuedAdmissionDocument.Status.SUPERSEDED,
            is_current_version=False
        )
    else:
        new_version = 1

    year_prefix = active_ay.name.split("/")[0] if active_ay else timezone.now().year
    app_seq = application.application_number.split("-")[-1]
    doc_ref = f"UMS/ADM/{year_prefix}/{app_seq}"
    if new_version > 1:
        doc_ref = f"{doc_ref}/V{new_version}"

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
        signatory=signatory_user,
        signatory_name=signatory_user.get_full_name() if signatory_user else (template.signatory_name if template else "Dr. Margaret Omolo, PhD"),
        signatory_title=(sig_profile.title if sig_profile and sig_profile.title else (template.signatory_title if template else "Academic Registrar")),
        signatory_office=(sig_profile.department_or_office if sig_profile and sig_profile.department_or_office else "Directorate of Academic Affairs"),
        signature_version=sig_profile.version if sig_profile else 1,
        co_signatory=co_signatory_user,
        co_signatory_name=co_signatory_user.get_full_name() if co_signatory_user else "",
        co_signatory_title=co_sig_profile.title if co_sig_profile else "",
    )

    # Snapshot signature image(s) to guarantee historical immutability
    if sig_profile and sig_profile.signature_image:
        try:
            sig_profile.signature_image.open("rb")
            snap_data = sig_profile.signature_image.read()
            doc_record.signature_snapshot.save(
                f"sig_snap_{doc_ref.replace('/', '_')}_v{new_version}.png",
                ContentFile(snap_data),
                save=False
            )
        except Exception:
            pass

    if co_sig_profile and co_sig_profile.signature_image:
        try:
            co_sig_profile.signature_image.open("rb")
            co_snap_data = co_sig_profile.signature_image.read()
            doc_record.co_signature_snapshot.save(
                f"co_sig_snap_{doc_ref.replace('/', '_')}_v{new_version}.png",
                ContentFile(co_snap_data),
                save=False
            )
        except Exception:
            pass

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
            "signatory": signatory_user.username if signatory_user else "None",
            "signature_version": doc_record.signature_version,
        }
    )
    if signatory_user:
        log_activity(
            user=user,
            action=AuditLog.Action.DOCUMENT_SIGNED,
            module=AuditLog.Module.SIGNATURES,
            entity="IssuedAdmissionDocument",
            entity_id=doc_record.id,
            description=f"Admission letter Ref: {doc_ref} signed with {signatory_user.get_full_name()}'s official signature (v{doc_record.signature_version}).",
            new_state={
                "document_reference": doc_ref,
                "signatory_id": signatory_user.id,
                "signatory_name": doc_record.signatory_name,
                "signature_version": doc_record.signature_version,
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
