import csv
import io
from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StudentProfile
from .models import AcademicTerm, Course, Exam, ExamAppeal, ExamRoom, Result
from .examination_forms import ExaminationForm, RoomForm, TermForm
from . import examination_services as workflow


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
    return render(request, 'examinations/index.html', {'page': Paginator(qs.order_by('-date', 'start_time', 'pk'), 20).get_page(request.GET.get('page')), 'staff': staff, 'admin': workflow.is_admin(user), 'counts': counts, 'terms': AcademicTerm.objects.all(), 'statuses': Exam.Status.choices, 'q': q, 'selected_status': status, 'selected_term': term})


@login_required
def edit(request, pk=None):
    if not (workflow.is_admin(request.user) or request.user.is_faculty):
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
    rows = list(exam.results.select_related('student__user', 'exam').order_by('seat_number', 'student__roll_no'))
    entered = sum(r.marks_obtained is not None or r.attendance == 'ABSENT' for r in rows)
    S = Exam.Status
    catalogue = {
        'schedule': ('Schedule & register candidates', False, 'primary',
                     'Checks room capacity, invigilator and candidate clashes, then locks in the '
                     'candidate register and seat numbers for this sitting.'),
        'start': ('Open marks capture', False, 'primary',
                  'Opens the marks sheet to the course lecturer and internal examiner. Marks stay private to staff until published.'),
        'submit_internal': ('Submit for Internal Review', False, 'primary',
                            'Marks are forwarded to the Internal Examiner (IE) for moderation and verification.'),
        'review_internal': ('Sign off Internal Review', False, 'primary',
                            'Confirms internal moderation is complete. Forwards marks to external review (if assigned) or approval.'),
        'review_external': ('Sign off External Review', False, 'primary',
                            'External Examiner (EE) signs off on moderated marks and forwards for administrative approval.'),
        'submit': ('Submit marks as final', False, 'dark',
                   'Locks the marks sheet against further edits and sends the results to the administrator '
                   'for approval. Only an administrator can reopen it, with a written reason.'),
        'approve': ('Approve results', False, 'primary',
                    'Confirms the marks are correct and ready for release. Students still cannot see them '
                    'until the results are published.'),
        'publish': ('Publish to students', False, 'success',
                    'Releases the results to every candidate immediately, and makes them count towards the '
                    'weighted course total. Students may then lodge appeals.'),
        'return': ('Return for correction', True, 'warning',
                   'Rolls the sitting back to marks capture so the lecturer can correct it. Approval and '
                   'publication must both be repeated afterwards.'),
        'reopen': ('Withdraw published results', True, 'danger',
                   'Immediately hides the published results from students and reopens the marks sheet. '
                   'The sitting must be resubmitted, reapproved and republished.'),
        'reschedule': ('Return to draft for rescheduling', True, 'warning',
                       'Deletes the candidate register and seat allocations, and returns the sitting to draft.'),
        'cancel': ('Cancel examination', True, 'danger',
                   'Cancels the sitting permanently. It cannot be reinstated — create a new examination instead.'),
    }
    available = []
    if editor:
        available += {
            S.DRAFT: ['schedule'],
            S.SCHEDULED: ['start'],
            S.MARKING: ['submit_internal', 'submit'],
            S.INTERNAL_REVIEW: ['review_internal'],
        }.get(exam.status, [])
    if workflow.can_review_external(request.user, exam) and exam.status == S.EXTERNAL_REVIEW:
        available.append('review_external')
    if workflow.is_admin(request.user):
        available += {
            S.MARKING: ['submit_internal', 'submit'],
            S.INTERNAL_REVIEW: ['review_internal', 'return'],
            S.EXTERNAL_REVIEW: ['review_external', 'return'],
            S.SUBMITTED: ['approve', 'return'],
            S.APPROVED: ['publish', 'return'],
            S.PUBLISHED: ['reopen'],
            S.SCHEDULED: ['reschedule', 'cancel'],
            S.DRAFT: ['cancel']
        }.get(exam.status, [])
    actions = [(key,) + catalogue[key] for key in dict.fromkeys(available)]
    stages = [
        (S.DRAFT, 'Draft'),
        (S.SCHEDULED, 'Scheduled'),
        (S.MARKING, 'Marks capture'),
        (S.INTERNAL_REVIEW, 'Internal review'),
        (S.EXTERNAL_REVIEW, 'External review'),
        (S.SUBMITTED, 'Awaiting approval'),
        (S.APPROVED, 'Approved'),
        (S.PUBLISHED, 'Published')
    ]
    order = [value for value, _ in stages]
    position = order.index(exam.status) if exam.status in order else -1
    track = [{'label': label, 'state': 'done' if i < position else 'current' if i == position else 'todo'}
             for i, (value, label) in enumerate(stages)]
    grades = Counter(r.grade for r in rows if r.attendance != 'PENDING' and (r.marks_obtained is not None or r.attendance == 'ABSENT'))
    return render(request, 'examinations/detail.html', {
        'exam': exam, 'rows': rows, 'editor': editor,
        'admin': workflow.is_admin(request.user), 'actions': actions, 'track': track,
        'cancelled': exam.status == S.CANCELLED, 'entered': entered, 'total': len(rows),
        'passed': sum(r.outcome == 'Pass' for r in rows),
        'absent': sum(r.attendance == 'ABSENT' for r in rows),
        'grades': sorted(grades.items()),
        'audit': exam.audit_entries.select_related('actor')[:50] if editor else [],
        'appeals': ExamAppeal.objects.filter(result__exam=exam).select_related('result__student__user') if editor else []
    })


@login_required
@require_POST
def action(request, pk):
    staff_exam(request, pk)
    try:
        workflow.transition(request.user, pk, request.POST.get('action'), request.POST.get('reason', ''), request.POST.get('revision', ''))
        messages.success(request, 'Examination workflow updated.')
    except ValidationError as exc:
        error_message(request, exc)
    return redirect('examinations:detail', pk=pk)


@login_required
def marks(request, pk):
    exam = staff_exam(request, pk)
    workflow.require_editor(request.user, exam)
    rows = list(exam.results.select_related('student__user', 'exam').order_by('seat_number', 'student__roll_no'))
    if request.GET.get('format') == 'csv':
        if request.GET.get('template') == '1':
            return csv_response(
                f'exam-{pk}-marks-template.csv',
                ['roll_no', 'attendance', 'cat_marks', 'exam_marks', 'remarks'],
                ([r.student.roll_no, r.attendance, r.cat_marks if r.cat_marks is not None else '', r.exam_marks if r.exam_marks is not None else '', r.remarks] for r in rows)
            )
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

            if 'csv_file' in request.FILES:
                upload = request.FILES['csv_file']
                if upload.size > 1024 * 1024:
                    raise ValidationError('CSV upload must be no larger than 1 MB.')
                reader = csv.DictReader(io.StringIO(upload.read().decode('utf-8-sig')))
                valid_headers = [
                    ['roll_no', 'attendance', 'cat_marks', 'exam_marks', 'remarks'],
                    ['roll_no', 'attendance', 'marks', 'remarks'],
                    ['roll_no', 'student_name', 'attendance', 'cat_marks', 'exam_marks', 'total_marks', 'grade', 'remarks'],
                ]
                if reader.fieldnames not in valid_headers:
                    raise ValidationError('Use the provided CSV template without modifying its required column headers.')
                by_roll = {r.student.roll_no: str(r.pk) for r in rows}
                for record in reader:
                    roll = record.get('roll_no', '').strip()
                    if roll not in by_roll or by_roll[roll] in entries or None in record:
                        raise ValidationError('CSV contains duplicate, unknown or malformed candidates.')
                    entries[by_roll[roll]] = record
            else:
                entries = {
                    str(r.pk): {
                        'attendance': request.POST.get(f'attendance_{r.pk}', ''),
                        'cat_marks': request.POST.get(f'cat_marks_{r.pk}', ''),
                        'exam_marks': request.POST.get(f'exam_marks_{r.pk}', ''),
                        'marks': request.POST.get(f'marks_{r.pk}', ''),
                        'remarks': request.POST.get(f'remarks_{r.pk}', '')
                    }
                    for r in rows
                }
            workflow.save_marks(request.user, pk, entries, request.POST.get('revision'), examiner_data=examiner_data)
            messages.success(request, 'Marks saved successfully. Total marks and CUE grades updated.')
            return redirect('examinations:marks', pk=pk)
        except (UnicodeDecodeError, csv.Error):
            errors = ['The file must be a valid UTF-8 CSV.']
        except ValidationError as exc:
            errors = exc.messages
        for row in rows:
            data = entries.get(str(row.pk))
            if data:
                row.attendance = data.get('attendance')
                row.cat_marks = data.get('cat_marks')
                row.exam_marks = data.get('exam_marks')
                row.marks_obtained = data.get('marks')
                row.remarks = data.get('remarks')
    recorded = sum(r.attendance == 'ABSENT' or (r.attendance == 'PRESENT' and r.marks_obtained is not None) for r in rows)
    from accounts.models import FacultyProfile
    faculty_list = FacultyProfile.objects.select_related('user', 'department')
    return render(request, 'examinations/marks.html', {
        'exam': exam, 'rows': rows, 'errors': errors,
        'editable': exam.status == Exam.Status.MARKING,
        'admin': workflow.is_admin(request.user),
        'faculty_list': faculty_list,
        'recorded': recorded, 'total': len(rows), 'outstanding': len(rows) - recorded,
        'bands': sorted(exam.grade_bands, key=lambda b: b['minimum'], reverse=True),
        'component': f'CAT: max {exam.cat_max_marks:.0f} | Final Exam: max {exam.exam_max_marks:.0f} | Total: {exam.max_marks}',
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
    res = workflow.student_statement(student, term if term.isdigit() else None)
    results, groups = res[0], res[1]
    gpa = getattr(res, 'gpa', 0.0)
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
    from .models import GradingScale
    from .examination_forms import GradingScaleForm
    if kind == 'rooms':
        model, form_class, title = ExamRoom, RoomForm, 'Examination room'
    elif kind == 'terms':
        model, form_class, title = AcademicTerm, TermForm, 'Academic term'
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
    if request.GET.get('format') == 'csv':
        return csv_response('published-examination-results.csv', ['Roll number', 'Student', 'Course', 'Term', 'Assessment', 'Marks', 'Maximum', 'Weight (%)', 'Grade', 'Outcome'], ([r.student.roll_no, r.student.user.display_name, r.exam.course.code, str(r.exam.term or ''), r.exam.name, r.marks_obtained, r.exam.max_marks, r.exam.weight, r.grade, r.outcome] for r in qs))
    rows = list(qs)
    counts = Counter(r.outcome for r in rows)
    return render(request, 'examinations/report.html', {'page': Paginator(rows, 40).get_page(request.GET.get('page')), 'total': len(rows), 'passed': counts['Pass'], 'failed': counts['Fail'], 'absent': counts['Absent'], 'terms': AcademicTerm.objects.all(), 'selected_term': term, 'appeals': ExamAppeal.objects.filter(status='OPEN').select_related('result__exam__course', 'result__student__user')})
