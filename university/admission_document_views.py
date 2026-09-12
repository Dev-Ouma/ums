from decimal import Decimal
import io
import mimetypes
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from university.academic_calendar_services import get_current_academic_year
from university.audit_services import log_activity
from university.document_views import present_pdf
from university.models import (
    AcademicYear, AdmissionDocumentTemplate, Application, ApplicationAttachment,
    ApplicationCustomField, ApplicationCustomFieldValue, AuditLog,
    DocumentDeliveryLog, DocumentSignatureConfig, IssuedAdmissionDocument, Program
)
from university.admission_document_services import (
    build_admission_document_context, build_admission_letter_pdf_bytes,
    build_dynamic_fields_catalog, generate_admission_document,
    get_or_create_default_template, resend_admission_document,
    revoke_admission_document, SignatureRequiredError, SignatureAuthorizationError
)


def _ensure_admin(user):
    return user.is_authenticated and (user.is_admin_role or user.is_superuser)


# ==============================================================================
# 1. ADMIN ADMISSION DOCUMENTS LIST & DIRECTORY
# ==============================================================================

@login_required
def admin_admission_documents_list(request):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted to admissions administrators.")
        return redirect("university:dashboard")

    # Base queryset: applications admitted or enrolled or submitted
    queryset = Application.objects.all().select_related(
        "program", "program__department", "intake", "student"
    ).prefetch_related("issued_documents", "attachments").order_by("-created_at")

    # Search & filters
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(application_number__icontains=q) |
            Q(admitted_reg_no__icontains=q) |
            Q(email__icontains=q) |
            Q(national_id__icontains=q) |
            Q(student__roll_no__icontains=q)
        )

    program_id = request.GET.get("program")
    if program_id:
        queryset = queryset.filter(program_id=program_id)

    status_filter = request.GET.get("status")
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    letter_filter = request.GET.get("letter_status")
    if letter_filter == "ISSUED":
        queryset = queryset.filter(issued_documents__is_current_version=True).exclude(issued_documents__status=IssuedAdmissionDocument.Status.REVOKED)
    elif letter_filter == "PENDING":
        queryset = queryset.filter(status__in=[Application.Status.ACCEPTED, Application.Status.ENROLLED]).exclude(
            issued_documents__is_current_version=True
        )
    elif letter_filter == "REVOKED":
        queryset = queryset.filter(issued_documents__status=IssuedAdmissionDocument.Status.REVOKED)

    # Global KPI tallies
    all_admitted = Application.objects.filter(status__in=[Application.Status.ACCEPTED, Application.Status.ENROLLED])
    total_admitted = all_admitted.count()
    letters_issued = IssuedAdmissionDocument.objects.filter(is_current_version=True).exclude(status=IssuedAdmissionDocument.Status.REVOKED).count()
    letters_pending = total_admitted - letters_issued if total_admitted > letters_issued else 0
    total_resent = DocumentDeliveryLog.objects.count()
    total_attachments = ApplicationAttachment.objects.count()
    verified_attachments = ApplicationAttachment.objects.filter(verification_status=ApplicationAttachment.VerificationStatus.VERIFIED).count()

    paginator = Paginator(queryset, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    programs = Program.objects.all().order_by("name")
    academic_years = AcademicYear.objects.all().order_by("-start_date")

    context = {
        "page_obj": page_obj,
        "programs": programs,
        "academic_years": academic_years,
        "q": q,
        "selected_program": program_id,
        "selected_status": status_filter,
        "selected_letter": letter_filter,
        "total_admitted": total_admitted,
        "letters_issued": letters_issued,
        "letters_pending": letters_pending,
        "total_resent": total_resent,
        "total_attachments": total_attachments,
        "verified_attachments": verified_attachments,
    }
    return render(request, "admissions/admin_documents_list.html", context)


# ==============================================================================
# 2. ADMIN ADMISSION DOSSIER & DETAIL
# ==============================================================================

@login_required
def admin_admission_document_detail(request, pk):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted to admissions administrators.")
        return redirect("university:dashboard")

    app = get_object_or_404(
        Application.objects.select_related("program", "program__department", "intake", "student"),
        pk=pk
    )

    # Active issued admission document
    active_letter = app.issued_documents.filter(is_current_version=True).first()

    # All versioned documents
    all_versions = app.issued_documents.all().order_by("-version")

    # Submitted application attachments
    attachments = app.attachments.all().order_by("-uploaded_at")

    # Delivery logs for all documents of this application
    delivery_logs = DocumentDeliveryLog.objects.filter(
        document__application=app
    ).select_related("document", "sent_by").order_by("-sent_at")

    # Custom field values
    custom_values = app.custom_values.select_related("field").all()

    # Available templates for regeneration
    templates = AdmissionDocumentTemplate.objects.filter(is_active=True).order_by("-is_default", "name")

    # Central Document Signature Config and eligible signatories
    sig_config = DocumentSignatureConfig.objects.filter(
        document_type=DocumentSignatureConfig.DocumentType.ADMISSION_LETTER,
        is_active=True
    ).first()
    authorized_roles = sig_config.get_authorized_roles_list() if sig_config else ["REGISTRAR", "ADMIN", "STAFF"]
    signatories = (
        User.objects.filter(role__in=authorized_roles)
        .select_related("user_signature")
        .order_by("role", "first_name", "last_name")
    )

    context = {
        "app": app,
        "active_letter": active_letter,
        "all_versions": all_versions,
        "attachments": attachments,
        "delivery_logs": delivery_logs,
        "custom_values": custom_values,
        "templates": templates,
        "sig_config": sig_config,
        "signatories": signatories,
        "tokens_catalog": build_dynamic_fields_catalog(),
    }
    return render(request, "admissions/admin_document_detail.html", context)


# ==============================================================================
# 3. ADMIN ACTIONS: REGENERATE, RESEND, REVOKE, VERIFY
# ==============================================================================

@login_required
@require_POST
def admin_regenerate_admission_document(request, pk):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to perform this action.")
        return redirect("university:admin_admission_document_detail", pk=pk)

    app = get_object_or_404(Application, pk=pk)
    template_id = request.POST.get("template_id")
    signatory_id = request.POST.get("signatory_id")
    reason = request.POST.get("reason", "").strip() or "Administrative issuance / regeneration"
    rep_date_str = request.POST.get("reporting_date", "").strip()

    template = None
    if template_id:
        template = AdmissionDocumentTemplate.objects.filter(pk=template_id, is_active=True).first()

    signatory_user = None
    if signatory_id:
        signatory_user = User.objects.filter(pk=signatory_id).first()

    custom_overrides = {}
    if rep_date_str:
        try:
            from datetime import datetime
            dt = datetime.strptime(rep_date_str, "%Y-%m-%d").date()
            app.reporting_date = dt
            app.save(update_fields=["reporting_date"])
            custom_overrides["reporting_date"] = dt.strftime("%A, %d %B %Y")
        except ValueError:
            messages.error(request, "Enter a valid reporting date before generating the admission letter.")
            return redirect("university:admin_admission_document_detail", pk=app.pk)

    try:
        new_doc = generate_admission_document(
            application=app,
            template=template,
            user=request.user,
            custom_overrides=custom_overrides,
            reason=reason,
            issue_as_new_version=True,
            signatory_user=signatory_user,
        )
        messages.success(
            request,
            f"Admission Letter successfully finalized as Version {new_doc.version} (Ref: {new_doc.document_reference}) with official signatory snapshot."
        )
    except SignatureRequiredError as e:
        messages.error(request, f"⚠ Signature Required: {e}")
    except SignatureAuthorizationError as e:
        messages.error(request, f"⛔ Access Denied: {e}")
    except Exception as e:
        messages.error(request, f"Could not generate admission letter: {e}")

    return redirect("university:admin_admission_document_detail", pk=app.pk)


@login_required
@require_POST
def admin_resend_admission_document(request, pk):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to perform this action.")
        return redirect("university:admin_admission_document_detail", pk=pk)

    app = get_object_or_404(Application, pk=pk)
    active_letter = app.issued_documents.filter(is_current_version=True).first()
    if not active_letter:
        messages.error(request, "No active admission document exists to resend.")
        return redirect("university:admin_admission_document_detail", pk=app.pk)

    delivery_method = request.POST.get("delivery_method", DocumentDeliveryLog.Method.EMAIL)
    if delivery_method not in DocumentDeliveryLog.Method.values:
        messages.error(request, "Select a valid document delivery method.")
        return redirect("university:admin_admission_document_detail", pk=app.pk)
    recipient = request.POST.get("recipient", "").strip() or app.email
    subject = request.POST.get("subject", "").strip() or f"Official Admission Letter - {active_letter.document_reference}"
    message = request.POST.get("message", "").strip() or (
        f"Dear {app.full_name},\n\nPlease find attached your official University Letter of Offer (Admission Letter) "
        f"for {app.program.name if app.program else 'your degree programme'}.\n\n"
        f"Reference: {active_letter.document_reference}\n"
        f"Student Registration Number: {app.admitted_reg_no}\n\n"
        f"You can also access, preview and download this document anytime through your Student Portal under Reports > Admission Documents.\n\n"
        f"Sincerely,\nOffice of the Registrar (Academic Affairs)"
    )

    try:
        log_entry = resend_admission_document(
            document=active_letter,
            delivery_method=delivery_method,
            recipient=recipient,
            subject=subject,
            message=message,
            user=request.user,
            request=request
        )
        if log_entry.status == DocumentDeliveryLog.Status.FAILED:
            messages.warning(request, f"Resend attempted to {recipient}, but delivery encountered an issue: {log_entry.failure_reason}")
        else:
            messages.success(request, f"Admission Letter (v{active_letter.version}) successfully resent to {recipient} via {delivery_method}.")
    except Exception as e:
        messages.error(request, f"Failed to dispatch document: {str(e)}")

    return redirect("university:admin_admission_document_detail", pk=app.pk)


@login_required
@require_POST
def admin_revoke_admission_document(request, pk):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to perform this action.")
        return redirect("university:admin_admission_document_detail", pk=pk)

    app = get_object_or_404(Application, pk=pk)
    active_letter = app.issued_documents.filter(is_current_version=True).first()
    if not active_letter:
        messages.error(request, "No active admission letter found to revoke.")
        return redirect("university:admin_admission_document_detail", pk=app.pk)

    reason = request.POST.get("reason", "").strip() or "Administrative revocation"
    revoke_admission_document(active_letter, user=request.user, reason=reason)

    messages.warning(request, f"Admission Letter {active_letter.document_reference} (v{active_letter.version}) has been revoked and marked void.")
    return redirect("university:admin_admission_document_detail", pk=app.pk)


@login_required
@require_POST
def admin_verify_attachment(request, attachment_id):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to perform this action.")
        return redirect("university:dashboard")

    attachment = get_object_or_404(ApplicationAttachment, pk=attachment_id)
    new_status = request.POST.get("verification_status")
    notes = request.POST.get("verification_notes", "").strip()

    if new_status in ApplicationAttachment.VerificationStatus.values:
        old_status = attachment.verification_status
        attachment.verification_status = new_status
        attachment.verified_by = request.user
        attachment.verified_at = timezone.now()
        attachment.verification_notes = notes
        attachment.save()

        log_activity(
            user=request.user,
            action=AuditLog.Action.VERIFY_DOCUMENT,
            module=AuditLog.Module.ADMISSIONS,
            entity="ApplicationAttachment",
            entity_id=attachment.id,
            description=f"Updated attachment '{attachment.name}' status to {new_status} for application {attachment.application.application_number}. Notes: {notes}",
            new_state={
                "old_status": old_status,
                "new_status": new_status,
                "notes": notes,
                "attachment_id": attachment.id,
            }
        )
        messages.success(request, f"Document '{attachment.name}' updated to {attachment.get_verification_status_display()}.")
    else:
        messages.error(request, "Select a valid document verification status.")

    return redirect("university:admin_admission_document_detail", pk=attachment.application.pk)


# ==============================================================================
# 4. ADMIN PREVIEW & DOWNLOAD VIEWS
# ==============================================================================

@login_required
def admin_view_admission_document(request, doc_id):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to view this document.")
        return redirect("university:dashboard")

    doc = get_object_or_404(IssuedAdmissionDocument, pk=doc_id)

    if not doc.pdf_file:
        pdf_bytes = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_bytes = doc.pdf_file.read()
        except Exception:
            pdf_bytes = build_admission_letter_pdf_bytes(doc)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    response["Content-Disposition"] = f'inline; filename="{filename}"'

    return present_pdf(
        request,
        response,
        title=f"Admission Letter - {doc.document_reference} (v{doc.version})"
    )


@login_required
def admin_download_admission_document(request, doc_id):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to download this document.")
        return redirect("university:dashboard")

    doc = get_object_or_404(IssuedAdmissionDocument, pk=doc_id)

    if not doc.pdf_file:
        pdf_bytes = build_admission_letter_pdf_bytes(doc)
    else:
        try:
            pdf_bytes = doc.pdf_file.read()
        except Exception:
            pdf_bytes = build_admission_letter_pdf_bytes(doc)

    filename = f"Admission_Letter_{doc.document_reference.replace('/', '_')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ==============================================================================
# 5. ADMIN ADMISSION TEMPLATES MANAGEMENT
# ==============================================================================

@login_required
def admin_templates_list(request):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted.")
        return redirect("university:dashboard")

    templates = AdmissionDocumentTemplate.objects.all().select_related("program", "academic_year").order_by("-is_default", "name")
    tokens_catalog = build_dynamic_fields_catalog()

    context = {
        "templates": templates,
        "tokens_catalog": tokens_catalog,
    }
    return render(request, "admissions/admin_templates_list.html", context)


TEMPLATE_DEFAULTS = {
    "name": "Official Undergraduate Admission Offer",
    "header_title": "Office of the Deputy Vice-Chancellor<br/>(Academic Affairs)",
    "salutation_template": "Dear {{title_name}}, Admission Number: {{registration_number}}",
    "subject_template": "RE: ADMISSION INTO {{programme_name}} - {{academic_year}} ACADEMIC YEAR",
    "body_template": (
        "Following your application for admission to {{university_name}}, I wish to congratulate you on this achievement. "
        "You have been admitted on the basis of your qualifications, which are subject to verification by the University. "
        "When reporting, you will be required to present original and copies of the following:\n\n"
        "1. KCSE Certificate or Result Slip\n"
        "2. Birth Certificate\n"
        "3. National Identity Card or Passport\n"
        "4. Two coloured passport-size photographs\n"
        "5. Proof of payment of tuition fees"
    ),
    "terms_and_conditions": (
        "COMMENCEMENT DATE\n"
        "The programme will commence on {{reporting_date}}. You are, therefore, expected to report and complete your registration on this date.\n\n"
        "OTHER IMPORTANT INFORMATION\n"
        "i. Admission to the University does not guarantee accommodation in the Halls of Residence. Students not allocated university accommodation will be required to make private arrangements.\n"
        "ii. This admission offer is subject to your adherence to the University's Rules and Regulations.\n"
        "iii. In case of any queries, please contact the Admissions Office at {{university_email}} or Tel: {{university_phone}}"
    ),
    "fee_schedule_instructions": (
        "TUITION FEES\n"
        "You will pay {{tuition_fee}} as tuition fee in a Semester. "
        "For more information, please contact the Finance Office at {{finance_email}} or Tel: {{university_phone}}\n\n"
        "FEE PAYMENT\n"
        "You are required to follow the instructions below to pay the tuition fee:\n"
        "1. While logged in to the students portal, navigate to the \"STUDENT PAYMENT INSTRUCTIONS\" section at the bottom of the page.\n"
        "2. Click on \"Fee Payment\" / \"M-Pesa Payment\" and follow the prompts."
    ),
    "signatory_name": "DR. MARGARET OMOLO, PhD",
    "signatory_title": "ACADEMIC REGISTRAR",
    "verification_base_url": "https://ums.ac.ke/verify-admission/",
}


@login_required
def admin_template_editor(request, pk=None):
    if not _ensure_admin(request.user):
        messages.error(request, "Access restricted.")
        return redirect("university:dashboard")

    template = get_object_or_404(AdmissionDocumentTemplate, pk=pk) if pk else None
    programs = Program.objects.all().order_by("name")
    academic_years = AcademicYear.objects.all().order_by("-start_date")
    tokens_catalog = build_dynamic_fields_catalog()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        doc_type = request.POST.get("document_type", AdmissionDocumentTemplate.DocumentType.ADMISSION_LETTER)
        prog_id = request.POST.get("program") or None
        ay_id = request.POST.get("academic_year") or None
        is_active = request.POST.get("is_active") == "on"
        is_default = request.POST.get("is_default") == "on"

        header_title = request.POST.get("header_title", "").strip()
        salutation = request.POST.get("salutation_template", "").strip()
        subject = request.POST.get("subject_template", "").strip()
        body = request.POST.get("body_template", "").strip()
        terms = request.POST.get("terms_and_conditions", "").strip()
        fee_instructions = request.POST.get("fee_schedule_instructions", "").strip()
        sig_name = request.POST.get("signatory_name", "").strip()
        sig_title = request.POST.get("signatory_title", "").strip()
        verification_url = request.POST.get("verification_base_url", "").strip()

        if not name:
            messages.error(request, "Template name is required.")
        else:
            if is_default:
                # If marked default, unset other defaults for this document type
                AdmissionDocumentTemplate.objects.filter(document_type=doc_type).update(is_default=False)

            if template:
                template.name = name
                template.document_type = doc_type
                template.program_id = prog_id
                template.academic_year_id = ay_id
                template.is_active = is_active
                template.is_default = is_default
                template.version += 1
                template.header_title = header_title
                template.salutation_template = salutation
                template.subject_template = subject
                template.body_template = body
                template.terms_and_conditions = terms
                template.fee_schedule_instructions = fee_instructions
                template.signatory_name = sig_name
                template.signatory_title = sig_title
                template.verification_base_url = verification_url
                template.save()

                log_activity(
                    user=request.user,
                    action=AuditLog.Action.UPDATE,
                    module=AuditLog.Module.ADMISSIONS,
                    entity="AdmissionDocumentTemplate",
                    entity_id=template.id,
                    description=f"Updated admission document template '{template.name}' (v{template.version}).",
                )
                messages.success(request, f"Template '{template.name}' updated to Version {template.version}.")
            else:
                template = AdmissionDocumentTemplate.objects.create(
                    name=name,
                    document_type=doc_type,
                    program_id=prog_id,
                    academic_year_id=ay_id,
                    is_active=is_active,
                    is_default=is_default,
                    version=1,
                    header_title=header_title or TEMPLATE_DEFAULTS["header_title"],
                    salutation_template=salutation or TEMPLATE_DEFAULTS["salutation_template"],
                    subject_template=subject or TEMPLATE_DEFAULTS["subject_template"],
                    body_template=body or TEMPLATE_DEFAULTS["body_template"],
                    terms_and_conditions=terms or TEMPLATE_DEFAULTS["terms_and_conditions"],
                    fee_schedule_instructions=fee_instructions or TEMPLATE_DEFAULTS["fee_schedule_instructions"],
                    signatory_name=sig_name or TEMPLATE_DEFAULTS["signatory_name"],
                    signatory_title=sig_title or TEMPLATE_DEFAULTS["signatory_title"],
                    verification_base_url=verification_url or TEMPLATE_DEFAULTS["verification_base_url"],
                    created_by=request.user,
                )
                log_activity(
                    user=request.user,
                    action=AuditLog.Action.CREATE,
                    module=AuditLog.Module.ADMISSIONS,
                    entity="AdmissionDocumentTemplate",
                    entity_id=template.id,
                    description=f"Created admission document template '{template.name}'.",
                )
                messages.success(request, f"Template '{template.name}' created successfully.")

            return redirect("university:admin_templates_list")

    context = {
        "template": template,
        "programs": programs,
        "academic_years": academic_years,
        "tokens_catalog": tokens_catalog,
        "defaults": TEMPLATE_DEFAULTS,
    }
    return render(request, "admissions/admin_template_editor.html", context)


@login_required
def admin_template_preview(request, pk):
    if not _ensure_admin(request.user):
        messages.error(request, "Access denied. You do not have permission to preview this template.")
        return redirect("university:dashboard")

    template = get_object_or_404(AdmissionDocumentTemplate, pk=pk)

    # Use first available application as sample or bootstrap one
    sample_app = Application.objects.select_related("program").first()
    if not sample_app:
        prog = Program.objects.first()
        sample_app = Application(
            application_number="APP-SAMPLE-001",
            first_name="Aarav",
            last_name="Sharma",
            email="aarav.sharma@example.com",
            phone="+254 712 345 678",
            date_of_birth=timezone.now().date(),
            gender="MALE",
            national_id="38472910",
            address="P.O. Box 90100, Nairobi",
            secondary_school="Nairobi School",
            kcse_mean_grade="A-",
            kcse_year=2025,
            program=prog,
            admitted_reg_no="BCS/2026/00143",
        )

    # Temporary issued document instance
    sample_doc = IssuedAdmissionDocument(
        application=sample_app,
        template=template,
        document_reference=f"UMS/ADM/SAMPLE/001",
        version=template.version,
        status=IssuedAdmissionDocument.Status.DRAFT,
        issue_date=timezone.now().date(),
        reporting_date=timezone.now().date(),
    )
    context = build_admission_document_context(sample_app, document=sample_doc, template=template)
    sample_doc.rendered_context = context

    pdf_bytes = build_admission_letter_pdf_bytes(sample_doc)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="Template_Preview_{template.name}.pdf"'

    return present_pdf(
        request,
        response,
        title=f"Template Preview: {template.name} (v{template.version})"
    )
