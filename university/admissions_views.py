from university.document_views import present_pdf
from datetime import date, timedelta
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation
import re

from university.decorators import role_required
from accounts.models import Role
from university.admissions_services import (
    assign_admitted_reg_no,
    generate_admission_letter_pdf,
    generate_application_number,
    matriculate_applicant,
)
from university.models import (
    AcademicYear, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue,
    ApplicationFeePayment, Intake, Program
)
from university.upload_security import validate_uploaded_file
from university.settings_services import get_setting

logger = logging.getLogger(__name__)


_APPLICATION_ACCESS_SALT = "ums.application-access"
_APPLICATION_ACCESS_MAX_AGE = 24 * 60 * 60


def application_access_token(application):
    """Create a short-lived, signed bearer token for the applicant workflow."""
    return signing.dumps(
        {"application_id": application.pk}, salt=_APPLICATION_ACCESS_SALT)


def application_from_access_token(raw_token):
    if not raw_token:
        return None
    try:
        payload = signing.loads(
            raw_token, salt=_APPLICATION_ACCESS_SALT,
            max_age=_APPLICATION_ACCESS_MAX_AGE)
        return Application.objects.filter(pk=payload.get("application_id")).first()
    except (signing.BadSignature, TypeError, ValueError):
        return None


# ==============================================================================
# PUBLIC ADMISSIONS VIEWS
# ==============================================================================

def apply(request):
    """Public portal: Prospective students apply for undergraduate/diploma programmes."""
    programs = Program.objects.all().select_related("department").order_by("name")
    active_intake = Intake.objects.filter(is_active=True).order_by("-start_date").first()
    if not active_intake:
        # Create a default active intake if none exists yet
        active_ay = get_current_academic_year()
        active_intake = Intake.objects.create(
            name="September 2026 Regular Intake",
            academic_year=active_ay,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=90),
            is_active=True,
        )

    custom_fields = ApplicationCustomField.objects.filter(is_active=True).order_by("display_order")

    if request.method == "POST":
        program_id = request.POST.get("program")
        program = Program.objects.filter(pk=program_id).select_related("department").first()

        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        dob = request.POST.get("date_of_birth", "").strip()
        gender = request.POST.get("gender", "MALE")
        national_id = request.POST.get("national_id", "").strip()
        address = request.POST.get("address", "").strip()

        errors = []
        if program is None:
            errors.append("Please select a valid programme.")
        try:
            parsed_dob = date.fromisoformat(dob)
            if parsed_dob > timezone.now().date():
                errors.append("Date of birth cannot be in the future.")
        except ValueError:
            parsed_dob = None
            errors.append("Enter a valid date of birth.")
        try:
            from django.forms import EmailField
            EmailField().clean(email)
        except ValidationError:
            errors.append("Enter a valid email address.")
        if gender not in {choice[0] for choice in Application._meta.get_field("gender").choices}:
            errors.append("Please select a valid gender.")

        secondary_school = request.POST.get("secondary_school", "").strip()
        kcse_index = request.POST.get("kcse_index_number", "").strip()
        kcse_grade = request.POST.get("kcse_mean_grade", "C+").strip()
        kcse_year = request.POST.get("kcse_year", "2025").strip()

        if not (first_name and last_name and email and phone and dob and national_id):
            errors.insert(0, "Please fill in all mandatory personal details.")
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, "admissions/apply.html", {
                "programs": programs,
                "intake": active_intake,
                "custom_fields": custom_fields,
                "data": request.POST,
            })

        app_num = generate_application_number(active_intake)
        application = Application.objects.create(
            application_number=app_num,
            intake=active_intake,
            program=program,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            date_of_birth=parsed_dob,
            gender=gender,
            national_id=national_id,
            address=address,
            secondary_school=secondary_school,
            kcse_index_number=kcse_index,
            kcse_mean_grade=kcse_grade,
            kcse_year=int(kcse_year) if kcse_year.isdigit() else 2025,
            status=Application.Status.SUBMITTED,
        )

        # Save Custom Field Values
        for cf in custom_fields:
            cf_val = request.POST.get(f"custom_{cf.name}", "").strip()
            if cf_val:
                ApplicationCustomFieldValue.objects.create(
                    application=application,
                    field=cf,
                    value=cf_val
                )

        # Process Application Attachments
        file_mappings = [
            ("kcse_document", ApplicationAttachment.DocType.KCSE_CERTIFICATE, "KCSE Result Slip / Certificate"),
            ("id_document", ApplicationAttachment.DocType.NATIONAL_ID, "National ID / Birth Certificate / Passport"),
            ("passport_photo", ApplicationAttachment.DocType.PASSPORT_PHOTO, "Passport Size Photograph"),
            ("other_document", ApplicationAttachment.DocType.OTHER, "Other Supporting Document"),
        ]

        for input_name, doc_type, display_title in file_mappings:
            uploaded = request.FILES.get(input_name)
            if uploaded:
                try:
                    validate_uploaded_file(
                        uploaded,
                        extensions={".pdf", ".png", ".jpg", ".jpeg"},
                        mime_types={"application/pdf", "image/png", "image/jpeg"},
                    )
                except ValidationError as error:
                    messages.error(request, f"{display_title}: {error.message if hasattr(error, 'message') else error}")
                    continue
                ApplicationAttachment.objects.create(
                    application=application,
                    document_type=doc_type,
                    name=display_title,
                    file=uploaded,
                    file_name=uploaded.name,
                    file_size=uploaded.size,
                    mime_type=uploaded.content_type or "application/octet-stream",
                    verification_status=ApplicationAttachment.VerificationStatus.PENDING,
                    is_visible_to_student=True,
                )

        messages.success(
            request,
            f"Application submitted successfully! Your application reference number is {application.application_number}. "
            f"Please pay the application fee to complete your submission."
        )
        return redirect(
            f"{reverse('university:pay_application_fee', args=[application.pk])}"
            f"?access={application_access_token(application)}")

    return render(request, "admissions/apply.html", {
        "programs": programs,
        "intake": active_intake,
        "custom_fields": custom_fields,
    })



def pay_application_fee(request, pk):
    """Public: pay the non-refundable application processing fee before the
    application is queued for staff review."""
    application = get_object_or_404(Application, pk=pk)
    access_token = request.GET.get("access") or request.POST.get("access")
    authorized_application = application_from_access_token(access_token)
    if not authorized_application or authorized_application.pk != application.pk:
        return HttpResponse("Application access token required.", status=403)
    fee_amount = get_setting("application_fee_default", default=Decimal("1000.00"))

    if application.fee_paid:
        messages.info(request, "The application fee has already been paid for this application.")
        return redirect(
            f"{reverse('university:admissions_status')}?access={application_access_token(application)}")

    if request.method == "POST":
        method = request.POST.get("method", ApplicationFeePayment.Method.MPESA)
        reference = request.POST.get("reference", "").strip()
        if not reference:
            messages.error(request, "Please enter the transaction reference number from your payment channel.")
        elif method not in ApplicationFeePayment.Method.values:
            messages.error(request, "Please select a valid payment method.")
        elif not re.fullmatch(r"[A-Za-z0-9._/-]{3,60}", reference):
            messages.error(request, "Enter a valid payment reference using 3 to 60 letters, numbers, or - _ . / characters.")
        elif ApplicationFeePayment.objects.filter(reference__iexact=reference).exists():
            messages.error(request, "That payment reference has already been recorded. Check the reference or contact Admissions.")
        else:
            # No real payment gateway is integrated; the channel confirmation is
            # simulated here, matching how other payments are recorded in this system.
            ApplicationFeePayment.objects.create(
                application=application, amount=fee_amount, method=method, reference=reference,
                status=ApplicationFeePayment.Status.CONFIRMED, confirmed_at=timezone.now(),
            )
            messages.success(request, "Payment confirmed! Your application is now queued for processing.")
            return redirect(
                f"{reverse('university:admissions_status')}"
                f"?access={application_access_token(application)}")

    return render(request, "admissions/pay_fee.html", {
        "application": application, "fee_amount": fee_amount,
        "methods": ApplicationFeePayment.Method.choices,
        "application_access_token": access_token,
    })


def application_status(request):
    """Public portal: Check application decision and download admission letter."""
    ref = request.GET.get("ref", "").strip()
    national_id = request.GET.get("national_id", "").strip()
    access_token = request.GET.get("access", "").strip()
    application = None
    searched = False

    if access_token:
        application = application_from_access_token(access_token)
        searched = True
    elif ref or national_id:
        searched = True
        qs = Application.objects.select_related("program", "intake", "student")
        # Both independent identifiers are required. A reference number alone
        # is not an authorization factor because it is routinely printed on
        # receipts, emails, and public-facing application correspondence.
        if ref and national_id:
            application = qs.filter(
                application_number__iexact=ref, national_id__iexact=national_id
            ).first()

    return render(request, "admissions/status.html", {
        "application": application,
        "searched": searched,
        "ref": ref,
        "national_id": national_id,
        "application_access_token": application_access_token(application) if application else "",
    })


def download_admission_letter(request, pk):
    """Download official PDF admission offer letter for accepted applicants."""
    app = get_object_or_404(Application.objects.select_related("program", "intake", "student"), pk=pk)
    access_token = request.GET.get("access", "")
    if not application_from_access_token(access_token) == app:
        return HttpResponse("Application access token required.", status=403)
    if app.status not in [Application.Status.ACCEPTED, Application.Status.ENROLLED]:
        messages.warning(request, "Admission letter is available only for accepted applicants.")
        return redirect(
            f"{reverse('university:admissions_status')}?access={application_access_token(app)}")

    from university.admission_document_services import generate_admission_document, build_admission_letter_pdf_bytes
    doc = app.active_admission_document
    if not doc:
        user = request.user if request.user.is_authenticated else None
        doc = generate_admission_document(app, user=user, reason="Generated on public download request")

    if not doc.pdf_file:
        pdf_data = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_data = doc.pdf_file.read()
        except Exception:
            pdf_data = build_admission_letter_pdf_bytes(doc)

    response = HttpResponse(pdf_data, content_type="application/pdf")
    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return present_pdf(request, response, title=f"Admission Letter - {doc.document_reference}")



# ==============================================================================
# ADMIN ADMISSIONS MANAGEMENT VIEWS
# ==============================================================================

@role_required(Role.ADMIN)
def admin_admissions_list(request):
    """Admin: Overview and filtering of all prospective student applications."""
    qs = Application.objects.select_related("program", "intake", "student").order_by("-created_at")

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            application_number__icontains=q
        ) | qs.filter(
            first_name__icontains=q
        ) | qs.filter(
            last_name__icontains=q
        ) | qs.filter(
            national_id__icontains=q
        ) | qs.filter(
            email__icontains=q
        )

    # Filter status
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    # Filter program
    program_id = request.GET.get("program", "").strip()
    if program_id:
        qs = qs.filter(program_id=program_id)

    # Filter intake
    intake_id = request.GET.get("intake", "").strip()
    if intake_id:
        qs = qs.filter(intake_id=intake_id)

    # Stats
    all_apps = Application.objects.all()
    stats = {
        "total": all_apps.count(),
        "submitted": all_apps.filter(status=Application.Status.SUBMITTED).count(),
        "under_review": all_apps.filter(status=Application.Status.UNDER_REVIEW).count(),
        "accepted": all_apps.filter(status=Application.Status.ACCEPTED).count(),
        "enrolled": all_apps.filter(status=Application.Status.ENROLLED).count(),
        "rejected": all_apps.filter(status=Application.Status.REJECTED).count(),
    }

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "admissions/admin_list.html", {
        "page_obj": page_obj,
        "stats": stats,
        "programs": Program.objects.all().order_by("name"),
        "intakes": Intake.objects.all().order_by("-start_date"),
        "statuses": Application.Status.choices,
        "selected_status": status_filter,
        "selected_program": program_id,
        "selected_intake": intake_id,
        "q": q,
    })


@role_required(Role.ADMIN)
def admin_admission_detail(request, pk):
    """Admin: Review individual application, accept/reject, or edit decision."""
    app = get_object_or_404(Application.objects.select_related("program", "intake", "student", "reviewed_by"), pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        review_notes = request.POST.get("review_notes", "").strip()
        reporting_date = request.POST.get("reporting_date", "").strip()

        if action not in {"under_review", "accept", "reject"}:
            messages.error(request, "Select a valid admissions review action.")
            return redirect("university:admin_admission_detail", pk=app.pk)

        app.review_notes = review_notes
        app.reviewed_by = request.user
        app.reviewed_at = timezone.now()

        if reporting_date:
            try:
                app.reporting_date = date.fromisoformat(reporting_date)
            except ValueError:
                messages.error(request, "Enter a valid reporting date.")
                return redirect("university:admin_admission_detail", pk=app.pk)

        if action in ("under_review", "accept") and not app.fee_paid:
            messages.error(request, "The application fee has not been paid yet. "
                                    "Processing cannot proceed until payment is confirmed.")
            return redirect("university:admin_admission_detail", pk=app.pk)

        if action == "under_review":
            app.status = Application.Status.UNDER_REVIEW
            messages.info(request, f"Application {app.application_number} marked Under Review.")

        elif action == "accept":
            app.status = Application.Status.ACCEPTED
            if not app.admitted_reg_no:
                app.admitted_reg_no = assign_admitted_reg_no(app)
            app.save()
            from university.admission_document_services import generate_admission_document
            try:
                generate_admission_document(app, user=request.user, reason="Generated upon application acceptance")
            except Exception:
                logger.exception("Admission letter generation failed for application %s", app.pk)
                messages.warning(
                    request,
                    f"Applicant accepted and assigned Reg No: {app.admitted_reg_no}, "
                    "but the admission letter could not be generated. Review the document queue and retry.",
                )
            else:
                messages.success(request, f"Applicant accepted! Assigned Reg No: {app.admitted_reg_no}. Admission letter generated.")
            return redirect("university:admin_admission_detail", pk=app.pk)

        elif action == "reject":
            app.status = Application.Status.REJECTED
            messages.warning(request, f"Application {app.application_number} has been rejected.")

        app.save()
        return redirect("university:admin_admission_detail", pk=app.pk)


    return render(request, "admissions/admin_detail.html", {
        "app": app,
    })


@role_required(Role.ADMIN)
@require_POST
def admin_admission_matriculate(request, pk):
    """Register and enroll an accepted applicant as a StudentProfile + User."""
    app = get_object_or_404(Application, pk=pk)
    if app.status == Application.Status.ENROLLED and app.student:
        messages.info(request, "This applicant has already been registered and enrolled.")
        return redirect("university:student_detail", pk=app.student.pk)
    if app.status != Application.Status.ACCEPTED:
        messages.error(request, "Only accepted applicants can be registered and enrolled.")
        return redirect("university:admin_admission_detail", pk=app.pk)

    student_profile, user, _ = matriculate_applicant(app, created_by=request.user)
    messages.success(
        request,
        f"Student registration and enrollment completed. Student profile {student_profile.roll_no} created with the "
        f"username '{user.username}'. An activation link has been emailed so the student sets "
        f"their own password; resend it from User Management if it does not arrive."
    )
    return redirect("university:student_detail", pk=student_profile.pk)


@role_required(Role.ADMIN)
def admin_intakes(request):
    """Admin: Manage university academic intake cycles."""
    intakes = Intake.objects.select_related("academic_year").order_by("-start_date")
    academic_years = AcademicYear.objects.exclude(
        status__in=[AcademicYear.Status.CLOSED, AcademicYear.Status.ARCHIVED]
    ).order_by("-start_date")
    current_year = academic_years.filter(is_current=True).first()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        academic_year_id = request.POST.get("academic_year")
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")
        is_active = request.POST.get("is_active") == "on"

        academic_year = academic_years.filter(pk=academic_year_id).first() if academic_year_id else current_year

        try:
            parsed_start = date.fromisoformat(start_date) if start_date else None
            parsed_end = date.fromisoformat(end_date) if end_date else None
        except ValueError:
            parsed_start = parsed_end = None

        if not name or not parsed_start or not parsed_end or not academic_year:
            messages.error(request, "Enter a name, valid dates, and an academic year for the intake.")
        elif parsed_end < parsed_start:
            messages.error(request, "Intake end date cannot be before its start date.")
        else:
            Intake.objects.create(
                name=name,
                academic_year=academic_year,
                start_date=parsed_start,
                end_date=parsed_end,
                is_active=is_active,
            )
            messages.success(request, f"Intake '{name}' created successfully.")
            return redirect("university:admin_intakes")

    return render(request, "admissions/admin_intakes.html", {
        "intakes": intakes,
        "academic_years": academic_years,
        "current_year": current_year,
    })
