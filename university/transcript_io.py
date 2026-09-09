"""Published academic history and formal A4 reports; Oxford-standard official layout."""
import hashlib
import io
import json
from collections import Counter
from datetime import date
from xml.sax.saxutils import escape

from django.utils import timezone
from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet, make_qr_code_flowable)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker

from . import examination_services as workflow
from .models import GradingScale, grade_point_for

NOTES = ('Only complete, published course results are included in certified records. '
         'GPA is the credit-weighted sum of grade points divided by attempted credits; '
         'CGPA applies the same formula cumulatively across all completed academic sessions. '
         'Published supplementary or repeat assessments replace original grades where authorized. '
         'Credits are earned once per passed course unit. Grades use each examination’s saved '
         'grading scale.')


def build_transcript_context(student, term=None):
    statement = workflow.student_statement(student)
    complete = [g for g in statement.groups if g['complete']]
    by_term = {}
    for g in complete:
        by_term.setdefault(g['term'].pk if g['term'] else None, []).append(g)
    semesters, running, earned = [], [], {}
    for groups in sorted(by_term.values(), key=lambda gs: (gs[0]['term'].start_date if gs[0]['term'] else date.min, gs[0]['term'].pk if gs[0]['term'] else 0)):
        groups.sort(key=lambda g: g['course'].code)
        running.extend(groups)
        new_credits = 0
        for g in groups:
            if g['outcome'] == 'Pass' and g['course'].pk not in earned:
                earned[g['course'].pk] = g['course'].credits
                new_credits += g['course'].credits
        semesters.append(dict(term=groups[0]['term'], groups=groups,
            term_gpa=workflow.weighted_gpa(groups), cumulative_gpa=workflow.weighted_gpa(running),
            credits_attempted=sum(g['course'].credits for g in groups), credits_completed=new_credits))
    visible = [s for s in semesters if not term or (s['term'] and s['term'].pk == term.pk)]
    selected = [g for s in visible for g in s['groups']]
    legends = []
    for g in selected:
        bands = g['final'].grade_bands
        key = json.dumps(bands, sort_keys=True)
        entry = next((x for x in legends if x['key'] == key), None)
        if entry is None:
            entry = {'key': key, 'courses': [], 'bands': sorted(bands, key=lambda b: b['minimum'], reverse=True)}
            legends.append(entry)
        entry['courses'].append(f"{g['course'].code} ({g['term'] or 'Unassigned term'})")

    issued = timezone.localdate()
    fingerprint = [(g['course'].pk, g['course'].code, g['course'].title, g['course'].credits,
                    str(g['term']), str(g['total']), g['grade'], str(g['grade_point']),
                    [(c['effective'].pk, c['effective'].exam.revision) for c in g['components']]) for g in selected]
    digest = hashlib.sha256(json.dumps([student.pk, str(issued), fingerprint], sort_keys=True).encode()).hexdigest()[:12].upper()

    cgpa_val = workflow.weighted_gpa(running)
    period_gpa_val = workflow.weighted_gpa(selected)
    if cgpa_val is not None:
        if cgpa_val >= 3.5:
            standing = 'First Class Honours Equivalent'
        elif cgpa_val >= 3.0:
            standing = 'Second Class Honours (Upper Division)'
        elif cgpa_val >= 2.5:
            standing = 'Second Class Honours (Lower Division)'
        elif cgpa_val >= 2.0:
            standing = 'Pass / Normal Academic Progress'
        else:
            standing = 'Academic Warning / Probation'
    else:
        standing = 'In Progress'

    # Fallback CUE scales if no exam-specific bands
    cue_scales = list(GradingScale.objects.filter(is_active=True).order_by('order'))
    if not legends and cue_scales:
        legends = [{
            'key': 'cue_standard',
            'courses': ['Kenyan Commission for University Education (CUE) Standard Scale'],
            'bands': [{'grade': s.grade, 'minimum': float(s.min_mark), 'gp': float(s.grade_point), 'description': s.description} for s in cue_scales]
        }]

    return dict(
        student=student, semesters=visible, all_semesters=semesters,
        cgpa=cgpa_val, period_gpa=period_gpa_val,
        overall_standing=standing, honours_standing=standing,
        required_credits=getattr(student.program, 'required_credits', None) if student.program else None,
        overall_credits_attempted=sum(g['course'].credits for g in selected),
        overall_credits_completed=sum(s['credits_completed'] for s in visible),
        total_courses_completed=len({g['course'].pk for g in selected if g['outcome'] == 'Pass'}),
        grade_distribution=dict(sorted(Counter(g['grade'] for g in selected).items())),
        reference_no=f'UMS/TR/{issued.year}/{student.roll_no}-{digest}',
        digest=digest, generated_at=issued, legends=legends,
        excluded_count=len(statement.groups) - len(complete), notes=NOTES)


class TranscriptCanvas(PageNumberCanvas):
    def __init__(self, *args, watermark=None, tracking_info=None, **kwargs):
        kwargs.setdefault('footer_left', 'Academic Registry · Verify this record with the issuing institution')
        super().__init__(*args, watermark=watermark, tracking_info=tracking_info, **kwargs)


def number(value):
    return '—' if value is None else f'{value:.2f}'


def export_transcript_pdf(student, ctx, kind='provisional', site_name='University Management System',
                          site_address='', site_email='', site_phone='', logo_path=None,
                          verify_url=None, tracking_info=None):
    buffer = io.BytesIO()
    doc_titles = {
        'provisional': 'PROVISIONAL TRANSCRIPT OF RESULTS',
        'academic': 'OFFICIAL TRANSCRIPT OF ACADEMIC RECORD',
        'official': 'OFFICIAL TRANSCRIPT OF ACADEMIC RECORD',
        'performance': 'ACADEMIC PERFORMANCE & PROGRESSION REPORT'
    }
    title = doc_titles.get(kind, 'OFFICIAL ACADEMIC TRANSCRIPT')
    doc = ReportDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40,
                            topMargin=30, bottomMargin=48, title=title, author=site_name, invariant=1)
    styles = document_styles()

    # Oxford/Formal Typography Hierarchy
    body = ParagraphStyle('Record', fontName='Quicksand', fontSize=8.5, leading=11, textColor=colors.HexColor('#0f172a'))
    body_bold = ParagraphStyle('RecordBold', parent=body, fontName='Quicksand-Bold', textColor=colors.HexColor('#0f172a'))
    small = ParagraphStyle('Annotation', parent=body, fontSize=8, leading=10, textColor=colors.HexColor('#334155'))
    small_bold = ParagraphStyle('AnnotationBold', parent=small, fontName='Quicksand-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#0f172a'))
    heading = ParagraphStyle('Section', parent=body, fontName='Quicksand-Bold', fontSize=10, leading=13, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor('#0f172a'), keepWithNext=True)
    inst_title = ParagraphStyle('InstTitle', fontName='Quicksand-Bold', fontSize=15, leading=18, alignment=1, textColor=colors.HexColor('#0f172a'))
    inst_sub = ParagraphStyle('InstSub', fontName='Quicksand', fontSize=8.5, leading=11, alignment=1, textColor=colors.HexColor('#1e293b'))
    doc_title_style = ParagraphStyle('DocTitle', fontName='Quicksand-Bold', fontSize=12, leading=15, alignment=1, textColor=colors.HexColor('#0f172a'), spaceBefore=5, spaceAfter=3)

    def p(value, style=body):
        return Paragraph(escape(str(value)), style)

    def p_md(markup, style=body):
        """Wrap a string that already contains intentional ReportLab mini-markup
        (<b>, <br/>, <font>) written by this module — any interpolated dynamic
        values inside it must already be escape()'d by the caller before this
        is called, since this does not escape the string itself."""
        return Paragraph(markup, style)

    def make_table(rows, widths, header=True, custom_style=None):
        data = [[p(v, small_bold if header and i == 0 else (body_bold if isinstance(v, str) and v.startswith('**') else body)) for v in row] for i, row in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
        commands = [
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ]
        if header:
            commands += [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.0, colors.HexColor('#0f172a')),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
            ]
        if custom_style:
            commands.extend(custom_style)
        t.setStyle(TableStyle(commands))
        return t

    w = doc.width
    story = []

    # 1. Official Institutional Header (Crest + Title + Contact + Divider)
    header_table_data = []
    inst_contact = ' · '.join(x for x in [
        site_address,
        site_email,
        site_phone
    ] if x)

    if logo_path:
        logo = Image(logo_path)
        ratio = min(54 / logo.imageWidth, 54 / logo.imageHeight)
        logo.drawWidth, logo.drawHeight = logo.imageWidth * ratio, logo.imageHeight * ratio
        header_text = [
            p(site_name.upper(), inst_title),
            p('DIRECTORATE OF ACADEMIC AFFAIRS · OFFICE OF THE ACADEMIC REGISTRAR', inst_sub),
            p(inst_contact, ParagraphStyle('ContactP', parent=small, alignment=1)),
        ]
        header_table = Table([[logo, header_text]], colWidths=[65, w - 65])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        story.append(header_table)
    else:
        story.append(p(site_name.upper(), inst_title))
        story.append(p('DIRECTORATE OF ACADEMIC AFFAIRS · OFFICE OF THE ACADEMIC REGISTRAR', inst_sub))
        story.append(p(inst_contact, ParagraphStyle('ContactP', parent=small, alignment=1)))

    # Decorative formal double rule
    divider = Table([['']], colWidths=[w])
    divider.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, -1), 1.5, colors.HexColor('#0f172a')),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#64748b')),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    story.append(divider)
    story.append(Spacer(1, 4))

    # 2. Document Title Banner
    story.append(p(title, doc_title_style))
    if kind == 'provisional':
        story.append(p('PROVISIONAL ACADEMIC RECORD ISSUED FOR INTERIM REFERENCE — SUBJECT TO FINAL SENATE CONFIRMATION', ParagraphStyle('SubProv', parent=small, alignment=1, textColor=colors.HexColor('#b45309'), fontName='Quicksand-Bold')))
    elif kind == 'academic':
        story.append(p('ACADEMIC RECORD OF COMPLETED, PUBLISHED COURSE RESULTS', ParagraphStyle('SubAcad', parent=small, alignment=1, textColor=colors.HexColor('#334155'))))
    story.append(Spacer(1, 4))

    # 3. Student Profile Matrix
    program = student.program
    dept_name = program.department.name if program and program.department else 'Not recorded'
    matrix_rows = [
        ['Student name', student.user.display_name, 'Registration number', student.roll_no],
        ['Programme', program.name if program else 'Not recorded', 'Faculty / School', dept_name],
        ['Qualification level', program.get_level_display() if program else 'Not recorded', 'Current semester', f'Year {(student.current_semester + 1) // 2} · Sem {student.current_semester}'],
        ['Date of issue', ctx['generated_at'].strftime('%d %B %Y'), 'Transcript reference', ctx['reference_no']],
    ]
    matrix_table = Table([[p_md(f'<b>{escape(str(c))}</b>', small_bold) if j % 2 == 0 else p(c, body)
                          for j, c in enumerate(row)] for row in matrix_rows],
                         colWidths=[85, w/2 - 85, 95, w/2 - 95])
    matrix_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#94a3b8')),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f8fafc')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f8fafc')),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ]))
    story.append(matrix_table)
    story.append(Spacer(1, 6))

    # 4. Academic Semester Results Tables
    if not ctx['semesters']:
        story += [Spacer(1, 14), p('No certified published academic results are on record for the requested selection.', ParagraphStyle('EmptyP', parent=body, alignment=1))]

    for sem in ctx['semesters']:
        term = sem['term']
        term_label = f"ACADEMIC YEAR: {term.name.upper()} — SEMESTER RESULTS" if term else "ACADEMIC SESSION (UNASSIGNED TERM)"
        story.append(p(term_label, heading))

        if kind == 'provisional':
            # Provisional breakdown with CAT & Final Exam marks
            rows = [['Course', 'Course title', 'Credits', 'CAT', 'Exam', 'Total (%)', 'Grade', 'Points', 'Remarks']]
            col_w = [54, w - 350, 42, 34, 38, 50, 38, 40, 54]
            for g in sem['groups']:
                c = g['course']
                cat_m = g['components'][0]['effective'].cat_marks if g['components'] else None
                exam_m = g['components'][0]['effective'].exam_marks if g['components'] else None
                rows.append([
                    c.code,
                    c.title,
                    str(c.credits),
                    f"{cat_m:.0f}" if cat_m is not None else '—',
                    f"{exam_m:.0f}" if exam_m is not None else '—',
                    f"{g['total']:.1f}",
                    g['grade'],
                    number(g['grade_point']),
                    g['outcome']
                ])
            story.append(make_table(rows, col_w))
        elif kind != 'performance':
            # Official Academic Transcript: clean, formal curriculum record
            rows = [['Course', 'Course title', 'Level / Sem', 'Credits', 'Grade', 'Points', 'Remarks']]
            col_w = [56, w - 56 - 60 - 40 - 34 - 38 - 52, 60, 40, 34, 38, 52]
            for g in sem['groups']:
                c = g['course']
                rows.append([
                    c.code,
                    c.title,
                    f"Sem {c.semester_no}",
                    str(c.credits),
                    g['grade'],
                    number(g['grade_point']),
                    g['outcome']
                ])
            story.append(make_table(rows, col_w))

        # Term Progression Summary Strip
        term_summary = (
            f"<b>Semester Credits Attempted:</b> {sem['credits_attempted']}   |   "
            f"<b>Credits Earned:</b> {sem['credits_completed']}   |   "
            f"<b>Semester GPA:</b> {number(sem['term_gpa'])}   |   "
            f"<b>Cumulative GPA:</b> {number(sem['cumulative_gpa'])}"
        )
        term_box = Table([[p_md(term_summary, small)]], colWidths=[w])
        term_box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(term_box)
        story.append(Spacer(1, 4))

    # 5. Overall Academic Cumulative Summary
    story.append(p('OVERALL ACADEMIC CUMULATIVE SUMMARY', heading))
    period_or_cgpa = ctx['period_gpa'] if kind == 'provisional' else ctx['cgpa']
    summary_data = [
        [
            p_md(f"<b>Total Credits Attempted:</b> {ctx['overall_credits_attempted']}", body),
            p_md(f"<b>Total Credits Earned:</b> {ctx['overall_credits_completed']}", body),
            p_md(f"<b>Courses Completed:</b> {ctx['total_courses_completed']}", body),
        ],
        [
            p_md(f"<b>{'Selected period GPA' if kind == 'provisional' else 'Cumulative GPA (CGPA)'}:</b> <font size='10'><b>{number(period_or_cgpa)}</b></font>", body),
            p_md(f"<b>Academic Standing:</b> <b>{escape(str(ctx['overall_standing']))}</b>", body),
            p_md(f"<b>Required Credits:</b> {ctx['required_credits'] if ctx['required_credits'] is not None else 'Not recorded'}", body),
        ]
    ]
    sum_table = Table(summary_data, colWidths=[w/3, w/3, w/3])
    sum_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#0f172a')),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(sum_table)
    story.append(Spacer(1, 6))

    # 6. Performance Analytics Visuals (for Performance Report)
    if kind == 'performance' and ctx['semesters']:
        points = [(i, float(s['term_gpa'])) for i, s in enumerate(ctx['semesters'], 1) if s['term_gpa'] is not None]
        cumulative = [(i, float(s['cumulative_gpa'])) for i, s in enumerate(ctx['semesters'], 1) if s['cumulative_gpa'] is not None]
        if len(points) > 1:
            story.append(p('GRADE POINT AVERAGE (GPA & CGPA) PROGRESSION TREND', heading))
            chart = LinePlot()
            chart.x, chart.y, chart.width, chart.height = 30, 20, w - 60, 110
            chart.data = [points, cumulative]
            chart.lines[0].symbol = makeMarker('FilledCircle')
            chart.lines[1].symbol = makeMarker('Circle')
            chart.lines[0].strokeColor = colors.HexColor('#1e3a8a')
            chart.lines[1].strokeColor = colors.HexColor('#0284c7')
            chart.lines[1].strokeDashArray = [4, 3]
            chart.xValueAxis.valueMin = 1
            chart.xValueAxis.valueMax = max(2, len(ctx['semesters']))
            chart.xValueAxis.valueStep = 1
            chart.yValueAxis.valueMin = 0
            chart.yValueAxis.valueMax = max([4.0] + [v for _, v in points + cumulative])
            chart.yValueAxis.valueStep = 1.0
            drawing = Drawing(w, 140)
            drawing.add(chart)
            story.append(drawing)
            story.append(p('Solid Line: Semester GPA. Dashed Line: Cumulative CGPA. Horizontal Axis: Completed Terms in Chronological Sequence.', small))
            story.append(Spacer(1, 4))

        story.append(p('GRADE DISTRIBUTION BREAKDOWN', heading))
        dist_rows = [['Grade', 'Course Attempts']]
        cue_desc = {'A': '70%–100% (Excellent)', 'B': '60%–69% (Good)', 'C': '50%–59% (Satisfactory)', 'D': '40%–49% (Pass)', 'F': '0%–39% (Fail)'}
        for gr, ct in ctx['grade_distribution'].items():
            dist_rows.append([gr, f"{ct} course attempt{'s' if ct != 1 else ''}"])
        story.append(make_table(dist_rows, [80, w - 80]))
        story.append(Spacer(1, 6))

    # Saved scales are presented verbatim as minimum thresholds, including custom bands.
    story.append(p('APPLICABLE GRADING SCALES', heading))
    for leg in ctx['legends']:
        story.append(p('Applies to: ' + ', '.join(leg['courses']), small))
        scale_rows = [['Grade', 'Minimum mark (%)', 'Grade point', 'Description']]
        for band in leg['bands']:
            scale_rows.append([band['grade'], str(band['minimum']),
                number(band.get('gp', grade_point_for(band['grade']))), band.get('description') or '—'])
        story.append(make_table(scale_rows, [60, 115, 80, w - 255]))
        story.append(Spacer(1, 6))
    if not ctx['legends']:
        story.append(p('No grading scale is available for this selection.', small))

    # 8. Transcript Notes & Registry Certification Notice
    story.append(p('TRANSCRIPT NOTES & VERIFICATION NOTICE', heading))
    story.append(p(ctx['notes'], small))
    story.append(p_md(f"Official Registry Verification: Quote Document Serial <b>{escape(str(ctx['reference_no']))}</b> "
                      f"when contacting {escape(site_email) if site_email else 'the Academic Registry'}. "
                      f"Generated from the published records available on the date of issue.", small))

    # 9. Official Certification & Signatures Block (Oxford 3-column Layout with Live Verification QR)
    if kind != 'performance':
        story.append(Spacer(1, 8))
        qr_flowable = make_qr_code_flowable(verify_url or f"/verify/document/{ctx['reference_no']}/", size=48)
        sig_data = [
            [
                p_md('Prepared &amp; Verified By:<br/><br/><br/>________________________________<br/><b>Examinations Officer</b><br/>Academic Registry', small),
                [
                    p_md('<b>Registry Verification:</b>', ParagraphStyle('SealTitle', parent=small, alignment=1)),
                    Spacer(1, 2),
                    qr_flowable,
                    Spacer(1, 2),
                    p_md('<font size="6.5" color="#64748b">Scan to Verify Authenticity</font>', ParagraphStyle('SealSub', parent=small, alignment=1)),
                ],
                p_md('Approved &amp; Certified By:<br/><br/><br/>________________________________<br/><b>Academic Registrar</b><br/>Signature &amp; Official Stamp', small),
            ]
        ]
        sig_table = Table(sig_data, colWidths=[w * 0.38, w * 0.24, w * 0.38])
        sig_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether([sig_table]))

    def continued_header(pdf, document):
        pdf.saveState()
        pdf.setFont('Quicksand-Bold', 7.5)
        pdf.setFillColor(colors.HexColor('#64748b'))
        pdf.drawString(40, A4[1] - 22, f"{site_name.upper()} · {title} · Student: {student.roll_no} · Ref: {ctx['reference_no']}")
        pdf.setStrokeColor(colors.HexColor('#cbd5e1'))
        pdf.setLineWidth(0.4)
        pdf.line(40, A4[1] - 25, A4[0] - 40, A4[1] - 25)
        pdf.restoreState()

    def canvas_factory(*args, **kwargs):
        return TranscriptCanvas(*args, watermark='PROVISIONAL' if kind == 'provisional' else None,
                                tracking_info=tracking_info, **kwargs)

    doc.build(story, canvasmaker=canvas_factory, onLaterPages=continued_header)
    return buffer.getvalue()

