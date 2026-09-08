from decimal import Decimal, ROUND_DOWN
from django.db import migrations
from django.utils import timezone


def preserve(apps, schema_editor):
    Exam = apps.get_model('university', 'Exam')
    Result = apps.get_model('university', 'Result')
    Audit = apps.get_model('university', 'ExamAudit')
    groups = {}
    for exam in Exam.objects.all():
        groups.setdefault((exam.course_id, exam.term_id), []).append(exam)
    for exams in groups.values():
        weight = (Decimal(100) / len(exams)).quantize(Decimal('.01'), rounding=ROUND_DOWN)
        for index, exam in enumerate(exams):
            exam.weight = weight if index < len(exams) - 1 else Decimal(100) - weight * (len(exams) - 1)
            if Result.objects.filter(exam=exam).exists():
                exam.status = 'PUBLISHED'
                exam.published_at = timezone.now()
                Audit.objects.create(exam=exam, action='Legacy results preserved', detail='Existing results were already visible before examination workflow installation.')
            exam.save()
            for seat, result in enumerate(Result.objects.filter(exam=exam).order_by('student_id'), 1):
                result.attendance = 'PRESENT'
                result.seat_number = seat
                result.save()


class Migration(migrations.Migration):
    dependencies = [('university', '0002_exam_end_time_exam_grade_bands_exam_instructions_and_more')]
    operations = [migrations.RunPython(preserve, migrations.RunPython.noop)]
