from xml.sax.saxutils import escape
import io
import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet, make_qr_drawing, PRIMARY, PRIMARY_DARK, INK, MUTED, BORDER, ZEBRA)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from university.models import FeeInvoice, FeeStructure, Payment


class FeeScheduleMissingError(Exception):
    """
    Raised when no FeeStructure is configured for a student's program/year/
    semester (or the program at all). An official FeeInvoice is a real
    financial record the student is expected to pay against -- generating
    one with an invented amount just because nobody configured the fee
    schedule yet would silently bill the student a number with no basis in
    the institution's actual fee configuration. Callers must decide whether
    to hard-fail or skip invoicing (and log it) rather than fabricate.
    """
    pass


def get_or_create_semester_invoice(student, term=None, year_of_study=None, semester=None):
    """
    Ensure an official FeeInvoice exists for the student's semester registration.
    Uses FeeStructure configured for the student's program, year_of_study, and semester.

    Raises FeeScheduleMissingError if no matching (or general) FeeStructure
    exists for the student's program -- never fabricates a billed amount.
    """
    y = year_of_study or student.year_of_study or 1
    s = semester or student.semester or 1

    fee_struct = FeeStructure.objects.filter(
        program=student.program,
        year_of_study=y,
        semester=s
    ).first()

    if not fee_struct:
        # Fallback to general program fee structure if any
        fee_struct = FeeStructure.objects.filter(program=student.program).first()

    if not fee_struct:
        raise FeeScheduleMissingError(
            f"No fee structure is configured for "
            f"{student.program.name if student.program else 'this programme'} "
            f"(Year {y}, Semester {s}). Configure the fee schedule before invoicing this student."
        )

    billed_amount = fee_struct.total_fee
    term_name = term.name if term else f"Year {y} Semester {s}"
    title = f"{term_name} Tuition & Statutory Fees"

    invoice, created = FeeInvoice.objects.get_or_create(
        student=student,
        term=term,
        title=title,
        defaults={
            "amount": billed_amount,
            "amount_paid": Decimal("0.00"),
            "issued_on": timezone.now().date(),
            "due_date": timezone.now().date() + timedelta(days=30),
        }
    )
    return invoice, created


def check_financial_clearance(student, term=None, threshold_pct=100.0):
    """
    Determine if a student is financially cleared for examinations and academics.
    By default requires 100% clearance (or >= threshold_pct).
    """
    invoices = FeeInvoice.objects.filter(student=student)
    if term:
        # Check specific term invoices if present, or all if none
        term_invoices = invoices.filter(term=term)
        if term_invoices.exists():
            invoices = term_invoices

    total_billed = sum((inv.amount for inv in invoices), Decimal("0.00"))
    total_paid = sum((inv.amount_paid for inv in invoices), Decimal("0.00"))
    balance = total_billed - total_paid

    if total_billed > 0:
        pct = float((total_paid / total_billed) * 100)
    else:
        pct = 100.0

    is_cleared = (balance <= Decimal("0.00")) or (pct >= threshold_pct)

    if is_cleared:
        if balance < Decimal("0.00"):
            credit = abs(balance)
            msg = f"Financially cleared for examinations ({pct:.1f}% paid). Account credit: KES {credit:,.2f}."
        else:
            msg = f"Financially cleared for examinations ({pct:.1f}% paid)."
    else:
        msg = f"Financial clearance pending. Outstanding balance: KES {balance:,.2f} ({pct:.1f}% paid). Clearance requires {threshold_pct:.0f}%."

    return {
        "is_cleared": is_cleared,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "balance": balance,
        "credit": abs(balance) if balance < Decimal("0.00") else Decimal("0.00"),
        "has_credit": balance < Decimal("0.00"),
        "percentage_paid": round(pct, 1),
        "clearance_message": msg,
    }


class PDFBytes(bytes):
    """Bytes subclass with a getvalue() method for seamless compatibility."""
    def getvalue(self):
        return self


def generate_fee_receipt_pdf(payment):
    """Generate an official University Fee Payment Receipt as a PDF."""
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
    primary_color = colors.HexColor(PRIMARY)
    dark_gray = colors.HexColor(INK)
    muted_gray = colors.HexColor(MUTED)
    success_green = colors.HexColor("#047857")

    title_style = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=17,
        leading=21,
        textColor=primary_color,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=muted_gray,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "ReceiptBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "ReceiptBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    # Logo + Header
    branding = get_branding()
    logo_path = branding["logo_path"]
    if logo_path and os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=42, height=42)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 3))
        except Exception:
            pass

    inst_name = branding.get("site_name") or "WIGOT SCHOOL OF HOSPITALITY"
    if inst_name == "University Management System":
        inst_name = "WIGOT SCHOOL OF HOSPITALITY"

    story.append(Paragraph(escape(inst_name.upper()), title_style))
    story.append(Paragraph("FINANCE & STUDENT ACCOUNTS DEPARTMENT · MAMBOLEO, KISUMU", subtitle_style))
    story.append(Paragraph("OFFICIAL PAYMENT RECEIPT", ParagraphStyle("SubHead", parent=title_style, fontSize=12, leading=15, textColor=success_green)))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=12))

    student = payment.student or (payment.invoice.student if payment.invoice else None)
    user = student.user if student else None

    # Retrieve receipt metadata or fallback
    receipt = getattr(payment, "fee_receipt", None)
    if not receipt:
        try:
            receipt = FeeReceipt.objects.filter(payment=payment).first()
        except Exception:
            receipt = None

    if receipt:
        receipt_no = receipt.receipt_number
        prev_balance = receipt.previous_balance
        rem_balance = receipt.remaining_balance
    else:
        receipt_no = f"REC-{payment.paid_on.year if payment.paid_on else timezone.now().year}-{payment.id:06d}"
        prev_balance = Decimal("0.00")
        rem_balance = payment.invoice.balance if payment.invoice else Decimal("0.00")

    if payment.completed_at:
        paid_dt = payment.completed_at.strftime("%d %B %Y at %H:%M")
    elif payment.paid_on:
        paid_dt = payment.paid_on.strftime("%d %B %Y")
    else:
        paid_dt = timezone.now().strftime("%d %B %Y at %H:%M")

    # Real student/academic data only -- never a fabricated programme,
    # department, or academic-year value. When the relationship genuinely
    # isn't set on this payment, the receipt says so plainly rather than
    # printing a plausible-looking value that didn't come from the
    # database, per the explicit "no hard-coded or sample student data"
    # requirement for this document.
    inv_title = payment.invoice.title if payment.invoice else (payment.notes or "Tuition & Academic Fees Payment")
    prog_name = student.program.name if (student and student.program) else "Not Recorded"
    dept_name = student.program.department.name if (student and student.program and student.program.department) else "Not Recorded"

    ay = getattr(payment, "academic_year", None)
    ay_name = ay.name if ay else "Not Recorded"
    term = getattr(payment, "term", None)
    student_sem = getattr(student, "current_semester", getattr(student, "semester", 1)) if student else 1
    term_name = term.name if term else f"Semester {student_sem}"

    txn_ref = payment.provider_reference or getattr(payment, "reference", "") or payment.internal_reference or "N/A"
    fee_acc = payment.fee_account
    fee_acc_ref = (
        getattr(fee_acc, "account_identifier", None)
        or getattr(fee_acc, "name", None)
        or payment.internal_reference
        or "N/A"
    ) if fee_acc else (payment.internal_reference or "N/A")

    verify_url = f"https://ums.ac.ke/finance/receipt/{getattr(payment, 'reference', '') or payment.id}/"
    qr_drawing = make_qr_drawing(verify_url, size=52.0)

    header_table_data = [
        [Paragraph(f"<b>Receipt Number:</b> {receipt_no}", body_style),
         Paragraph(f"<b>Payment Date:</b> {paid_dt}", ParagraphStyle("RDate", parent=body_style, alignment=2)),
         qr_drawing or ""],
        [Paragraph(f"<b>Student Reg No:</b> <b>{student.roll_no if student else 'N/A'}</b>", body_style),
         Paragraph(f"<b>Payment Method:</b> {payment.method or 'Online'}", ParagraphStyle("RMethod", parent=body_style, alignment=2)),
         Paragraph("<font size='6' color='#64748b'>Scan to Verify</font>", ParagraphStyle("QRLbl", parent=body_style, alignment=1))],
        [Paragraph(f"<b>Student Name:</b> {user.display_name if user else (payment.payer_name or 'N/A')}", body_style),
         Paragraph(f"<b>M-Pesa / Txn Ref:</b> <b>{txn_ref}</b>", ParagraphStyle("RRef", parent=body_style, alignment=2)),
         ""],
        [Paragraph(f"<b>Programme:</b> {prog_name}", body_style),
         Paragraph(f"<b>Fee Account / Ref:</b> {fee_acc_ref}", ParagraphStyle("RRefAcc", parent=body_style, alignment=2)),
         ""],
        [Paragraph(f"<b>Department:</b> {dept_name}", body_style),
         Paragraph(f"<b>Academic Session:</b> {ay_name} · {term_name}", ParagraphStyle("RAcad", parent=body_style, alignment=2)),
         ""],
    ]
    htable = Table(header_table_data, colWidths=[235, 220, 60])
    htable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("SPAN", (2, 0), (2, 0)),
        ("ALIGN", (2, 0), (2, 1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(htable)
    story.append(Spacer(1, 10))

    # Payment Breakdown Table
    breakdown_data = [
        [Paragraph("<b>Item Description / Allocation</b>", body_bold), Paragraph("<b>Amount (KES)</b>", ParagraphStyle("THRight", parent=body_bold, alignment=2))],
        [Paragraph(f"Payment received towards: <b>{inv_title}</b>", body_style),
         Paragraph(f"{payment.amount:,.2f}", ParagraphStyle("TDRight", parent=body_style, alignment=2))],
        [Paragraph("<b>Previous Balance:</b>", body_style),
         Paragraph(f"KES {prev_balance:,.2f}", ParagraphStyle("PrevRight", parent=body_style, alignment=2))],
        [Paragraph("<b>Total Amount Paid (Received):</b>", body_bold),
         Paragraph(f"<b>KES {payment.amount:,.2f}</b>", ParagraphStyle("TotalRight", parent=body_bold, alignment=2, textColor=success_green))],
        [Paragraph("<b>Remaining Outstanding Balance:</b>", body_bold),
         Paragraph(f"<b>KES {rem_balance:,.2f}</b>", ParagraphStyle("BalRight", parent=body_bold, alignment=2, textColor=colors.HexColor("#b91c1c") if rem_balance > Decimal("0.00") else success_green))],
    ]
    btable = Table(breakdown_data, colWidths=[355, 160])
    btable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#ecfdf5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(btable)
    story.append(Spacer(1, 14))

    # Overall Student Financial Position
    clearance = check_financial_clearance(student) if student else {"is_cleared": True, "balance": Decimal('0.00'), "has_credit": False, "credit": Decimal('0.00')}
    if clearance.get("has_credit"):
        status_text = f"ACCOUNT IN CREDIT: KES {clearance['credit']:,.2f}"
        status_color = colors.HexColor("#0284c7")
    elif clearance["is_cleared"]:
        status_text = "ACCOUNT FULLY CLEARED"
        status_color = success_green
    else:
        status_text = f"NET OUTSTANDING BALANCE: KES {clearance['balance']:,.2f}"
        status_color = colors.HexColor("#b91c1c")

    pos_data = [
        [Paragraph("<b>Overall Student Account Status:</b>", body_style),
         Paragraph(f"<b>{status_text}</b>", ParagraphStyle("StatusTxt", parent=body_bold, textColor=status_color, alignment=2))]
    ]
    postable = Table(pos_data, colWidths=[240, 275])
    postable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(postable)
    story.append(Spacer(1, 18))

    # Sign-off and official stamp box
    sign_data = [
        [Paragraph("<b>Issued by:</b> University Cashier / Automated System<br/>Finance &amp; Student Accounts Office", body_style),
         Paragraph("<b>OFFICIAL STAMP / RECEIVED</b><br/>Valid without physical signature when verified online.", ParagraphStyle("StampTxt", parent=body_style, alignment=1, textColor=muted_gray))],
    ]
    stable = Table(sign_data, colWidths=[305, 210])
    stable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(KeepTogether([stable]))
    story.append(Spacer(1, 14))

    # Footer with Official Wigot School of Hospitality Contact Information & Disclaimer
    contact_footer = (
        "<b>Wigot School of Hospitality</b> &middot; Mamboleo, Kisumu, Off Kisumu–Kakamega Road<br/>"
        "Phone: +254 706 063 799 / +254 708 112 222 &middot; Email: info@wigotschoolofhospitality.com<br/>"
        "<i>Disclaimer: This official receipt is generated by the University Management System (UMS). "
        "Retain this document for your official records and semester examination clearances.</i>"
    )
    story.append(Paragraph(contact_footer, ParagraphStyle("FooterContact", parent=body_style, fontSize=7.5, leading=11, textColor=muted_gray, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return PDFBytes(buffer.getvalue())


def generate_student_statement_pdf(student):
    """Generate a formal Student Statement of Account (Ledger) as a PDF."""
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
    primary_color = colors.HexColor(PRIMARY)
    dark_gray = colors.HexColor(INK)
    muted_gray = colors.HexColor(MUTED)

    title_style = ParagraphStyle(
        "StmtTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=18,
        leading=22,
        textColor=primary_color,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "StmtSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=14,
        textColor=muted_gray,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "StmtBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "StmtBodyBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=9,
        leading=13,
        textColor=dark_gray,
    )

    story = []

    logo_path = get_branding()["logo_path"]
    if logo_path and os.path.exists(logo_path):
        try:
            img = RLImage(logo_path, width=40, height=40)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    branding = get_branding()
    story.append(Paragraph(escape(branding["site_name"].upper()), title_style))
    story.append(Paragraph("OFFICE OF THE BURSAR · STUDENT FINANCIAL SERVICES", subtitle_style))
    from university.institution_domain_services import get_institution_settings
    institution = get_institution_settings()
    story.append(Paragraph(escape(branding.get("contact_line", "") or institution.get("website_url", "")), subtitle_style))
    story.append(Paragraph("FEE STATEMENT", ParagraphStyle("Head", parent=title_style, fontSize=13, leading=16, textColor=primary_color)))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=12))

    user = student.user
    today_str = timezone.now().strftime("%d %B %Y %H:%M")
    verify_url = f"{institution.get('portal_url') or institution.get('website_url') or 'https://localhost'}/finance/statement/{student.roll_no}/"
    qr_drawing = make_qr_drawing(verify_url, size=52.0)

    info_data = [
        [Paragraph(f"<b>Student Name:</b> {user.display_name}", body_style),
         Paragraph(f"<b>Statement Date:</b> {today_str}", ParagraphStyle("RD", parent=body_style, alignment=2)),
         qr_drawing or ""],
        [Paragraph(f"<b>Registration No:</b> <b>{student.roll_no}</b>", body_style),
         Paragraph(f"<b>Year of Study:</b> Year {student.year_of_study or 1} Semester {student.semester or 1}", ParagraphStyle("RY", parent=body_style, alignment=2)),
         Paragraph("<font size='6' color='#64748b'>Scan to Verify</font>", ParagraphStyle("QRLbl", parent=body_style, alignment=1))],
        [Paragraph(f"<b>Programme:</b> {student.program.name if student.program else '—'}", body_style),
         Paragraph(f"<b>Department:</b> {student.program.department.name if (student.program and student.program.department) else '—'}", ParagraphStyle("RDept", parent=body_style, alignment=2)),
         ""],
    ]
    itable = Table(info_data, colWidths=[240, 225, 58])
    itable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (2, 0), (2, 0)),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(itable)
    story.append(Spacer(1, 12))

    # Compile unified ledger transactions (Invoices as Debits, Payments as Credits)
    transactions = []
    invoices = FeeInvoice.objects.filter(student=student).order_by("issued_on", "id")
    for inv in invoices:
        transactions.append({
            "date": inv.issued_on,
            "ref": f"INV-{inv.id:04d}",
            "description": inv.title,
            "debit": inv.amount,
            "credit": Decimal("0.00"),
            "sort_key": (inv.issued_on, 0, inv.id),
        })

    payments = Payment.objects.filter(
        Q(student=student) | Q(invoice__student=student),
        status=Payment.Status.SUCCESSFUL
    ).select_related("invoice", "fee_account").distinct().order_by("paid_on", "id")
    for pmt in payments:
        inv_title = pmt.invoice.title if pmt.invoice else "Tuition & Fee Allocation"
        ref_text = pmt.provider_reference or pmt.internal_reference or f"REC-{pmt.id:04d}"
        transactions.append({
            "date": pmt.paid_on,
            "ref": ref_text,
            "description": f"Payment: {pmt.method} ({inv_title})",
            "debit": Decimal("0.00"),
            "credit": pmt.amount,
            "sort_key": (pmt.paid_on, 1, pmt.id),
        })

    transactions.sort(key=lambda x: x["sort_key"])

    rows = [
        [
            Paragraph("<b>Date</b>", body_bold),
            Paragraph("<b>Reference</b>", body_bold),
            Paragraph("<b>Description</b>", body_bold),
            Paragraph("<b>Debit (KES)</b>", ParagraphStyle("THD", parent=body_bold, alignment=2)),
            Paragraph("<b>Credit (KES)</b>", ParagraphStyle("THC", parent=body_bold, alignment=2)),
            Paragraph("<b>Balance (KES)</b>", ParagraphStyle("THB", parent=body_bold, alignment=2)),
        ]
    ]

    running_bal = Decimal("0.00")
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for t in transactions:
        running_bal += (t["debit"] - t["credit"])
        total_debit += t["debit"]
        total_credit += t["credit"]
        dt_str = t["date"].strftime("%Y-%m-%d") if t["date"] else "—"
        deb_str = f"{t['debit']:,.2f}" if t["debit"] > 0 else "—"
        cred_str = f"{t['credit']:,.2f}" if t["credit"] > 0 else "—"

        rows.append([
            Paragraph(dt_str, body_style),
            Paragraph(t["ref"], body_style),
            Paragraph(t["description"], body_style),
            Paragraph(deb_str, ParagraphStyle("RDeb", parent=body_style, alignment=2)),
            Paragraph(cred_str, ParagraphStyle("RCred", parent=body_style, alignment=2)),
            Paragraph(f"{running_bal:,.2f}", ParagraphStyle("RBal", parent=body_style, alignment=2)),
        ])

    # Summary Row
    rows.append([
        Paragraph("<b>Totals</b>", body_bold),
        Paragraph("", body_bold),
        Paragraph("", body_bold),
        Paragraph(f"<b>{total_debit:,.2f}</b>", ParagraphStyle("RTD", parent=body_bold, alignment=2)),
        Paragraph(f"<b>{total_credit:,.2f}</b>", ParagraphStyle("RTC", parent=body_bold, alignment=2)),
        Paragraph(f"<b>{running_bal:,.2f}</b>", ParagraphStyle("RTB", parent=body_bold, alignment=2, textColor=colors.HexColor("#b91c1c") if running_bal > 0 else colors.HexColor("#047857"))),
    ])

    col_widths = [65, 95, 143, 70, 70, 80]
    ledger_table = Table(rows, colWidths=col_widths)
    ledger_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(ledger_table)
    story.append(Spacer(1, 16))

    # Statement footer
    clearance = check_financial_clearance(student)
    stat_msg = "ELIGIBLE FOR EXAMINATIONS &amp; ACADEMIC SERVICES" if clearance["is_cleared"] else "EXAMINATION CLEARANCE PENDING DUE TO OUTSTANDING BALANCE"
    stat_color = "#047857" if clearance["is_cleared"] else "#b91c1c"
    footer_text = (
        f"<b>Official Status:</b> <font color='{stat_color}'><b>{stat_msg}</b></font><br/>"
        f"<i>This document is an official computer-generated statement of student account issued by the University Management System. Verify online via the student finance portal.</i>"
    )
    ftable = Table([[Paragraph(footer_text, ParagraphStyle("FootP", parent=body_style, fontSize=8.5, leading=12))]], colWidths=[523])
    ftable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether([ftable]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
