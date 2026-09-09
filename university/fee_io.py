import csv
import io

from django.utils import timezone

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Table, TableStyle
)

from university.student_io import NumberedCanvas


# ==============================================================================
# FEE EXPORT FUNCTIONS (CSV, EXCEL, PDF)
# ==============================================================================

def export_fee_csv(queryset):
    """Generate CSV byte string with UTF-8 BOM for fee reports."""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    headers = [
        "Invoice ID", "Invoice Title", "Student Roll No", "Student Name",
        "Student Email", "Academic Term", "Amount Billed (KES)",
        "Amount Paid (KES)", "Balance Due (KES)", "Status", "Issued Date", "Due Date"
    ]
    writer.writerow(headers)

    for inv in queryset:
        student = inv.student
        user = student.user
        term_name = inv.term.name if inv.term else "—"
        issued = inv.issued_on.strftime("%Y-%m-%d") if inv.issued_on else "—"
        due = inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "—"

        writer.writerow([
            f"INV-{inv.id:04d}",
            inv.title,
            student.roll_no,
            user.display_name,
            user.email,
            term_name,
            f"{float(inv.amount):.2f}",
            f"{float(inv.amount_paid):.2f}",
            f"{float(inv.balance):.2f}",
            inv.status,
            issued,
            due,
        ])

    return buffer.getvalue().encode("utf-8")


def export_fee_excel(queryset, site_name="University Management System", stats=None):
    """Generate professionally styled Excel (.xlsx) file for Fee Invoices."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fee Invoices"
    ws.views.sheetView[0].showGridLines = True

    primary_color = "6C5CE7"      # Emerald Green
    header_fill_color = "4834D4"  # Deep Emerald
    zebra_color = "ECFDF5"        # Light Mint tint
    border_color = "CBD5E1"

    font_title = Font(name="Arial", size=15, bold=True, color="FFFFFF")
    font_sub = Font(name="Arial", size=10, italic=True, color="D1FAE5")
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Arial", size=10, color="1E293B")
    font_bold_data = Font(name="Arial", size=10, bold=True, color="1E293B")
    font_total = Font(name="Arial", size=10, bold=True, color="4834D4")

    thin_border = Border(
        left=Side(style="thin", color=border_color),
        right=Side(style="thin", color=border_color),
        top=Side(style="thin", color=border_color),
        bottom=Side(style="thin", color=border_color),
    )
    total_border = Border(
        top=Side(style="thin", color="4834D4"),
        bottom=Side(style="double", color="4834D4"),
    )

    # 1. Title Banner
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = f"  {site_name.upper()} — FEE INVOICES & FINANCIAL REPORT"
    title_cell.font = font_title
    title_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    title_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 36

    # 2. Subtitle / Metadata Row
    total_billed = sum(float(i.amount) for i in queryset)
    total_paid = sum(float(i.amount_paid) for i in queryset)
    total_balance = sum(float(i.balance) for i in queryset)

    ws.merge_cells("A2:J2")
    sub_cell = ws["A2"]
    sub_cell.value = (
        f"  Generated: {timezone.now().strftime('%d %b %Y, %H:%M')} | "
        f"Invoices: {queryset.count()} | "
        f"Billed: KES {total_billed:,.0f} | "
        f"Collected: KES {total_paid:,.0f} | "
        f"Outstanding: KES {total_balance:,.0f}"
    )
    sub_cell.font = font_sub
    sub_cell.fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
    sub_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[2].height = 20

    ws.row_dimensions[3].height = 8

    # 3. Column Headers
    headers = [
        "Invoice ID", "Student Roll No", "Student Name", "Invoice Title",
        "Term", "Amount Billed (KES)", "Amount Paid (KES)", "Balance Due (KES)",
        "Status", "Due Date"
    ]
    header_row_idx = 4
    ws.row_dimensions[header_row_idx].height = 28

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row_idx, column=col_num)
        cell.value = header
        cell.font = font_header
        cell.fill = PatternFill(start_color=header_fill_color, end_color=header_fill_color, fill_type="solid")
        if "Amount" in header or "Balance" in header:
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif header in ["Invoice ID", "Student Roll No", "Term", "Status", "Due Date"]:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = thin_border

    # 4. Data Rows
    for row_idx, inv in enumerate(queryset, start=5):
        ws.row_dimensions[row_idx].height = 22
        fill_color = zebra_color if row_idx % 2 == 0 else "FFFFFF"
        row_fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")

        student = inv.student
        term_name = inv.term.name if inv.term else "—"
        due = inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "—"

        values = [
            (f"INV-{inv.id:04d}", font_bold_data, "center", "@"),
            (student.roll_no, font_data, "center", "@"),
            (student.user.display_name, font_data, "left", "@"),
            (inv.title, font_data, "left", "@"),
            (term_name, font_data, "center", "@"),
            (float(inv.amount), font_data, "right", "#,##0.00"),
            (float(inv.amount_paid), font_data, "right", "#,##0.00"),
            (float(inv.balance), font_bold_data, "right", "#,##0.00"),
            (inv.status, font_data, "center", "@"),
            (due, font_data, "center", "@"),
        ]

        for col_idx, (val, font, align_h, num_fmt) in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = val
            cell.font = font
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal=align_h, vertical="center")
            cell.number_format = num_fmt
            cell.border = thin_border

    # 5. Totals Row
    last_data_row = 4 + queryset.count()
    totals_row_idx = last_data_row + 1
    ws.row_dimensions[totals_row_idx].height = 25

    label_cell = ws.cell(row=totals_row_idx, column=4)
    label_cell.value = "TOTALS:"
    label_cell.font = font_total
    label_cell.alignment = Alignment(horizontal="right", vertical="center")

    total_billed_cell = ws.cell(row=totals_row_idx, column=6)
    total_billed_cell.value = total_billed
    total_billed_cell.font = font_total
    total_billed_cell.number_format = "#,##0.00"
    total_billed_cell.alignment = Alignment(horizontal="right", vertical="center")
    total_billed_cell.border = total_border

    total_paid_cell = ws.cell(row=totals_row_idx, column=7)
    total_paid_cell.value = total_paid
    total_paid_cell.font = font_total
    total_paid_cell.number_format = "#,##0.00"
    total_paid_cell.alignment = Alignment(horizontal="right", vertical="center")
    total_paid_cell.border = total_border

    total_bal_cell = ws.cell(row=totals_row_idx, column=8)
    total_bal_cell.value = total_balance
    total_bal_cell.font = font_total
    total_bal_cell.number_format = "#,##0.00"
    total_bal_cell.alignment = Alignment(horizontal="right", vertical="center")
    total_bal_cell.border = total_border

    # 6. Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row in [1, 2, 3]:
                continue
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["F"].width = 20
    ws.column_dimensions["G"].width = 20
    ws.column_dimensions["H"].width = 20

    ws.freeze_panes = "A5"

    output = io.BytesIO()
    finish_worksheet(ws, header_row=4, data_end=4 + queryset.count())
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def export_fee_pdf(queryset, site_name="University Management System", logo_path=None, filter_text=None):
    """Generate high-quality branded PDF financial report in landscape A4."""
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

    title_style = ParagraphStyle(
        "FeeReportTitle",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#4834D4"),
    )
    meta_style = ParagraphStyle(
        "FeeReportMeta",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#64748B"),
        alignment=2,
    )
    th_style = ParagraphStyle(
        "FeeTH",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=0,
    )
    th_right = ParagraphStyle(
        "FeeTHRight",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=2,
    )
    td_style = ParagraphStyle(
        "FeeTD",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    td_bold = ParagraphStyle(
        "FeeTDBold",
        parent=styles["Normal"],
        fontName="Quicksand-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4834D4"),
    )
    td_right = ParagraphStyle(
        "FeeTDRight",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
        alignment=2,
    )
    td_center = ParagraphStyle(
        "FeeTDCenter",
        parent=styles["Normal"],
        fontName="Quicksand",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
        alignment=1,
    )

    story = []
    branding = get_branding()
    branding.update(site_name=site_name, logo_path=logo_path or branding['logo_path'])
    story.append(letterhead(doc.width, 'Fee Invoices Report',
        subtitle=f"Total records: {queryset.count()}", filter_text=filter_text, branding=branding))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#6C5CE7"), spaceAfter=10))

    # 2. Summary KPI Strip
    total_billed = sum(float(inv.amount) for inv in queryset)
    total_paid = sum(float(inv.amount_paid) for inv in queryset)
    total_balance = sum(float(inv.balance) for inv in queryset)
    kpi_data = [
        [
            Paragraph("<b>Total Invoices</b><br/><font size='10'><b>" + str(queryset.count()) + "</b></font>", td_center),
            Paragraph("<b>Total Billed</b><br/><font size='10' color='#6C5CE7'><b>KES " + f"{total_billed:,.0f}" + "</b></font>", td_center),
            Paragraph("<b>Total Collected</b><br/><font size='10' color='#10B981'><b>KES " + f"{total_paid:,.0f}" + "</b></font>", td_center),
            Paragraph("<b>Outstanding Balance</b><br/><font size='10' color='#EF4444'><b>KES " + f"{total_balance:,.0f}" + "</b></font>", td_center),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[190, 190, 190, 194])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#A7F3D0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.transparent, spaceAfter=8))

    # 3. Invoices Table
    headers = [
        Paragraph("<b>Inv #</b>", th_style),
        Paragraph("<b>Student</b>", th_style),
        Paragraph("<b>Invoice Title</b>", th_style),
        Paragraph("<b>Term</b>", th_style),
        Paragraph("<b>Billed (KES)</b>", th_right),
        Paragraph("<b>Paid (KES)</b>", th_right),
        Paragraph("<b>Balance (KES)</b>", th_right),
        Paragraph("<b>Status</b>", th_style),
    ]

    col_widths = [55, 150, 160, 75, 85, 85, 85, 70]
    data = [headers]

    for inv in queryset:
        student = inv.student
        term_name = inv.term.name if inv.term else "—"

        status_color = "#10B981" if inv.status == "PAID" else ("#F59E0B" if inv.status == "PARTIAL" else "#EF4444")
        status_html = f"<font color='{status_color}'><b>{inv.status}</b></font>"

        row = [
            Paragraph(f"#{inv.id:04d}", td_bold),
            Paragraph(f"<b>{student.user.display_name}</b><br/><font size='7' color='#64748B'>{student.roll_no}</font>", td_style),
            Paragraph(inv.title, td_style),
            Paragraph(term_name, td_center),
            Paragraph(f"{float(inv.amount):,.0f}", td_right),
            Paragraph(f"{float(inv.amount_paid):,.0f}", td_right),
            Paragraph(f"<b>{float(inv.balance):,.0f}</b>", td_right),
            Paragraph(status_html, td_center),
        ]
        data.append(row)

    if len(data) == 1:
        empty_msg = Paragraph("<i>No fee records match the active search/filter criteria.</i>", td_style)
        data.append([empty_msg] + [""] * (len(headers) - 1))
    else:
        # Totals footer row
        totals_row = [
            Paragraph("<b>TOTALS</b>", td_bold),
            Paragraph("", td_style),
            Paragraph("", td_style),
            Paragraph("", td_style),
            Paragraph(f"<b>{total_billed:,.0f}</b>", td_right),
            Paragraph(f"<b>{total_paid:,.0f}</b>", td_right),
            Paragraph(f"<b>{total_balance:,.0f}</b>", td_right),
            Paragraph("", td_style),
        ]
        data.append(totals_row)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C5CE7")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2 if len(data) > 2 else -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]

    if len(data) > 2:
        # Style the totals row at the bottom
        t_style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#ECFDF5")))
        t_style.append(("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.HexColor("#6C5CE7")))

    t.setStyle(TableStyle(t_style))
    story.append(t)
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.getvalue()
