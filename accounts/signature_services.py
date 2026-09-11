import io
import os
import uuid
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image

from accounts.models import UserSignature, UserSignatureHistory

MAX_SIGNATURE_SIZE = 2500 * 1024  # 2.5 MB
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

SIGNATURE_MAGIC_BYTES = {
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".webp": [b"RIFF"],
}


def validate_signature_file(uploaded_file):
    """
    Validates uploaded signature image for allowed extension, magic header bytes,
    file size, and valid dimensions. Raises ValidationError on failure.
    """
    if not uploaded_file:
        raise ValidationError("No signature file was provided.")

    if uploaded_file.size > MAX_SIGNATURE_SIZE:
        raise ValidationError("Signature file size must not exceed 2.5 MB.")

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"Unsupported file format '{ext}'. Allowed formats: PNG, JPG, JPEG, WebP.")

    # Magic byte inspection
    initial_pos = uploaded_file.tell() if hasattr(uploaded_file, "tell") else 0
    header = uploaded_file.read(16)
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(initial_pos)

    valid_headers = SIGNATURE_MAGIC_BYTES.get(ext, [])
    if not any(header.startswith(sig) for sig in valid_headers):
        if ext == ".webp" and header.startswith(b"RIFF") and b"WEBP" in header[:16]:
            pass  # Valid WebP
        else:
            raise ValidationError("The uploaded file signature does not match a valid image header.")

    # Image dimension check using Pillow
    try:
        img = Image.open(uploaded_file)
        img.verify()
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(initial_pos)
        img = Image.open(uploaded_file)
        w, h = img.size
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(initial_pos)
    except Exception as e:
        raise ValidationError(f"Corrupt or invalid image file: {str(e)}")

    if w < 100 or h < 30:
        raise ValidationError(f"Signature image is too small ({w}x{h}px). Minimum dimensions are 100x30px.")
    if w > 3000 or h > 1500:
        raise ValidationError(f"Signature image is too large ({w}x{h}px). Maximum dimensions are 3000x1500px.")

    return True


def generate_safe_signature_filename(user, original_filename, version=1):
    """Generates an unguessable, secure storage filename."""
    ext = os.path.splitext(original_filename)[1].lower() or ".png"
    random_token = uuid.uuid4().hex[:12]
    return f"sig_user_{user.id}_v{version}_{random_token}{ext}"


def save_user_signature(
    user,
    signature_file=None,
    title="",
    department="",
    department_or_office="",
    status=UserSignature.Status.ACTIVE,
    reason="",
    actor=None,
    request=None,
):
    """
    Saves or updates a user's central signature profile.
    Automatically handles versioning and archives older versions into UserSignatureHistory.
    """
    actor = actor or user
    now = timezone.now()
    dept_val = (department_or_office or department or "").strip()

    existing_sig = (
        getattr(user, "user_signature", None)
        or getattr(user, "signature", None)
        or UserSignature.objects.filter(user=user).first()
    )

    if signature_file:
        validate_signature_file(signature_file)

    if existing_sig:
        # If new file is uploaded, archive current active signature into history
        if signature_file:
            old_version = existing_sig.version
            if existing_sig.signature_image:
                UserSignatureHistory.objects.create(
                    signature=existing_sig,
                    user=user,
                    version=old_version,
                    signature_image=existing_sig.signature_image,
                    title=existing_sig.title,
                    department_or_office=existing_sig.department_or_office,
                    status=existing_sig.status,
                    valid_from=existing_sig.activated_at or existing_sig.created_at,
                    valid_until=now,
                    change_reason=reason or "Archived on signature replacement",
                )
            new_version = old_version + 1
            filename = generate_safe_signature_filename(user, signature_file.name, version=new_version)
            existing_sig.signature_image.save(filename, signature_file, save=False)
            existing_sig.version = new_version
            existing_sig.activated_at = now

        if title:
            existing_sig.title = title.strip()
        if dept_val:
            existing_sig.department_or_office = dept_val

        existing_sig.status = status or UserSignature.Status.ACTIVE
        existing_sig.is_active = (existing_sig.status == UserSignature.Status.ACTIVE)
        existing_sig.save()
        sig_record = existing_sig
        action_type = "SIGNATURE_UPDATE" if signature_file else "SIGNATURE_INFO_UPDATE"
    else:
        new_version = 1
        sig_record = UserSignature(
            user=user,
            title=(title or "").strip(),
            department_or_office=dept_val,
            version=new_version,
            status=status or UserSignature.Status.ACTIVE,
            is_active=((status or UserSignature.Status.ACTIVE) == UserSignature.Status.ACTIVE),
            activated_at=now,
        )
        if signature_file:
            filename = generate_safe_signature_filename(user, signature_file.name, version=new_version)
            sig_record.signature_image.save(filename, signature_file, save=False)
        sig_record.save()
        action_type = "SIGNATURE_UPLOAD"

    # Audit logging
    try:
        from university.models import AuditLog
        from university.audit_services import log_activity
        log_activity(
            user=actor,
            action=AuditLog.Action.SIGNATURE_UPDATE if action_type == "SIGNATURE_UPDATE" else AuditLog.Action.SIGNATURE_UPLOAD,
            module=AuditLog.Module.SIGNATURES,
            entity="UserSignature",
            entity_id=sig_record.id,
            description=f"{'Uploaded new' if action_type == 'SIGNATURE_UPLOAD' else 'Updated'} signature (v{sig_record.version}) for {user.display_name} ({user.username}). Designation: {sig_record.title}.",
            new_state={
                "user_id": user.id,
                "version": sig_record.version,
                "title": sig_record.title,
                "department": sig_record.department_or_office,
                "status": sig_record.status,
            }
        )
    except Exception:
        pass

    return sig_record


def approve_user_signature(user, actor=None, request=None):
    """Approves and activates a user's official signature."""
    actor = actor or user
    sig = getattr(user, "user_signature", None) or getattr(user, "signature", None) or UserSignature.objects.filter(user=user).first()
    if not sig:
        return None

    now = timezone.now()
    sig.status = UserSignature.Status.ACTIVE
    sig.is_active = True
    sig.activated_at = now
    sig.verified_at = now
    sig.verified_by = actor
    sig.save(update_fields=["status", "is_active", "activated_at", "verified_at", "verified_by", "updated_at"])

    try:
        from university.models import AuditLog
        from university.audit_services import log_activity
        log_activity(
            user=actor,
            action=AuditLog.Action.SIGNATURE_APPROVE,
            module=AuditLog.Module.SIGNATURES,
            entity="UserSignature",
            entity_id=sig.id,
            description=f"Approved and activated signature profile for {user.display_name} ({user.username}).",
            new_state={"is_active": True, "status": sig.status}
        )
    except Exception:
        pass

    return sig


def deactivate_user_signature(user, actor=None, reason="", request=None):
    """Deactivates user's signature so it cannot be used on new documents."""
    actor = actor or user
    sig = getattr(user, "user_signature", None) or getattr(user, "signature", None) or UserSignature.objects.filter(user=user).first()
    if not sig:
        return None

    sig.is_active = False
    sig.status = UserSignature.Status.INACTIVE
    sig.save(update_fields=["is_active", "status", "updated_at"])

    try:
        from university.models import AuditLog
        from university.audit_services import log_activity
        log_activity(
            user=actor,
            action=AuditLog.Action.SIGNATURE_DEACTIVATE,
            module=AuditLog.Module.SIGNATURES,
            entity="UserSignature",
            entity_id=sig.id,
            description=f"Deactivated signature profile for {user.display_name} ({user.username}). Reason: {reason}",
            new_state={"is_active": False, "status": sig.status}
        )
    except Exception:
        pass

    return sig


def revoke_user_signature(user, actor=None, reason="", request=None):
    """Revokes user's official signature and voids its usability on new documents."""
    actor = actor or user
    sig = getattr(user, "user_signature", None) or getattr(user, "signature", None) or UserSignature.objects.filter(user=user).first()
    if not sig:
        return None

    now = timezone.now()
    sig.is_active = False
    sig.status = UserSignature.Status.REVOKED
    sig.revoked_at = now
    sig.save(update_fields=["is_active", "status", "revoked_at", "updated_at"])

    try:
        from university.models import AuditLog
        from university.audit_services import log_activity
        log_activity(
            user=actor,
            action=AuditLog.Action.SIGNATURE_REVOKE,
            module=AuditLog.Module.SIGNATURES,
            entity="UserSignature",
            entity_id=sig.id,
            description=f"Revoked official signature for {user.display_name} ({user.username}). Reason: {reason}",
            new_state={"is_active": False, "status": sig.status}
        )
    except Exception:
        pass

    return sig


def remove_user_signature(user, actor=None, request=None):
    """Removes the active signature image file while preserving historical versions."""
    actor = actor or user
    sig = getattr(user, "user_signature", None) or getattr(user, "signature", None) or UserSignature.objects.filter(user=user).first()
    if not sig:
        return None

    now = timezone.now()
    if sig.signature_image:
        UserSignatureHistory.objects.create(
            signature=sig,
            user=user,
            version=sig.version,
            signature_image=sig.signature_image,
            title=sig.title,
            department_or_office=sig.department_or_office,
            status=UserSignature.Status.REVOKED,
            valid_from=sig.activated_at or sig.created_at,
            valid_until=now,
        )

    sig.signature_image.delete(save=False)
    sig.signature_image = None
    sig.is_active = False
    sig.status = UserSignature.Status.REVOKED
    sig.revoked_at = now
    sig.save()

    try:
        from university.models import AuditLog
        from university.audit_services import log_activity
        log_activity(
            user=actor,
            action=AuditLog.Action.SIGNATURE_REMOVE,
            module=AuditLog.Module.SIGNATURES,
            entity="UserSignature",
            entity_id=sig.id,
            description=f"Removed active signature for {user.display_name} ({user.username}).",
            new_state={"is_active": False, "status": sig.status}
        )
    except Exception:
        pass

    return sig


def get_user_active_signature(user):
    """Returns active UserSignature instance if valid, else None."""
    if not user or not user.is_authenticated:
        return None
    sig = getattr(user, "user_signature", None) or getattr(user, "signature", None) or UserSignature.objects.filter(user=user).first()
    if sig and sig.is_usable:
        return sig
    return None

