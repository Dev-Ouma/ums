from university.document_views import present_pdf
from university.reporting_services import generate_report_pdf, generate_report_excel
import csv
import io
from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StudentProfile
from .models import AcademicTerm, Course, Exam, ExamAppeal, ExamRoom, Result, DocumentReleaseControl
from .document_access_services import check_document_access
from .examination_forms import ExaminationForm, RoomForm, TermForm
from . import examination_services as workflow
from . import marks_io


def error_message(request, exc):
    messages.error(request, '; '.join(exc.messages))


def staff_exam(request, pk):
    return get_object_or_404(workflow.staff_scope(request.user), pk=pk)


def csv_response(filename, header, rows):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        # Stop spreadsheet formula interpretation in user-provided text.
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in row])
    return response


@login_required
def index(request):
    user = request.user
    staff = workflow.is_admin(user) or user.is_faculty
    qs = workflow.staff_scope(user) if staff else Exam.objects.filter(results__student__user=user).exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED]).select_related('course', 'room', 'term', 'invigilator__user')
    counts = dict(qs.values_list('status').annotate(n=Count('pk')))
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(course__code__icontains=q) | Q(course__title__icontains=q))
    status = request.GET.get('status', '')
    if status in dict(Exam.Status.choices):
        qs = qs.filter(status=status)
    term = request.GET.get('term', '')
    if term.isdigit():
        qs = qs.filter(term_id=term)
    if request.GET.get('format') == 'csv':
        return csv_response('examination-timetable.csv', ['Course', 'Assessment', 'Term', 'Date', 'Start', 'End', 'Room', 'Status'], ([x.course.code, x.name, str(x.term or ''), x.date, x.start_time, x.end_time, str(x.room or ''), x.get_status_display()] for x in qs))
    can_create = workflow.can_create_exams(user)
    return render(request, 'examinations/index.html', {
        'page': Paginator(qs.order_by('-date', 'start_time', 'pk'), 20).get_page(request.GET.get('page')),
        'staff': staff,
        'admin': workflow.is_admin(user),
        'can_create': can_create,
        'counts': counts,
        'terms': AcademicTerm.objects.all(),
        'statuses': Exam.Status.choices,
        'q': q,
        'selected_status': status,
        'selected_term': term
    })


@login_required
def edit(request, pk=None):
    if pk is None or request.GET.get('original'):
        if not workflow.can_create_exams(request.user):
            raise PermissionDenied("Only Heads of Department (HOD), Deans, and Administrators are authorized to create examinations.")
    elif not (workflow.is_admin(request.user) or request.user.is_faculty):
        raise PermissionDenied
    exam = staff_exam(request, pk) if pk else Exam()
    if pk:
        workflow.require_editor(request.user, exam)
        if exam.status != Exam.Status.DRAFT:
            messages.error(request, 'Only drafts can be edited. Reschedule a scheduled exam to return it to draft.')
            return redirect('examinations:detail', pk=exam.pk)
    initial = {}
    course = request.GET.get('course')
    if course and course.isdigit():
        initial['course'] = course
    original = request.GET.get('original')
    if original and original.isdigit():
        source = staff_exam(request, int(original))
        initial.update(course=source.course_id, term=source.term_id, original_exam=source.pk, kind=Exam.Kind.SUPPLEMENTARY, name=source.name + ' supplementary', max_marks=source.max_marks, pass_mark=source.pass_mark)
    form = ExaminationForm(request.POST or None, instance=exam, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if pk:
                    current = Exam.objects.select_for_update().get(pk=pk)
                    if current.status != Exam.Status.DRAFT or str(current.revision) != request.POST.get('revision'):
                        raise ValidationError('The exam changed. Reload before editing.')
                exam = form.save(commit=False)
                exam.revision += 1
                exam.save()
                workflow.audit(exam, request.user, 'Draft updated' if pk else 'Exam created', f'{exam.name}; {exam.kind}; {exam.weight}%')
            messages.success(request, 'Exam draft saved. Review the details and schedule it to create the candidate register.')
            return redirect('examinations:detail', pk=exam.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, 'examinations/form.html', {'form': form, 'title': 'Edit examination' if pk else 'Create examination', 'subtitle': 'CAT contributes 30%; the final examination contributes 70%. Marks remain private until approved and published.', 'revision': exam.revision, 'back': 'examinations:index'})


@login_required
def detail(request, pk):
    exam = staff_exam(request, pk)
    editor = workflow.can_mark(request.user, exam)
    can_create = workflow.can_create_exams(request.user)
    can_hod = workflow.can_hod_approve(request.user, exam)
    can_dean = workflow.can_dean_publish(request.user, exam)
    can_unpub = workflow.can_unpublish(request.user, exam)
    user_role = workflow.get_user_exam_role(request.user, exam)

    rows = list(exam.results.select_related('student__user', 'exam').order_by('seat_number', 'student__roll_no'))
    entered = sum(r.marks_obtained is not None or r.attendance == 'ABSENT' for r in rows)
    S = Exam.Status

    catalogue = {
        'schedule': (
            'Schedule & Register Candidates', False, 'primary',
            'Validates room capacity, invigilator and candidate schedules, then generates candidate seat numbers.',
            None
        ),
        'start': (
            'Open Marks Capture', False, 'primary',
            'Opens the marks sheet to the course lecturer and internal examiner. Marks remain confidential to teaching staff.',
            None
        ),
        'submit': (
            'Submit Marks to HoD', False, 'primary',
            'Finalizes marks capture and forwards the marks sheet to the Head of Department (HoD) for review and approval.',
            None
        ),
        'resubmit': (
            'Resubmit Marks to HoD', False, 'primary',
            'Resubmits corrected marks to the Head of Department (HoD) as a new marks version.',
            None
        ),
        'hod_approve': (
            'Approve Marks (HoD)', False, 'success',
            'Head of Department endorses departmental marks and forwards them for Dean / Senate publication.',
            None
        ),
        'hod_send_back': (
            'Return to Instructor for Correction (HoD)', True, 'warning',
            'Returns marks sheet back to the course lecturer for corrections. A clear reason is required.',
            'sendBackModal'
        ),
        'reopen_approved': (
            'Reopen Approved Marks (HoD)', True, 'warning',
            'Withdraws HoD approval and returns the marks sheet to the instructor for necessary corrections.',
            'sendBackModal'
        ),
        'publish': (
            'Publish Official Results (Dean)', False, 'success',
            'Formally releases official examination results to students. Results appear on student transcripts and grade statements.',
            None
        ),
        'dean_send_back_hod': (
            'Return to HoD (Dean)', True, 'warning',
            'Returns marks sheet back to the Head of Department for departmental revision.',
            'sendBackModal'
        ),
        'dean_send_back_instructor': (
            'Return to Instructor (Dean)', True, 'warning',
            'Bypasses the HoD and returns marks sheet directly to the course lecturer for correction.',
            'sendBackModal'
        ),
        'unpublish': (
            'Unpublish Official Results (Dean / Registrar)', True, 'danger',
            'Hides published results from student statements while preserving complete history, versions, and audit trails.',
            'unpublishModal'
        ),
        'reschedule': (
            'Return to Draft for Rescheduling', True, 'warning',
            'Clears the candidate register and seat allocations, returning the exam to draft status.',
            None
        ),
        'cancel': (
            'Cancel Examination', True, 'danger',
            'Cancels the examination sitting permanently. This action cannot be undone.',
            None
        ),
    }

    available = []
    # 1. Instructor / Editor actions:
    if editor:
        if exam.status == S.DRAFT:
            available.append('schedule')
        elif exam.status == S.SCHEDULED:
            available.append('start')
        elif exam.status == S.MARKING:
            available.append('submit')
        elif exam.status in (S.RETURNED_TO_INSTRUCTOR, S.CORRECTION, S.UNPUBLISHED):
            available.append('resubmit')

    # 2. HoD actions:
    if can_hod:
        if exam.status in (S.HOD_REVIEW, S.SUBMITTED):
            available.extend(['hod_approve', 'hod_send_back'])
        elif exam.status == S.HOD_APPROVED:
            available.append('reopen_approved')

    # 3. Dean / Registrar / Admin actions:
    if can_dean:
        if exam.status in (S.HOD_APPROVED, S.DEAN_REVIEW, S.APPROVED):
            available.extend(['publish', 'dean_send_back_hod', 'dean_send_back_instructor'])
        elif exam.status == S.RETURNED_TO_HOD:
            available.append('dean_send_back_instructor')

    if can_unpub and exam.status == S.PUBLISHED:
        available.append('unpublish')

    if workflow.is_admin(request.user):
        if exam.status == S.SCHEDULED:
            available.extend(['reschedule', 'cancel'])
        elif exam.status == S.DRAFT:
            available.append('cancel')

    actions = [(key,) + catalogue[key] for key in dict.fromkeys(available) if key in catalogue]

    track_stages = [
        ('Draft', ['DRAFT']),
        ('Scheduled', ['SCHEDULED']),
        ('Marks Entry', ['MARKING', 'RETURNED_INSTRUCTOR', 'CORRECTION', 'UNPUBLISHED']),
        ('HoD Review', ['HOD_REVIEW', 'SUBMITTED', 'RESUBMITTED', 'RETURNED_HOD']),
        ('HoD Approved', ['HOD_APPROVED', 'DEAN_REVIEW', 'APPROVED']),
        ('Published', ['PUBLISHED']),
    ]

    current_stage_idx = -1
    for idx, (stage_name, status_list) in enumerate(track_stages):
        if exam.status in status_list:
            current_stage_idx = idx
            break

    track = []
    for idx, (stage_name, _) in enumerate(track_stages):
        if exam.status == S.CANCELLED:
            state = 'todo'
        elif idx < current_stage_idx:
            state = 'done'
        elif idx == current_stage_idx:
            state = 'current'
        else:
            state = 'todo'
        track.append({'label': stage_name, 'state': state})

    grades = Counter(r.grade for r in rows if r.attendance != 'PENDING' and (r.marks_obtained is not None or r.attendance == 'ABSENT'))
    workflow_events = exam.workflow_events.select_related('actor').all()[:30]
    marks_versions = exam.marks_versions.select_related('created_by').all()

    return render(request, 'examinations/detail.html', {
        'exam': exam,
        'rows': rows,
        'editor': editor,
        'admin': workflow.is_admin(request.user),
        'can_create': can_create,
        'can_hod': can_hod,
        'can_dean': can_dean,
        'can_unpublish': can_unpub,
        'user_role': user_role,
        'actions': actions,
        'track': track,
        'cancelled': exam.status == S.CANCELLED,
        'entered': entered,
        'total': len(rows),
        'passed': sum(r.outcome == 'Pass' for r in rows),
        'absent': sum(r.attendance == 'ABSENT' for r in rows),
        'grades': sorted(grades.items()),
        'workflow_events': workflow_events,
        'marks_versions': marks_versions,
        'audit': exam.audit_entries.select_related('actor')[:50] if (editor or can_hod or can_dean) else [],
        'appeals': ExamAppeal.objects.filter(result__exam=exam).select_related('result__student__user') if editor else []
    })


@login_required
@require_POST
def action(request, pk):
    staff_exam(request, pk)
    try:
        ip = request.META.get('REMOTE_ADDR') or ''
        ua = request.META.get('HTTP_USER_AGENT') or ''
        workflow.transition(
            request.user,
            pk,
            action=request.POST.get('action'),
            reason=request.POST.get('reason', ''),
            revision=request.POST.get('revision', ''),
            reason_category=request.POST.get('reason_category', ''),
            comments=request.POST.get('comments', ''),
            target_stage=request.POST.get('target_stage', ''),
            ip_address=ip,
            user_agent=ua,
        )
        messages.success(request, 'Examination workflow updated successfully.')
    except (ValidationError, PermissionDenied) as exc:
        error_message(request, exc)
    return redirect('examinations:detail', pk=pk)


@login_required
def marks(request, pk=None):
    user = request.user
    staff = workflow.is_admin(user) or user.is_faculty
    if not staff:
        raise PermissionDenied

    available_exams = workflow.staff_scope(user).exclude(
        status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED]
    ).select_related('course', 'term').order_by('course__code', 'name')

    if not pk:
        exam_param = request.GET.get('exam') or request.GET.get('exam_id') or request.POST.get('exam_id')
        if exam_param and str(exam_param).isdigit():
            pk = int(exam_param)
        elif available_exams.exists():
            marking_exam = available_exams.filter(status=Exam.Status.MARKING).first()
            pk = marking_exam.pk if marking_exam else available_exams.first().pk
        else:
            return render(request, 'examinations/marks.html', {
                'exam': None,
                'available_exams': available_exams,
                'rows': [],
                'errors': [],
                'editable': False,
                'admin': workflow.is_admin(user),
            })

    exam = staff_exam(request, pk)
    workflow.require_editor(request.user, exam)
    
    retake_qs = Result.objects.filter(
        student_id=OuterRef('student_id'),
        exam__course_id=exam.course_id,
        exam__term__start_date__lt=exam.term.start_date if exam.term else '1900-01-01'
    )
    rows = list(exam.results.annotate(is_retake=Exists(retake_qs)).select_related('student__user', 'exam').order_by('seat_number', 'student__roll_no'))

    fmt = request.GET.get('format', '').lower()
    if fmt in ('excel', 'xlsx'):
        content = marks_io.generate_exam_marks_template(exam, fmt='excel')
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="exam-{pk}-marks-template.xlsx"'
        return response
    elif fmt == 'csv':
        if request.GET.get('template') == '1':
            content = marks_io.generate_exam_marks_template(exam, fmt='csv')
            response = HttpResponse(content, content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="exam-{pk}-marks-template.csv"'
            return response
        return csv_response(
            f'exam-{pk}-marks.csv',
            ['roll_no', 'student_name', 'attendance', 'cat_marks', 'exam_marks', 'total_marks', 'grade', 'remarks'],
            ([r.student.roll_no, r.student.user.display_name, r.attendance, r.cat_marks if r.cat_marks is not None else '', r.exam_marks if r.exam_marks is not None else '', r.marks_obtained if r.marks_obtained is not None else '', r.grade, r.remarks] for r in rows)
        )

    errors = []
    if request.method == 'POST':
        entries = {}
        examiner_data = {}
        try:
            if 'update_examiners' in request.POST:
                examiner_data['internal_examiner'] = request.POST.get('internal_examiner', '')
                examiner_data['external_examiner'] = request.POST.get('external_examiner', '')
                examiner_data['external_examiner_name'] = request.POST.get('external_examiner_name', '')
                examiner_data['examiner_remarks'] = request.POST.get('examiner_remarks', '')

            upload = request.FILES.get('marks_file') or request.FILES.get('csv_file')
            if upload:
                parsed = marks_io.parse_exam_marks_file(upload, exam)
                if not parsed['entries']:
                    all_errs = []
                    for item in parsed['items']:
                        all_errs.extend(item['errors'])
                    raise ValidationError(all_errs or ['No valid marks records found in the uploaded file.'])
                entries = parsed['entries']
                workflow.save_marks(request.user, pk, entries, request.POST.get('revision'), examiner_data=examiner_data)
                msg = f"Successfully uploaded marks for {parsed['valid_count']} candidate(s). Total marks and CUE grades updated."
                if parsed['error_count'] > 0:
                    messages.warning(request, f"{parsed['error_count']} row(s) had errors and were skipped.")
                messages.success(request, msg)
                return redirect('examinations:marks', pk=pk)
            else:
                entries = {
                    str(r.pk): {
                        'attendance': request.POST.get(f'attendance_{r.pk}', ''),
                        'cat_marks': request.POST.get(f'cat_marks_{r.pk}', ''),
                        'exam_marks': request.POST.get(f'exam_marks_{r.pk}', ''),
                        'exam_marks_ie': request.POST.get(f'exam_marks_ie_{r.pk}', ''),
                        'exam_marks_ee': request.POST.get(f'exam_marks_ee_{r.pk}', ''),
                        'marks': request.POST.get(f'marks_{r.pk}', ''),
                        'remarks': request.POST.get(f'remarks_{r.pk}', '')
                    }
                    for r in rows
                }
                workflow.save_marks(request.user, pk, entries, request.POST.get('revision'), examiner_data=examiner_data)
                messages.success(request, 'Marks saved successfully. Total marks and CUE grades updated.')
                return redirect('examinations:marks', pk=pk)
        except (UnicodeDecodeError, csv.Error):
            errors = ['The file must be a valid UTF-8 CSV or Excel spreadsheet.']
        except ValidationError as exc:
            errors = exc.messages
        for row in rows:
            data = entries.get(str(row.pk))
            if data:
                row.attendance = data.get('attendance')
                row.cat_marks = data.get('cat_marks')
                row.exam_marks = data.get('exam_marks')
                row.exam_marks_ie = data.get('exam_marks_ie')
                row.exam_marks_ee = data.get('exam_marks_ee')
                row.marks_obtained = data.get('marks')
                row.remarks = data.get('remarks')
    recorded = sum(r.attendance == 'ABSENT' or (r.attendance == 'PRESENT' and r.marks_obtained is not None) for r in rows)
    from accounts.models import FacultyProfile
    faculty_list = FacultyProfile.objects.select_related('user', 'department')

    editable = exam.status in [
        Exam.Status.MARKING,
        Exam.Status.RETURNED_TO_INSTRUCTOR,
        Exam.Status.CORRECTION,
        Exam.Status.UNPUBLISHED,
    ]
    ie_editable = exam.status in [
        Exam.Status.MARKING,
        Exam.Status.RETURNED_TO_INSTRUCTOR,
        Exam.Status.CORRECTION,
        Exam.Status.UNPUBLISHED,
        Exam.Status.INTERNAL_REVIEW,
    ]
    ee_editable = exam.status == Exam.Status.EXTERNAL_REVIEW
    latest_event = exam.workflow_events.first()
    marks_versions = exam.marks_versions.all()

    return render(request, 'examinations/marks.html', {
        'exam': exam,
        'available_exams': available_exams,
        'rows': rows,
        'errors': errors,
        'editable': editable,
        'ie_editable': ie_editable,
        'ee_editable': ee_editable,
        'latest_event': latest_event,
        'marks_versions': marks_versions,
        'admin': workflow.is_admin(request.user),
        'faculty_list': faculty_list,
        'recorded': recorded, 'total': len(rows), 'outstanding': len(rows) - recorded,
        'bands': sorted(exam.grade_bands, key=lambda b: b['minimum'], reverse=True),
        'component': f'CAT: max {exam.cat_max_marks:.0f} | Final Exam IE: max {exam.exam_max_marks:.0f} | Final Exam EE: max {exam.exam_max_marks:.0f} | Total: {exam.max_marks}',
    })


@login_required
def register(request, pk):
    exam = staff_exam(request, pk)
    rows = exam.results.select_related('student__user').order_by('seat_number')
    return render(request, 'examinations/register.html', {'exam': exam, 'rows': rows})


@login_required
def admission(request):
    student = get_object_or_404(StudentProfile, user=request.user)
    rows = Result.objects.filter(student=student, exam__status__in=[Exam.Status.SCHEDULED, Exam.Status.MARKING, Exam.Status.INTERNAL_REVIEW, Exam.Status.EXTERNAL_REVIEW, Exam.Status.SUBMITTED, Exam.Status.APPROVED]).select_related('exam__course', 'exam__term', 'exam__room').order_by('exam__date', 'exam__start_time')
    return render(request, 'examinations/admission.html', {'student': student, 'rows': rows})


@login_required
def statement(request, student_id=None):
    if student_id:
        if not workflow.is_admin(request.user):
            raise PermissionDenied
        student = get_object_or_404(StudentProfile.objects.select_related('user', 'program'), pk=student_id)
    else:
        student = StudentProfile.objects.select_related('user', 'program').filter(user=request.user).first()
        if not student:
            if workflow.is_admin(request.user):
                student = StudentProfile.objects.select_related('user', 'program').first()
                if not student:
                    messages.warning(request, 'No student profiles found.')
                    return redirect('examinations:index')
            else:
                raise PermissionDenied
    term = request.GET.get('term', '')
    term_obj = AcademicTerm.objects.filter(pk=term).first() if term.isdigit() else None

    # Access control & release window check
    allowed, reason, control, is_bypass = check_document_access(
        student, DocumentReleaseControl.DocumentType.RESULTS_STATEMENT, term=term_obj, user=request.user
    )
    if not allowed:
        return render(request, 'documents/locked.html', {
            'student': student,
            'title': 'Result Statement Unavailable',
            'reason': reason,
            'control': control,
        }, status=403)

    res = workflow.student_statement(student, term if term.isdigit() else None)
    results, groups = res[0], res[1]
    gpa = getattr(res, 'gpa', 0.0)
    if request.GET.get('format') in ('pdf', 'excel'):
        data = {'title': 'Student Result Statement', 'key': 'Results',
            'generated_at': timezone.now(), 'generated_by': request.user.display_name,
            'meta': {'orientation': 'landscape'},
            'columns': ['Course', 'Term', 'Assessment', 'Marks', 'Maximum', 'Weight (%)', 'Grade', 'Grade Point', 'Outcome', 'Approval State'],
            'rows': [[r.exam.course.code, str(r.exam.term or ''), r.exam.name, r.marks_obtained, r.exam.max_marks, r.exam.weight, r.grade, r.grade_point, r.outcome, r.exam.get_status_display()] for r in results],
            'applied_filters': [f'Term: {term}'] if term else []}
        data['title'] += f' · {student.user.display_name} · {student.roll_no}'
        if request.GET.get('format') == 'pdf':
            response = HttpResponse(generate_report_pdf(data), content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="results.pdf"'
            return present_pdf(request, response, data['title'])
        response = HttpResponse(generate_report_excel(data), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="results.xlsx"'
        return response
    if request.GET.get('format') == 'csv':
        return csv_response('student-results.csv', ['Course', 'Term', 'Assessment', 'Marks', 'Maximum', 'Weight (%)', 'Grade', 'Grade Point', 'Outcome'], ([r.exam.course.code, str(r.exam.term or ''), r.exam.name, r.marks_obtained, r.exam.max_marks, r.exam.weight, r.grade, r.grade_point, r.outcome] for r in results))
    return render(request, 'examinations/statement.html', {'student': student, 'results': results, 'groups': groups, 'gpa': gpa, 'terms': AcademicTerm.objects.all(), 'selected_term': term, 'own': student.user_id == request.user.pk})


@login_required
@require_POST
def appeal(request, pk):
    get_object_or_404(Result, pk=pk, student__user=request.user)
    try:
        workflow.create_appeal(request.user, pk, request.POST.get('reason', ''))
        messages.success(request, 'Your appeal was submitted for review.')
    except ValidationError as exc:
        error_message(request, exc)
    return redirect('examinations:statement')


@login_required
@require_POST
def resolve(request, pk):
    if not workflow.is_admin(request.user):
        raise PermissionDenied
    appeal = get_object_or_404(ExamAppeal.objects.select_related('result'), pk=pk)
    try:
        workflow.resolve_appeal(request.user, pk, request.POST.get('decision'), request.POST.get('resolution', ''))
        messages.success(request, 'Appeal resolved. Accepted appeals must go through marks submission, approval and publication again.')
    except ValidationError as exc:
        error_message(request, exc)
    return redirect('examinations:detail', pk=appeal.result.exam_id)


@login_required
def setup(request, kind, pk=None):
    if not workflow.is_admin(request.user):
        raise PermissionDenied
    if kind == 'terms':
        messages.info(request, "Academic terms and semesters are managed centrally in Academic Calendar & Sessions.")
        return redirect('university:admin_academic_calendar')

    from .models import GradingScale
    from .examination_forms import GradingScaleForm
    if kind == 'rooms':
        model, form_class, title = ExamRoom, RoomForm, 'Examination room'
    elif kind == 'grading':
        model, form_class, title = GradingScale, GradingScaleForm, 'CUE Grading Scale'
    else:
        raise PermissionDenied

    instance = get_object_or_404(model, pk=pk) if pk else None
    if kind == 'rooms' and instance and instance.exams.exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED]).exists():
        messages.error(request, 'This room has scheduled or historical exams. Create a new room to preserve their capacity and location.')
        return redirect('examinations:rooms')
    if kind == 'terms' and instance and Exam.objects.filter(term=instance).exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED]).exists():
        messages.error(request, 'This term has scheduled or historical exams; its dates are locked.')
        return redirect('examinations:terms')
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, title + ' saved.')
        return redirect('examinations:' + kind)
    return render(request, 'examinations/setup.html', {'form': form, 'title': title, 'objects': model.objects.all(), 'kind': kind})


@login_required
def report(request):
    if not workflow.is_admin(request.user):
        raise PermissionDenied
    term = request.GET.get('term', '')
    qs = Result.objects.filter(exam__status=Exam.Status.PUBLISHED).select_related('student__user', 'exam__course', 'exam__term')
    if term.isdigit():
        qs = qs.filter(exam__term_id=term)
    if request.GET.get('format') in ('pdf', 'excel'):
        data = {'title': 'Published Examination Results', 'key': 'Results',
            'generated_at': timezone.now(), 'generated_by': request.user.display_name,
            'meta': {'orientation': 'landscape'},
            'columns': ['Registration Number', 'Student', 'Course', 'Term', 'Assessment', 'Marks', 'Maximum', 'Weight (%)', 'Grade', 'Outcome', 'Approval State'],
            'rows': [[r.student.roll_no, r.student.user.display_name, r.exam.course.code, str(r.exam.term or ''), r.exam.name, r.marks_obtained, r.exam.max_marks, r.exam.weight, r.grade, r.outcome, r.exam.get_status_display()] for r in qs],
            'applied_filters': [f'Term: {term}'] if term else []}
        if request.GET.get('format') == 'pdf':
            response = HttpResponse(generate_report_pdf(data), content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="results.pdf"'
            return present_pdf(request, response, data['title'])
        response = HttpResponse(generate_report_excel(data), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="results.xlsx"'
        return response
    if request.GET.get('format') == 'csv':
        return csv_response('published-examination-results.csv', ['Roll number', 'Student', 'Course', 'Term', 'Assessment', 'Marks', 'Maximum', 'Weight (%)', 'Grade', 'Outcome'], ([r.student.roll_no, r.student.user.display_name, r.exam.course.code, str(r.exam.term or ''), r.exam.name, r.marks_obtained, r.exam.max_marks, r.exam.weight, r.grade, r.outcome] for r in qs))
    rows = list(qs)
    counts = Counter(r.outcome for r in rows)
    return render(request, 'examinations/report.html', {'page': Paginator(rows, 40).get_page(request.GET.get('page')), 'total': len(rows), 'passed': counts['Pass'], 'failed': counts['Fail'], 'absent': counts['Absent'], 'terms': AcademicTerm.objects.all(), 'selected_term': term, 'appeals': ExamAppeal.objects.filter(status='OPEN').select_related('result__exam__course', 'result__student__user')})
