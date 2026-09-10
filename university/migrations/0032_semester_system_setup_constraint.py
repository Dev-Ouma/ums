from django.db import migrations, models


def normalize_duplicate_semesters(apps, schema_editor):
    AcademicTerm = apps.get_model("university", "AcademicTerm")
    seen = set()
    for term in AcademicTerm.objects.order_by("academic_year_id", "term_type", "semester_number", "-is_current", "pk"):
        key = (term.academic_year_id, term.term_type, term.semester_number)
        if key not in seen:
            seen.add(key)
            continue

        next_number = term.semester_number
        while (term.academic_year_id, term.term_type, next_number) in seen:
            next_number += 1
        term.semester_number = next_number
        term.save(update_fields=["semester_number"])
        seen.add((term.academic_year_id, term.term_type, term.semester_number))


class Migration(migrations.Migration):

    dependencies = [
        ("university", "0031_notice_ticker_controls"),
    ]

    operations = [
        migrations.RunPython(normalize_duplicate_semesters, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="academicterm",
            constraint=models.UniqueConstraint(
                fields=("academic_year", "term_type", "semester_number"),
                name="unique_semester_number_per_academic_year",
            ),
        ),
    ]
