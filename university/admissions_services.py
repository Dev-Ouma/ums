from xml.sax.saxutils import escape
import io
import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from accounts.models import Role, StudentProfile
from university.models import AcademicTerm, Application, FeeInvoice, FeeStructure, SemesterRegistration, AuditLog
from university.audit_services import log_activity
from university.academic_calendar_services import get_current_academic_year, get_current_semester
from university.student_numbering_services import generate_student_registration_number

User = get_user_model()


def generate_application_number(intake=None):
    """Generate a unique application reference number (e.g. APP-2026-0001)."""
    year = timezone.now().year
    prefix = f"APP-{year}-"
    last_app = Application.objects.filter(application_number__startswith=prefix).order_by("-id").first()
    if last_app:
        try:
            last_seq = int(last_app.application_number.split("-")[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = Application.objects.count() + 1
    else:
        new_seq = 1
    return f"{prefix}{new_seq:04d}"


def assign_admitted_reg_no(application):
    """Assign a formal university registration number using configurable institutional template."""
    return generate_student_registration_number(
        program=application.program,
        intake=application.intake,
    )


def generate_admission_letter_pdf(application):
    """Generate a formal university admission letter as a PDF byte buffer."""
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    styles = document_styles()
    primary_color = colors.HexColor("#1e3a8a")  # University Deep Navy
    dark_gray = colors.HexColor("#1f2937")
    muted_gray = colors.HexColor("#4b5563")

    title_style = ParagraphStyle(
        "LetterTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=18,
        leading=22,
        textColor=primary_color,
        alignment=1,  # Centered
    )
    subtitle_style = ParagraphStyle(
        "LetterSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=14,
        textColor=muted_gray,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "HeadingStyle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=13,
        leading=17,
        textColor=primary_color,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=15,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "LetterBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=10,
        leading=15,
        textColor=dark_gray,
    )

    story = []

    # Logo + Header
    logo_path = get_branding()["logo_path"]
    if logo_path and os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=50, height=50)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 6))
        except Exception:
            pass

    story.append(Paragraph(escape(get_branding()["site_name"].upper()), title_style))
    story.append(Paragraph("OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)", subtitle_style))
    story.append(Paragraph("P.O. Box 90100 - 00100, Nairobi, Kenya · admissions@ums.ac.ke · www.ums.ac.ke", subtitle_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=15))

    # Reference and Date Table
    issue_date = timezone.now().strftime("%d %B %Y")
    reg_no = application.admitted_reg_no or assign_admitted_reg_no(application)
    ref_table_data = [
        [Paragraph(f"<b>Ref No:</b> UMS/REG/ADM/{application.application_number}", body_style),
         Paragraph(f"<b>Date:</b> {issue_date}", ParagraphStyle("RightDate", parent=body_style, alignment=2))],
        [Paragraph(f"<b>Student Reg No:</b> <b>{reg_no}</b>", body_style),
         Paragraph(f"<b>Status:</b> PROVISIONAL ADMISSION", ParagraphStyle("RightStatus", parent=body_bold, alignment=2, textColor=colors.HexColor("#047857")))],
    ]
    ref_table = Table(ref_table_data, colWidths=[300, 215])
    ref_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(ref_table)
    story.append(Spacer(1, 12))

    # Applicant Address
    applicant_bio = (
        f"<b>{application.full_name}</b><br/>"
        f"National ID/Passport: {application.national_id}<br/>"
        f"Email: {application.email} | Phone: {application.phone}<br/>"
        f"{application.address or 'Nairobi, Kenya'}"
    )
    story.append(Paragraph(applicant_bio, body_style))
    story.append(Spacer(1, 14))

    # Subject Line
    program_title = application.program.name if application.program else "Undergraduate Degree Programme"
    department_name = application.program.department.name if (application.program and application.program.department) else "Faculty of Science & Technology"
    story.append(Paragraph(f"<u>LETTER OF OFFER: ADMISSION TO {program_title.upper()}</u>", heading_style))
    story.append(Spacer(1, 10))

    # Body Paragraphs
    reporting_dt = application.reporting_date.strftime("%A, %d %B %Y") if application.reporting_date else "Monday, 14 September 2026"
    intake_name = application.intake.name if application.intake else "September 2026 Academic Intake"

    p1 = (
        f"I am pleased to inform you that following your application, the University Admissions Board has offered you "
        f"admission into the <b>{program_title}</b> in the <b>{department_name}</b> for the <b>{intake_name}</b>."
    )
    story.append(Paragraph(p1, body_style))
    story.append(Spacer(1, 8))

    p2 = (
        f"You are expected to report at the Main Campus for orientation, document verification, and registration on "
        f"<b>{reporting_dt}</b> at 8:00 AM. Failure to report within two weeks of the reporting date without prior written "
        f"approval from the Registrar will result in the forfeiture of this offer."
    )
    story.append(Paragraph(p2, body_style))
    story.append(Spacer(1, 10))

    # Fee Quotation Summary Box
    fee_struct = FeeStructure.objects.filter(program=application.program, year_of_study=1, semester=1).first()
    tuition = fee_struct.tuition_fee if fee_struct else Decimal("45000.00")
    total_term1 = fee_struct.total_fee if fee_struct else Decimal("55500.00")

    fee_summary_data = [
        [Paragraph("<b>Estimated Year 1 Semester 1 Fee Schedule</b>", body_bold), ""],
        [Paragraph("Tuition Fee:", body_style), Paragraph(f"KES {tuition:,.2f}", body_style)],
        [Paragraph("Statutory & Administrative Fees (Reg, Exam, Library, Medical, ICT):", body_style),
         Paragraph(f"KES {(total_term1 - tuition):,.2f}", body_style)],
        [Paragraph("<b>Total First Semester Fees:</b>", body_bold),
         Paragraph(f"<b>KES {total_term1:,.2f}</b>", body_bold)],
    ]
    fee_table = Table(fee_summary_data, colWidths=[380, 135])
    fee_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(fee_table)
    story.append(Spacer(1, 10))

    # Mandatory Requirements
    p3 = (
        "<b>Important Conditions of Offer:</b><br/>"
        "1. Present original and certified copies of your KCSE/High School result slip/certificate, National ID, and birth certificate.<br/>"
        "2. Pay at least 75% of the first-semester fees into the University Bank Account (Absa Bank, A/C No: 03-094-1234567, Westlands Branch) or via M-Pesa Paybill 247247 prior to registration.<br/>"
        "3. Complete the online student bio-data and medical examination questionnaire."
    )
    story.append(Paragraph(p3, body_style))
    story.append(Spacer(1, 16))

    # Sign-off block
    sign_table_data = [
        [Paragraph("Yours sincerely,", body_style), Paragraph("<b>OFFICIAL UNIVERSITY SEAL</b>", ParagraphStyle("Seal", parent=body_style, alignment=1, textColor=colors.HexColor("#9ca3af")))],
        [Spacer(1, 20), ""],
        [Paragraph("<b>Dr. Margaret Omolo, PhD</b><br/>Registrar, Academic & Student Affairs<br/>University Management System", body_style),
         Paragraph("Scan QR / Enter Ref to Verify<br/>ums.ac.ke/verify-admission/", ParagraphStyle("VerifyText", parent=body_style, alignment=1, fontSize=8, textColor=muted_gray))],
    ]
    sign_table = Table(sign_table_data, colWidths=[310, 205])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(sign_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


@transaction.atomic
def matriculate_applicant(application, created_by=None):
    """
    Convert an ACCEPTED applicant into an official enrolled student.
    - Generates roll_no if not set
    - Creates or fetches User account with Role.STUDENT
    - Creates StudentProfile linked to user & program
    - Assigns the currently active Academic Year/Semester (from Admin Setup, never hard-coded)
      and creates the student's first SemesterRegistration for it
    - Links application.student to StudentProfile
    - Updates application.status to ENROLLED
    - Automatically creates initial FeeInvoice from FeeStructure, tied to the active term
    """
    if application.student:
        return application.student, application.student.user, None

    # Active academic calendar context, per Admin Academic Year/Semester Setup — never hard-coded.
    active_ay = get_current_academic_year()
    active_term = get_current_semester()

    # Determine roll number using configurable numbering engine
    if not application.admitted_reg_no:
        application.admitted_reg_no = assign_admitted_reg_no(application)

    roll_no = application.admitted_reg_no

    # Create User account
    username = roll_no.lower().replace("/", ".").replace(" ", "")
    existing_user = User.objects.filter(username=username).first()
    if not existing_user:
        existing_user = User.objects.filter(email=application.email).first()

    default_password = "demo1234"
    if not existing_user:
        user = User.objects.create_user(
            username=username,
            email=application.email,
            first_name=application.first_name,
            last_name=application.last_name,
            role=Role.STUDENT,
        )
        user.set_password(default_password)
        user.save()
    else:
        user = existing_user
        if user.role != Role.STUDENT:
            user.role = Role.STUDENT
            user.save(update_fields=["role"])

    # Create StudentProfile
    student_profile = StudentProfile.objects.filter(user=user).first()
    if not student_profile:
        student_profile = StudentProfile.objects.create(
            user=user,
            roll_no=roll_no,
            program=application.program,
            address=application.address or "Nairobi, Kenya",
            date_of_birth=application.date_of_birth,
            current_semester=1,
            status=StudentProfile.Status.ACTIVE,
            guardian_name=f"Parent of {application.first_name}",
        )

    # Initialize the first SemesterRegistration for the active academic term,
    if active_term and active_term.academic_year:
        ay_str = active_term.academic_year.name
    elif active_term:
        ay_str = f"{active_term.start_date.year}/{active_term.end_date.year}"
    elif active_ay:
        ay_str = active_ay.name
    else:
        ay_str = "2026/2027"
    if active_term:
        SemesterRegistration.objects.get_or_create(
            student=student_profile, term=active_term,
            defaults={
                "semester_no": student_profile.current_semester,
                "academic_year": ay_str,
                "status": SemesterRegistration.DRAFT,
            }
        )

    # Link and update application
    application.student = student_profile
    application.status = Application.Status.ENROLLED
    application.reviewed_by = created_by or application.reviewed_by
    application.reviewed_at = timezone.now()
    application.save()

    # Ensure admission document exists and links to student_profile
    from university.admission_document_services import generate_admission_document
    doc = application.issued_documents.filter(is_current_version=True).first()
    if not doc:
        generate_admission_document(application, user=created_by, reason="Auto-generated on matriculation")
    else:
        if not doc.student:
            doc.student = student_profile
            doc.save(update_fields=["student"])

    # Log matriculation in Audit Trail
    log_activity(
        user=created_by,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.STUDENTS,
        entity="StudentProfile",
        entity_id=student_profile.id,
        description=f"Matriculated applicant {application.application_number} as student {student_profile.roll_no} into {student_profile.program.name if student_profile.program else 'General'}.",
        new_state={
            "roll_no": student_profile.roll_no,
            "program": student_profile.program.code if student_profile.program else "",
            "academic_year": ay_str,
            "term": active_term.name if active_term else "",
        }
    )

    # Automatically generate initial semester invoice from fee structure
    fee_struct = FeeStructure.objects.filter(
        program=application.program,
        year_of_study=1,
        semester=1
    ).first()

    billed_amount = fee_struct.total_fee if fee_struct else Decimal("55500.00")
    due_date = timezone.now().date() + timedelta(days=30)
    FeeInvoice.objects.get_or_create(
        student=student_profile,
        title="Year 1 Semester 1 Tuition & Statutory Fees",
        defaults={
            "amount": billed_amount,
            "amount_paid": Decimal("0.00"),
            "issued_on": timezone.now().date(),
            "due_date": due_date,
            "term": active_term,
        }
    )

    return student_profile, user, default_password
