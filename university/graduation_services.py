from xml.sax.saxutils import escape
from datetime import date, datetime
from decimal import Decimal
import io
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Avg, Sum
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from accounts.models import StudentProfile
from university.audit_services import log_activity
from university.financial_services import check_financial_clearance
from university.models import (
    AuditLog, DepartmentClearance, Enrollment, GraduationApplication,
    GraduationCeremony, Result
)
from university.settings_services import get_setting

User = get_user_model()


def classify_degree_honours(cgpa):
    """Classify honours based on standard CUE / University CGPA scale."""
    val = Decimal(str(cgpa))
    if val >= Decimal("3.70"):
        return GraduationApplication.Classification.FIRST_CLASS
    elif val >= Decimal("3.00"):
        return GraduationApplication.Classification.SECOND_UPPER
    elif val >= Decimal("2.00"):
        return GraduationApplication.Classification.SECOND_LOWER
    elif val >= Decimal("1.00"):
        return GraduationApplication.Classification.PASS
    return GraduationApplication.Classification.NOT_APPLICABLE


def audit_graduation_eligibility(student_profile):
    """
    Perform a comprehensive academic and financial audit for graduation candidacy.
    Returns: dict with eligible, cgpa, classification, credits_earned, and audit issues.
    """
    issues = []
    
    # 1. Financial Clearance Check
    fin_status = check_financial_clearance(student_profile)
    fee_balance = fin_status["balance"]
    if fee_balance > Decimal("0.00"):
        issues.append(f"Outstanding fee balance of KES {fee_balance:,.2f} must be settled.")

    # 2. Results and CGPA calculation
    results = Result.objects.filter(student=student_profile).select_related("exam__course")
    if not results.exists():
        issues.append("No published academic results found for this student.")
        return {
            "eligible": False,
            "cgpa": Decimal("0.00"),
            "classification": GraduationApplication.Classification.NOT_APPLICABLE,
            "credits_earned": 0,
            "failed_courses": [],
            "issues": issues,
        }

    failed_results = []
    total_gpa_points = Decimal("0.00")
    total_credits = 0

    grade_points_map = {
        "A": Decimal("4.00"),
        "B": Decimal("3.00"),
        "C": Decimal("2.00"),
        "D": Decimal("1.00"),
        "E": Decimal("0.00"),
        "F": Decimal("0.00"),
    }

    for r in results:
        g = (r.grade or "F").upper()
        course = getattr(r.exam, "course", None) if hasattr(r, "exam") else None
        cr = getattr(course, "credits", 3) if course else 3
        pts = grade_points_map.get(g, Decimal("0.00"))
        
        if g in ("E", "F"):
            failed_results.append(course.code if course else "UNKNOWN")
        else:
            total_credits += cr
        
        total_gpa_points += (pts * cr)

    all_credits = sum(getattr(r.exam.course, "credits", 3) if (hasattr(r, "exam") and hasattr(r.exam, "course") and r.exam.course) else 3 for r in results) or 1
    cgpa = round(total_gpa_points / Decimal(str(all_credits)), 2)

    if failed_results:
        distinct_failed = sorted(list(set(failed_results)))
        issues.append(f"Unresolved failing grades in course(s): {', '.join(distinct_failed)}.")

    # Minimum required credits for standard 4-year degree (typical 120-140)
    prog_duration = getattr(student_profile.program, "duration_years", 4) if student_profile.program else 4
    min_required_credits = prog_duration * 30  # e.g., 120 credits
    if total_credits < min_required_credits:
        issues.append(f"Completed {total_credits} credits, below the minimum degree requirement of {min_required_credits} credits.")

    classification = classify_degree_honours(cgpa)
    eligible = len(issues) == 0

    return {
        "eligible": eligible,
        "cgpa": cgpa,
        "classification": classification,
        "credits_earned": total_credits,
        "failed_courses": failed_results,
        "issues": issues,
    }


@transaction.atomic
def initiate_student_clearance(student_profile, ceremony=None, request=None):
    """
    Register a student for graduation clearance, creating the application
    and initializing the 5 mandatory departmental clearance stations.
    """
    audit = audit_graduation_eligibility(student_profile)
    curr_year = timezone.now().year
    clean_roll = student_profile.roll_no.replace("/", "").replace(" ", "").upper()
    cert_serial = f"NIU/DEG/{curr_year}/{clean_roll}"

    app, created = GraduationApplication.objects.update_or_create(
        student=student_profile,
        defaults={
            "ceremony": ceremony or GraduationCeremony.objects.filter(status=GraduationCeremony.Status.PLANNED).first(),
            "total_credits_earned": audit["credits_earned"],
            "final_cgpa": audit["cgpa"],
            "classification": audit["classification"],
            "certificate_serial": cert_serial,
            "status": GraduationApplication.Status.CLEARANCE_IN_PROGRESS,
            "notes": "Clearance initiated. Awaiting departmental sign-offs.",
        }
    )

    # Initialize 5 mandatory clearance stations
    stations = [
        DepartmentClearance.DepartmentType.FINANCE,
        DepartmentClearance.DepartmentType.LIBRARY,
        DepartmentClearance.DepartmentType.ACADEMIC_DEAN,
        DepartmentClearance.DepartmentType.STUDENT_AFFAIRS,
        DepartmentClearance.DepartmentType.REGISTRAR,
    ]

    for st in stations:
        dc, _ = DepartmentClearance.objects.get_or_create(
            application=app,
            department=st,
            defaults={"status": DepartmentClearance.ClearanceStatus.PENDING}
        )

        # Auto-clear Finance if balance is 0.00
        if st == DepartmentClearance.DepartmentType.FINANCE:
            fin_info = check_financial_clearance(student_profile)
            if fin_info["balance"] <= Decimal("0.00"):
                dc.status = DepartmentClearance.ClearanceStatus.CLEARED
                dc.cleared_at = timezone.now()
                dc.remarks = "Verified zero outstanding balance. Auto-cleared by financial ledger."
                dc.save()

        # Auto-clear Library if no active/overdue loans
        elif st == DepartmentClearance.DepartmentType.LIBRARY:
            from university.models import BookLoan
            active_loans = BookLoan.objects.filter(borrower=student_profile.user, status__in=["ACTIVE", "OVERDUE"]).count()
            if active_loans == 0:
                dc.status = DepartmentClearance.ClearanceStatus.CLEARED
                dc.cleared_at = timezone.now()
                dc.remarks = "No borrowed books or overdue fines. Auto-cleared by library repository."
                dc.save()

    # Log action to Audit Trail
    log_activity(
        request=request,
        user=student_profile.user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.ACADEMICS,
        entity="GraduationApplication",
        entity_id=app.id,
        description=f"Initiated graduation clearance for {student_profile.roll_no} - {student_profile.user.display_name}",
        new_state={"cgpa": str(audit["cgpa"]), "classification": audit["classification"]},
    )

    return app, audit


@transaction.atomic
def process_department_clearance(clearance_id, status, user=None, remarks="", request=None):
    """
    Process a departmental sign-off (cleared or rejected) for a student's graduation clearance.
    """
    dc = DepartmentClearance.objects.select_for_update().get(pk=clearance_id)
    dc.status = status
    dc.cleared_by = user
    dc.cleared_at = timezone.now()
    dc.remarks = remarks
    dc.save()

    app = dc.application
    all_clearances = app.clearances.all()

    if all(c.status == DepartmentClearance.ClearanceStatus.CLEARED for c in all_clearances):
        app.status = GraduationApplication.Status.CLEARED
        app.notes = "All 5 departmental clearance stations approved. Ready for Senate conferment."
        app.save(update_fields=["status", "notes"])
    elif any(c.status == DepartmentClearance.ClearanceStatus.REJECTED for c in all_clearances):
        app.status = GraduationApplication.Status.REJECTED
        app.notes = f"Clearance rejected or held by {dc.get_department_display()}."
        app.save(update_fields=["status", "notes"])
    elif app.status not in (GraduationApplication.Status.SENATE_APPROVED, GraduationApplication.Status.GRADUATED):
        app.status = GraduationApplication.Status.CLEARANCE_IN_PROGRESS
        app.notes = "Clearance in progress. Awaiting departmental sign-offs."
        app.save(update_fields=["status", "notes"])

    # Log to Audit Log
    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ACADEMICS,
        entity="DepartmentClearance",
        entity_id=dc.id,
        description=f"{dc.get_department_display()} set clearance to {dc.status} for {app.student.roll_no} (Remarks: {remarks})",
        new_state={"status": dc.status, "remarks": remarks},
    )

    return dc


@transaction.atomic
def approve_senate_graduation(application_id, user=None, request=None):
    """Senate degree conferment sign-off."""
    app = GraduationApplication.objects.select_for_update().get(pk=application_id)
    app.status = GraduationApplication.Status.SENATE_APPROVED
    app.senate_approved_at = timezone.now()
    app.save(update_fields=["status", "senate_approved_at"])

    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.MARKS_APPROVAL,
        module=AuditLog.Module.ACADEMICS,
        entity="GraduationApplication",
        entity_id=app.id,
        description=f"Senate approved degree conferment for {app.student.roll_no} with {app.get_classification_display()}",
    )
    return app


# ==============================================================================
# PDF GENERATION: DEGREE CERTIFICATE & CLEARANCE CERTIFICATE
# ==============================================================================

def generate_degree_certificate_pdf(application):
    """
    Generate an official landscape Degree Certificate PDF with institutional seal,
    gold ornamental border, Senate signatures, and security serial.
    """
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = document_styles()
    inst_name = get_branding()["site_name"]
    motto = get_setting("institution_motto", "Excellence in Knowledge, Integrity in Leadership")

    navy = colors.HexColor("#1A365D")
    gold = colors.HexColor("#B7791F")
    dark = colors.HexColor("#2D3748")

    title_style = ParagraphStyle(
        "CertTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=24,
        leading=30,
        alignment=1,
        textColor=navy,
    )

    motto_style = ParagraphStyle(
        "CertMotto",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=14,
        alignment=1,
        textColor=gold,
    )

    cert_text = ParagraphStyle(
        "CertBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=13,
        leading=20,
        alignment=1,
        textColor=dark,
    )

    name_style = ParagraphStyle(
        "CertStudent",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=22,
        leading=28,
        alignment=1,
        textColor=navy,
    )

    degree_style = ParagraphStyle(
        "CertDegree",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=18,
        leading=24,
        alignment=1,
        textColor=gold,
    )

    elements = []

    # Outer border table
    elements.append(Paragraph(f"<b>{inst_name.upper()}</b>", title_style))
    elements.append(Paragraph(f'"{motto}"', motto_style))
    elements.append(Spacer(1, 14))

    elements.append(Paragraph("By the authority of the University Senate and Council, be it known that", cert_text))
    elements.append(Spacer(1, 10))

    student_name = application.student.user.get_full_name() or application.student.user.username
    elements.append(Paragraph(f"<b>{student_name.upper()}</b>", name_style))
    elements.append(Paragraph(f"Registration No: <b>{application.student.roll_no}</b>", cert_text))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("having satisfied all requirements of the Faculty and the University Senate has been admitted to the degree of", cert_text))
    elements.append(Spacer(1, 8))

    prog_name = application.student.program.name if application.student.program else "Bachelor of Science"
    elements.append(Paragraph(f"<b>{prog_name.upper()}</b>", degree_style))
    elements.append(Paragraph(f"<b>{application.get_classification_display().upper()}</b>", cert_text))
    elements.append(Spacer(1, 14))

    ceremony_date_str = application.ceremony.ceremony_date.strftime("%B %d, %Y") if application.ceremony else date.today().strftime("%B %d, %Y")
    elements.append(Paragraph(f"Conferred on this <b>{ceremony_date_str}</b> with all the rights, honours, and privileges thereto appertaining.", cert_text))
    elements.append(Spacer(1, 24))

    # Signatures
    sig_data = [
        [
            Paragraph("____________________________<br/><b>VICE-CHANCELLOR</b>", ParagraphStyle("VC", alignment=1, fontSize=10, leading=14)),
            Paragraph(f"<b>[ SEAL ]</b><br/><font size=8 color='#718096'>Serial: {application.certificate_serial or 'PENDING'}</font>", ParagraphStyle("Seal", alignment=1, fontSize=11, leading=14, textColor=navy)),
            Paragraph("____________________________<br/><b>ACADEMIC REGISTRAR</b>", ParagraphStyle("REG", alignment=1, fontSize=10, leading=14)),
        ]
    ]
    sig_table = Table(sig_data, colWidths=[240, 240, 240])
    sig_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_clearance_certificate_pdf(application):
    """
    Generate an official portrait Certificate of University Clearance PDF
    displaying sign-offs from all 5 administrative and academic departments.
    """
    buffer = io.BytesIO()
    doc = ReportDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = document_styles()
    inst_name = get_branding()["site_name"]

    header_style = ParagraphStyle(
        "ClHeader",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=16,
        leading=20,
        alignment=1,
        textColor=colors.HexColor("#1A365D"),
    )

    sub_style = ParagraphStyle(
        "ClSub",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=12,
        leading=16,
        alignment=1,
        textColor=colors.HexColor("#2B6CB0"),
    )

    elements = []
    elements.append(Paragraph(inst_name.upper(), header_style))
    elements.append(Paragraph("OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)", sub_style))
    elements.append(Paragraph("CERTIFICATE OF UNIVERSITY EXIT CLEARANCE", ParagraphStyle("Title", alignment=1, fontName="Quicksand-Bold", fontSize=13, leading=18, textColor=colors.HexColor("#D69E2E"))))
    elements.append(Spacer(1, 14))

    # Student Info Grid
    st = application.student
    student_name = st.user.get_full_name() or st.user.username
    prog_name = st.program.name if st.program else "General Degree"
    
    info_data = [
        [Paragraph("<b>Student Full Name:</b>", styles["Normal"]), Paragraph(student_name, styles["Normal"]),
         Paragraph("<b>Registration No:</b>", styles["Normal"]), Paragraph(st.roll_no, styles["Normal"])],
        [Paragraph("<b>Programme of Study:</b>", styles["Normal"]), Paragraph(prog_name, styles["Normal"]),
         Paragraph("<b>National ID / Passport:</b>", styles["Normal"]), Paragraph(st.user.username, styles["Normal"])],
        [Paragraph("<b>Cumulative GPA:</b>", styles["Normal"]), Paragraph(f"{application.final_cgpa:.2f}", styles["Normal"]),
         Paragraph("<b>Clearance Status:</b>", styles["Normal"]), Paragraph(f"<b>{application.get_status_display()}</b>", styles["Normal"])],
    ]
    info_table = Table(info_data, colWidths=[130, 130, 130, 130])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#EDF2F7")),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 16))

    # Department Clearance Matrix
    elements.append(Paragraph("<b>DEPARTMENTAL CLEARANCE SIGN-OFF VERIFICATION</b>", ParagraphStyle("MatrixH", fontName="Quicksand-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#2D3748"))))
    elements.append(Spacer(1, 6))

    matrix_headers = ["Department / Station", "Status", "Verified By", "Date Cleared", "Remarks / Hold Notes"]
    matrix_rows = [matrix_headers]

    for dc in application.clearances.all():
        cleared_date = dc.cleared_at.strftime("%Y-%m-%d") if dc.cleared_at else "—"
        verifier = dc.cleared_by.get_full_name() or dc.cleared_by.username if dc.cleared_by else "System Auto"
        status_txt = f"<font color='green'><b>CLEARED</b></font>" if dc.status == "CLEARED" else f"<font color='red'><b>{dc.status}</b></font>"
        matrix_rows.append([
            Paragraph(f"<b>{dc.get_department_display()}</b>", styles["Normal"]),
            Paragraph(status_txt, styles["Normal"]),
            Paragraph(verifier, styles["Normal"]),
            Paragraph(cleared_date, styles["Normal"]),
            Paragraph(dc.remarks or "All requirements fulfilled.", styles["Normal"]),
        ])

    matrix_table = Table(matrix_rows, colWidths=[120, 75, 105, 80, 140])
    matrix_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A365D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Quicksand-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ]))
    elements.append(matrix_table)
    elements.append(Spacer(1, 24))

    # Final Registrar Seal
    final_note = Paragraph(
        "This is to certify that the student named above has returned all university property, cleared all financial obligations, and has been fully discharged by all designated departments.",
        ParagraphStyle("FN", fontName="Quicksand", fontSize=9, leading=13, textColor=colors.HexColor("#4A5568"))
    )
    elements.append(final_note)
    elements.append(Spacer(1, 20))

    seal_table = Table([
        [
            Paragraph("____________________________<br/><b>Head, Academic Division</b>", styles["Normal"]),
            Paragraph("<b>OFFICIAL UNIVERSITY CLEARANCE STAMP</b><br/><font size=7 color='#718096'>Verified by Registrar Records Office</font>", ParagraphStyle("SealTxt", alignment=1, fontSize=8, leading=11)),
            Paragraph(f"<b>Date Issued:</b> {date.today().strftime('%B %d, %Y')}<br/><b>Certificate Serial:</b> {application.certificate_serial}", styles["Normal"]),
        ]
    ], colWidths=[170, 180, 170])
    elements.append(seal_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
