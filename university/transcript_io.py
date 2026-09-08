"""Published academic history and formal A4 reports; Oxford-standard official layout."""
import hashlib
import io
import json
from collections import Counter
from datetime import date
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker

from . import examination_services as workflow
from .models import GradingScale, grade_point_for

NOTES = ('Only complete, published course results are included in certified records. '
         'GPA is the credit-weighted sum of grade points divided by attempted credits; '
         'CGPA applies the same formula cumulatively across all completed academic sessions. '
         'Published supplementary or repeat assessments replace original grades where authorized. '
         'Credits are earned once per passed course unit. Grading adheres strictly to the Kenyan '
         'Commission for University Education (CUE) standards.')


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


class TranscriptCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.states = []

    def showPage(self):
        self.states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        count = len(self.states)
        for state in self.states:
            self.__dict__.update(state)
            self.saveState()
            # Professional footer line
            self.setStrokeColor(colors.HexColor('#94a3b8'))
            self.setLineWidth(0.5)
            self.line(40, 36, A4[0]-40, 36)
            self.setFont('Times-Roman', 7.5)
            self.setFillColor(colors.HexColor('#475569'))
            self.drawString(40, 24, 'Academic Registry · Certified Official University Document · Any unauthorized alteration renders this record void.')
            self.drawRightString(A4[0]-40, 24, f'Page {self._pageNumber} of {count}')
            self.restoreState()
            super().showPage()
        super().save()


def number(value):
    return '—' if value is None else f'{value:.2f}'


def export_transcript_pdf(student, ctx, kind='provisional', site_name='University Management System',
                          site_address='', site_email='', site_phone='', logo_path=None):
    buffer = io.BytesIO()
    doc_titles = {
        'provisional': 'PROVISIONAL TRANSCRIPT OF RESULTS',
        'academic': 'OFFICIAL TRANSCRIPT OF ACADEMIC RECORD',
        'official': 'OFFICIAL TRANSCRIPT OF ACADEMIC RECORD',
        'performance': 'ACADEMIC PERFORMANCE & PROGRESSION REPORT'
    }
    title = doc_titles.get(kind, 'OFFICIAL ACADEMIC TRANSCRIPT')
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40,
                            topMargin=30, bottomMargin=48, title=title, author=site_name, invariant=1)
    styles = getSampleStyleSheet()

    # Oxford/Formal Typography Hierarchy
    body = ParagraphStyle('Record', fontName='Times-Roman', fontSize=8.5, leading=11, textColor=colors.HexColor('#1e293b'))
    body_bold = ParagraphStyle('RecordBold', parent=body, fontName='Times-Bold')
    small = ParagraphStyle('Annotation', parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor('#475569'))
    small_bold = ParagraphStyle('AnnotationBold', parent=small, fontName='Times-Bold', textColor=colors.HexColor('#0f172a'))
    heading = ParagraphStyle('Section', parent=body, fontName='Times-Bold', fontSize=10, leading=13, spaceBefore=7, spaceAfter=4, textColor=colors.HexColor('#0f172a'), keepWithNext=True)
    inst_title = ParagraphStyle('InstTitle', fontName='Times-Bold', fontSize=15, leading=18, alignment=1, textColor=colors.HexColor('#0f172a'))
    inst_sub = ParagraphStyle('InstSub', fontName='Times-Roman', fontSize=8.5, leading=11, alignment=1, textColor=colors.HexColor('#334155'))
    doc_title_style = ParagraphStyle('DocTitle', fontName='Times-Bold', fontSize=12, leading=15, alignment=1, textColor=colors.HexColor('#0f172a'), spaceBefore=4, spaceAfter=2)

    def p(value, style=body):
        return Paragraph(escape(str(value)), style)

    def make_table(rows, widths, header=True, custom_style=None):
        data = [[p(v, small_bold if header and i == 0 else (body_bold if isinstance(v, str) and v.startswith('**') else body)) for v in row] for i, row in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
        commands = [
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ]
        if header:
            commands += [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.0, colors.HexColor('#1e293b')),
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
        site_address or 'Main Campus, University Way',
        site_email or 'registry@ums.ac.ke',
        site_phone or '+254 (0) 20 1234567'
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
        story.append(p('PROVISIONAL ACADEMIC RECORD ISSUED FOR INTERIM REFERENCE — SUBJECT TO FINAL SENATE CONFIRMATION', ParagraphStyle('SubProv', parent=small, alignment=1, textColor=colors.HexColor('#b45309'), fontName='Times-Bold')))
    elif kind == 'academic':
        story.append(p('CERTIFIED PERMANENT TRANSCRIPT OF COMPLETED UNIVERSITY STUDIES', ParagraphStyle('SubAcad', parent=small, alignment=1, textColor=colors.HexColor('#334155'))))
    story.append(Spacer(1, 4))

    # 3. Student Profile Matrix
    program = student.program
    dept_name = program.department.name if program and program.department else 'General Studies'
    matrix_rows = [
        ['Student name', student.user.display_name, 'Registration number', student.roll_no],
        ['Programme', program.name if program else 'Undecided', 'Faculty / School', dept_name],
        ['Qualification level', program.get_level_display() if program else 'Undergraduate Degree', 'Current semester', f'Year {(student.current_semester + 1) // 2} · Sem {student.current_semester}'],
        ['Date of issue', ctx['generated_at'].strftime('%d %B %Y'), 'Transcript reference', ctx['reference_no']],
    ]
    matrix_table = Table([[p(f'<b>{c}</b>' if j % 2 == 0 else c, small if j % 2 == 0 else body) for j, c in enumerate(row)] for row in matrix_rows],
                         colWidths=[85, w/2 - 85, 95, w/2 - 95])
    matrix_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#94a3b8')),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e2e8f0')),
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
            rows = [['Course', 'Course title', 'Units', 'CAT (30)', 'Exam (70)', 'Total (%)', 'Grade', 'Points', 'Remarks']]
            col_w = [64, w - 64 - 32 - 38 - 42 - 42 - 36 - 36 - 46, 32, 38, 42, 42, 36, 36, 46]
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
            rows = [['Course', 'Course title', 'Level / Sem', 'Units', 'Grade', 'Points', 'Remarks']]
            col_w = [68, w - 68 - 62 - 36 - 40 - 50 - 52, 62, 36, 40, 50, 52]
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
            f"<b>Semester Units Attempted:</b> {sem['credits_attempted']}   |   "
            f"<b>Units Earned:</b> {sem['credits_completed']}   |   "
            f"<b>Semester GPA:</b> {number(sem['term_gpa'])}   |   "
            f"<b>Cumulative GPA:</b> {number(sem['cumulative_gpa'])}"
        )
        term_box = Table([[p(term_summary, small)]], colWidths=[w])
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
            p(f"<b>Total Units Attempted:</b> {ctx['overall_credits_attempted']}", body),
            p(f"<b>Total Units Earned:</b> {ctx['overall_credits_completed']}", body),
            p(f"<b>Courses Completed:</b> {ctx['total_courses_completed']}", body),
        ],
        [
            p(f"<b>Cumulative GPA (CGPA):</b> <font size='10'><b>{number(period_or_cgpa)} / 4.00</b></font>", body),
            p(f"<b>Academic Standing:</b> <b>{ctx['overall_standing']}</b>", body),
            p(f"<b>Curriculum Status:</b> Normal Academic Progression", body),
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
            chart.yValueAxis.valueMax = 4.0
            chart.yValueAxis.valueStep = 1.0
            drawing = Drawing(w, 140)
            drawing.add(chart)
            story.append(drawing)
            story.append(p('Solid Line: Semester GPA. Dashed Line: Cumulative CGPA. Horizontal Axis: Completed Terms in Chronological Sequence.', small))
            story.append(Spacer(1, 4))

        story.append(p('GRADE DISTRIBUTION BREAKDOWN', heading))
        dist_rows = [['Grade', 'Letter Description', 'Units / Course Attempts']]
        cue_desc = {'A': '70%–100% (Excellent)', 'B': '60%–69% (Good)', 'C': '50%–59% (Satisfactory)', 'D': '40%–49% (Pass)', 'F': '0%–39% (Fail)'}
        for gr, ct in ctx['grade_distribution'].items():
            dist_rows.append([gr, cue_desc.get(gr, 'Curriculum mark'), f"{ct} course attempt{'s' if ct != 1 else ''}"])
        story.append(make_table(dist_rows, [60, w - 200, 140]))
        story.append(Spacer(1, 6))

    # 7. Kenyan CUE Grading Structure Legend
    if kind != 'performance':
        story.append(p('KENYAN CUE STANDARD GRADING SCALE & GRADE POINTS', heading))
        scale_rows = [['Grade', 'Marks Range', 'Grade Point (GP)', 'CUE Classification / Description']]
        for leg in ctx['legends']:
            for b in leg['bands']:
                gp_val = b.get('gp', grade_point_for(b['grade']))
                desc_val = b.get('description', '')
                if not desc_val:
                    desc_val = {'A': 'First Class Honours Equivalent / Excellent',
                                'B': 'Second Class Honours (Upper Division) / Good',
                                'C': 'Second Class Honours (Lower Division) / Satisfactory',
                                'D': 'Pass / Minimal Progression',
                                'F': 'Fail / Academic Deficiency'}.get(b['grade'], 'Academic standard')
                min_m = b['minimum']
                max_m = 100 if b['grade'] == 'A' else (min_m + 9.99 if min_m < 70 else 100)
                scale_rows.append([b['grade'], f"{min_m:.0f}% – {max_m:.0f}%", number(gp_val), desc_val])
            break  # one standardized scale table
        story.append(make_table(scale_rows, [45, 95, 85, w - 225]))
        story.append(Spacer(1, 6))

    # 8. Transcript Notes & Registry Certification Notice
    story.append(p('TRANSCRIPT NOTES & VERIFICATION NOTICE', heading))
    story.append(p(ctx['notes'], small))
    story.append(p(f"Official Registry Verification: Quote Document Serial <b>{ctx['reference_no']}</b> directly to {site_email or 'registry@example.com'}. This computerized record is securely generated and audited.", small))

    # 9. Official Certification & Signatures Block (Oxford 3-column Layout)
    if kind != 'performance':
        story.append(Spacer(1, 10))
        sig_data = [
            [
                p('Prepared & Verified By:<br/><br/><br/>________________________________<br/><b>Examinations Officer</b><br/>Academic Registry', small),
                p('Official University Seal:<br/><br/><b>[ OFFICIAL SEAL ]</b><br/>Directorate of Academic Affairs', ParagraphStyle('SealP', parent=small, alignment=1)),
                p('Approved & Certified By:<br/><br/><br/>________________________________<br/><b>Academic Registrar</b><br/>Signature & Official Stamp', small),
            ]
        ]
        sig_table = Table(sig_data, colWidths=[w * 0.38, w * 0.24, w * 0.38])
        sig_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(sig_table)

    def continued_header(pdf, document):
        pdf.saveState()
        pdf.setFont('Times-Roman', 7.5)
        pdf.setFillColor(colors.HexColor('#64748b'))
        pdf.drawString(40, A4[1] - 22, f"{site_name.upper()} · {title} · Student: {student.roll_no} · Ref: {ctx['reference_no']}")
        pdf.setStrokeColor(colors.HexColor('#cbd5e1'))
        pdf.setLineWidth(0.4)
        pdf.line(40, A4[1] - 25, A4[0] - 40, A4[1] - 25)
        pdf.restoreState()

    doc.build(story, canvasmaker=TranscriptCanvas, onLaterPages=continued_header)
    return buffer.getvalue()

