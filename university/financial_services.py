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


def get_or_create_semester_invoice(student, term=None, year_of_study=None, semester=None):
    """
    Ensure an official FeeInvoice exists for the student's semester registration.
    Uses FeeStructure configured for the student's program, year_of_study, and semester.
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

    billed_amount = fee_struct.total_fee if fee_struct else Decimal("55500.00")
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


def generate_fee_receipt_pdf(payment):
    """Generate an official University Fee Payment Receipt as a PDF."""
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
    primary_color = colors.HexColor(PRIMARY)
    dark_gray = colors.HexColor(INK)
    muted_gray = colors.HexColor(MUTED)

    title_style = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=18,
        leading=22,
        textColor=primary_color,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=14,
        textColor=muted_gray,
        alignment=1,
    )
    body_style = ParagraphStyle(
        "ReceiptBody",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=10,
        leading=15,
        textColor=dark_gray,
    )
    body_bold = ParagraphStyle(
        "ReceiptBodyBold",
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
            img = RLImage(logo_path, width=45, height=45)
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    story.append(Paragraph(escape(get_branding()["site_name"].upper()), title_style))
    story.append(Paragraph("FINANCE & STUDENT ACCOUNTS DEPARTMENT", subtitle_style))
    story.append(Paragraph("OFFICIAL PAYMENT RECEIPT", ParagraphStyle("SubHead", parent=title_style, fontSize=13, leading=16, textColor=colors.HexColor("#047857"))))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=15))

    student = payment.student or (payment.invoice.student if payment.invoice else None)
    user = student.user if student else None
    receipt_no = f"REC-{payment.id:06d}"
    paid_dt = payment.paid_on.strftime("%d %B %Y") if payment.paid_on else timezone.now().strftime("%d %B %Y")
    inv_title = payment.invoice.title if payment.invoice else "Direct Student Fee Payment"
    if payment.invoice:
        if payment.invoice.balance < Decimal("0.00"):
            inv_balance_str = f"CREDIT: KES {payment.invoice.credit:,.2f}"
            bal_label = "Invoice Account Credit:"
        else:
            inv_balance_str = f"KES {payment.invoice.balance:,.2f}"
            bal_label = "Remaining Invoice Balance:"
    else:
        inv_balance_str = "Allocated Across Fees"
        bal_label = "Remaining Invoice Balance:"

    verify_url = f"https://ums.ac.ke/finance/receipt/{payment.reference or payment.id}/"
    qr_drawing = make_qr_drawing(verify_url, size=52.0)

    header_table_data = [
        [Paragraph(f"<b>Receipt No:</b> {receipt_no}", body_style),
         Paragraph(f"<b>Date:</b> {paid_dt}", ParagraphStyle("RDate", parent=body_style, alignment=2)),
         qr_drawing or ""],
        [Paragraph(f"<b>Student Reg No:</b> <b>{student.roll_no if student else 'N/A'}</b>", body_style),
         Paragraph(f"<b>Payment Method:</b> {payment.method}", ParagraphStyle("RMethod", parent=body_style, alignment=2)),
         Paragraph("<font size='6' color='#64748b'>Scan to Verify</font>", ParagraphStyle("QRLbl", parent=body_style, alignment=1))],
        [Paragraph(f"<b>Student Name:</b> {user.display_name if user else 'N/A'}", body_style),
         Paragraph(f"<b>Transaction Ref:</b> {payment.reference}", ParagraphStyle("RRef", parent=body_style, alignment=2)),
         ""],
        [Paragraph(f"<b>Programme:</b> {student.program.name if (student and student.program) else '—'}", body_style),
         Paragraph(f"<b>Invoice / Purpose:</b> {inv_title}", ParagraphStyle("RInv", parent=body_style, alignment=2)),
         ""],
    ]
    htable = Table(header_table_data, colWidths=[240, 215, 60])
    htable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("SPAN", (2, 0), (2, 0)),
        ("ALIGN", (2, 0), (2, 1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(htable)
    story.append(Spacer(1, 14))

    # Payment Breakdown Table
    breakdown_data = [
        [Paragraph("<b>Item Description</b>", body_bold), Paragraph("<b>Amount (KES)</b>", ParagraphStyle("THRight", parent=body_bold, alignment=2))],
        [Paragraph(f"Payment received towards {inv_title}", body_style),
         Paragraph(f"{payment.amount:,.2f}", ParagraphStyle("TDRight", parent=body_style, alignment=2))],
        [Paragraph("<b>Total Amount Paid:</b>", body_bold),
         Paragraph(f"<b>KES {payment.amount:,.2f}</b>", ParagraphStyle("TotalRight", parent=body_bold, alignment=2, textColor=colors.HexColor("#047857")))],
        [Paragraph(bal_label, body_style),
         Paragraph(inv_balance_str, ParagraphStyle("BalRight", parent=body_style, alignment=2))],
    ]
    btable = Table(breakdown_data, colWidths=[360, 155])
    btable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#e2e8f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(btable)
    story.append(Spacer(1, 20))

    # Overall Student Financial Position
    clearance = check_financial_clearance(student) if student else {"is_cleared": True, "balance": Decimal('0.00'), "has_credit": False, "credit": Decimal('0.00')}
    if clearance.get("has_credit"):
        status_text = f"ACCOUNT IN CREDIT: KES {clearance['credit']:,.2f}"
        status_color = colors.HexColor("#0284c7")
    elif clearance["is_cleared"]:
        status_text = "ACCOUNT FULLY CLEARED"
        status_color = colors.HexColor("#047857")
    else:
        status_text = f"OUTSTANDING BALANCE: KES {clearance['balance']:,.2f}"
        status_color = colors.HexColor("#b91c1c")

    pos_data = [
        [Paragraph("<b>Overall Student Account Status:</b>", body_style),
         Paragraph(f"<b>{status_text}</b>", ParagraphStyle("StatusTxt", parent=body_bold, textColor=status_color, alignment=2))]
    ]
    postable = Table(pos_data, colWidths=[250, 265])
    postable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(postable)
    story.append(Spacer(1, 24))

    # Sign-off and official stamp box
    sign_data = [
        [Paragraph("<b>Issued by:</b> University Cashier / System<br/>Finance & Accounts Office", body_style),
         Paragraph("<b>OFFICIAL STAMP / RECEIVED</b><br/>Valid without signature if verified online.", ParagraphStyle("StampTxt", parent=body_style, alignment=1, textColor=muted_gray))],
    ]
    stable = Table(sign_data, colWidths=[310, 205])
    stable.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(KeepTogether([stable]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


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

    story.append(Paragraph(escape(get_branding()["site_name"].upper()), title_style))
    story.append(Paragraph("OFFICE OF THE BURSAR · STUDENT FINANCIAL SERVICES", subtitle_style))
    story.append(Paragraph("STUDENT STATEMENT OF ACCOUNT", ParagraphStyle("Head", parent=title_style, fontSize=13, leading=16, textColor=primary_color)))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=12))

    user = student.user
    today_str = timezone.now().strftime("%d %B %Y %H:%M")
    verify_url = f"https://ums.ac.ke/finance/statement/{student.roll_no}/"
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
