"""
Public Credential & Academic Document Verification Portal.
Permits employers, embassies, universities, and regulatory authorities to verify
the authentic certification status of academic transcripts and degree documents.
"""
from django.shortcuts import render
from django.utils import timezone
from accounts.models import StudentProfile
from university.models import AuditLog
from university.transcript_io import build_transcript_context
from university.audit_services import log_activity, get_client_ip, detect_device_type


def public_verify_document(request, reference_no=None):
    """
    Publicly accessible verification endpoint for transcripts and academic documents.
    Validates document reference serial against the institutional record and SHA-256 digest.
    """
    ref = (reference_no or request.GET.get("ref") or "").strip()
    
    context = {
        "reference_no": ref,
        "is_verified": False,
        "status": "NOT_FOUND",
        "status_label": "Unverified / Record Not Found",
        "verified_at": timezone.now(),
        "student": None,
        "ctx": None,
    }

    if not ref:
        return render(request, "verification/verify_document.html", context)

    # Reference serial format: UMS/TR/<YEAR>/<ROLL_NO>-<DIGEST>
    # Handle possible URL encoding or dash replacements
    clean_ref = ref.replace("%2F", "/").replace("%20", " ")
    
    student = None
    target_digest = None

    # Search for matching student by matching roll number within the reference
    for sp in StudentProfile.objects.select_related("user", "program", "program__department").all():
        if sp.roll_no in clean_ref:
            student = sp
            break

    if student:
        try:
            ctx = build_transcript_context(student)
            actual_ref = ctx.get("reference_no", "")
            actual_digest = ctx.get("digest", "")
            
            context["student"] = student
            context["ctx"] = ctx

            if clean_ref == actual_ref or (actual_digest and actual_digest in clean_ref):
                context["is_verified"] = True
                context["status"] = "VALID"
                context["status_label"] = "Certified Authentic Academic Record"
            else:
                context["is_verified"] = False
                context["status"] = "AMENDED"
                context["status_label"] = "Superseded or Inactive Digest (Academic Record Amended)"
        except Exception:
            context["status"] = "ERROR"
            context["status_label"] = "Verification check encountered a system error"

    # Log public verification request for security monitoring
    try:
        ip = get_client_ip(request)
        device = detect_device_type(request.META.get("HTTP_USER_AGENT", ""))
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=AuditLog.Action.VIEW,
            module=AuditLog.Module.ACADEMICS,
            ip_address=ip,
            device=device,
            details=f"Public document verification query for reference '{clean_ref}' -> Status: {context['status']}"
        )
    except Exception:
        pass

    return render(request, "verification/verify_document.html", context)
