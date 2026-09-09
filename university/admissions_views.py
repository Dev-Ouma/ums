from university.document_views import present_pdf
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation

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
from university.settings_services import get_setting


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
        program = get_object_or_404(Program, pk=program_id)

        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        dob = request.POST.get("date_of_birth", "").strip()
        gender = request.POST.get("gender", "MALE")
        national_id = request.POST.get("national_id", "").strip()
        address = request.POST.get("address", "").strip()

        secondary_school = request.POST.get("secondary_school", "").strip()
        kcse_index = request.POST.get("kcse_index_number", "").strip()
        kcse_grade = request.POST.get("kcse_mean_grade", "C+").strip()
        kcse_year = request.POST.get("kcse_year", "2025").strip()

        if not (first_name and last_name and email and phone and dob and national_id):
            messages.error(request, "Please fill in all mandatory personal details.")
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
            date_of_birth=dob,
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
        return redirect("university:pay_application_fee", pk=application.pk)

    return render(request, "admissions/apply.html", {
        "programs": programs,
        "intake": active_intake,
        "custom_fields": custom_fields,
    })



def pay_application_fee(request, pk):
    """Public: pay the non-refundable application processing fee before the
    application is queued for staff review."""
    application = get_object_or_404(Application, pk=pk)
    fee_amount = get_setting("application_fee_default", default=Decimal("1000.00"))

    if application.fee_paid:
        messages.info(request, "The application fee has already been paid for this application.")
        return redirect(f"/admissions/status/?ref={application.application_number}")

    if request.method == "POST":
        method = request.POST.get("method", ApplicationFeePayment.Method.MPESA)
        reference = request.POST.get("reference", "").strip()
        if not reference:
            messages.error(request, "Please enter the transaction reference number from your payment channel.")
        else:
            # No real payment gateway is integrated; the channel confirmation is
            # simulated here, matching how other payments are recorded in this system.
            ApplicationFeePayment.objects.create(
                application=application, amount=fee_amount, method=method, reference=reference,
                status=ApplicationFeePayment.Status.CONFIRMED, confirmed_at=timezone.now(),
            )
            messages.success(request, "Payment confirmed! Your application is now queued for processing.")
            return redirect(f"/admissions/status/?ref={application.application_number}")

    return render(request, "admissions/pay_fee.html", {
        "application": application, "fee_amount": fee_amount, "methods": ApplicationFeePayment.Method.choices,
    })


def application_status(request):
    """Public portal: Check application decision and download admission letter."""
    ref = request.GET.get("ref", "").strip()
    national_id = request.GET.get("national_id", "").strip()
    application = None
    searched = False

    if ref or national_id:
        searched = True
        qs = Application.objects.select_related("program", "intake", "student")
        if ref and national_id:
            application = qs.filter(application_number__iexact=ref, national_id__iexact=national_id).first()
        elif ref:
            application = qs.filter(application_number__iexact=ref).first()
        elif national_id:
            application = qs.filter(national_id__iexact=national_id).first()

    return render(request, "admissions/status.html", {
        "application": application,
        "searched": searched,
        "ref": ref,
        "national_id": national_id,
    })


def download_admission_letter(request, pk):
    """Download official PDF admission offer letter for accepted applicants."""
    app = get_object_or_404(Application.objects.select_related("program", "intake", "student"), pk=pk)
    if app.status not in [Application.Status.ACCEPTED, Application.Status.ENROLLED]:
        messages.warning(request, "Admission letter is available only for accepted applicants.")
        return redirect(f"/admissions/status/?ref={app.application_number}")

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

        app.review_notes = review_notes
        app.reviewed_by = request.user
        app.reviewed_at = timezone.now()

        if reporting_date:
            app.reporting_date = reporting_date

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
            except Exception as e:
                pass
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
    """One-click matriculation: Convert accepted applicant into enrolled StudentProfile + User."""
    app = get_object_or_404(Application, pk=pk)
    if app.status != Application.Status.ACCEPTED and not app.student:
        messages.error(request, "Only accepted applicants can be matriculated.")
        return redirect("university:admin_admission_detail", pk=app.pk)

    student_profile, user, default_password = matriculate_applicant(app, created_by=request.user)
    messages.success(
        request,
        f"Matriculation complete! Created Student profile for {student_profile.roll_no} (User: {user.username}, Password: {default_password})."
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

        if name and start_date and end_date and academic_year:
            Intake.objects.create(
                name=name,
                academic_year=academic_year,
                start_date=start_date,
                end_date=end_date,
                is_active=is_active,
            )
            messages.success(request, f"Intake '{name}' created successfully.")
            return redirect("university:admin_intakes")
        else:
            messages.error(request, "Please fill in all intake fields.")

    return render(request, "admissions/admin_intakes.html", {
        "intakes": intakes,
        "academic_years": academic_years,
        "current_year": current_year,
    })
