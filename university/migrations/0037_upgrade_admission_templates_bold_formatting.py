from django.db import migrations


def upgrade_templates_to_bold(apps, schema_editor):
    Template = apps.get_model("university", "AdmissionDocumentTemplate")
    templates = Template.objects.filter(is_active=True)

    salutation = "Dear <b>{{title_name}}</b>, Admission Number: <b>{{registration_number}}</b>"
    subject = "RE: OFFER OF ADMISSION TO <b>{{programme_name}}</b> (CODE: <b>{{programme_code}}</b>) — <b>{{academic_year}}</b> ACADEMIC YEAR"
    body = (
        "Following your application for admission to <b>{{university_name}}</b>, we are pleased to inform you that you have been offered admission to the:\n\n"
        "<b>{{programme_name}}</b>\n"
        "<b>Programme Code:</b> {{programme_code}}\n"
        "<b>Academic Year:</b> {{academic_year}}\n"
        "<b>Intake:</b> {{intake}}\n"
        "<b>Faculty / School:</b> {{faculty_name}}\n"
        "<b>Department:</b> {{department_name}}\n"
        "<b>Level / Study Mode:</b> {{level}} ({{study_mode}})\n"
        "<b>Campus:</b> {{campus}}\n"
        "<b>Admission Reference:</b> {{document_reference}}\n\n"
        "You have been admitted on the basis of your declared academic qualifications, which are subject to formal verification upon reporting. "
        "When reporting, you will be required to present the <b>original and copies</b> of the following mandatory documents:\n\n"
        "1. <b>KCSE Certificate or Official Result Slip</b> (verified against KNEC records)\n"
        "2. <b>Birth Certificate</b> or national identification document\n"
        "3. <b>National Identity Card or Passport</b>\n"
        "4. <b>Two recent coloured passport-size photographs</b> (bearing your name and registration number on the reverse)\n"
        "5. <b>Official proof of payment of tuition fees</b>"
    )
    terms = (
        "<b>ADMISSION REPORTING DATE & ACCEPTANCE DEADLINE</b>\n"
        "The programme will commence on <b>{{reporting_date}}</b>. You are, therefore, expected to report and complete your registration on or before <b>{{acceptance_deadline}}</b>.\n\n"
        "<b>IMPORTANT CONDITIONS OF ADMISSION</b>\n"
        "i. <b>Accommodation:</b> Admission to the University does not guarantee accommodation in the Halls of Residence. Students not allocated university accommodation will be required to make private arrangements.\n"
        "ii. <b>Rules & Regulations:</b> This admission offer is subject to your strict adherence to the University's <b>Rules and Regulations</b>.\n"
        "iii. <b>Inquiries:</b> In case of any queries, please contact the Admissions Office at <b>{{university_email}}</b> or Tel: <b>{{university_phone}}</b>."
    )
    fee_instructions = (
        "<b>TUITION FEES & PAYMENT SCHEDULE</b>\n"
        "You will pay <b>{{tuition_fee}}</b> as tuition fee in a Semester (Estimated total first-semester charges: <b>{{total_fees}}</b>). "
        "For more information, please contact the Finance Directorate at <b>{{finance_email}}</b> or Tel: <b>{{university_phone}}</b>.\n\n"
        "<b>FEE PAYMENT INSTRUCTIONS</b>\n"
        "You are required to follow the official instructions below to pay the tuition fee:\n"
        "1. While logged in to the students portal (<b>{{portal_url}}</b>), navigate to the <b>\"STUDENT PAYMENT INSTRUCTIONS\"</b> section.\n"
        "2. Click on <b>\"Fee Payment\"</b> / <b>\"M-Pesa Payment\"</b> and follow the prompts.\n"
        "   (<b>M-Pesa Paybill:</b> {{mpesa_paybill}}, <b>Account:</b> {{registration_number}} | <b>Bank:</b> {{bank_name}}, <b>Account:</b> {{bank_account}}, Branch: {{bank_branch}})\n"
        "3. Ensure you obtain an <b>official electronic receipt</b> upon payment to complete registration clearance."
    )

    for tmpl in templates:
        tmpl.salutation_template = salutation
        tmpl.subject_template = subject
        tmpl.body_template = body
        tmpl.terms_and_conditions = terms
        tmpl.fee_schedule_instructions = fee_instructions
        tmpl.save(update_fields=[
            "salutation_template",
            "subject_template",
            "body_template",
            "terms_and_conditions",
            "fee_schedule_instructions",
        ])


class Migration(migrations.Migration):
    dependencies = [("university", "0036_alter_departmentclearance_options_and_more")]
    operations = [migrations.RunPython(upgrade_templates_to_bold, migrations.RunPython.noop)]
