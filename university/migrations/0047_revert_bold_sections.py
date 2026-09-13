# -*- coding: utf-8 -*-
"""Migration to revert bold formatting in AdmissionDocumentTemplate.
Restores the original template strings (no <b> tags) for the default admission letter.
"""

from django.db import migrations


def revert_bold_sections(apps, schema_editor):
    Template = apps.get_model("university", "AdmissionDocumentTemplate")
    # Target the active default template (id=1 in this project) or any active template
    templates = Template.objects.filter(is_active=True)

    salutation = "Dear {{title_name}}, Admission Number: {{registration_number}}"
    subject = "RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR"
    body = (
        "Following your application for admission to {{university_name}}, I wish to congratulate you on this achievement. "
        "You have been admitted on the basis of your qualifications, which are subject to verification by the University. "
        "When reporting, you will be required to present original and copies of the following:\n\n"
        "1. KCSE Certificate or Result Slip\n"
        "2. Birth Certificate\n"
        "3. National Identity Card or Passport\n"
        "4. Two coloured passport-size photographs\n"
        "5. Proof of payment of tuition fees"
    )
    terms = (
        "COMMENCEMENT DATE\n"
        "The programme will commence on {{reporting_date}}. You are, therefore, expected to report and complete your registration on this date.\n\n"
        "OTHER IMPORTANT INFORMATION\n"
        "i. Admission to the University does not guarantee accommodation in the Halls of Residence. Students not allocated university accommodation will be required to make private arrangements.\n"
        "ii. This admission offer is subject to your adherence to the University's Rules and Regulations.\n"
        "iii. In case of any queries, please contact the Admissions Office at {{university_email}} or Tel: {{university_phone}}"
    )
    fee = (
        "TUITION FEES\n"
        "You will pay {{tuition_fee}} as tuition fee in a Semester. "
        "For more information, please contact the Finance Office at {{finance_email}} or Tel: {{university_phone}}\n\n"
        "FEE PAYMENT\n"
        "You are required to follow the instructions below to pay the tuition fee:\n"
        "1. While logged in to the students portal, navigate to the \"STUDENT PAYMENT INSTRUCTIONS\" section at the bottom of the page.\n"
        "2. Click on \"Fee Payment\" / \"M-Pesa Payment\" and follow the prompts."
    )

    for tmpl in templates:
        tmpl.salutation_template = salutation
        tmpl.subject_template = subject
        tmpl.body_template = body
        tmpl.terms_and_conditions = terms
        tmpl.fee_schedule_instructions = fee
        tmpl.save(update_fields=[
            "salutation_template",
            "subject_template",
            "body_template",
            "terms_and_conditions",
            "fee_schedule_instructions",
        ])

class Migration(migrations.Migration):
    dependencies = [("university", "0046_bold_important_sections")]
    operations = [migrations.RunPython(revert_bold_sections, migrations.RunPython.noop)]
