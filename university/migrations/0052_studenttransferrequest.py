from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
from django.utils import timezone

class Migration(migrations.Migration):
    dependencies = [("university", "0051_migrate_legacy_statuses")]
    operations = [migrations.CreateModel(name="StudentTransferRequest", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("reason", models.TextField()), ("supporting_document", models.FileField(blank=True, null=True, upload_to="student_transfers/%Y/%m/")),
        ("status", models.CharField(choices=[("PENDING", "Pending Review"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")], db_index=True, default="PENDING", max_length=12)),
        ("review_comments", models.TextField(blank=True, default="")), ("created_at", models.DateTimeField(default=timezone.now)), ("reviewed_at", models.DateTimeField(blank=True, null=True)),
        ("from_program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="outgoing_transfers", to="university.program")),
        ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transfer_requests", to="accounts.studentprofile")),
        ("to_program", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="incoming_transfers", to="university.program")),
        ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_transfers", to=settings.AUTH_USER_MODEL)),
    ], options={"ordering": ["-created_at"]}), migrations.AddConstraint(model_name="studenttransferrequest", constraint=models.UniqueConstraint(condition=models.Q(status="PENDING"), fields=("student", "to_program"), name="one_pending_transfer_per_target"))]
