from xml.sax.saxutils import escape
import io
import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from university.document_design import (ReportDocTemplate, document_styles, document_fonts,
    PageNumberCanvas, letterhead, get_branding, finish_worksheet)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle
)

from accounts.models import Role, StudentProfile
from university.models import AcademicTerm, Application, FeeInvoice, FeeStructure, SemesterRegistration, AuditLog
from university.audit_services import log_activity
from university.academic_calendar_services import get_current_academic_year, get_current_semester
from university.student_numbering_services import generate_student_registration_number

User = get_user_model()


def generate_application_number(intake=None):
    """Generate a unique application reference number (e.g. APP-2026-0001)."""
    year = timezone.now().year
    prefix = f"APP-{year}-"
    last_app = Application.objects.filter(application_number__startswith=prefix).order_by("-id").first()
    if last_app:
        try:
            last_seq = int(last_app.application_number.split("-")[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = Application.objects.count() + 1
    else:
        new_seq = 1
    return f"{prefix}{new_seq:04d}"


def assign_admitted_reg_no(application):
    """Assign a formal university registration number using configurable institutional template."""
    return generate_student_registration_number(
        program=application.program,
        intake=application.intake,
    )


def generate_admission_letter_pdf(application):
    """Generate a formal university admission letter as a PDF byte buffer."""
    from university.admission_document_services import (
        generate_admission_document, build_admission_letter_pdf_bytes
    )
    doc = getattr(application, "active_admission_document", None)
    if not doc:
        doc = application.issued_documents.filter(is_current_version=True).first()
    if not doc:
        doc = generate_admission_document(application, reason="Generated via admission letter service")
    return build_admission_letter_pdf_bytes(doc)


@transaction.atomic
def matriculate_applicant(application, created_by=None):
    """
    Convert an ACCEPTED applicant into an official enrolled student.
    - Generates roll_no if not set
    - Creates or fetches User account with Role.STUDENT
    - Creates StudentProfile linked to user & program
    - Assigns the currently active Academic Year/Semester (from Admin Setup, never hard-coded)
      and creates the student's first SemesterRegistration for it
    - Links application.student to StudentProfile
    - Updates application.status to ENROLLED
    - Automatically creates initial FeeInvoice from FeeStructure, tied to the active term
    """
    if application.student:
        return application.student, application.student.user, None

    # Active academic calendar context, per Admin Academic Year/Semester Setup — never hard-coded.
    active_ay = get_current_academic_year()
    active_term = get_current_semester()

    # Determine roll number using configurable numbering engine
    if not application.admitted_reg_no:
        application.admitted_reg_no = assign_admitted_reg_no(application)

    roll_no = application.admitted_reg_no

    # Central identity. Admissions never mints credentials of its own: it asks
    # the user management service for the one account this person will ever have,
    # and that service issues a single-use activation link instead of a password.
    from university.identity_models import UserType
    from university.identity_services import create_user_account, provision_student_account

    username = roll_no.lower().replace("/", ".").replace(" ", "")
    existing_user = User.objects.filter(username=username).first()
    if not existing_user:
        existing_user = User.objects.filter(email=application.email).first()

    if existing_user:
        # Re-matriculation or a pre-existing identity: reuse it, never duplicate it.
        user = existing_user
        if user.role != Role.STUDENT:
            user.role = Role.STUDENT
            user.save(update_fields=["role"])
    else:
        created = create_user_account(
            user_type=UserType.STUDENT,
            first_name=application.first_name,
            last_name=application.last_name,
            email=application.email or "",
            username=username,
            phone=application.phone or "",
            role=Role.STUDENT,
            password_mode="LINK",
            actor=created_by,
            notify=True,
        )
        user = created["user"]

    # Create StudentProfile
    student_profile = StudentProfile.objects.filter(user=user).first()
    if not student_profile:
        student_profile = StudentProfile.objects.create(
            user=user,
            roll_no=roll_no,
            program=application.program,
            address=application.address or "Nairobi, Kenya",
            date_of_birth=application.date_of_birth,
            current_semester=1,
            status=StudentProfile.Status.ACTIVE,
            guardian_name=application.guardian_name or f"Parent of {application.first_name}",
            guardian_relationship=application.guardian_relationship or "Parent",
            guardian_phone=application.guardian_phone or "",
            guardian_email=application.guardian_email or "",
            guardian_address=application.guardian_address or "",
        )
    else:
        # Update existing profile with guardian details if empty
        if application.guardian_name and not student_profile.guardian_name:
            student_profile.guardian_name = application.guardian_name
            student_profile.guardian_relationship = application.guardian_relationship
            student_profile.guardian_phone = application.guardian_phone
            student_profile.guardian_email = application.guardian_email
            student_profile.guardian_address = application.guardian_address
            student_profile.save(update_fields=[
                "guardian_name", "guardian_relationship", "guardian_phone", "guardian_email", "guardian_address"
            ])

    # Institutional email, account status and the student's identity envelope.
    # Idempotent, so re-running matriculation never produces a second identity.
    provision_student_account(student_profile, actor=created_by, notify=False)

    # Initialize the first SemesterRegistration for the active academic term,
    if active_term and active_term.academic_year:
        ay_str = active_term.academic_year.name
    elif active_term:
        ay_str = f"{active_term.start_date.year}/{active_term.end_date.year}"
    elif active_ay:
        ay_str = active_ay.name
    else:
        ay_str = "2026/2027"
    if active_term:
        SemesterRegistration.objects.get_or_create(
            student=student_profile, term=active_term,
            defaults={
                "semester_no": student_profile.current_semester,
                "academic_year": ay_str,
                "status": SemesterRegistration.DRAFT,
            }
        )

    # Link and update application
    application.student = student_profile
    application.status = Application.Status.ENROLLED
    application.reviewed_by = created_by or application.reviewed_by
    application.reviewed_at = timezone.now()
    application.save()

    # Ensure admission document exists and links to student_profile
    from university.admission_document_services import generate_admission_document
    doc = application.issued_documents.filter(is_current_version=True).first()
    if not doc:
        generate_admission_document(application, user=created_by, reason="Auto-generated on matriculation")
    else:
        if not doc.student:
            doc.student = student_profile
            doc.save(update_fields=["student"])

    # Log matriculation in Audit Trail
    log_activity(
        user=created_by,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.STUDENTS,
        entity="StudentProfile",
        entity_id=student_profile.id,
        description=f"Matriculated applicant {application.application_number} as student {student_profile.roll_no} into {student_profile.program.name if student_profile.program else 'General'}.",
        new_state={
            "roll_no": student_profile.roll_no,
            "program": student_profile.program.code if student_profile.program else "",
            "academic_year": ay_str,
            "term": active_term.name if active_term else "",
        }
    )

    # Automatically generate initial semester invoice from fee structure
    fee_struct = FeeStructure.objects.filter(
        program=application.program,
        year_of_study=1,
        semester=1
    ).first()

    billed_amount = fee_struct.total_fee if fee_struct else Decimal("55500.00")
    due_date = timezone.now().date() + timedelta(days=30)
    FeeInvoice.objects.get_or_create(
        student=student_profile,
        title="Year 1 Semester 1 Tuition & Statutory Fees",
        defaults={
            "amount": billed_amount,
            "amount_paid": Decimal("0.00"),
            "issued_on": timezone.now().date(),
            "due_date": due_date,
            "term": active_term,
        }
    )

    # The third value is retained for callers that used to surface a starting
    # password. Matriculation no longer issues one: the student sets their own
    # through the activation link, so there is nothing to hand over.
    return student_profile, user, None
