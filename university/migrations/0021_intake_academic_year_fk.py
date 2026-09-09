import django.db.models.deletion
from django.db import migrations, models


def link_intakes_to_academic_years(apps, schema_editor):
    Intake = apps.get_model("university", "Intake")
    AcademicYear = apps.get_model("university", "AcademicYear")
    for intake in Intake.objects.all():
        raw = (intake.academic_year_old or "").strip()
        ay = AcademicYear.objects.filter(name=raw).first()
        if ay is None and raw:
            ay = AcademicYear.objects.create(
                name=raw,
                start_date=intake.start_date,
                end_date=intake.end_date,
            )
        intake.academic_year = ay
        intake.save(update_fields=["academic_year"])


def unlink_intakes_from_academic_years(apps, schema_editor):
    Intake = apps.get_model("university", "Intake")
    for intake in Intake.objects.all():
        intake.academic_year_old = intake.academic_year.name if intake.academic_year else ""
        intake.save(update_fields=["academic_year_old"])


class Migration(migrations.Migration):

    dependencies = [
        ("university", "0020_alter_recyclebinitem_module"),
    ]

    operations = [
        migrations.RenameField(
            model_name="intake",
            old_name="academic_year",
            new_name="academic_year_old",
        ),
        migrations.AddField(
            model_name="intake",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="intakes",
                to="university.academicyear",
            ),
        ),
        migrations.RunPython(
            link_intakes_to_academic_years,
            unlink_intakes_from_academic_years,
        ),
        migrations.RemoveField(
            model_name="intake",
            name="academic_year_old",
        ),
    ]
