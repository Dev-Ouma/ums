from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_populate_cohorts")]

    operations = [
        migrations.AlterField(
            model_name="studentprofile",
            name="cohort",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="students",
                to="university.cohort",
            ),
        ),
    ]
