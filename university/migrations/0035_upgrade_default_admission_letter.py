from django.db import migrations


def upgrade_system_default(apps, schema_editor):
    Template = apps.get_model("university", "AdmissionDocumentTemplate")
    old_names = {"Standard University Admission Letter", "Standard Undergraduate Admission Letter"}
    template = Template.objects.filter(is_default=True, name__in=old_names).order_by("id").first()
    if not template:
        return
    template.name = "Official Undergraduate Admission Offer"
    template.subject_template = "OFFER OF ADMISSION: {{programme_name}} ({{programme_code}})"
    template.body_template = (
        "Following consideration of your application by the University Admissions Board, I am pleased to offer you "
        "admission to the <b>{{programme_name}}</b> ({{programme_code}}), offered through {{department_name}}, "
        "{{faculty_name}}. Your place is reserved for the <b>{{intake}}</b> of the {{academic_year}} academic year "
        "({{semester}}).\n\n"
        "Please report for orientation, original-document verification, and Student Registration and Enrollment on "
        "<b>{{reporting_date}}</b>. Bring this letter together with your original academic certificates and the "
        "identification documents listed below. Your admission will be confirmed after the required checks are completed.\n\n"
        "We look forward to welcoming you to {{university_name}} and wish you every success in your studies."
    )
    template.terms_and_conditions = (
        "1. This offer is conditional upon verification of the original academic certificates, identification document, and birth certificate.\n"
        "2. Student Registration and Enrollment is completed only after the University's admission and finance checks are satisfied.\n"
        "3. You are bound by the University Charter, Statutes, Student Handbook, and all applicable academic and conduct regulations.\n"
        "4. The University may withdraw this offer if information or documents supplied in support of the application are inaccurate, fraudulent, or materially incomplete."
    )
    template.fee_schedule_instructions = (
        "Before reporting, confirm the current fee schedule and approved payment instructions with the Finance Office.\n"
        "Payment reference: {{application_number}}\n"
        "Estimated first-semester tuition: {{tuition_fee}}\n"
        "Estimated first-semester total: {{total_fees}}\n"
        "Use only payment channels published by the University and retain the official receipt."
    )
    template.version = max(template.version, 2)
    template.save(update_fields=["name", "subject_template", "body_template", "terms_and_conditions", "fee_schedule_instructions", "version", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("university", "0034_backupsetting_disaster_recovery_procedure_and_more")]
    operations = [migrations.RunPython(upgrade_system_default, migrations.RunPython.noop)]
