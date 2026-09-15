from university.document_views import present_pdf
from university.reporting_services import generate_report_pdf, generate_report_excel
import csv
import io
import uuid
from collections import Counter
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import StudentProfile
from .models import AcademicTerm, Course, Exam, ExamAppeal, ExamRoom, Result, DocumentReleaseControl
from .permissions_services import has_user_permission
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
    # Redirect ?status=SCHEDULED to the new Exam Schedules management view
    if status == 'SCHEDULED' and staff and request.GET.get('view') != 'legacy':
        return redirect('examinations:schedule_list')
    if status in dict(Exam.Status.choices):
        qs = qs.filter(status=status)
    term = request.GET.get('term', '')
    if term.isdigit():
        qs = qs.filter(term_id=term)
    if request.GET.get('format') == 'csv':
        return csv_response('examination-timetable.csv', ['Course', 'Assessment', 'Term', 'Date', 'Start', 'End', 'Room', 'Status'], ([x.course.code, x.name, str(x.term or ''), x.date, x.start_time, x.end_time, str(x.room or ''), x.get_status_display()] for x in qs))
    can_create = workflow.can_create_exams(user)
    scoped = workflow.staff_scope(user) if staff else Exam.objects.none()
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
        'selected_term': term,
        'can_view_marks': staff and has_user_permission(user, 'exams.view_marks'),
        'can_moderate': staff and has_user_permission(user, 'exams.moderate_marks'),
        'can_publish': staff and has_user_permission(user, 'exams.publish_results'),
        'can_senate': staff and has_user_permission(user, 'reports.senate_marksheet'),
        'workflow_counts': {
            'capture': scoped.filter(status__in=[Exam.Status.SCHEDULED, Exam.Status.MARKING, Exam.Status.RETURNED_TO_INSTRUCTOR, Exam.Status.CORRECTION]).count(),
            'submission': scoped.filter(status__in=[Exam.Status.MARKING, Exam.Status.RETURNED_TO_INSTRUCTOR, Exam.Status.CORRECTION]).count(),
            'approval': scoped.filter(status__in=[Exam.Status.SUBMITTED, Exam.Status.HOD_REVIEW, Exam.Status.RESUBMITTED]).count(),
            'publication': scoped.filter(status__in=[Exam.Status.HOD_APPROVED, Exam.Status.DEAN_REVIEW, Exam.Status.APPROVED]).count(),
        },
    })


@login_required
def workflow_queue(request, stage):
    """Role-scoped operational queues for submission, approval and publication."""
    stages = {
        'submission': {
            'title': 'Marks Submission',
            'subtitle': 'Complete marksheets and submit them to the Head of Department.',
            'statuses': [Exam.Status.MARKING, Exam.Status.RETURNED_TO_INSTRUCTOR, Exam.Status.CORRECTION],
            'permission': 'exams.view_marks',
            'action_label': 'Open marksheet',
            'action_route': 'examinations:marks',
        },
        'approval': {
            'title': 'Exam Marks Approval',
            'subtitle': 'Review submitted marksheets, return exceptions, or approve departmental results.',
            'statuses': [Exam.Status.SUBMITTED, Exam.Status.HOD_REVIEW, Exam.Status.RESUBMITTED],
            'permission': 'exams.moderate_marks',
            'action_label': 'Review submission',
            'action_route': 'examinations:detail',
        },
        'publication': {
            'title': 'Exam Marks Publish',
            'subtitle': 'Release approved results to students after the required academic review.',
            'statuses': [Exam.Status.HOD_APPROVED, Exam.Status.DEAN_REVIEW, Exam.Status.APPROVED, Exam.Status.PUBLISHED],
            'permission': 'exams.publish_results',
            'action_label': 'Review publication',
            'action_route': 'examinations:detail',
        },
    }
    config = stages.get(stage)
    if not config or not has_user_permission(request.user, config['permission']):
        raise PermissionDenied
    qs = workflow.staff_scope(request.user).filter(status__in=config['statuses'])
    term = request.GET.get('term', '')
    q = request.GET.get('q', '').strip()
    if term.isdigit():
        qs = qs.filter(term_id=term)
    if q:
        qs = qs.filter(Q(course__code__icontains=q) | Q(course__title__icontains=q) | Q(name__icontains=q))
    return render(request, 'examinations/workflow_queue.html', {
        'stage': stage,
        'queue': config,
        'page': Paginator(qs.order_by('date', 'course__code'), 20).get_page(request.GET.get('page')),
        'terms': AcademicTerm.objects.all(),
        'selected_term': term,
        'q': q,
    })


@login_required
def attendance(request, pk=None):
    exams = workflow.staff_scope(request.user).exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED])
    if not workflow.is_admin(request.user) and not has_user_permission(request.user, 'exams.create_exam'):
        exams = exams.filter(invigilator__user=request.user)
    exams = exams.annotate(candidate_count=Count('results'), attendance_count=Count('results', filter=~Q(results__attendance='PENDING')), absent_count=Count('results', filter=Q(results__attendance='ABSENT'))).order_by('-date', 'start_time')
    if pk is None:
        term = request.GET.get('term', '')
        q = request.GET.get('q', '').strip()
        if term.isdigit(): exams = exams.filter(term_id=term)
        if q: exams = exams.filter(Q(course__code__icontains=q) | Q(course__title__icontains=q) | Q(name__icontains=q))
        return render(request, 'examinations/attendance_list.html', {'page': Paginator(exams, 20).get_page(request.GET.get('page')), 'terms': AcademicTerm.objects.all(), 'selected_term': term, 'q': q})
    exam = get_object_or_404(exams, pk=pk)
    if not workflow.can_record_attendance(request.user, exam): raise PermissionDenied
    rows = list(exam.results.select_related('student__user').order_by('seat_number', 'student__roll_no'))
    if request.method == 'POST':
        bulk = request.POST.get('bulk_action', '')
        selected = set(request.POST.getlist('selected'))
        entries = {str(row.pk): bulk for row in rows if str(row.pk) in selected} if bulk in ('PRESENT', 'ABSENT', 'PENDING') else {str(row.pk): request.POST.get(f'attendance_{row.pk}', row.attendance) for row in rows}
        if bulk and not entries:
            messages.warning(request, 'Select at least one candidate for the bulk action.')
            return redirect('examinations:attendance_detail', pk=pk)
        try:
            changed = workflow.save_attendance(request.user, pk, entries)
            messages.success(request, f'Attendance saved for {changed} candidate(s).')
            return redirect('examinations:attendance_detail', pk=pk)
        except (ValidationError, PermissionDenied) as exc: error_message(request, exc)
    return render(request, 'examinations/attendance_detail.html', {'exam': exam, 'rows': rows, 'recorded': sum(r.attendance != 'PENDING' for r in rows), 'present': sum(r.attendance == 'PRESENT' for r in rows), 'absent': sum(r.attendance == 'ABSENT' for r in rows), 'locked': exam.status not in (Exam.Status.SCHEDULED, Exam.Status.MARKING)})


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
    can_edit_marks = workflow.can_mark(request.user, exam)
    can_view_marks = (
        can_edit_marks
        or workflow.can_hod_approve(request.user, exam)
        or workflow.can_dean_publish(request.user, exam)
    )
    if not can_view_marks:
        raise PermissionDenied(
            "You may view marks only for courses you teach, or for exams awaiting "
            "your review as HoD/Dean/Registrar/Exam Officer."
        )

    preview_session_key = f'marks_preview_{exam.pk}'
    if request.GET.get('cancel_preview') == '1':
        request.session.pop(preview_session_key, None)
        return redirect('examinations:marks', pk=pk)

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
    preview = None
    if request.method == 'POST':
        entries = {}
        examiner_data = {}
        try:
            if 'update_examiners' in request.POST:
                examiner_data['internal_examiner'] = request.POST.get('internal_examiner', '')
                examiner_data['external_examiner'] = request.POST.get('external_examiner', '')
                examiner_data['external_examiner_name'] = request.POST.get('external_examiner_name', '')
                examiner_data['examiner_remarks'] = request.POST.get('examiner_remarks', '')

            if request.POST.get('confirm_upload') == '1':
                stored = request.session.get(preview_session_key)
                token = request.POST.get('preview_token')
                expired = False
                if stored:
                    parsed_at = timezone.datetime.fromisoformat(stored['parsed_at'])
                    if timezone.is_naive(parsed_at):
                        parsed_at = timezone.make_aware(parsed_at)
                    expired = (timezone.now() - parsed_at).total_seconds() > 1800
                if not stored or stored.get('token') != token or expired or str(exam.revision) != str(stored.get('revision')):
                    request.session.pop(preview_session_key, None)
                    messages.error(request, 'Your marks preview has expired or the exam changed since you uploaded the file. Please upload again.')
                    return redirect('examinations:marks', pk=pk)
                entries = stored['entries']
                workflow.save_marks(request.user, pk, entries, stored['revision'], examiner_data=examiner_data)
                imported = len(entries)
                skipped = stored.get('error_count', 0)
                request.session.pop(preview_session_key, None)
                workflow.audit(exam, request.user, 'Bulk marks imported', f'{imported} imported, {skipped} skipped (errors) from prior preview')
                msg = f"Imported marks for {imported} candidate(s)."
                if skipped:
                    msg += f" {skipped} row(s) were skipped due to errors and were not imported."
                messages.success(request, msg)
                return redirect('examinations:marks', pk=pk)

            upload = request.FILES.get('marks_file') or request.FILES.get('csv_file')
            if upload:
                parsed = marks_io.parse_exam_marks_file(upload, exam)
                if not parsed['entries']:
                    all_errs = []
                    for item in parsed['items']:
                        all_errs.extend(item['errors'])
                    raise ValidationError(all_errs or ['No valid marks records found in the uploaded file.'])

                preview_token = uuid.uuid4().hex
                request.session[preview_session_key] = {
                    'token': preview_token,
                    'revision': str(exam.revision),
                    'entries': parsed['entries'],
                    'error_count': parsed['error_count'],
                    'parsed_at': timezone.now().isoformat(),
                }
                request.session.modified = True
                workflow.audit(
                    exam, request.user, 'Bulk marks preview generated',
                    f"{parsed['valid_count']} valid, {parsed['warning_count']} warning, "
                    f"{parsed['error_count']} error row(s) from '{upload.name}'"
                )

                for item in parsed['items']:
                    item['grade'] = exam.grade_for(float(item['total_marks'])) if item['total_marks'] is not None else None

                preview = {
                    'token': preview_token,
                    'filename': upload.name,
                    'items': parsed['items'],
                    'total_count': parsed['total_count'],
                    'valid_count': parsed['valid_count'],
                    'warning_count': parsed['warning_count'],
                    'error_count': parsed['error_count'],
                }
                # Fall through to the shared render at the end of the view,
                # which builds the full page context; `preview` rides along.
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

    # Read-only for anyone who isn't the assigned instructor/internal examiner
    # (or admin) — HoD/Dean/Registrar/Exam Officer can view but never edit here;
    # their action is to approve/return/publish from the exam detail page.
    editable = can_edit_marks and exam.status in [
        Exam.Status.MARKING,
        Exam.Status.RETURNED_TO_INSTRUCTOR,
        Exam.Status.CORRECTION,
        Exam.Status.UNPUBLISHED,
    ]
    ie_editable = can_edit_marks and exam.status in [
        Exam.Status.MARKING,
        Exam.Status.RETURNED_TO_INSTRUCTOR,
        Exam.Status.CORRECTION,
        Exam.Status.UNPUBLISHED,
        Exam.Status.INTERNAL_REVIEW,
    ]
    ee_editable = workflow.can_review_external(request.user, exam) and exam.status == Exam.Status.EXTERNAL_REVIEW
    latest_event = exam.workflow_events.first()
    marks_versions = exam.marks_versions.all()

    return render(request, 'examinations/marks.html', {
        'exam': exam,
        'available_exams': available_exams,
        'rows': rows,
        'errors': errors,
        'preview': preview,
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


# ---------------------------------------------------------------------------
# Examination Schedule Management Views
# ---------------------------------------------------------------------------
from .models import (
    ExamSchedule, ExamScheduleItem, Program, Cohort, AcademicYear, ExamRoom
)
import json


@login_required
def schedule_list(request):
    """Main schedule list – filtered by cohort, with search and pagination."""
    user = request.user
    if not (workflow.is_admin(user) or user.is_faculty):
        raise PermissionDenied

    cohorts = Cohort.objects.all()
    programs = Program.objects.order_by('code')
    academic_years = AcademicYear.objects.all()
    rooms = ExamRoom.objects.filter(active=True)

    selected_cohort = request.GET.get('cohort', '')
    q = request.GET.get('q', '').strip()

    qs = ExamSchedule.objects.select_related(
        'program', 'cohort', 'academic_year', 'term', 'created_by'
    ).prefetch_related('items__course')

    if selected_cohort:
        qs = qs.filter(cohort_id=selected_cohort)
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(program__code__icontains=q) |
            Q(program__name__icontains=q)
        )

    page = Paginator(qs, 10).get_page(request.GET.get('page'))

    return render(request, 'examinations/schedule_list.html', {
        'page': page,
        'cohorts': cohorts,
        'programs': programs,
        'academic_years': academic_years,
        'rooms': rooms,
        'selected_cohort': selected_cohort,
        'q': q,
        'terms': AcademicTerm.objects.all(),
        'exam_types': ExamSchedule.EXAM_TYPE_CHOICES,
        'status_choices': ExamSchedule.STATUS_CHOICES,
        'mode_choices': ExamScheduleItem.MODE_CHOICES,
        'session_choices': ExamScheduleItem.SESSION_CHOICES,
        'core_choices': ExamScheduleItem.CORE_CHOICES,
    })


@login_required
@require_POST
def schedule_create(request):
    """Create a new exam schedule with items from POST data."""
    user = request.user
    if not workflow.can_create_exams(user):
        raise PermissionDenied

    try:
        program = get_object_or_404(Program, pk=request.POST.get('program'))
        cohort = Cohort.objects.filter(pk=request.POST.get('cohort')).first()
        academic_year = AcademicYear.objects.filter(pk=request.POST.get('academic_year')).first()
        term = AcademicTerm.objects.filter(pk=request.POST.get('term')).first()
        study_year = int(request.POST.get('study_year', 1))
        semester = int(request.POST.get('semester', 1))
        exam_type = request.POST.get('exam_type', 'Regular')
        name = request.POST.get('name', '').strip()
        status = request.POST.get('status', 'Active')

        if not name:
            messages.error(request, 'Exam name is required.')
            return redirect('examinations:schedule_list')

        with transaction.atomic():
            sched = ExamSchedule.objects.create(
                name=name,
                program=program,
                cohort=cohort,
                academic_year=academic_year,
                term=term,
                study_year=study_year,
                semester=semester,
                exam_type=exam_type,
                status=status,
                created_by=user,
            )
            # Parse course items from POST
            _save_schedule_items(request, sched, term)

        messages.success(request, f'Examination schedule "{sched.name}" created successfully.')
    except (ValueError, ValidationError) as e:
        messages.error(request, f'Error creating schedule: {e}')

    return redirect('examinations:schedule_list')


@login_required
@require_POST
def schedule_edit(request, pk):
    """Update an existing exam schedule and its items."""
    user = request.user
    if not workflow.can_create_exams(user):
        raise PermissionDenied

    sched = get_object_or_404(ExamSchedule, pk=pk)

    try:
        sched.program = get_object_or_404(Program, pk=request.POST.get('program'))
        sched.cohort = Cohort.objects.filter(pk=request.POST.get('cohort')).first()
        sched.academic_year = AcademicYear.objects.filter(pk=request.POST.get('academic_year')).first()
        sched.term = AcademicTerm.objects.filter(pk=request.POST.get('term')).first()
        sched.study_year = int(request.POST.get('study_year', 1))
        sched.semester = int(request.POST.get('semester', 1))
        sched.exam_type = request.POST.get('exam_type', sched.exam_type)
        sched.name = request.POST.get('name', sched.name).strip()
        sched.status = request.POST.get('status', sched.status)

        with transaction.atomic():
            sched.save()
            sched.items.all().delete()
            _save_schedule_items(request, sched, sched.term)

        messages.success(request, f'Schedule "{sched.name}" updated.')
    except (ValueError, ValidationError) as e:
        messages.error(request, f'Error updating schedule: {e}')

    return redirect('examinations:schedule_list')


def _save_schedule_items(request, sched, term):
    """Parse course items from POST and create ExamScheduleItem + sync Exam."""
    course_ids = request.POST.getlist('course_id')
    for i, cid in enumerate(course_ids):
        try:
            course = Course.objects.get(pk=cid)
        except Course.DoesNotExist:
            continue

        is_core = request.POST.getlist('is_core')[i] if i < len(request.POST.getlist('is_core')) else 'Core'
        is_practical = request.POST.getlist('is_practical')[i] if i < len(request.POST.getlist('is_practical')) else 'No'
        mode = request.POST.getlist('mode_of_exam')[i] if i < len(request.POST.getlist('mode_of_exam')) else 'Physical'
        center = request.POST.getlist('center_name')[i] if i < len(request.POST.getlist('center_name')) else ''
        exam_date_str = request.POST.getlist('exam_date')[i] if i < len(request.POST.getlist('exam_date')) else ''
        exam_session = request.POST.getlist('exam_session')[i] if i < len(request.POST.getlist('exam_session')) else 'Morning'

        from datetime import datetime
        exam_date = None
        if exam_date_str:
            try:
                exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        room = None
        room_id_list = request.POST.getlist('room')
        if i < len(room_id_list) and room_id_list[i]:
            room = ExamRoom.objects.filter(pk=room_id_list[i]).first()

        # Create or sync underlying Exam object
        exam_obj = None
        if exam_date and term:
            exam_obj, _ = Exam.objects.get_or_create(
                course=course,
                term=term,
                name=f"{sched.name} - {course.code}",
                defaults={
                    'date': exam_date,
                    'room': room,
                    'status': Exam.Status.SCHEDULED,
                    'kind': Exam.Kind.FINAL,
                    'weight': 70,
                }
            )
            # Update date/room if exam already existed
            if exam_obj.date != exam_date or exam_obj.room != room:
                exam_obj.date = exam_date
                exam_obj.room = room
                exam_obj.save(update_fields=['date', 'room'])

        ExamScheduleItem.objects.create(
            schedule=sched,
            course=course,
            is_core=is_core,
            is_practical=(is_practical.lower() in ('yes', 'true', '1', 'on')),
            mode_of_exam=mode,
            room=room,
            center_name=center or (room.name if room else 'ONLINE'),
            exam_date=exam_date,
            exam_session=exam_session,
            exam=exam_obj,
        )


def _schedule_item_eligible_students(item):
    """
    Students eligible to sit a scheduled exam item's course. Prefers the
    actual candidate roster if this item is already linked to a real Exam
    sitting; otherwise derives eligibility from active course enrollment
    for the schedule's term, the same authoritative source used elsewhere.
    """
    from django.db.models import Q as _Q
    schedule = item.schedule
    if item.exam_id:
        student_ids = list(item.exam.results.values_list('student_id', flat=True))
    else:
        from .models import Enrollment
        enrollments = Enrollment.objects.filter(course=item.course, status=Enrollment.ACTIVE)
        if schedule.term_id:
            enrollments = enrollments.filter(term_id=schedule.term_id)
        student_ids = list(enrollments.values_list('student_id', flat=True))
    return (StudentProfile.objects.filter(pk__in=student_ids)
            .select_related('user', 'program').order_by('roll_no'))


@login_required
def schedule_item_students(request, item_id):
    """Eligible-students roster for one Exam Schedule item — web view, PDF, XLS, CSV."""
    user = request.user
    if not (workflow.is_admin(user) or user.is_faculty):
        raise PermissionDenied

    item = get_object_or_404(
        ExamScheduleItem.objects.select_related('schedule__program', 'schedule__cohort', 'schedule__academic_year', 'course', 'exam'),
        pk=item_id
    )
    students = _schedule_item_eligible_students(item)

    fmt = request.GET.get('format', '').lower()
    if fmt == 'pdf':
        from .examination_operations import generate_schedule_item_roster_pdf
        content = generate_schedule_item_roster_pdf(item, students)
        response = HttpResponse(content, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{item.course.code}-eligible-students.pdf"'
        return response
    if fmt in ('xlsx', 'excel'):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Eligible Students"
        ws.append(["#", "Roll Number", "Student Name", "Programme"])
        for idx, sp in enumerate(students, start=1):
            ws.append([idx, sp.roll_no, sp.user.display_name, sp.program.code if sp.program else ""])
        out = io.BytesIO()
        wb.save(out)
        response = HttpResponse(
            out.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{item.course.code}-eligible-students.xlsx"'
        return response
    if fmt == 'csv':
        return csv_response(
            f'{item.course.code}-eligible-students.csv',
            ['roll_no', 'student_name', 'programme'],
            ([sp.roll_no, sp.user.display_name, sp.program.code if sp.program else ''] for sp in students)
        )

    try:
        page_size = int(request.GET.get('page_size', 25))
    except ValueError:
        page_size = 25
    page_size = page_size if page_size in (25, 50, 100) else 25

    page = Paginator(students, page_size).get_page(request.GET.get('page'))

    querydict = request.GET.copy()
    querydict.pop('page', None)
    base_querystring = querydict.urlencode()

    return render(request, 'examinations/schedule_item_students.html', {
        'item': item, 'page': page, 'page_size': page_size,
        'base_querystring': base_querystring, 'total': students.count(),
    })


@login_required
def schedule_detail(request, pk):
    """Return schedule detail as JSON for the course list modal."""
    sched = get_object_or_404(
        ExamSchedule.objects.select_related('program', 'cohort', 'academic_year', 'term'),
        pk=pk
    )
    items = sched.items.select_related('course', 'room').all()
    data = {
        'id': sched.pk,
        'name': sched.name,
        'program': f"{sched.program.code} - {sched.program.name}",
        'cohort': str(sched.cohort) if sched.cohort else '',
        'academic_year': str(sched.academic_year) if sched.academic_year else '',
        'study_year': sched.study_year,
        'semester': sched.semester,
        'exam_type': sched.exam_type,
        'status': sched.status,
        'items': [{
            'id': item.pk,
            'course_code': item.course.code,
            'course_title': item.course.title,
            'specialization': item.specialization,
            'is_core': item.is_core,
            'is_practical': item.is_practical,
            'mode_of_exam': item.mode_of_exam,
            'center_name': item.center_name,
            'exam_date': item.exam_date.strftime('%Y-%m-%d') if item.exam_date else '',
            'exam_session': item.exam_session,
        } for item in items],
    }
    return HttpResponse(json.dumps(data), content_type='application/json')


@login_required
@require_POST
def schedule_delete(request, pk):
    """Delete a schedule and its items."""
    if not workflow.can_create_exams(request.user):
        raise PermissionDenied
    sched = get_object_or_404(ExamSchedule, pk=pk)
    name = sched.name
    sched.delete()
    messages.success(request, f'Schedule "{name}" deleted.')
    return redirect('examinations:schedule_list')


@login_required
@require_POST
def schedule_publish(request, pk):
    """Mark a schedule as Published and transition all linked exams to SCHEDULED."""
    if not workflow.can_create_exams(request.user):
        raise PermissionDenied
    sched = get_object_or_404(ExamSchedule, pk=pk)
    sched.status = ExamSchedule.STATUS_PUBLISHED
    sched.save(update_fields=['status'])
    # Ensure all linked exams are SCHEDULED
    for item in sched.items.filter(exam__isnull=False).select_related('exam'):
        if item.exam.status == Exam.Status.DRAFT:
            item.exam.status = Exam.Status.SCHEDULED
            item.exam.save(update_fields=['status'])
    messages.success(request, f'Schedule "{sched.name}" published successfully.')
    return redirect('examinations:schedule_list')


@login_required
@require_POST
def schedule_toggle_status(request, pk):
    """Toggle a schedule between Active and Inactive."""
    if not workflow.can_create_exams(request.user):
        raise PermissionDenied
    sched = get_object_or_404(ExamSchedule, pk=pk)
    if sched.status == ExamSchedule.STATUS_ACTIVE:
        sched.status = ExamSchedule.STATUS_INACTIVE
    else:
        sched.status = ExamSchedule.STATUS_ACTIVE
    sched.save(update_fields=['status'])
    messages.success(request, f'Schedule status changed to {sched.status}.')
    return redirect('examinations:schedule_list')


@login_required
def schedule_courses_api(request):
    """Return JSON list of courses for a given programme, study_year, semester."""
    program_id = request.GET.get('program')
    study_year = request.GET.get('study_year', '1')
    semester = request.GET.get('semester', '1')

    if not program_id:
        return HttpResponse(json.dumps([]), content_type='application/json')

    try:
        year = int(study_year)
        sem = int(semester)
    except (ValueError, TypeError):
        year, sem = 1, 1

    # Calculate semester_no from study_year + semester (e.g. Year 2 Sem 1 = semester_no 3)
    program = Program.objects.filter(pk=program_id).first()
    if not program:
        return HttpResponse(json.dumps([]), content_type='application/json')

    semesters_per_year = program.semesters_per_year or 2
    target_semester_no = (year - 1) * semesters_per_year + sem

    courses = Course.objects.filter(
        program=program,
        semester_no=target_semester_no,
        status=Course.STATUS_ACTIVE
    ).order_by('code')

    # Fallback: if no courses found for exact semester_no, return all active courses for the program
    if not courses.exists():
        courses = Course.objects.filter(
            program=program,
            status=Course.STATUS_ACTIVE
        ).order_by('code')

    data = [{
        'id': c.pk,
        'code': c.code,
        'title': c.title,
        'credits': c.credits,
        'semester_no': c.semester_no,
    } for c in courses]

    return HttpResponse(json.dumps(data), content_type='application/json')
