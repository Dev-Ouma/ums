from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import UserSignature, UserSignatureHistory
from accounts.signature_services import (
    approve_user_signature,
    deactivate_user_signature,
    revoke_user_signature,
)
from university.audit_services import log_activity
from university.models import AuditLog, DocumentSignatureConfig

User = get_user_model()


def _ensure_admin_or_registrar(user):
    if not user or not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or getattr(user, "role", "") in ["ADMIN", "REGISTRAR"]
    )


@login_required
def admin_signature_config_dashboard(request):
    """
    Central administration board for document signature policies and
    institutional staff digital signatures.
    """
    if not _ensure_admin_or_registrar(request.user):
        messages.error(request, "Access restricted to Administrators and Registrars.")
        return redirect("university:dashboard")

    # Ensure configs exist for all DocumentTypes
    existing_types = set(DocumentSignatureConfig.objects.values_list("document_type", flat=True))
    for choice, label in DocumentSignatureConfig.DocumentType.choices:
        if choice not in existing_types:
            DocumentSignatureConfig.objects.create(
                document_type=choice,
                title=f"{label} Signature Policy",
                is_signature_required=True,
                required_roles="REGISTRAR,ADMIN,STAFF" if choice != "ACADEMIC_TRANSCRIPT" else "REGISTRAR,ADMIN",
                primary_label="Academic Registrar" if choice in ["ADMISSION_LETTER", "ACADEMIC_TRANSCRIPT", "DEGREE_CERTIFICATE"] else "Authorized Officer",
                primary_position=DocumentSignatureConfig.SignaturePosition.BOTTOM_RIGHT,
                is_active=True,
            )

    configs = DocumentSignatureConfig.objects.all().prefetch_related("authorized_users").order_by("document_type")

    # Staff / Officer signatures list
    staff_users = (
        User.objects.filter(role__in=["ADMIN", "REGISTRAR", "STAFF", "FACULTY", "DEAN", "HOD", "FINANCE"])
        .select_related("user_signature")
        .order_by("role", "first_name", "last_name")
    )

    # Filter by search
    q = request.GET.get("q", "").strip()
    if q:
        staff_users = staff_users.filter(
            first_name__icontains=q
        ) | staff_users.filter(
            last_name__icontains=q
        ) | staff_users.filter(
            username__icontains=q
        ) | staff_users.filter(
            email__icontains=q
        )

    # Signature audit history
    recent_sig_audits = (
        AuditLog.objects.filter(module=AuditLog.Module.SIGNATURES)
        .order_by("-timestamp")[:15]
    )

    return render(
        request,
        "identity/signature_management.html",
        {
            "nav": "signatures",
            "configs": configs,
            "staff_users": staff_users,
            "recent_audits": recent_sig_audits,
            "search_query": q,
        },
    )


@login_required
@require_POST
def admin_signature_config_edit(request, pk):
    """Update institutional document signature requirements."""
    if not _ensure_admin_or_registrar(request.user):
        messages.error(request, "Access restricted to Administrators.")
        return redirect("university:admin_signature_config_dashboard")

    config = get_object_or_404(DocumentSignatureConfig, pk=pk)

    is_required = request.POST.get("is_signature_required") == "on"
    roles = request.POST.get("required_roles", "").strip() or "ADMIN,REGISTRAR,STAFF"
    primary_label = request.POST.get("primary_label", "").strip() or "Authorized Signatory"
    primary_pos = request.POST.get("primary_position", DocumentSignatureConfig.SignaturePosition.BOTTOM_RIGHT)
    secondary_label = request.POST.get("secondary_label", "").strip()
    secondary_pos = request.POST.get("secondary_position", DocumentSignatureConfig.SignaturePosition.BOTTOM_LEFT)
    num_sigs = int(request.POST.get("number_of_signatures", 1))
    is_active = request.POST.get("is_active") == "on"

    old_state = {
        "is_signature_required": config.is_signature_required,
        "required_roles": config.required_roles,
        "number_of_signatures": config.number_of_signatures,
    }

    config.is_signature_required = is_required
    config.required_roles = roles
    config.primary_label = primary_label
    config.primary_position = primary_pos
    config.secondary_label = secondary_label
    config.secondary_position = secondary_pos
    config.number_of_signatures = num_sigs
    config.is_active = is_active
    config.save()

    log_activity(
        user=request.user,
        action=AuditLog.Action.CONFIG_CHANGE,
        module=AuditLog.Module.SIGNATURES,
        entity="DocumentSignatureConfig",
        entity_id=config.id,
        description=f"Updated signature policy for {config.get_document_type_display()}",
        previous_state=old_state,
        new_state={
            "is_signature_required": is_required,
            "required_roles": roles,
            "number_of_signatures": num_sigs,
        },
    )

    messages.success(request, f"Signature configuration for {config.get_document_type_display()} updated successfully.")
    return redirect("university:admin_signature_config_dashboard")


@login_required
@require_POST
def admin_user_signature_status_change(request, user_id):
    """Administrative action to verify, approve, activate, deactivate, or revoke a staff signature."""
    if not _ensure_admin_or_registrar(request.user):
        messages.error(request, "Access restricted to Administrators and Registrars.")
        return redirect("university:admin_signature_config_dashboard")

    target_user = get_object_or_404(User, pk=user_id)
    action = request.POST.get("status_action")
    reason = request.POST.get("reason", "").strip() or f"Admin action: {action}"

    sig = UserSignature.objects.filter(user=target_user).first()
    if not sig or not sig.signature_image:
        messages.error(request, f"User {target_user.get_full_name()} does not have an uploaded signature.")
        return redirect("university:admin_signature_config_dashboard")

    if action == "approve":
        approve_user_signature(target_user, actor=request.user, request=request)
        messages.success(request, f"Signature for {target_user.get_full_name()} approved and activated.")
    elif action == "deactivate":
        deactivate_user_signature(target_user, actor=request.user, reason=reason, request=request)
        messages.warning(request, f"Signature for {target_user.get_full_name()} deactivated.")
    elif action == "revoke":
        revoke_user_signature(target_user, actor=request.user, reason=reason, request=request)
        messages.error(request, f"Signature for {target_user.get_full_name()} revoked.")
    else:
        messages.error(request, "Invalid signature status action requested.")

    return redirect("university:admin_signature_config_dashboard")
