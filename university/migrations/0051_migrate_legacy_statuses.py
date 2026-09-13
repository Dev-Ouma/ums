from django.db import migrations


def migrate_legacy_statuses(apps, schema_editor):
    Exam = apps.get_model('university', 'Exam')
    Result = apps.get_model('university', 'Result')
    MarksVersion = apps.get_model('university', 'MarksVersion')

    # 1. Update APPROVED -> HOD_APPROVED
    Exam.objects.filter(status='APPROVED').update(status='HOD_APPROVED')

    # 2. Update SUBMITTED -> HOD_REVIEW
    Exam.objects.filter(status='SUBMITTED').update(status='HOD_REVIEW')

    # 3. Create initial MarksVersion (v1) for PUBLISHED exams with results
    published_exams = Exam.objects.filter(status='PUBLISHED')
    for exam in published_exams:
        if not MarksVersion.objects.filter(exam=exam, version_number=1).exists():
            results = Result.objects.filter(exam=exam).select_related('student')
            if results.exists():
                snapshot = [
                    {
                        'student_id': r.student_id,
                        'roll_no': getattr(r.student, 'roll_number', '') or str(r.student_id),
                        'cat': float(r.cat_marks) if r.cat_marks is not None else None,
                        'ie': float(r.exam_marks_ie) if r.exam_marks_ie is not None else None,
                        'ee': float(r.exam_marks_ee) if r.exam_marks_ee is not None else None,
                        'exam_marks': float(r.exam_marks) if r.exam_marks is not None else None,
                        'total': float(r.marks_obtained) if r.marks_obtained is not None else None,
                        'attendance': r.attendance,
                        'remarks': r.remarks or '',
                    }
                    for r in results
                ]
                MarksVersion.objects.create(
                    exam=exam,
                    version_number=1,
                    status='PUBLISHED',
                    snapshot=snapshot,
                    notes='Initial baseline snapshot migrated from published results.'
                )


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('university', '0050_marks_workflow_enhancement'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_statuses, reverse_migration),
    ]
