import io
import os
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from university.financial_services import check_financial_clearance
from university.models import Enrollment, Exam, Result


def generate_exam_card_pdf(student, term=None):
    """
    Generate an official Student Examination Card (Admission Slip) as a PDF byte buffer.
    Enforces financial clearance: If the student has an uncleared balance, outputs a
    formal Financial Hold notice.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    primary_color = colors.HexColor("#1e3a8a")
    dark_gray = colors.HexColor("#1f2937")
    muted_gray = colors.HexColor("#4b5563")

    title_style = ParagraphStyle(
        "CardTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=primary_color,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "CardSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=muted_gray,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "CardBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "CardBodyBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    # Logo + Header
    logo_path = os.path.join(settings.BASE_DIR, "static", "img", "ums-logo.png")
    if os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=40, height=40)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    story.append(Paragraph("UNIVERSITY MANAGEMENT SYSTEM", title_style))
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
            fontName="Helvetica-Bold",
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

    # Official Signatures and Stamp Block
    stamp_data = [
        [
            Paragraph("<b>Student Signature:</b> ____________________<br/>Date: ________________________", body_style),
            Paragraph("<b>FINANCIAL CLEARANCE STAMP</b><br/><font color='#047857'><b>★ CERTIFIED CLEARED ★</b></font><br/>Registrar / Finance Officer", ParagraphStyle("FinStamp", parent=body_style, alignment=1, textColor=colors.HexColor("#047857"))),
        ]
    ]
    stamp_table = Table(stamp_data, colWidths=[290, 233])
    stamp_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(stamp_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_nominal_roll_pdf(exam):
    """
    Generate the official Examination Nominal Roll / Attendance Signing Sheet for a course exam.
    Used by room invigilators to record student booklet numbers and signatures.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    primary_color = colors.HexColor("#1e3a8a")
    dark_gray = colors.HexColor("#1f2937")

    title_style = ParagraphStyle(
        "RollTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=primary_color,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "RollBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "RollBodyBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    story.append(Paragraph("UNIVERSITY MANAGEMENT SYSTEM", title_style))
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
        f"<b>INVIGILATOR'S CERTIFICATION:</b><br/>"
        f"Total Candidates Registered: <b>{len(enrollments)}</b> &nbsp; | &nbsp; "
        f"Candidates Present: _______ &nbsp; | &nbsp; Candidates Absent: _______<br/>"
        f"Chief Invigilator Name: __________________________ Signature: ______________________ Date: ___________"
    )
    story.append(Paragraph(cert_text, ParagraphStyle("Cert", parent=body_style, fontSize=9, leading=14)))

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
