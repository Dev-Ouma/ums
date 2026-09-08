"""Examination workflow: all writes lock the exam and check role, scope and state."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Course, Enrollment, Exam, ExamAppeal, ExamAudit, Result, grade_point_for


def is_admin(user):
    return user.is_authenticated and (user.is_admin_role or user.is_superuser)


def staff_scope(user):
    qs = Exam.objects.select_related(
        'course__faculty__user', 'term', 'room', 'invigilator__user',
        'internal_examiner__user', 'external_examiner__user', 'original_exam'
    )
    if is_admin(user):
        return qs
    if user.is_authenticated and user.is_faculty:
        return qs.filter(
            Q(course__faculty__user=user) |
            Q(invigilator__user=user) |
            Q(internal_examiner__user=user) |
            Q(external_examiner__user=user)
        )
    return qs.none()


def can_mark(user, exam):
    if is_admin(user):
        return True
    if not (user.is_authenticated and user.is_faculty):
        return False
    is_course_lecturer = bool(exam.course.faculty_id and exam.course.faculty.user_id == user.pk)
    is_internal_examiner = bool(exam.internal_examiner_id and exam.internal_examiner.user_id == user.pk)
    return is_course_lecturer or is_internal_examiner


def can_review_external(user, exam):
    if is_admin(user):
        return True
    if not (user.is_authenticated and user.is_faculty):
        return False
    return bool(exam.external_examiner_id and exam.external_examiner.user_id == user.pk)


def require_editor(user, exam):
    if not can_mark(user, exam):
        raise PermissionDenied


def audit(exam, user, action, detail=''):
    ExamAudit.objects.create(exam=exam, actor=user, action=action, detail=detail)


def eligible_students(exam):
    if exam.original_exam_id:
        return [r.student_id for r in exam.original_exam.results.select_related('exam') if r.outcome in ('Fail', 'Absent')]
    return list(Enrollment.objects.filter(course=exam.course, term=exam.term, status=Enrollment.ACTIVE).values_list('student_id', flat=True))


def check_schedule(exam):
    # Auto-assign internal examiner if not set
    if not exam.internal_examiner_id and exam.course.faculty_id:
        exam.internal_examiner = exam.course.faculty

    exam.full_clean()
    if not all([exam.term_id, exam.room_id, exam.invigilator_id, exam.start_time, exam.end_time]):
        raise ValidationError('Set the academic term, room, invigilator, start and end times before scheduling.')
    if not exam.room.active:
        raise ValidationError('Choose an active room.')
    students = eligible_students(exam)
    if not students:
        raise ValidationError('No eligible candidates. Check active course enrollments for this term, or failed/absent original results.')
    if len(students) > exam.room.capacity:
        raise ValidationError(f'{len(students)} candidates exceed the room capacity of {exam.room.capacity}.')
    peers = Exam.objects.exclude(pk=exam.pk).exclude(status__in=[Exam.Status.DRAFT, Exam.Status.CANCELLED])
    overlaps = peers.filter(date=exam.date, start_time__lt=exam.end_time, end_time__gt=exam.start_time)
    if overlaps.filter(room=exam.room).exists():
        raise ValidationError('Room clash: another exam uses this room during the selected time.')
    if overlaps.filter(invigilator=exam.invigilator).exists():
        raise ValidationError('Invigilator clash: this faculty member is already assigned at that time.')
    if overlaps.filter(results__student_id__in=students).exists():
        raise ValidationError('Student clash: a candidate has another exam during the selected time.')
    if exam.original_exam_id:
        if peers.filter(original_exam_id=exam.original_exam_id).exists():
            raise ValidationError('A supplementary sitting already exists for this assessment.')
    elif peers.filter(course=exam.course, term=exam.term, kind=exam.kind, original_exam__isnull=True).exists():
        raise ValidationError('This course and term already has a scheduled CAT or final of the same type. Edit or cancel it first.')
    return students


def check_complete(exam):
    rows = list(exam.results.all())
    if not rows or any(r.attendance == 'PENDING' or (r.attendance == 'PRESENT' and r.marks_obtained is None) for r in rows):
        raise ValidationError('Record attendance for every candidate and marks for every present candidate before submission.')
    for row in rows:
        row.full_clean()


@transaction.atomic
def transition(user, exam_id, action, reason='', revision=None):
    exam = Exam.objects.select_for_update().select_related(
        'course__faculty__user', 'internal_examiner__user', 'external_examiner__user',
        'room', 'term', 'original_exam'
    ).get(pk=exam_id)

    if action in ('review_external',) and not can_review_external(user, exam):
        raise PermissionDenied
    elif action not in ('review_external',):
        require_editor(user, exam)

    if revision is not None and str(exam.revision) != str(revision):
        raise ValidationError('This exam changed in another window. Reload before continuing.')
    before = exam.status
    admin_actions = {'approve', 'publish', 'reopen', 'cancel', 'reschedule'}
    if action in admin_actions and not is_admin(user):
        raise PermissionDenied

    if action == 'schedule' and before == Exam.Status.DRAFT:
        Course.objects.select_for_update().get(pk=exam.course_id)
        students = check_schedule(exam)
        Result.objects.bulk_create([Result(exam=exam, student_id=pk, seat_number=i) for i, pk in enumerate(sorted(students), 1)])
        exam.status = Exam.Status.SCHEDULED
    elif action == 'start' and before == Exam.Status.SCHEDULED:
        if exam.date > timezone.localdate():
            raise ValidationError('Marks entry opens on the examination date.')
        exam.status = Exam.Status.MARKING
    elif action == 'submit_internal' and before == Exam.Status.MARKING:
        check_complete(exam)
        exam.status = Exam.Status.INTERNAL_REVIEW
    elif action == 'review_internal' and before == Exam.Status.INTERNAL_REVIEW:
        check_complete(exam)
        exam.internal_reviewed_at = timezone.now()
        exam.internal_reviewed_by = user
        if exam.external_examiner_id or exam.external_examiner_name:
            exam.status = Exam.Status.EXTERNAL_REVIEW
        else:
            exam.status = Exam.Status.SUBMITTED
    elif action == 'review_external' and before == Exam.Status.EXTERNAL_REVIEW:
        check_complete(exam)
        exam.external_reviewed_at = timezone.now()
        exam.external_reviewed_by = user
        exam.status = Exam.Status.SUBMITTED
    elif action == 'submit' and before in (Exam.Status.MARKING, Exam.Status.INTERNAL_REVIEW, Exam.Status.EXTERNAL_REVIEW):
        check_complete(exam)
        if not exam.internal_reviewed_at:
            exam.internal_reviewed_at = timezone.now()
            exam.internal_reviewed_by = user
        exam.status = Exam.Status.SUBMITTED
    elif action == 'approve' and before == Exam.Status.SUBMITTED:
        check_complete(exam)
        exam.status = Exam.Status.APPROVED
    elif action == 'publish' and before == Exam.Status.APPROVED:
        check_complete(exam)
        exam.status = Exam.Status.PUBLISHED
        exam.published_at = timezone.now()
    elif action in ('return', 'reopen') and before in ({Exam.Status.INTERNAL_REVIEW, Exam.Status.EXTERNAL_REVIEW, Exam.Status.SUBMITTED, Exam.Status.APPROVED} if action == 'return' else {Exam.Status.PUBLISHED}):
        if not reason.strip():
            raise ValidationError('A reason is required to return or reopen results.')
        exam.status = Exam.Status.MARKING
        exam.published_at = None
    elif action == 'cancel' and before in (Exam.Status.DRAFT, Exam.Status.SCHEDULED):
        if not reason.strip():
            raise ValidationError('A cancellation reason is required.')
        exam.status = Exam.Status.CANCELLED
    elif action == 'reschedule' and before == Exam.Status.SCHEDULED:
        if not reason.strip():
            raise ValidationError('A rescheduling reason is required.')
        exam.results.all().delete()
        exam.status = Exam.Status.DRAFT
    else:
        raise ValidationError('That action is not available in the current exam state.')
    exam.revision += 1
    exam.save()
    audit(exam, user, action.replace('_', ' ').title(), f'{before} → {exam.status}. {reason.strip()}')
    return exam


class StatementResult(tuple):
    def __new__(cls, results, groups, gpa=0.0):
        obj = super().__new__(cls, (results, groups))
        obj.results = results
        obj.groups = groups
        obj.gpa = gpa
        return obj


def parse_decimal_mark(val, roll_no, field_name='mark'):
    raw = str(val).strip() if val is not None else ''
    if not raw:
        return None
    try:
        mark = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f'{roll_no}: enter a numeric {field_name}.')
    if not mark.is_finite() or mark.as_tuple().exponent < -2:
        raise ValidationError(f'{roll_no}: use finite {field_name} with up to two decimal places.')
    return mark


@transaction.atomic
def save_marks(user, exam_id, entries, revision, examiner_data=None):
    exam = Exam.objects.select_for_update().select_related('course__faculty__user').get(pk=exam_id)
    require_editor(user, exam)
    if exam.status != Exam.Status.MARKING:
        raise ValidationError('Marks are locked. The exam must be in marks entry.')
    if str(exam.revision) != str(revision):
        raise ValidationError('Another user changed this exam. Reload to avoid overwriting their work.')
    rows = {str(r.pk): r for r in exam.results.select_related('student', 'exam')}
    if not entries or not set(entries).issubset(rows):
        raise ValidationError('The marks sheet contains invalid candidates.')

    # Update examiner assignments if provided
    if examiner_data and is_admin(user):
        if 'internal_examiner' in examiner_data:
            exam.internal_examiner_id = examiner_data['internal_examiner'] or None
        if 'external_examiner' in examiner_data:
            exam.external_examiner_id = examiner_data['external_examiner'] or None
        if 'external_examiner_name' in examiner_data:
            exam.external_examiner_name = examiner_data['external_examiner_name'].strip()
        if 'examiner_remarks' in examiner_data:
            exam.examiner_remarks = examiner_data['examiner_remarks'].strip()

    changes = []
    for key, data in entries.items():
        row = rows[key]
        attendance = data.get('attendance', '').strip().upper()
        if attendance not in ('PENDING', 'PRESENT', 'ABSENT'):
            raise ValidationError(f'{row.student.roll_no}: choose valid attendance.')

        cat_val = data.get('cat_marks', None)
        exam_val = data.get('exam_marks', None)
        total_val = data.get('marks', None)

        cat_mark = parse_decimal_mark(cat_val, row.student.roll_no, 'CAT mark')
        exam_mark = parse_decimal_mark(exam_val, row.student.roll_no, 'exam mark')
        total_mark = parse_decimal_mark(total_val, row.student.roll_no, 'mark')

        if attendance == 'ABSENT' and (cat_mark is not None or exam_mark is not None or total_mark is not None):
            raise ValidationError(f'{row.student.roll_no}: absent candidates cannot receive marks.')

        if attendance == 'PRESENT':
            if cat_mark is not None or exam_mark is not None:
                total_mark = round((cat_mark or Decimal(0)) + (exam_mark or Decimal(0)), 2)
            elif total_mark is not None:
                c_calc = round(min(exam.cat_max_marks, total_mark * Decimal('0.3')), 2)
                e_calc = round(total_mark - c_calc, 2)
                cat_mark, exam_mark = c_calc, e_calc

        before = f'{row.attendance}/{row.cat_marks}+{row.exam_marks}={row.marks_obtained}/{row.remarks}'
        row.attendance = attendance
        row.cat_marks = cat_mark if attendance == 'PRESENT' else None
        row.exam_marks = exam_mark if attendance == 'PRESENT' else None
        row.marks_obtained = total_mark if attendance == 'PRESENT' else None
        row.remarks = data.get('remarks', '').strip()
        row.full_clean()
        changes.append(f'{row.student.roll_no}: {before} → {row.attendance}/{row.cat_marks}+{row.exam_marks}={row.marks_obtained}/{row.remarks}')

    for key in entries:
        rows[key].save(update_fields=['attendance', 'cat_marks', 'exam_marks', 'marks_obtained', 'remarks'])
    exam.revision += 1
    exam.save(update_fields=['revision', 'internal_examiner', 'external_examiner', 'external_examiner_name', 'examiner_remarks'])
    audit(exam, user, 'Marks saved', '\n'.join(changes))


@transaction.atomic
def create_appeal(user, result_id, reason):
    result = Result.objects.select_for_update().select_related('student', 'exam').get(pk=result_id)
    if not user.is_student or result.student.user_id != user.pk:
        raise PermissionDenied
    if result.exam.status != Exam.Status.PUBLISHED:
        raise ValidationError('Only published results may be appealed.')
    if not reason.strip() or len(reason) > 2000:
        raise ValidationError('Provide an appeal reason of 1–2000 characters.')
    if result.appeals.filter(status='OPEN').exists():
        raise ValidationError('You already have an open appeal for this result.')
    appeal = ExamAppeal.objects.create(result=result, reason=reason.strip())
    audit(result.exam, user, 'Appeal submitted', f'Appeal #{appeal.pk}: {reason.strip()}')
    return appeal


@transaction.atomic
def resolve_appeal(user, appeal_id, decision, resolution):
    if not is_admin(user):
        raise PermissionDenied
    appeal = ExamAppeal.objects.select_for_update().select_related('result__exam').get(pk=appeal_id)
    if appeal.status != 'OPEN' or decision not in ('ACCEPTED', 'REJECTED') or not resolution.strip() or len(resolution) > 2000:
        raise ValidationError('Select a decision and give a resolution for an open appeal (up to 2000 characters).')
    if decision == 'ACCEPTED':
        exam = appeal.result.exam
        if exam.status == Exam.Status.PUBLISHED:
            transition(user, exam.pk, 'reopen', f'Appeal #{appeal.pk}: {resolution}')
        elif exam.status != Exam.Status.MARKING:
            raise ValidationError('Return this exam to marks entry before accepting this appeal.')
    appeal.status, appeal.resolution = decision, resolution.strip()
    appeal.resolved_at, appeal.resolved_by = timezone.now(), user
    appeal.save()
    audit(appeal.result.exam, user, 'Appeal resolved', f'#{appeal.pk} {decision}: {resolution}')


def student_statement(student, term=None):
    results = Result.objects.filter(
        student=student, exam__status=Exam.Status.PUBLISHED
    ).select_related('exam__course', 'exam__term', 'exam__original_exam').prefetch_related('appeals')
    if term:
        results = results.filter(exam__term_id=term)
    groups = {}
    # Latest published supplementary sitting replaces its original component.
    supplements = {}
    for row in sorted(results, key=lambda r: (r.exam.date, r.exam_id)):
        if row.exam.original_exam_id:
            supplements[row.exam.original_exam_id] = row
    for row in results:
        if row.exam.original_exam_id:
            continue
        key = (row.exam.course_id, row.exam.term_id)
        group = groups.setdefault(key, {
            'course': row.exam.course, 'term': row.exam.term, 'components': [],
            'total': Decimal(0), 'weight': Decimal(0), 'kinds': set(), 'final': None
        })
        effective = supplements.get(row.exam_id, row)
        contribution = (effective.marks_obtained or Decimal(0)) * row.exam.weight / Decimal(effective.exam.max_marks)
        group['components'].append({'result': row, 'effective': effective, 'contribution': contribution})
        group['total'] += contribution
        group['weight'] += row.exam.weight
        group['kinds'].add(row.exam.kind)
        if row.exam.kind == Exam.Kind.FINAL:
            group['final'] = row.exam

    complete_groups = []
    for group in groups.values():
        is_complete = (
            (group['weight'] == 100 and {Exam.Kind.CAT, Exam.Kind.FINAL}.issubset(group['kinds']))
            or (group['final'] is not None and group['weight'] == 100 and len(group['components']) == 1)
        )
        is_complete = is_complete and all(
            c['effective'].attendance == 'ABSENT' or
            (c['effective'].attendance == 'PRESENT' and c['effective'].marks_obtained is not None)
            for c in group['components']
        )
        group['complete'] = is_complete
        if is_complete and group['final']:
            group['grade'] = group['final'].grade_for(float(group['total']))
            group['grade_point'] = exam_grade_point(group['final'], group['grade'])
            group['outcome'] = 'Pass' if group['total'] >= group['final'].pass_mark else 'Fail'
            complete_groups.append(group)
        elif is_complete and group['components']:
            # Fallback to first component's exam grade
            first_exam = group['components'][0]['result'].exam
            group['grade'] = first_exam.grade_for(float(group['total']))
            group['grade_point'] = exam_grade_point(first_exam, group['grade'])
            group['outcome'] = 'Pass' if group['total'] >= first_exam.pass_mark else 'Fail'
            complete_groups.append(group)
        else:
            group['grade'] = 'Incomplete'
            group['grade_point'] = 0.0
            group['outcome'] = 'Awaiting published components'

    # Compute Cumulative GPA across complete course groups
    gpa = weighted_gpa(complete_groups)
    return StatementResult(results, list(groups.values()), gpa)



def exam_grade_point(exam, grade):
    """Use the examination's saved scale, retaining legacy grade-only bands."""
    band = next((b for b in exam.grade_bands if b['grade'] == grade), {})
    return Decimal(str(band.get('gp', grade_point_for(grade))))


def weighted_gpa(groups):
    credits = sum(g['course'].credits for g in groups)
    if not credits:
        return None
    points = sum(Decimal(str(g['grade_point'])) * g['course'].credits for g in groups)
    return (points / credits).quantize(Decimal('0.01'))
