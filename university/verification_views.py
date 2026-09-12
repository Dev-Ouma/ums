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

    # Reference serial format: UMS/TR/<YEAR>/<ROLL_NO>-<DIGEST> or UMS/ADM/<YEAR>/<SEQ>
    # Handle possible URL encoding or dash replacements
    clean_ref = ref.replace("%2F", "/").replace("%20", " ")
    clean_ref_slash = clean_ref.replace("-", "/")

    # 1. Check for Issued Admission Letter
    from university.models import IssuedAdmissionDocument
    from university.admission_document_services import compute_admission_document_checksum

    issued_doc = IssuedAdmissionDocument.objects.filter(
        document_reference__iexact=clean_ref
    ).select_related("application", "application__program", "application__intake", "student", "student__user").first()

    if not issued_doc:
        issued_doc = IssuedAdmissionDocument.objects.filter(
            document_reference__iexact=clean_ref_slash
        ).select_related("application", "application__program", "application__intake", "student", "student__user").first()

    if issued_doc:
        app = issued_doc.application
        rendered_ctx = issued_doc.rendered_context or {}
        full_hash = compute_admission_document_checksum(issued_doc, rendered_ctx)
        
        context["is_verified"] = issued_doc.status in [IssuedAdmissionDocument.Status.ISSUED, IssuedAdmissionDocument.Status.CURRENT]
        context["status"] = "VALID" if context["is_verified"] else issued_doc.status
        context["status_label"] = "Certified Authentic Admission Offer" if context["is_verified"] else f"Admission Letter {issued_doc.get_status_display()}"
        context["document_type"] = "Official Letter of Admission"
        context["admission_doc"] = issued_doc
        context["application"] = app
        context["ctx"] = {
            "student_name": app.full_name,
            "registration_number": rendered_ctx.get("registration_number") or app.admitted_reg_no or (app.student.roll_no if app.student else "N/A"),
            "programme_name": app.program.name if app.program else "Undergraduate Degree",
            "programme_code": app.program.code if app.program else "",
            "intake": app.intake.name if app.intake else "Regular Intake",
            "academic_year": str(issued_doc.academic_year.name if issued_doc.academic_year else "2026/2027"),
            "digest": full_hash,
            "issue_date": issued_doc.issue_date,
            "signatory_name": issued_doc.signatory_name or "Dr. Margaret Omolo, PhD",
            "signatory_title": issued_doc.signatory_title or "Academic Registrar",
        }
    else:
        student = None
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
