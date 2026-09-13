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
import json
import re
import os
import uuid

from django.contrib.auth import authenticate, login
from django.db.models import Q

from university.decorators import role_required
from accounts.models import Role
from university.admissions_services import (
    assign_admitted_reg_no,
    generate_admission_letter_pdf,
    generate_application_number,
    matriculate_applicant,
)
from university.applicant_auth_services import (
    register_applicant,
    verify_applicant_otp,
    get_applicant_active_application,
)
from university.admissions_draft_services import (
    get_or_create_applicant_draft,
    update_applicant_draft,
    calculate_completion_percentage,
    serialize_draft_state,
    validate_and_submit_application,
    get_draft_documents_metadata,
)
from university.models import (
    AcademicYear, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue,
    ApplicationFeePayment, AuditLog, Intake, Program
)
from university.audit_services import log_activity
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


import os
import uuid
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import Http404, HttpResponse, JsonResponse
from university.academic_calendar_services import get_current_academic_year


def format_file_size(size_bytes):
    if not size_bytes or size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


@require_POST
def upload_admission_document(request):
    """
    Asynchronously uploads, strictly validates (PDF, PNG, JPG), and saves
    an admission supporting document into the applicant's draft storage and DB attachment.
    """
    doc_type = request.POST.get("document_type", "").strip()
    valid_doc_types = {
        "kcse_document": "KCSE Result Slip / Certificate",
        "id_document": "National ID / Birth Certificate / Passport",
        "passport_photo": "Passport Size Photograph",
        "other_document": "Other Supporting Document",
    }
    if doc_type not in valid_doc_types:
        return JsonResponse({"success": False, "error": "Invalid document type."}, status=400)

    uploaded = request.FILES.get("file") or request.FILES.get(doc_type)
    if not uploaded:
        return JsonResponse({"success": False, "error": "No file was selected for upload."}, status=400)

    # Strict Validation: PDF, PNG, JPG, Max 5MB
    MAX_SIZE = 5 * 1024 * 1024
    ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
    ALLOWED_MIMES = {"application/pdf", "image/png", "image/jpeg", "image/pjpeg"}

    try:
        validate_uploaded_file(
            uploaded,
            extensions=ALLOWED_EXTS,
            mime_types=ALLOWED_MIMES,
            max_bytes=MAX_SIZE,
        )
    except ValidationError as err:
        err_msg = err.message if hasattr(err, "message") else str(err)
        return JsonResponse({"success": False, "error": err_msg}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Validation failed: {str(e)}"}, status=400)

    # Ensure session exists
    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key

    # Get active draft Application
    draft = get_or_create_applicant_draft(request)

    # Store file in draft storage
    ext = os.path.splitext(uploaded.name)[1].lower()
    safe_name = f"{doc_type}_{uuid.uuid4().hex[:10]}{ext}"
    sub_dir = f"applications/draft_attachments/{session_key}"
    stored_path = default_storage.save(f"{sub_dir}/{safe_name}", uploaded)

    draft_docs = request.session.get("draft_application_documents", {})
    # Remove previous draft file if replaced
    if doc_type in draft_docs and draft_docs[doc_type].get("file_path"):
        try:
            prev_path = draft_docs[doc_type]["file_path"]
            if default_storage.exists(prev_path):
                default_storage.delete(prev_path)
        except Exception:
            pass

    draft_docs[doc_type] = {
        "file_path": stored_path,
        "original_name": uploaded.name,
        "file_size": uploaded.size,
        "file_size_formatted": format_file_size(uploaded.size),
        "mime_type": uploaded.content_type or "application/octet-stream",
        "uploaded_at": timezone.now().isoformat(),
        "display_name": valid_doc_types[doc_type],
    }
    request.session["draft_application_documents"] = draft_docs
    request.session.modified = True

    # Persist directly to draft ApplicationAttachment
    doc_type_mapping = {
        "kcse_document": ApplicationAttachment.DocType.KCSE_CERTIFICATE,
        "id_document": ApplicationAttachment.DocType.NATIONAL_ID,
        "passport_photo": ApplicationAttachment.DocType.PASSPORT_PHOTO,
        "other_document": ApplicationAttachment.DocType.OTHER,
    }
    model_doc_type = doc_type_mapping.get(doc_type)
    if model_doc_type and draft:
        existing_att = draft.attachments.filter(document_type=model_doc_type).first()
        uploaded.seek(0)
        if existing_att:
            existing_att.file = uploaded
            existing_att.file_name = uploaded.name
            existing_att.file_size = uploaded.size
            existing_att.mime_type = uploaded.content_type or "application/octet-stream"
            existing_att.save()
        else:
            ApplicationAttachment.objects.create(
                application=draft,
                document_type=model_doc_type,
                name=valid_doc_types[doc_type],
                file=uploaded,
                file_name=uploaded.name,
                file_size=uploaded.size,
                mime_type=uploaded.content_type or "application/octet-stream",
                verification_status=ApplicationAttachment.VerificationStatus.PENDING,
                is_visible_to_student=True,
            )
        draft.draft_version += 1
        draft.save(update_fields=["draft_version", "updated_at"])

    return JsonResponse({
        "success": True,
        "message": f"{valid_doc_types[doc_type]} uploaded and verified successfully.",
        "document_type": doc_type,
        "file_name": uploaded.name,
        "file_size": uploaded.size,
        "file_size_formatted": format_file_size(uploaded.size),
        "version": draft.draft_version if draft else 1,
        "completion_percentage": calculate_completion_percentage(draft, request) if draft else 0,
    })


@require_POST
def remove_admission_document(request):
    """Removes a previously saved draft document from the session, storage, and draft attachments."""
    doc_type = request.POST.get("document_type", "").strip()
    draft_docs = request.session.get("draft_application_documents", {})
    if doc_type in draft_docs:
        stored_path = draft_docs[doc_type].get("file_path")
        if stored_path:
            try:
                if default_storage.exists(stored_path):
                    default_storage.delete(stored_path)
            except Exception:
                pass
        del draft_docs[doc_type]
        request.session["draft_application_documents"] = draft_docs
        request.session.modified = True

    draft = get_or_create_applicant_draft(request)
    doc_type_mapping = {
        "kcse_document": ApplicationAttachment.DocType.KCSE_CERTIFICATE,
        "id_document": ApplicationAttachment.DocType.NATIONAL_ID,
        "passport_photo": ApplicationAttachment.DocType.PASSPORT_PHOTO,
        "other_document": ApplicationAttachment.DocType.OTHER,
    }
    model_doc_type = doc_type_mapping.get(doc_type)
    if model_doc_type and draft:
        draft.attachments.filter(document_type=model_doc_type).delete()
        draft.draft_version += 1
        draft.save(update_fields=["draft_version", "updated_at"])

    return JsonResponse({
        "success": True,
        "message": "Document removed successfully.",
        "document_type": doc_type,
        "version": draft.draft_version if draft else 1,
        "completion_percentage": calculate_completion_percentage(draft, request) if draft else 0,
    })


def api_get_draft(request):
    """
    GET /api/admissions/draft/ or /admissions/api/draft/
    Returns the active application draft, saved step, completion percentage,
    and metadata for already uploaded documents.
    """
    draft = get_or_create_applicant_draft(request)
    state = serialize_draft_state(draft, request=request)
    return JsonResponse({"success": True, "draft": state})


@require_POST
def api_save_draft(request):
    """
    PATCH/POST /api/admissions/draft/ or /admissions/api/draft/
    Accepts partial field updates with optimistic locking and tenant isolation.
    """
    draft = get_or_create_applicant_draft(request)

    payload = {}
    step = None
    client_version = None

    if request.content_type == "application/json" or (request.body and not request.POST):
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            if isinstance(body_data, dict):
                step = body_data.get("step")
                client_version = body_data.get("version")
                payload = body_data.get("data") if "data" in body_data else body_data
        except (ValueError, UnicodeDecodeError):
            payload = request.POST.dict()
    else:
        payload = request.POST.dict()
        step = request.POST.get("step")
        client_version = request.POST.get("version")

    if isinstance(payload, dict):
        if "step" in payload and step is None:
            step = payload.pop("step", None)
        if "version" in payload and client_version is None:
            client_version = payload.pop("version", None)

    success, res = update_applicant_draft(
        application=draft,
        data=payload if isinstance(payload, dict) else {},
        step=step,
        client_version=client_version,
        request=request,
    )

    if not success:
        status_code = 409 if res.get("conflict") else 400
        return JsonResponse(res, status=status_code)

    return JsonResponse(res)


@require_POST
def api_submit_application(request):
    """
    POST /api/admissions/submit/ or /admissions/api/submit/
    Runs strict validation across all sections and transitions status to READY_FOR_PAYMENT.
    """
    draft = get_or_create_applicant_draft(request)

    payload = {}
    if request.content_type == "application/json" or (request.body and not request.POST):
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            if isinstance(body_data, dict):
                payload = body_data.get("data", body_data)
        except Exception:
            payload = request.POST.dict()
    else:
        payload = request.POST.dict()

    success, res = validate_and_submit_application(
        application=draft,
        payload=payload if isinstance(payload, dict) else {},
        request=request,
    )

    if not success:
        return JsonResponse(res, status=400)

    return JsonResponse(res)


def applicant_register(request):
    """Applicant account creation / express interest view."""
    if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
        return redirect("university:applicant_dashboard")

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "").strip()

        try:
            user, otp = register_applicant(request, first_name, last_name, email, phone, password)
            messages.success(request, f"Verification code sent to {email}. Please enter the 6-digit OTP below.")
            return redirect(f"{reverse('university:applicant_verify_otp')}?email={email}")
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))

    return render(request, "admissions/applicant_register.html")


def applicant_verify_otp(request):
    """OTP Verification view for newly registered applicants."""
    email = request.GET.get("email", "").strip().lower() or request.POST.get("email", "").strip().lower()
    pending = request.session.get("applicant_pending_verification", {})

    if request.method == "POST":
        otp_code = request.POST.get("otp_code", "").strip()
        try:
            user = verify_applicant_otp(request, email, otp_code)
            messages.success(request, f"Welcome {user.first_name}! Your applicant account is verified.")
            return redirect("university:applicant_dashboard")
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))

    demo_otp = pending.get("otp_code") if pending.get("email") == email else ""

    return render(request, "admissions/applicant_verify_otp.html", {
        "email": email,
        "demo_otp": demo_otp,
    })


def applicant_login(request):
    """Applicant sign in."""
    if request.user.is_authenticated:
        if getattr(request.user, "role", "") == Role.APPLICANT:
            return redirect("university:applicant_dashboard")
        return redirect("university:dashboard")

    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()

        user = authenticate(request, username=identifier, password=password)
        if not user and "@" in identifier:
            from django.contrib.auth import get_user_model
            U = get_user_model()
            u_obj = U.objects.filter(email__iexact=identifier).first()
            if u_obj:
                user = authenticate(request, username=u_obj.username, password=password)

        if user:
            login(request, user)
            messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")
            if user.role == Role.APPLICANT:
                return redirect("university:applicant_dashboard")
            return redirect("university:dashboard")
        else:
            messages.error(request, "Invalid email/username or password. Please try again.")

    return render(request, "admissions/applicant_login.html")


def applicant_dashboard(request):
    """Applicant Dashboard showing live status, checklist, fee payment, and final submission."""
    if not request.user.is_authenticated:
        return redirect("university:applicant_login")

    applications = Application.objects.filter(
        Q(applicant_user=request.user) | Q(email__iexact=request.user.email)
    ).select_related("program", "intake").prefetch_related("fee_payments", "attachments").order_by("-created_at")

    active_app = applications.first()
    fee_amount = get_setting("application_fee_default", default=Decimal("1000.00"))

    return render(request, "admissions/applicant_dashboard.html", {
        "applicant": request.user,
        "applications": applications,
        "application": active_app,
        "fee_amount": fee_amount,
        "application_access_token": application_access_token(active_app) if active_app else "",
    })


def apply(request):
    """
    Prospective student application form with real-time draft auto-save & state recovery.
    Hydrates existing draft data on load and delegates strict validation on submission.
    """
    active_intake = Intake.objects.filter(is_active=True).first()
    programs = Program.objects.filter(status=Program.Status.ACTIVE).select_related("department")
    custom_fields = ApplicationCustomField.objects.filter(is_active=True)

    if request.method == "POST":
        user = request.user if request.user.is_authenticated else None
        session_key = request.session.session_key
        draft = None
        if user and getattr(user, "role", "") in (Role.APPLICANT, Role.STUDENT, ""):
            draft = Application.objects.filter(applicant_user=user, status__in=[Application.Status.DRAFT, Application.Status.IN_PROGRESS]).first()
        elif session_key:
            draft = Application.objects.filter(session_key=session_key, status__in=[Application.Status.DRAFT, Application.Status.IN_PROGRESS]).first()

        created_in_this_request = False
        if not draft:
            draft = get_or_create_applicant_draft(request)
            created_in_this_request = True

        success, res = validate_and_submit_application(
            application=draft,
            payload=request.POST.dict(),
            request=request,
        )
        if success:
            messages.success(
                request,
                f"Application saved successfully! Reference Number: {draft.application_number}. "
                f"Please proceed to pay the application processing fee."
            )
            return redirect(res["redirect_url"])
        else:
            if created_in_this_request:
                draft.delete()
                draft = None
            for error in res.get("errors", []):
                messages.error(request, error)
            
            session_docs = request.session.get("draft_application_documents", {})
            return render(request, "admissions/apply.html", {
                "programs": programs,
                "intake": active_intake,
                "custom_fields": custom_fields,
                "draft_application": draft,
                "draft_state_json": json.dumps({"fields": request.POST.dict(), "documents": session_docs}),
                "draft_documents": session_docs,
                "guardian_relationships": Application.GUARDIAN_RELATIONSHIPS,
                "data": request.POST.dict(),
                "draft_step": 1,
                "completion_percentage": 0,
                "draft_version": 1,
            })

    draft = get_or_create_applicant_draft(request)
    state = serialize_draft_state(draft, request=request)

    return render(request, "admissions/apply.html", {
        "programs": programs,
        "intake": active_intake,
        "custom_fields": custom_fields,
        "draft_application": draft,
        "draft_state_json": json.dumps(state),
        "draft_documents": state["documents"],
        "guardian_relationships": Application.GUARDIAN_RELATIONSHIPS,
        "data": state["fields"],
        "draft_step": draft.draft_step,
        "completion_percentage": state["completion_percentage"],
        "draft_version": draft.draft_version,
    })


def pay_application_fee(request, pk):
    """Pay the application processing fee with server-side validation and receipt generation."""
    application = get_object_or_404(Application, pk=pk)
    access_token = request.GET.get("access") or request.POST.get("access")
    authorized_application = application_from_access_token(access_token)
    token_valid = bool(authorized_application and authorized_application.pk == application.pk)
    is_owner = request.user.is_authenticated and (
        application.applicant_user_id == request.user.id or application.email.lower() == request.user.email.lower()
    )
    is_staff = request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")
    )
    if not (token_valid or is_owner or is_staff):
        messages.error(request, "Please log in to access this application payment.")
        return redirect("university:applicant_login")

    fee_amount = get_setting("application_fee_default", default=Decimal("1000.00"))

    if application.fee_paid:
        messages.info(request, "The application fee has already been paid and verified.")
        if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
            return redirect("university:applicant_dashboard")
        return redirect(f"{reverse('university:admissions_status')}?access={application_access_token(application)}")

    if request.method == "POST":
        method = request.POST.get("method", ApplicationFeePayment.Method.MPESA)
        reference = request.POST.get("reference", "").strip()

        if not reference:
            messages.error(request, "Please enter your payment reference / transaction code.")
        elif method not in ApplicationFeePayment.Method.values:
            messages.error(request, "Please select a valid payment method.")
        elif not re.fullmatch(r"[A-Za-z0-9._/-]{3,60}", reference):
            messages.error(request, "Enter a valid payment reference using 3 to 60 alphanumeric characters.")
        elif ApplicationFeePayment.objects.filter(reference__iexact=reference).exists():
            messages.error(request, "That payment reference has already been recorded. Check the reference or contact Admissions.")
        else:
            receipt_no = f"PAY-{timezone.now().year}-{ApplicationFeePayment.objects.count() + 10001:06d}"
            payment = ApplicationFeePayment.objects.create(
                application=application,
                applicant_user=request.user if request.user.is_authenticated else application.applicant_user,
                receipt_number=receipt_no,
                amount=fee_amount,
                method=method,
                reference=reference,
                status=ApplicationFeePayment.Status.CONFIRMED,
                confirmed_at=timezone.now(),
            )
            # Update status to READY_FOR_SUBMISSION
            application.status = Application.Status.READY_FOR_SUBMISSION
            application.save(update_fields=["status"])

            log_activity(
                request=request,
                user=request.user if request.user.is_authenticated else None,
                action=AuditLog.Action.CREATE,
                module=AuditLog.Module.FEES,
                entity="ApplicationFeePayment",
                entity_id=payment.pk,
                description=f"Application fee of KES {fee_amount} paid for application {application.application_number}. Receipt: {receipt_no}",
            )

            messages.success(
                request,
                f"Payment of KES {fee_amount:,.2f} confirmed! Receipt: {receipt_no}. "
                f"Your application is now ready for final submission."
            )
            if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
                return redirect("university:applicant_dashboard")
            return redirect(f"{reverse('university:admissions_status')}?access={application_access_token(application)}")

    return render(request, "admissions/pay_fee.html", {
        "application": application,
        "fee_amount": fee_amount,
        "methods": ApplicationFeePayment.Method.choices,
        "application_access_token": access_token or application_access_token(application),
    })


@require_POST
def submit_application(request, pk):
    """Explicit final submission of an application after payment is confirmed."""
    application = get_object_or_404(Application, pk=pk)
    access_token = request.POST.get("access") or request.GET.get("access")
    authorized_application = application_from_access_token(access_token)
    token_valid = bool(authorized_application and authorized_application.pk == application.pk)
    is_owner = request.user.is_authenticated and (
        application.applicant_user_id == request.user.id or application.email.lower() == request.user.email.lower()
    )
    is_staff = request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")
    )

    if not (token_valid or is_owner or is_staff):
        messages.error(request, "Access denied to submit this application.")
        return redirect("university:applicant_login")

    if not application.fee_paid:
        messages.error(request, "Application fee must be paid and verified before final submission.")
        return redirect(f"{reverse('university:pay_application_fee', args=[application.pk])}?access={application_access_token(application)}")

    if application.status in [Application.Status.SUBMITTED, Application.Status.UNDER_REVIEW, Application.Status.ACCEPTED, Application.Status.ENROLLED]:
        messages.info(request, "Your application has already been submitted.")
    else:
        application.status = Application.Status.SUBMITTED
        application.save(update_fields=["status"])
        log_activity(
            request=request,
            user=request.user if request.user.is_authenticated else None,
            action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.ADMISSIONS,
            entity="Application",
            entity_id=application.pk,
            description=f"Application {application.application_number} explicitly submitted for review.",
        )
        messages.success(request, f"Application {application.application_number} submitted successfully! Your application is now queued for admissions review.")

    if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
        return redirect("university:applicant_dashboard")
    return redirect(f"{reverse('university:admissions_status')}?access={application_access_token(application)}")


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


def view_admission_letter(request, pk):
    """View official PDF admission offer letter in interactive document viewer."""
    return _serve_admission_letter(request, pk, as_attachment=False)


def download_admission_letter(request, pk):
    """Download official PDF admission offer letter for accepted applicants."""
    as_attachment = request.GET.get("download") == "1" or request.GET.get("attachment") == "1"
    return _serve_admission_letter(request, pk, as_attachment=as_attachment)


def _serve_admission_letter(request, pk, as_attachment=False):
    app = get_object_or_404(Application.objects.select_related("program", "intake", "student"), pk=pk)

    is_staff = request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")
    )
    is_applicant = (
        request.user.is_authenticated and (
            app.applicant_user == request.user or
            (hasattr(app, "student") and app.student and getattr(app.student, "user", None) == request.user)
        )
    )
    access_token = request.GET.get("access", "")
    token_valid = bool(access_token and application_from_access_token(access_token) == app)

    if not (is_staff or is_applicant or token_valid):
        return HttpResponse("Application access token required.", status=403)
    if app.status not in [Application.Status.ACCEPTED, Application.Status.ENROLLED]:
        messages.warning(request, "Admission letter is available only for accepted applicants.")
        return redirect(
            f"{reverse('university:admissions_status')}?access={application_access_token(app)}")

    from university.admission_document_services import generate_admission_document, build_admission_letter_pdf_bytes
    doc = getattr(app, "active_admission_document", None) or app.issued_documents.filter(is_current_version=True).first()
    if not doc:
        user = request.user if request.user.is_authenticated else None
        doc = generate_admission_document(app, user=user, reason="Generated on applicant letter request")

    if not doc.pdf_file:
        pdf_data = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_data = doc.pdf_file.read()
        except Exception:
            pdf_data = build_admission_letter_pdf_bytes(doc)

    response = HttpResponse(pdf_data, content_type="application/pdf")
    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    if as_attachment:
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return present_pdf(request, response, title=f"Admission Letter - {doc.document_reference}")


@login_required
@require_POST
def applicant_accept_offer(request, pk):
    """Applicant action to confirm acceptance of official admission offer."""
    app = get_object_or_404(
        Application.objects.select_related("program", "intake", "student"),
        pk=pk
    )
    if app.applicant_user != request.user and not (request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")):
        messages.error(request, "Access denied. You do not have permission to accept this admission offer.")
        return redirect("university:applicant_dashboard")

    if app.status != Application.Status.ACCEPTED:
        messages.warning(request, f"Application is currently in '{app.get_status_display()}' state. Offer acceptance is applicable only for Accepted offers.")
        return redirect("university:applicant_dashboard")

    app.review_notes = (app.review_notes or "") + f"\n[Offer Accepted by Applicant on {timezone.now():%Y-%m-%d %H:%M UTC}]"
    app.save(update_fields=["review_notes", "updated_at"])

    log_activity(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ADMISSIONS,
        entity="Application",
        entity_id=app.id,
        description=f"Applicant {app.full_name} ({app.application_number}) accepted admission offer for {app.program.name if app.program else 'Degree Programme'}.",
        new_state={
            "application_number": app.application_number,
            "accepted_at": str(timezone.now()),
            "status": app.status,
        }
    )

    messages.success(
        request,
        f"🎉 Congratulations {app.first_name}! You have formally accepted your admission offer for {app.program.name if app.program else 'your degree programme'}. "
        f"Please download your official Admission Letter and proceed with reporting/registration preparations."
    )
    return redirect("university:applicant_dashboard")



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
