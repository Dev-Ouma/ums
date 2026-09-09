from xml.sax.saxutils import escape
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from university.audit_services import log_activity
from university.models import (
    AuditLog, AttachmentAssessment, AttachmentLogbookEntry,
    AttachmentPlacement
)
from university.settings_services import get_setting

User = get_user_model()


def apply_for_attachment(
    student_profile,
    company_name,
    company_branch_location,
    company_address,
    company_supervisor_name,
    company_supervisor_email,
    company_supervisor_phone,
    department_or_unit,
    start_date,
    end_date,
    offer_letter=None,
    term=None,
    request=None
):
    """
    Submit or update an industrial attachment application for a student.
    Enforces minimum 8 weeks (56 days) duration and generates unique letter reference.
    """
    if start_date >= end_date:
        raise ValidationError("Attachment start date must be before the end date.")

    days = (end_date - start_date).days
    if days < 50:
        raise ValidationError("Industrial attachment must span at least 8 weeks to meet academic requirements.")

    clean_roll = student_profile.roll_no.replace("/", "").replace(" ", "").upper()
    curr_year = timezone.now().year
    intro_ref = f"NIU/ATT/{curr_year}/{clean_roll}"

    placement, created = AttachmentPlacement.objects.get_or_create(
        student=student_profile,
        defaults={
            "term": term,
            "company_name": company_name,
            "company_branch_location": company_branch_location,
            "company_address": company_address,
            "company_supervisor_name": company_supervisor_name,
            "company_supervisor_email": company_supervisor_email,
            "company_supervisor_phone": company_supervisor_phone,
            "department_or_unit": department_or_unit,
            "start_date": start_date,
            "end_date": end_date,
            "offer_letter": offer_letter,
            "intro_letter_reference": intro_ref,
            "status": AttachmentPlacement.Status.SUBMITTED,
        }
    )

    if not created:
        placement.company_name = company_name
        placement.company_branch_location = company_branch_location
        placement.company_address = company_address
        placement.company_supervisor_name = company_supervisor_name
        placement.company_supervisor_email = company_supervisor_email
        placement.company_supervisor_phone = company_supervisor_phone
        placement.department_or_unit = department_or_unit
        placement.start_date = start_date
        placement.end_date = end_date
        if offer_letter:
            placement.offer_letter = offer_letter
        if not placement.intro_letter_reference:
            placement.intro_letter_reference = intro_ref
        if placement.status in (AttachmentPlacement.Status.DRAFT, AttachmentPlacement.Status.REJECTED):
            placement.status = AttachmentPlacement.Status.SUBMITTED
        placement.save()

    log_activity(
        request=request,
        user=student_profile.user,
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="AttachmentPlacement",
        entity_id=placement.id,
        new_state={
            "student": student_profile.roll_no,
            "company": company_name,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "reference": placement.intro_letter_reference,
        }
    )

    return placement


@transaction.atomic
def approve_attachment_application(placement_id, admin_user, request=None):
    """Admin approves student's attachment placement."""
    placement = AttachmentPlacement.objects.get(id=placement_id)
    placement.status = AttachmentPlacement.Status.APPROVED
    placement.save(update_fields=["status", "updated_at"])

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="AttachmentPlacement",
        entity_id=placement.id,
        new_state={"status": "APPROVED", "approved_by": admin_user.username}
    )
    return placement


@transaction.atomic
def reject_attachment_application(placement_id, admin_user, reason="", request=None):
    """Admin rejects attachment placement application."""
    placement = AttachmentPlacement.objects.get(id=placement_id)
    placement.status = AttachmentPlacement.Status.REJECTED
    placement.remarks = reason
    placement.save(update_fields=["status", "remarks", "updated_at"])

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="AttachmentPlacement",
        entity_id=placement.id,
        new_state={"status": "REJECTED", "reason": reason}
    )
    return placement


@transaction.atomic
def assign_academic_supervisor(placement_id, faculty_profile, admin_user, request=None):
    """Assign a faculty member as academic supervisor for this placement."""
    placement = AttachmentPlacement.objects.get(id=placement_id)
    placement.academic_supervisor = faculty_profile
    if placement.status == AttachmentPlacement.Status.APPROVED:
        placement.status = AttachmentPlacement.Status.IN_PROGRESS
    placement.save(update_fields=["academic_supervisor", "status", "updated_at"])

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="AttachmentPlacement",
        entity_id=placement.id,
        new_state={
            "supervisor": faculty_profile.user.get_full_name() or faculty_profile.user.username,
            "status": placement.status,
        }
    )
    return placement


def record_logbook_entry(
    placement,
    week_number,
    date_from,
    date_to,
    activities_summary,
    skills_acquired,
    challenges_encountered="",
    company_supervisor_signed=False
):
    """Record or update student's weekly logbook entry."""
    entry, created = AttachmentLogbookEntry.objects.get_or_create(
        attachment=placement,
        week_number=week_number,
        defaults={
            "date_from": date_from,
            "date_to": date_to,
            "activities_summary": activities_summary,
            "skills_acquired": skills_acquired,
            "challenges_encountered": challenges_encountered,
            "company_supervisor_signed": company_supervisor_signed,
        }
    )
    if not created:
        entry.date_from = date_from
        entry.date_to = date_to
        entry.activities_summary = activities_summary
        entry.skills_acquired = skills_acquired
        entry.challenges_encountered = challenges_encountered
        entry.company_supervisor_signed = company_supervisor_signed
        entry.save()

    return entry


def review_logbook_entry(entry_id, faculty_user, feedback):
    """Academic supervisor reviews and records remarks on a logbook entry."""
    entry = AttachmentLogbookEntry.objects.get(id=entry_id)
    entry.faculty_supervisor_reviewed = True
    entry.faculty_feedback = feedback
    entry.save(update_fields=["faculty_supervisor_reviewed", "faculty_feedback"])
    return entry


@transaction.atomic
def submit_attachment_assessment(
    placement_id,
    assessor_faculty,
    org_suitability,
    attendance_score,
    technical_score,
    logbook_score,
    presentation_score,
    assessor_comments,
    industry_comments="",
    request=None
):
    """
    Faculty assessor grades the industrial attachment using standard 5-part rubric.
    Updates placement status to COMPLETED and records final grade.
    """
    placement = AttachmentPlacement.objects.get(id=placement_id)

    assessment, created = AttachmentAssessment.objects.get_or_create(
        attachment=placement,
        assessor=assessor_faculty,
        defaults={
            "organization_suitability_score": Decimal(str(org_suitability)),
            "student_attendance_score": Decimal(str(attendance_score)),
            "technical_skills_score": Decimal(str(technical_score)),
            "logbook_maintenance_score": Decimal(str(logbook_score)),
            "oral_presentation_score": Decimal(str(presentation_score)),
            "assessor_comments": assessor_comments,
            "industry_supervisor_comments": industry_comments,
        }
    )

    if not created:
        assessment.organization_suitability_score = Decimal(str(org_suitability))
        assessment.student_attendance_score = Decimal(str(attendance_score))
        assessment.technical_skills_score = Decimal(str(technical_score))
        assessment.logbook_maintenance_score = Decimal(str(logbook_score))
        assessment.oral_presentation_score = Decimal(str(presentation_score))
        assessment.assessor_comments = assessor_comments
        assessment.industry_supervisor_comments = industry_comments

    assessment.full_clean()
    assessment.save()

    # Update parent placement
    placement.final_score = assessment.total_score
    placement.final_grade = assessment.grade
    placement.status = AttachmentPlacement.Status.COMPLETED
    placement.save(update_fields=["final_score", "final_grade", "status", "updated_at"])

    log_activity(
        request=request,
        user=assessor_faculty.user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="AttachmentAssessment",
        entity_id=assessment.id,
        new_state={
            "student": placement.student.roll_no,
            "total_score": str(assessment.total_score),
            "grade": assessment.grade,
            "status": "COMPLETED",
        }
    )

    return assessment


# ==============================================================================
# PDF GENERATION ENGINES
# ==============================================================================

def generate_attachment_intro_letter_pdf(placement):
    """
    Generate official University Introductory / Recommendation Letter (PDF).
    Features university header, student details, 8-12 week training purpose,
    and Registrar's signature block.
    """
    buf = io.BytesIO()
    doc = ReportDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=54,
        rightMargin=54,
        topMargin=44,
        bottomMargin=44
    )

    styles = document_styles()
    primary_color = colors.HexColor("#1A365D") # Deep Navy
    text_color = colors.HexColor("#2D3748")
    accent_color = colors.HexColor("#C59B27") # Gold Accent

    title_style = ParagraphStyle(
        "UniTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=15,
        leading=19,
        textColor=primary_color,
        alignment=1
    )
    sub_style = ParagraphStyle(
        "UniSub",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
        alignment=1
    )
    heading_style = ParagraphStyle(
        "SubjectHeading",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=11,
        leading=15,
        textColor=primary_color,
        alignment=0
    )
    body_style = ParagraphStyle(
        "LetterBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=16,
        textColor=text_color,
        alignment=4 # Justified
    )
    meta_style = ParagraphStyle(
        "MetaStyle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=text_color
    )

    elements = []

    # 1. University Header
    inst_name = get_branding()["site_name"]
    motto = get_setting("INSTITUTION_MOTTO", "Excellence, Integrity and Innovation")
    reg_email = get_setting("REGISTRAR_EMAIL", get_branding()["contact_email"])
    reg_phone = get_setting("REGISTRAR_PHONE", get_branding()["contact_phone"])

    elements.append(Paragraph(inst_name.upper(), title_style))
    elements.append(Paragraph("OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)", ParagraphStyle("SubH", parent=sub_style, fontName="Quicksand-Bold", fontSize=9.5)))
    elements.append(Paragraph(f"<i>\"{motto}\"</i>", sub_style))
    elements.append(Paragraph(f"Tel: {reg_phone} &bull; Email: {reg_email}", sub_style))
    elements.append(Spacer(1, 10))
    elements.append(HRFlowable(width="100%", thickness=2, color=primary_color, spaceAfter=14))

    # 2. Reference & Date
    ref_no = placement.intro_letter_reference or f"NIU/ATT/{timezone.now().year}/REF"
    curr_date = timezone.now().strftime("%B %d, %Y")

    ref_table = Table([
        [
            Paragraph(f"<b>Our Ref:</b> {ref_no}", meta_style),
            Paragraph(f"<b>Date:</b> {curr_date}", ParagraphStyle("DateRight", parent=meta_style, alignment=2))
        ]
    ], colWidths=[280, 208])
    ref_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(ref_table)
    elements.append(Spacer(1, 14))

    # 3. Addressee
    addressee = (
        f"<b>TO:</b><br/>"
        f"The Managing Director / Human Resource Manager,<br/>"
        f"<b>{placement.company_name}</b>,<br/>"
        f"{placement.company_branch_location}.<br/>"
        f"{placement.company_address or ''}"
    )
    elements.append(Paragraph(addressee, meta_style))
    elements.append(Spacer(1, 16))

    # 4. Subject Line
    student = placement.student
    student_name = student.user.get_full_name() or student.user.username
    prog_name = student.program.name if student.program else "Undergraduate Degree"
    dept_name = student.program.department.name if (student.program and student.program.department) else "Computing & Informatics"

    subject = f"<u>RE: RECOMMENDATION FOR INDUSTRIAL ATTACHMENT: {student_name.upper()} (REG NO: {student.roll_no})</u>"
    elements.append(Paragraph(subject, heading_style))
    elements.append(Spacer(1, 12))

    # 5. Body Text
    p1 = (
        f"This is to certify that <b>{student_name}</b> (Registration Number: <b>{student.roll_no}</b>) is a "
        f"bonafide, full-time student at {inst_name} in the Department of {dept_name}, currently undertaking "
        f"studies leading to the award of <b>{prog_name}</b>."
    )
    elements.append(Paragraph(p1, body_style))
    elements.append(Spacer(1, 10))

    duration_wks = placement.duration_weeks or 10
    start_str = placement.start_date.strftime("%B %d, %Y")
    end_str = placement.end_date.strftime("%B %d, %Y")

    p2 = (
        f"As part of the curriculum approved by the University Senate and the Commission for University Education (CUE), "
        f"the candidate is required to undergo a mandatory period of <b>Industrial Attachment ({duration_wks} weeks)</b> "
        f"between <b>{start_str}</b> and <b>{end_str}</b>. The objective of this training is to provide the student with "
        f"hands-on industry exposure, practical skills, and professional ethics complementary to their academic coursework."
    )
    elements.append(Paragraph(p2, body_style))
    elements.append(Spacer(1, 10))

    p3 = (
        f"We kindly request your esteemed organization to offer this student an attachment opportunity in your "
        f"<b>{placement.department_or_unit}</b> or related division. While on attachment, the student is covered under "
        f"the University Student Personal Accident Insurance Policy. During the attachment period, designated university "
        f"academic supervisors will conduct on-site or virtual supervisory assessments."
    )
    elements.append(Paragraph(p3, body_style))
    elements.append(Spacer(1, 10))

    p4 = (
        "Any assistance extended to this student will be highly appreciated. Should you require any further "
        "clarification, please do not hesitate to contact our office through the credentials provided above."
    )
    elements.append(Paragraph(p4, body_style))
    elements.append(Spacer(1, 26))

    # 6. Sign-off & Verification Seal Stamp
    signoff = (
        "Yours faithfully,<br/><br/><br/>"
        "<b>Prof. J. K. Sang, PhD</b><br/>"
        "Registrar (Academic Affairs)<br/>"
        f"<i>{inst_name}</i>"
    )

    seal_box = (
        "<font color='#718096'><b>[ OFFICIAL UNIVERSITY SEAL ]</b><br/>"
        "Accredited by Commission for<br/>University Education (CUE)<br/>"
        f"Serial: {ref_no}</font>"
    )

    sign_table = Table([
        [Paragraph(signoff, meta_style), Paragraph(seal_box, ParagraphStyle("Seal", parent=meta_style, alignment=1))]
    ], colWidths=[310, 178])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBEFORE", (1, 0), (1, 0), 1, colors.HexColor("#CBD5E0")),
        ("LEFTPADDING", (1, 0), (1, 0), 16),
    ]))
    elements.append(sign_table)

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()


def generate_attachment_logbook_pdf(placement):
    """
    Generate comprehensive Industrial Attachment Logbook & Assessment Report (PDF).
    Compiles student profile, placement credentials, weekly activities log,
    and faculty supervisor grading rubric.
    """
    buf = io.BytesIO()
    doc = ReportDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=44,
        rightMargin=44,
        topMargin=36,
        bottomMargin=36
    )

    styles = document_styles()
    primary_color = colors.HexColor("#1A365D")
    accent_color = colors.HexColor("#2B6CB0")
    border_color = colors.HexColor("#CBD5E0")
    bg_light = colors.HexColor("#F7FAFC")

    title_style = ParagraphStyle(
        "LogbookTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=14,
        leading=18,
        textColor=primary_color,
        alignment=1
    )
    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=10.5,
        leading=14,
        textColor=primary_color
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#2D3748")
    )
    meta_bold = ParagraphStyle(
        "MetaB",
        parent=meta_style,
        fontName="Quicksand-Bold"
    )
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#4A5568")
    )

    elements = []

    # Title Banner
    inst_name = get_branding()["site_name"]
    elements.append(Paragraph(inst_name.upper(), title_style))
    elements.append(Paragraph("DIRECTORATE OF INDUSTRIAL LIAISON & PRACTICAL TRAINING", ParagraphStyle("Sub", parent=meta_style, fontName="Quicksand-Bold", alignment=1, fontSize=9.5)))
    elements.append(Paragraph("INDUSTRIAL ATTACHMENT LOGBOOK & ASSESSMENT DOSSIER", ParagraphStyle("Sub2", parent=meta_style, fontName="Quicksand", alignment=1, fontSize=8.5, textColor=accent_color)))
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    # Student & Placement Credentials Grid
    student = placement.student
    student_name = student.user.get_full_name() or student.user.username
    prog_name = student.program.name if student.program else "Undergraduate Degree"
    dept_name = student.program.department.name if (student.program and student.program.department) else "Department"
    sup_name = placement.academic_supervisor.user.get_full_name() if placement.academic_supervisor else "Pending Assignment"

    info_data = [
        [
            Paragraph("<b>Student Name:</b>", meta_style), Paragraph(student_name, meta_bold),
            Paragraph("<b>Registration No:</b>", meta_style), Paragraph(student.roll_no, meta_bold),
        ],
        [
            Paragraph("<b>Programme:</b>", meta_style), Paragraph(prog_name, meta_style),
            Paragraph("<b>Department:</b>", meta_style), Paragraph(dept_name, meta_style),
        ],
        [
            Paragraph("<b>Host Company:</b>", meta_style), Paragraph(placement.company_name, meta_bold),
            Paragraph("<b>Location:</b>", meta_style), Paragraph(placement.company_branch_location, meta_style),
        ],
        [
            Paragraph("<b>Host Supervisor:</b>", meta_style), Paragraph(f"{placement.company_supervisor_name} ({placement.company_supervisor_phone})", meta_style),
            Paragraph("<b>Faculty Supervisor:</b>", meta_style), Paragraph(sup_name, meta_style),
        ],
        [
            Paragraph("<b>Attachment Period:</b>", meta_style), Paragraph(f"{placement.start_date} to {placement.end_date} ({placement.duration_weeks} wks)", meta_style),
            Paragraph("<b>Status:</b>", meta_style), Paragraph(placement.get_status_display().upper(), meta_bold),
        ],
    ]

    info_table = Table(info_data, colWidths=[100, 160, 110, 138])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_light),
        ("BOX", (0, 0), (-1, -1), 1, border_color),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 14))

    # Weekly Logbook Entries Table
    elements.append(Paragraph("WEEKLY ACTIVITY DIARY & PRACTICAL LOG", h2_style))
    elements.append(Spacer(1, 6))

    entries = placement.logbook_entries.all().order_by("week_number")
    if entries.exists():
        log_data = [
            [
                Paragraph("<b>Wk</b>", meta_bold),
                Paragraph("<b>Date Range</b>", meta_bold),
                Paragraph("<b>Tasks Performed & Technical Exposure</b>", meta_bold),
                Paragraph("<b>Competencies Acquired</b>", meta_bold),
                Paragraph("<b>Supervisor Review</b>", meta_bold),
            ]
        ]
        for e in entries:
            dt_str = f"{e.date_from.strftime('%b %d')} - {e.date_to.strftime('%b %d')}"
            review_str = e.faculty_feedback if e.faculty_supervisor_reviewed else "Pending Review"
            log_data.append([
                Paragraph(f"<b>W{e.week_number}</b>", meta_style),
                Paragraph(dt_str, small_style),
                Paragraph(e.activities_summary[:280] + ("..." if len(e.activities_summary) > 280 else ""), small_style),
                Paragraph(e.skills_acquired[:160] + ("..." if len(e.skills_acquired) > 160 else ""), small_style),
                Paragraph(f"<i>{review_str}</i>", small_style),
            ])

        log_table = Table(log_data, colWidths=[28, 72, 190, 120, 98])
        log_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
            ("BOX", (0, 0), (-1, -1), 1, border_color),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(log_table)
    else:
        elements.append(Paragraph("<i>No weekly logbook entries have been submitted yet.</i>", meta_style))

    elements.append(Spacer(1, 14))

    # Assessment Rubric Results (if assessed)
    if hasattr(placement, "assessment"):
        asm = placement.assessment
        elements.append(Paragraph("ACADEMIC SUPERVISOR EVALUATION & RUBRIC SCORE", h2_style))
        elements.append(Spacer(1, 6))

        rubric_data = [
            [Paragraph("<b>Evaluation Criteria</b>", meta_bold), Paragraph("<b>Max Score</b>", meta_bold), Paragraph("<b>Awarded Score</b>", meta_bold)],
            [Paragraph("1. Organization Suitability & Relevance of Work", meta_style), Paragraph("10.00", meta_style), Paragraph(f"{asm.organization_suitability_score:,.2f}", meta_bold)],
            [Paragraph("2. Student Attendance, Punctuality & Industry Conduct", meta_style), Paragraph("15.00", meta_style), Paragraph(f"{asm.student_attendance_score:,.2f}", meta_bold)],
            [Paragraph("3. Technical Skills Application & Problem Solving", meta_style), Paragraph("35.00", meta_style), Paragraph(f"{asm.technical_skills_score:,.2f}", meta_bold)],
            [Paragraph("4. Weekly Logbook Maintenance & Completeness", meta_style), Paragraph("20.00", meta_style), Paragraph(f"{asm.logbook_maintenance_score:,.2f}", meta_bold)],
            [Paragraph("5. Oral Defense / Presentation & Viva Voce", meta_style), Paragraph("20.00", meta_style), Paragraph(f"{asm.oral_presentation_score:,.2f}", meta_bold)],
            [
                Paragraph("<b>TOTAL WEIGHTED GRADE</b>", meta_bold),
                Paragraph("<b>100.00</b>", meta_bold),
                Paragraph(f"<b>{asm.total_score:,.2f}% (Grade: {asm.grade})</b>", ParagraphStyle("GradeG", parent=meta_bold, textColor=colors.HexColor("#2B6CB0"))),
            ],
        ]

        rubric_table = Table(rubric_data, colWidths=[290, 98, 120])
        rubric_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EBF8FF")),
            ("BOX", (0, 0), (-1, -1), 1, border_color),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(rubric_table)
        elements.append(Spacer(1, 10))

        if asm.assessor_comments:
            elements.append(Paragraph(f"<b>Assessor Comments:</b> {asm.assessor_comments}", meta_style))
            elements.append(Spacer(1, 6))

    # Sign-off Blocks
    elements.append(Spacer(1, 14))
    sign_data = [
        [
            Paragraph("____________________________<br/><b>Student Signature</b>", meta_style),
            Paragraph("____________________________<br/><b>Industry Supervisor Signature & Stamp</b>", meta_style),
            Paragraph("____________________________<br/><b>Faculty Assessor Signature & Date</b>", meta_style),
        ]
    ]
    sign_table = Table(sign_data, colWidths=[165, 185, 158])
    sign_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(sign_table)

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()
