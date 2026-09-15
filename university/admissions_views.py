from university.document_views import present_pdf
from datetime import date, timedelta
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from university.security_decorators import rate_limit
from decimal import Decimal, InvalidOperation
import json
import re
import os
import uuid

from django.contrib.auth import authenticate, login
from django.db.models import Q

from university.decorators import role_required
from accounts.models import Role, FacultyProfile
from university.models import StaffRoleAssignment
from university.admissions_services import (
    assign_admitted_reg_no,
    generate_admission_letter_pdf,
    generate_application_number,
    matriculate_applicant,
)
from university.admission_document_services import (
    SignatureAuthorizationError,
    SignatureRequiredError,
)
from university.applicant_auth_services import (
    register_applicant,
    verify_applicant_otp,
    get_applicant_active_application,
)
from university.admissions_draft_services import (
    get_or_create_applicant_draft,
    get_active_applicant_draft,
    empty_draft_state,
    update_applicant_draft,
    calculate_completion_percentage,
    serialize_draft_state,
    validate_and_submit_application,
    get_draft_documents_metadata,
    get_available_intakes_data,
    get_default_active_intake,
    COUNTRIES_LIST,
    KENYAN_COUNTIES,
)
from university.models import (
    AcademicYear, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue,
    ApplicationFeePayment, AuditLog, Intake, Program, Cohort
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


@rate_limit("app-doc-upload", limit=30, window_seconds=60)
@require_POST
def upload_admission_document(request):
    """
    Asynchronously uploads, strictly validates (PDF, PNG, JPG), and saves
    an admission supporting document into the applicant's draft storage and DB attachment.
    Enforces document immutability once application is submitted.
    """
    if request.user.is_authenticated:
        if Application.objects.filter(
            applicant_user=request.user,
            status__in=[
                Application.Status.SUBMITTED,
                Application.Status.UNDER_REVIEW,
                Application.Status.ACCEPTED,
                Application.Status.ENROLLED,
            ],
        ).exists():
            return JsonResponse({
                "success": False,
                "error": "Application documents cannot be modified after application submission.",
            }, status=403)

    draft = get_or_create_applicant_draft(request)
    if draft and draft.status not in [Application.Status.DRAFT, Application.Status.IN_PROGRESS]:
        return JsonResponse({
            "success": False,
            "error": "Application documents cannot be modified after application submission.",
        }, status=403)

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

    # Store file in draft storage
    original_name = os.path.basename(uploaded.name or "")
    ext = os.path.splitext(original_name)[1].lower()
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
        "original_name": original_name,
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
            existing_att.file.save(safe_name, uploaded, save=False)
            existing_att.file_name = original_name
            existing_att.file_size = uploaded.size
            existing_att.mime_type = uploaded.content_type or "application/octet-stream"
            existing_att.save()
        else:
            attachment = ApplicationAttachment(
                application=draft,
                document_type=model_doc_type,
                name=valid_doc_types[doc_type],
                file_name=original_name,
                file_size=uploaded.size,
                mime_type=uploaded.content_type or "application/octet-stream",
                verification_status=ApplicationAttachment.VerificationStatus.PENDING,
                is_visible_to_student=True,
            )
            attachment.file.save(safe_name, uploaded, save=True)
        draft.draft_version += 1
        draft.save(update_fields=["draft_version", "updated_at"])

    return JsonResponse({
        "success": True,
        "message": f"{valid_doc_types[doc_type]} uploaded and verified successfully.",
        "document_type": doc_type,
        "file_name": original_name,
        "file_size": uploaded.size,
        "file_size_formatted": format_file_size(uploaded.size),
        "version": draft.draft_version if draft else 1,
        "completion_percentage": calculate_completion_percentage(draft, request) if draft else 0,
    })


@rate_limit("app-doc-remove", limit=30, window_seconds=60)
@require_POST
def remove_admission_document(request):
    """Removes a previously saved draft document from the session, storage, and draft attachments."""
    if request.user.is_authenticated:
        if Application.objects.filter(
            applicant_user=request.user,
            status__in=[
                Application.Status.SUBMITTED,
                Application.Status.UNDER_REVIEW,
                Application.Status.ACCEPTED,
                Application.Status.ENROLLED,
            ],
        ).exists():
            return JsonResponse({
                "success": False,
                "error": "Application documents cannot be modified after application submission.",
            }, status=403)

    draft = get_or_create_applicant_draft(request)
    if draft and draft.status not in [Application.Status.DRAFT, Application.Status.IN_PROGRESS]:
        return JsonResponse({
            "success": False,
            "error": "Application documents cannot be modified after application submission.",
        }, status=403)

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


def api_available_intakes(request):
    """
    GET /api/admissions/intakes/available/ or /admissions/api/intakes/available/
    Returns structured list of all available intakes with status flags and active default.
    """
    data = get_available_intakes_data()
    return JsonResponse({"success": True, **data})


def api_locations(request):
    """
    GET /api/admissions/locations/ or /admissions/api/locations/
    Returns structured country and Kenyan county datasets for smart dropdowns.
    """
    return JsonResponse({
        "success": True,
        "countries": COUNTRIES_LIST,
        "counties": KENYAN_COUNTIES,
        "default_country": "Kenya",
        "default_county": "Nairobi",
    })


@require_safe
def api_get_draft(request):
    """
    GET /api/admissions/draft/ or /admissions/api/draft/
    Returns the active application draft, saved step, completion percentage,
    and metadata for already uploaded documents.
    """
    draft = get_active_applicant_draft(request)
    state = serialize_draft_state(draft, request=request) if draft else None
    return JsonResponse({"success": True, "draft": state})


@rate_limit("app-draft-save", limit=60, window_seconds=60)
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
        status_code = 409 if res.get("conflict") else 422
        return JsonResponse(res, status=status_code)

    return JsonResponse(res)


@rate_limit("app-submit-api", limit=10, window_seconds=60)
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
        return JsonResponse(res, status=422)

    return JsonResponse(res)


@rate_limit("applicant-register", limit=10, window_seconds=300)
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


@rate_limit("applicant-verify-otp", limit=10, window_seconds=300)
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

    demo_otp = pending.get("otp_code") if (settings.DEBUG and pending.get("email") == email) else ""

    return render(request, "admissions/applicant_verify_otp.html", {
        "email": email,
        "demo_otp": demo_otp,
    })


@rate_limit("applicant-login", limit=10, window_seconds=300)
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
            from university.identity_services import enforce_concurrent_session_policy
            enforce_concurrent_session_policy(user, keep_session_key=request.session.session_key)
            log_activity(
                request=request,
                user=user,
                action=AuditLog.Action.LOGIN,
                module=AuditLog.Module.AUTH,
                entity="ApplicantUser",
                entity_id=user.pk,
                description=f"Applicant {user.username} signed in to portal.",
            )
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


@rate_limit("admissions-apply", limit=30, window_seconds=60)
def apply(request):
    """
    Prospective student application form with real-time draft auto-save & state recovery.
    Hydrates existing draft data on load and delegates strict validation on submission.
    """
    intakes_data = get_available_intakes_data()
    active_intake = get_default_active_intake()
    programs = Program.objects.filter(status=Program.Status.ACTIVE).select_related("department", "department__school")
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
            post_fields = request.POST.dict()
            error_state = {"fields": post_fields, "documents": session_docs, "intakes_data": intakes_data}
            return render(request, "admissions/apply.html", {
                "programs": programs,
                "intake": active_intake,
                "intakes_data": intakes_data,
                "available_intakes": intakes_data.get("intakes", []),
                "countries": COUNTRIES_LIST,
                "kenyan_counties": KENYAN_COUNTIES,
                "custom_fields": custom_fields,
                "draft_application": draft,
                "draft_state": error_state,
                "draft_state_json": json.dumps(error_state),
                "draft_documents": session_docs,
                "guardian_relationships": Application.GUARDIAN_RELATIONSHIPS,
                "data": post_fields,
                "field_errors": res.get("field_errors", {}),
                "draft_step": 1,
                "completion_percentage": 0,
                "draft_version": 1,
            })

    draft = get_active_applicant_draft(request)
    state = serialize_draft_state(draft, request=request) if draft else empty_draft_state()

    return render(request, "admissions/apply.html", {
        "programs": programs,
        "intake": (draft.intake if draft else None) or active_intake,
        "intakes_data": intakes_data,
        "available_intakes": intakes_data.get("intakes", []),
        "countries": COUNTRIES_LIST,
        "kenyan_counties": KENYAN_COUNTIES,
        "custom_fields": custom_fields,
        "draft_application": draft,
        "draft_state": state,
        "draft_state_json": json.dumps(state),
        "draft_documents": state["documents"],
        "guardian_relationships": Application.GUARDIAN_RELATIONSHIPS,
        "data": state["fields"],
        "draft_step": draft.draft_step if draft else state["step"],
        "completion_percentage": state["completion_percentage"],
        "draft_version": draft.draft_version if draft else state["version"],
    })


@rate_limit("app-fee-pay", limit=15, window_seconds=60)
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
        if request.user.is_authenticated:
            return HttpResponseForbidden("Access denied. You do not have permission to access this application fee payment.")
        messages.error(request, "Please log in to access this application payment.")
        return redirect("university:applicant_login")

    fee_amount = get_setting("application_fee_default", default=Decimal("1000.00"))

    # Dynamically retrieve active centralized payment channels (Pochi la Biashara and M-Pesa Paybill)
    from university.models import FeeAccount
    pochi_account = FeeAccount.objects.filter(
        account_type=FeeAccount.AccountType.POCHI_LA_BIASHARA,
        status=FeeAccount.Status.ACTIVE,
    ).first()
    pochi_number = pochi_account.account_identifier if pochi_account else "0113636154"

    paybill_account = FeeAccount.objects.filter(
        account_type=FeeAccount.AccountType.MPESA_PAYBILL,
        status=FeeAccount.Status.ACTIVE,
    ).first()
    paybill_number = paybill_account.account_identifier if paybill_account else "222111"

    if application.fee_paid:
        messages.info(request, "The application fee has already been paid and verified.")
        if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
            return redirect("university:applicant_dashboard")
        return redirect(f"{reverse('university:admissions_status')}?access={application_access_token(application)}")

    # Applications that are under active board review, accepted, or enrolled cannot submit new payments.
    # Similarly, fully submitted applications with confirmed fee payment cannot submit duplicate payments.
    if application.status in [Application.Status.UNDER_REVIEW, Application.Status.ACCEPTED, Application.Status.ENROLLED] or (
        application.status == Application.Status.SUBMITTED and application.fee_paid
    ):
        messages.info(request, f"Application {application.application_number} has already been submitted for review.")
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
            from django.db import IntegrityError, transaction
            from django.db.models import Q
            from university.models import Payment

            # Check if reference already exists as a confirmed provider transaction in Payment
            verified_provider_payment = Payment.objects.filter(
                Q(provider_reference__iexact=reference) | Q(reference__iexact=reference),
                status=Payment.Status.SUCCESSFUL,
                invoice__isnull=True,
            ).first()

            try:
                with transaction.atomic():
                    if verified_provider_payment and verified_provider_payment.amount >= fee_amount:
                        receipt_no = f"APPFEE-{timezone.now().year}-{reference}"
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
                        application.status = Application.Status.READY_FOR_SUBMISSION
                        application.save(update_fields=["status", "updated_at"])
                        messages.success(request, f"Payment verified successfully! Receipt number: {receipt_no}. You may now submit your application.")
                    else:
                        payment = ApplicationFeePayment.objects.create(
                            application=application,
                            applicant_user=request.user if request.user.is_authenticated else application.applicant_user,
                            receipt_number=None,
                            amount=fee_amount,
                            method=method,
                            reference=reference,
                            status=ApplicationFeePayment.Status.PENDING,
                        )
                        application.status = Application.Status.PAYMENT_PENDING
                        application.save(update_fields=["status", "updated_at"])
                        messages.success(
                            request,
                            f"Payment reference submitted for verification. Amount expected: KES {fee_amount:,.2f}. "
                            "Your application will be ready for final submission after Finance or Admissions confirms the transaction."
                        )

                    log_activity(
                        request=request,
                        user=request.user if request.user.is_authenticated else None,
                        action=AuditLog.Action.CREATE,
                        module=AuditLog.Module.FEES,
                        entity="ApplicationFeePayment",
                        entity_id=payment.pk,
                        description=(
                            f"Application fee reference submitted for verification for application "
                            f"{application.application_number}. Method: {payment.get_method_display()}, Ref: {reference}."
                        ),
                    )
            except IntegrityError:
                messages.error(request, "That payment reference has already been recorded. Check the reference or contact Admissions.")
                return redirect("university:pay_application_fee", pk=application.pk)

            if request.user.is_authenticated and getattr(request.user, "role", "") == Role.APPLICANT:
                return redirect("university:applicant_dashboard")
            return redirect(f"{reverse('university:admissions_status')}?access={application_access_token(application)}")

    return render(request, "admissions/pay_fee.html", {
        "application": application,
        "fee_amount": fee_amount,
        "pochi_number": pochi_number,
        "paybill_number": paybill_number,
        "methods": ApplicationFeePayment.Method.choices,
        "application_access_token": access_token or application_access_token(application),
    })


@rate_limit("app-submit-final", limit=10, window_seconds=60)
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
        if request.user.is_authenticated:
            return HttpResponseForbidden("Access denied. You do not have permission to submit this application.")
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


@rate_limit("app-status-query", limit=30, window_seconds=60, methods=("GET",))
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
        revoked_doc = app.issued_documents.filter(status="REVOKED").first()
        if revoked_doc and not is_staff:
            messages.error(
                request,
                "Your admission letter has been revoked by the university admissions office. "
                "Please contact the Registrar's Office for assistance."
            )
            return redirect(
                f"{reverse('university:admissions_status')}?access={application_access_token(app)}"
            )
        user = request.user if request.user.is_authenticated else None
        try:
            doc = generate_admission_document(app, user=user, reason="Generated on applicant letter request")
        except (SignatureRequiredError, SignatureAuthorizationError) as exc:
            if is_staff:
                messages.error(request, f"Admission letter cannot be issued yet: {exc}")
                return redirect("university:admin_admission_document_detail", pk=app.pk)
            messages.error(
                request,
                "Your admission letter is being finalized by the admissions office. "
                "Please check again after the official signatory has been configured.",
            )
            return redirect(
                f"{reverse('university:admissions_status')}?access={application_access_token(app)}"
            )

    # Existing letters are stored as immutable PDF snapshots. When an admin
    # revises the assigned template, issue a new current version on the next
    # student request so the student never remains on stale letter content.
    if doc and doc.template and doc.version < doc.template.version:
        try:
            doc = generate_admission_document(
                app,
                template=doc.template,
                user=request.user if is_staff else None,
                reason=f"Automatically regenerated after template revision to version {doc.template.version}",
                issue_as_new_version=True,
            )
        except (SignatureRequiredError, SignatureAuthorizationError) as exc:
            if is_staff:
                messages.error(request, f"Admission letter cannot be regenerated: {exc}")
                return redirect("university:admin_admission_document_detail", pk=app.pk)

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
        return HttpResponseForbidden("Access denied. You do not have permission to accept this admission offer.")

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


@login_required
@require_POST
def applicant_decline_offer(request, pk):
    """Applicant action to decline an official admission offer."""
    app = get_object_or_404(
        Application.objects.select_related("program", "intake", "student"),
        pk=pk
    )
    if app.applicant_user != request.user and not (request.user.is_staff or request.user.is_superuser or getattr(request.user, "role", "") in (Role.ADMIN, "ADMIN")):
        return HttpResponseForbidden("Access denied. You do not have permission to decline this admission offer.")

    if app.status != Application.Status.ACCEPTED:
        messages.warning(request, f"Application is currently in '{app.get_status_display()}' state. Only active accepted offers can be declined.")
        return redirect("university:applicant_dashboard")

    reason = request.POST.get("reason", "").strip() or "Declined by applicant"
    app.review_notes = (app.review_notes or "") + f"\n[Offer Declined by Applicant on {timezone.now():%Y-%m-%d %H:%M UTC}. Reason: {reason}]"
    app.status = Application.Status.REJECTED
    app.save(update_fields=["status", "review_notes", "updated_at"])

    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.ADMISSIONS,
        entity="Application",
        entity_id=app.id,
        description=f"Applicant {app.full_name} ({app.application_number}) declined admission offer for {app.program.name if app.program else 'Degree Programme'}. Reason: {reason}",
        new_state={
            "application_number": app.application_number,
            "declined_at": str(timezone.now()),
            "status": app.status,
            "reason": reason,
        }
    )

    messages.info(request, "You have declined the admission offer. Your decision has been recorded.")
    return redirect("university:applicant_dashboard")



# ==============================================================================
# ADMIN ADMISSIONS MANAGEMENT VIEWS
# ==============================================================================

def _admissions_scope(user):
    if user.is_superuser or user.role == Role.ADMIN:
        return True, set()
    assignments = StaffRoleAssignment.objects.filter(user=user, is_active=True, role__code__iexact="dean").select_related("department__school")
    school_ids = {a.department.school_id for a in assignments if a.department and a.department.school_id}
    if not school_ids:
        profile = FacultyProfile.objects.filter(user=user).select_related("department__school").first()
        if profile and profile.department and profile.department.school_id:
            school_ids.add(profile.department.school_id)
    return False, school_ids


@login_required
def admin_admissions_list(request):
    """Admin: Overview and filtering of all prospective student applications."""
    central_admin, school_ids = _admissions_scope(request.user)
    if not central_admin and not school_ids:
        raise PermissionDenied
    qs = Application.objects.exclude(status=Application.Status.DRAFT).select_related("program", "intake", "student").order_by("-created_at")
    if not central_admin:
        qs = qs.filter(program__department__school_id__in=school_ids)

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
    all_apps = Application.objects.exclude(status=Application.Status.DRAFT) if central_admin else qs.model.objects.exclude(status=Application.Status.DRAFT).filter(program__department__school_id__in=school_ids)
    stats = {
        "total": all_apps.count(),
        "submitted": all_apps.filter(status=Application.Status.SUBMITTED).count(),
        "under_review": all_apps.filter(status=Application.Status.UNDER_REVIEW).count(),
        "accepted": all_apps.filter(status=Application.Status.ACCEPTED).count(),
        "enrolled": all_apps.filter(status=Application.Status.ENROLLED).count(),
        "rejected": all_apps.filter(status=Application.Status.REJECTED).count(),
    }

    try:
        page_size = int(request.GET.get("page_size", "20"))
    except (TypeError, ValueError):
        page_size = 20
    if page_size not in {20, 50, 100, 200}:
        page_size = 20
    paginator = Paginator(qs, page_size)
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
        "page_size": page_size,
    })


@login_required
def admin_admission_detail(request, pk):
    """Admin: Review individual application, accept/reject, or edit decision."""
    central_admin, school_ids = _admissions_scope(request.user)
    if not central_admin and not school_ids:
        raise PermissionDenied
    app_qs = Application.objects.select_related("program", "program__department__school", "intake", "student", "reviewed_by")
    if not central_admin:
        app_qs = app_qs.filter(program__department__school_id__in=school_ids)
    app = get_object_or_404(app_qs, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        if not central_admin and action in {"accept", "reject", "confirm_payment"}:
            raise PermissionDenied
        review_notes = request.POST.get("review_notes", "").strip()
        reporting_date = request.POST.get("reporting_date", "").strip()

        if action not in {"under_review", "accept", "reject", "confirm_payment"}:
            messages.error(request, "Select a valid admissions review action.")
            return redirect("university:admin_admission_detail", pk=app.pk)

        # ── State machine guards ────────────────────────────────────
        # An ENROLLED application already has a student profile, user
        # account, and semester registration. Changing its status to
        # anything else would orphan those records.  Formal student
        # withdrawal / deferment must be used instead.
        if app.status == Application.Status.ENROLLED and action in {"under_review", "accept", "reject"}:
            messages.error(
                request,
                "This applicant is already enrolled as an active student. "
                "Status changes must be processed through Student Withdrawal "
                "or Deferment, not through the admissions review workflow."
            )
            return redirect("university:admin_admission_detail", pk=app.pk)

        # Only SUBMITTED or UNDER_REVIEW applications can be moved to
        # under_review or accepted.  Draft / In-Progress / Payment-stage
        # applications have not completed their submission process.
        REVIEWABLE_STATUSES = {
            Application.Status.SUBMITTED,
            Application.Status.UNDER_REVIEW,
        }
        if action in {"under_review", "accept"} and app.status not in REVIEWABLE_STATUSES:
            messages.error(
                request,
                f"Cannot mark application as "
                f"'{'Under Review' if action == 'under_review' else 'Accepted'}' "
                f"because it is currently in '{app.get_status_display()}' status. "
                f"The application must be fully submitted first."
            )
            return redirect("university:admin_admission_detail", pk=app.pk)

        # Only SUBMITTED / UNDER_REVIEW / ACCEPTED applications can be rejected.
        REJECTABLE_STATUSES = {
            Application.Status.SUBMITTED,
            Application.Status.UNDER_REVIEW,
            Application.Status.ACCEPTED,
        }
        if action == "reject" and app.status not in REJECTABLE_STATUSES:
            messages.error(
                request,
                f"Cannot reject an application in '{app.get_status_display()}' status."
            )
            return redirect("university:admin_admission_detail", pk=app.pk)

        # ── Confirm payment (separate workflow) ─────────────────────
        if action == "confirm_payment":
            payment_id = request.POST.get("payment_id")
            pending_payment = app.fee_payments.filter(
                pk=payment_id,
                status=ApplicationFeePayment.Status.PENDING,
            ).first()
            if not pending_payment:
                messages.error(request, "Select a pending application-fee payment to confirm.")
                return redirect("university:admin_admission_detail", pk=app.pk)

            pending_payment.status = ApplicationFeePayment.Status.CONFIRMED
            pending_payment.confirmed_at = timezone.now()
            pending_payment.receipt_number = f"APPFEE-{timezone.now().year}-{pending_payment.pk:06d}"
            pending_payment.save(update_fields=["status", "confirmed_at", "receipt_number"])

            if app.status in [Application.Status.READY_FOR_PAYMENT, Application.Status.PAYMENT_PENDING, Application.Status.PAID]:
                app.status = Application.Status.READY_FOR_SUBMISSION
                app.save(update_fields=["status", "updated_at"])

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.UPDATE,
                module=AuditLog.Module.FEES,
                entity="ApplicationFeePayment",
                entity_id=pending_payment.pk,
                description=(
                    f"Confirmed application fee payment {pending_payment.reference} "
                    f"for application {app.application_number}."
                ),
            )
            messages.success(request, f"Application fee confirmed. Receipt: {pending_payment.receipt_number}.")
            return redirect("university:admin_admission_detail", pk=app.pk)

        # ── Review metadata ─────────────────────────────────────────
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
        "central_admin": central_admin,
        "pending_fee_payment": app.fee_payments.filter(status=ApplicationFeePayment.Status.PENDING).first(),
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

    try:
        student_profile, user, _ = matriculate_applicant(app, created_by=request.user)
    except (SignatureRequiredError, SignatureAuthorizationError) as exc:
        messages.error(request, f"Student enrollment could not be completed because the admission letter cannot be issued: {exc}")
        return redirect("university:admin_admission_detail", pk=app.pk)

    if app.issued_documents.filter(is_current_version=True).exists():
        messages.success(
            request,
            f"Student registration and enrollment completed. Student profile {student_profile.roll_no} created with the "
            f"username '{user.username}'. An activation link has been emailed so the student sets "
            f"their own password; resend it from User Management if it does not arrive."
        )
    else:
        messages.warning(
            request,
            f"Student registration and enrollment completed for {student_profile.roll_no}, but the admission letter is still pending signature configuration. "
            "Configure an active official signature, then issue the letter from Admission Documents."
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


@role_required(Role.ADMIN)
def admin_cohorts(request):
    """Admin: Manage university cohorts."""
    from datetime import date
    cohorts = Cohort.objects.all().order_by("-start_date", "-created_at")
    academic_years = AcademicYear.objects.exclude(
        status__in=[AcademicYear.Status.CLOSED, AcademicYear.Status.ARCHIVED]
    ).order_by("-start_date", "name")

    if request.method == "POST":
        cohort_id = request.POST.get("cohort_id", "").strip()
        month = request.POST.get("month", "").strip().upper()
        academic_year_id = request.POST.get("academic_year", "").strip()
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")
        description = request.POST.get("description", "").strip()

        if month not in ["JAN", "MAY", "SEP"]:
            messages.error(request, "Cohorts can only be in January, May, or September.")
            return redirect("university:admin_cohorts")
        
        academic_year = academic_years.filter(pk=academic_year_id).first()
        if not academic_year:
            messages.error(request, "Select a valid academic year from the configured academic years.")
            return redirect("university:admin_cohorts")

        year = str(academic_year.start_date.year)
        name = f"{month}-{year}"

        existing = Cohort.objects.filter(pk=cohort_id).first() if cohort_id.isdigit() else None
        if existing:
            try:
                parsed_start = date.fromisoformat(start_date) if start_date else None
                parsed_end = date.fromisoformat(end_date) if end_date else None
            except ValueError:
                parsed_start = parsed_end = None
            if parsed_start and parsed_end and parsed_end < parsed_start:
                messages.error(request, "Cohort end date cannot be before its start date.")
            else:
                existing.start_date = parsed_start
                existing.end_date = parsed_end
                existing.description = description
                existing.save(update_fields=["start_date", "end_date", "description"])
                messages.success(request, f"Cohort '{existing.name}' updated successfully.")
                return redirect("university:admin_cohorts")
        elif Cohort.objects.filter(name=name).exists():
            messages.error(request, "A cohort with this name already exists.")
        else:
            try:
                parsed_start = date.fromisoformat(start_date) if start_date else None
            except ValueError:
                parsed_start = None
            try:
                parsed_end = date.fromisoformat(end_date) if end_date else None
            except ValueError:
                parsed_end = None

            Cohort.objects.create(
                name=name,
                start_date=parsed_start,
                end_date=parsed_end,
                description=description
            )
            messages.success(request, f"Cohort '{name}' created successfully.")
            return redirect("university:admin_cohorts")

    edit_id = request.GET.get("edit", "").strip()
    edit_cohort = Cohort.objects.filter(pk=edit_id).first() if edit_id.isdigit() else None
    edit_month, edit_year = (edit_cohort.name.split("-", 1) if edit_cohort and "-" in edit_cohort.name else ("SEP", ""))
    edit_academic_year = academic_years.filter(start_date__year=edit_year).first() if edit_year.isdigit() else None
    return render(request, "admissions/admin_cohorts.html", {
        "cohorts": cohorts, "edit_cohort": edit_cohort,
        "edit_month": edit_month, "edit_year": edit_year,
        "academic_years": academic_years, "edit_academic_year": edit_academic_year,
    })
