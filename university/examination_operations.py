from xml.sax.saxutils import escape
import io
import os
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet, make_qr_code_flowable)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from university.financial_services import check_financial_clearance
from university.models import Enrollment, Exam, Result


def generate_exam_card_pdf(student, term=None, verify_url=None, tracking_info=None):
    """
    Generate an official Student Examination Card (Admission Slip) as a PDF byte buffer.
    Enforces financial clearance: If the student has an uncleared balance, outputs a
    formal Financial Hold notice.
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
    primary_color = colors.HexColor("#6C5CE7")
    dark_gray = colors.HexColor("#1f2937")
    muted_gray = colors.HexColor("#4b5563")

    title_style = ParagraphStyle(
        "CardTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=17,
        leading=21,
        textColor=primary_color,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "CardSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=muted_gray,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "CardBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "CardBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    # Logo + Header
    logo_path = get_branding()["logo_path"]
    if logo_path and os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=40, height=40)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    story.append(Paragraph(escape(get_branding()["site_name"].upper()), title_style))
    story.append(Paragraph("DIRECTORATE OF EXAMINATIONS & TIMETABLING", subtitle_style))
    story.append(Paragraph("OFFICIAL STUDENT EXAMINATION CARD", ParagraphStyle("CardHead", parent=title_style, fontSize=13, leading=16, textColor=primary_color)))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    # Financial Clearance Gate
    clearance = check_financial_clearance(student, term=term)
    if not clearance["is_cleared"]:
        # Block card generation with formal Financial Hold Notice
        story.append(Spacer(1, 30))
        hold_style = ParagraphStyle(
            "HoldStyle",
            parent=styles["Normal"],
            fontName="Quicksand-Bold",
            fontSize=16,
            leading=22,
            textColor=colors.HexColor("#b91c1c"),
            alignment=1,
        )
        story.append(Paragraph("EXAMINATION CARD WITHHELD — FINANCIAL HOLD", hold_style))
        story.append(Spacer(1, 15))

        hold_body = (
            f"Dear <b>{student.user.display_name}</b> (Reg No: <b>{student.roll_no}</b>),<br/><br/>"
            f"Your examination card for the current academic session cannot be generated due to an outstanding fee balance.<br/><br/>"
            f"<b>Total Billed:</b> KES {clearance['total_billed']:,.2f}<br/>"
            f"<b>Total Paid:</b> KES {clearance['total_paid']:,.2f}<br/>"
            f"<b>Outstanding Balance:</b> <font color='#b91c1c'><b>KES {clearance['balance']:,.2f}</b></font><br/>"
            f"<b>Clearance Percentage:</b> {clearance['percentage_paid']:.1f}% (Required: 100%)<br/><br/>"
            f"Please proceed to the Finance Office or submit your fee payment via the Student Portal to clear your balance and unlock your official examination card."
        )
        story.append(Paragraph(hold_body, ParagraphStyle("HoldBody", parent=body_style, fontSize=11, leading=16)))
        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()

    # If Cleared: Generate Full Exam Card
    term_title = term.name if term else f"Year {student.year_of_study or 1} Semester {student.semester or 1}"
    user = student.user

    info_data = [
        [Paragraph(f"<b>Student Name:</b> {user.display_name}", body_style),
         Paragraph(f"<b>Academic Term:</b> {term_title}", ParagraphStyle("RightT", parent=body_style, alignment=2))],
        [Paragraph(f"<b>Registration No:</b> <b>{student.roll_no}</b>", body_style),
         Paragraph(f"<b>Year of Study:</b> Year {student.year_of_study or 1} Sem {student.semester or 1}", ParagraphStyle("RightY", parent=body_style, alignment=2))],
        [Paragraph(f"<b>Programme:</b> {student.program.name if student.program else '—'}", body_style),
         Paragraph(f"<b>Financial Status:</b> <font color='#047857'><b>CLEARED (100%)</b></font>", ParagraphStyle("RightF", parent=body_style, alignment=2))],
    ]
    itable = Table(info_data, colWidths=[290, 233])
    itable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(itable)
    story.append(Spacer(1, 10))

    # Fetch Registered Courses
    enrollments_qs = Enrollment.objects.filter(student=student, status__in=["ACTIVE", "ENROLLED"]).select_related("course")
    if term:
        term_enr = enrollments_qs.filter(term=term)
        if term_enr.exists():
            enrollments_qs = term_enr

    courses = [e.course for e in enrollments_qs]
    if not courses:
        # Fallback to general program courses if none enrolled
        courses = list(student.program.courses.filter(is_active=True)[:6]) if student.program else []

    table_rows = [
        [
            Paragraph("<b>#</b>", body_bold),
            Paragraph("<b>Course Code</b>", body_bold),
            Paragraph("<b>Course Title</b>", body_bold),
            Paragraph("<b>Units</b>", ParagraphStyle("TC", parent=body_bold, alignment=1)),
            Paragraph("<b>Exam Date / Hall</b>", body_bold),
            Paragraph("<b>Invigilator Sign</b>", ParagraphStyle("TI", parent=body_bold, alignment=1)),
        ]
    ]

    total_credits = 0
    for idx, course in enumerate(courses, start=1):
        credits = course.credits if hasattr(course, "credits") else 3
        total_credits += credits

        # Lookup exam date/room if scheduled
        exam_obj = Exam.objects.filter(course=course).first()
        if exam_obj and exam_obj.date:
            dt_hall = f"{exam_obj.date.strftime('%d/%m/%Y')}<br/>{exam_obj.room.name if exam_obj.room else 'Exam Hall 1'}"
        else:
            dt_hall = "Scheduled<br/>Main Hall"

        table_rows.append([
            Paragraph(str(idx), body_style),
            Paragraph(f"<b>{course.code}</b>", body_style),
            Paragraph(course.title, body_style),
            Paragraph(str(credits), ParagraphStyle("CU", parent=body_style, alignment=1)),
            Paragraph(dt_hall, ParagraphStyle("DH", parent=body_style, fontSize=8, leading=10)),
            Paragraph("[ &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; ]", ParagraphStyle("IS", parent=body_style, alignment=1, textColor=muted_gray)),
        ])

    table_rows.append([
        Paragraph("<b>Total</b>", body_bold),
        Paragraph("", body_bold),
        Paragraph(f"<b>{len(courses)} Courses Registered</b>", body_bold),
        Paragraph(f"<b>{total_credits}</b>", ParagraphStyle("TCU", parent=body_bold, alignment=1)),
        Paragraph("", body_bold),
        Paragraph("", body_bold),
    ])

    col_widths = [25, 75, 188, 40, 95, 100]
    courses_table = Table(table_rows, colWidths=col_widths)
    courses_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(courses_table)
    story.append(Spacer(1, 12))

    # Candidate Instructions Box
    instr_text = (
        "<b>RULES & INSTRUCTIONS FOR CANDIDATES:</b><br/>"
        "1. You MUST present this Examination Card alongside your valid University Student ID Card at every session.<br/>"
        "2. Candidates must be in the examination room at least 15 minutes before the scheduled start time.<br/>"
        "3. Unauthorized materials, including mobile phones, programmable calculators, and notes, are strictly prohibited in the exam room.<br/>"
        "4. This card is valid ONLY when stamped by the Finance Office confirming full fee payment."
    )
    story.append(Paragraph(instr_text, ParagraphStyle("Instr", parent=body_style, fontSize=8, leading=11)))
    story.append(Spacer(1, 14))

    # Official Signatures and Stamp Block with Verification QR
    ref_code = f"EXAM-CARD/{term.name.replace(' ', '') if term else 'SESSION'}/{student.roll_no}"
    qr_url = verify_url or f"/verify/document/{ref_code}/"
    qr_flowable = make_qr_code_flowable(qr_url, size=46)

    stamp_data = [
        [
            Paragraph("<b>Candidate Signature:</b> ____________________<br/>Date: ________________________", body_style),
            [
                Paragraph("<b>VERIFICATION QR</b>", ParagraphStyle("QRTitle", parent=body_style, fontSize=7.5, alignment=1)),
                Spacer(1, 1),
                qr_flowable,
                Paragraph("<font size='6.5' color='#64748b'>Invigilator Scan</font>", ParagraphStyle("QRSub", parent=body_style, alignment=1)),
            ],
            Paragraph("<b>FINANCIAL CLEARANCE</b><br/><font color='#047857'><b>★ CLEARED FOR EXAMS ★</b></font><br/>Academic Registry / Finance Stamp", ParagraphStyle("FinStamp", parent=body_style, alignment=1, textColor=colors.HexColor("#047857"))),
        ]
    ]
    stamp_table = Table(stamp_data, colWidths=[205, 110, 208])
    stamp_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(KeepTogether([stamp_table]))

    def canvas_factory(*args, **kwargs):
        return PageNumberCanvas(*args, tracking_info=tracking_info, **kwargs)

    doc.build(story, canvasmaker=canvas_factory)
    buffer.seek(0)
    return buffer.getvalue()


def generate_nominal_roll_pdf(exam):
    """
    Generate the official Examination Nominal Roll / Attendance Signing Sheet for a course exam.
    Used by room invigilators to record student booklet numbers and signatures.
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
    primary_color = colors.HexColor("#6C5CE7")
    dark_gray = colors.HexColor("#1f2937")

    title_style = ParagraphStyle(
        "RollTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=16,
        leading=20,
        textColor=primary_color,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "RollBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "RollBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    story.append(Paragraph(escape(get_branding()["site_name"].upper()), title_style))
    story.append(Paragraph("OFFICE OF EXAMINATIONS · ROOM ATTENDANCE & NOMINAL ROLL", ParagraphStyle("NRSub", parent=title_style, fontSize=11, leading=15, textColor=colors.HexColor("#4b5563"))))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=10))

    course = exam.course
    exam_date_str = exam.date.strftime("%d %B %Y") if exam.date else "—"
    exam_room_str = exam.room.name if exam.room else "Main Examination Hall"

    meta_data = [
        [Paragraph(f"<b>Course:</b> {course.code} - {course.title}", body_style),
         Paragraph(f"<b>Date:</b> {exam_date_str}", ParagraphStyle("RD", parent=body_style, alignment=2))],
        [Paragraph(f"<b>Department:</b> {course.department.name if course.department else '—'}", body_style),
         Paragraph(f"<b>Room:</b> {exam_room_str}", ParagraphStyle("RR", parent=body_style, alignment=2))],
        [Paragraph(f"<b>Exam Type:</b> {exam.get_kind_display() if hasattr(exam, 'get_kind_display') else 'Final Exam'}", body_style),
         Paragraph(f"<b>Max Marks:</b> {exam.exam_max_marks} pts", ParagraphStyle("RM", parent=body_style, alignment=2))],
    ]
    mtable = Table(meta_data, colWidths=[310, 213])
    mtable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(mtable)
    story.append(Spacer(1, 10))

    # Retrieve Enrolled Students
    enrollments = Enrollment.objects.filter(course=course, status="ENROLLED").select_related("student__user").order_by("student__roll_no")

    rows = [
        [
            Paragraph("<b>Seat</b>", body_bold),
            Paragraph("<b>Roll No</b>", body_bold),
            Paragraph("<b>Student Name</b>", body_bold),
            Paragraph("<b>Booklet Serial No.</b>", body_bold),
            Paragraph("<b>Candidate Signature</b>", body_bold),
            Paragraph("<b>Invigilator Init</b>", ParagraphStyle("TInit", parent=body_bold, alignment=1)),
        ]
    ]

    for idx, enr in enumerate(enrollments, start=1):
        st = enr.student
        rows.append([
            Paragraph(f"{idx:02d}", body_style),
            Paragraph(f"<b>{st.roll_no}</b>", body_style),
            Paragraph(st.user.display_name, body_style),
            Paragraph("[ &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; ]", ParagraphStyle("BS", parent=body_style, textColor=colors.HexColor("#9ca3af"))),
            Paragraph("[ &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; &nbsp; ]", ParagraphStyle("CS", parent=body_style, textColor=colors.HexColor("#9ca3af"))),
            Paragraph("[ &nbsp; &nbsp; &nbsp; ]", ParagraphStyle("II", parent=body_style, alignment=1, textColor=colors.HexColor("#9ca3af"))),
        ])

    col_widths = [35, 85, 163, 95, 105, 40]
    roll_table = Table(rows, colWidths=col_widths)
    roll_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(roll_table)
    story.append(Spacer(1, 15))

    # Invigilator Summary & Certification
    cert_text = (
        f"<b>INVIGILATOR'S SUMMARY &amp; ATTENDANCE CERTIFICATION:</b><br/>"
        f"Total Candidates Registered: <b>{len(enrollments)}</b> &nbsp; | &nbsp; "
        f"Candidates Present: _______ &nbsp; | &nbsp; Candidates Absent: _______<br/><br/>"
        f"Chief Invigilator Name: __________________________ &nbsp; Signature: ______________________ &nbsp; Date: ___________"
    )
    cert_table = Table([[Paragraph(cert_text, ParagraphStyle("Cert", parent=body_style, fontSize=8.5, leading=13))]], colWidths=[523])
    cert_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether([cert_table]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def cap_supplementary_grade(grade, marks):
    """
    Apply Kenyan CUE university regulation:
    Supplementary examination marks are capped at a maximum of 50% / Grade 'C'.
    """
    capped_marks = min(Decimal(str(marks)), Decimal("50.0"))
    if grade in ["A", "B"]:
        return "C", capped_marks
    return grade, capped_marks
