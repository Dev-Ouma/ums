import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils import timezone

from accounts.models import Role
from university.models import Application, Intake, Program, ApplicationFeePayment, AuditLog
from university.audit_services import log_activity
from university.settings_services import get_setting
from university.institution_domain_services import get_institution_settings
from accounts.email_identity_service import EmailIdentityService
from university.identity_services import enforce_concurrent_session_policy

logger = logging.getLogger(__name__)
User = get_user_model()


def generate_otp_code() -> str:
    """Generate a cryptographically secure 6-digit numeric OTP."""
    return f"{secrets.randbelow(900000) + 100000}"


def register_applicant(request, first_name: str, last_name: str, email: str, phone: str, password: str):
    """
    Registers a new applicant user account with Role.APPLICANT and dispatches verification OTP.
    """
    email = email.strip().lower()
    first_name = first_name.strip()
    last_name = last_name.strip()
    phone = phone.strip()

    if not (first_name and last_name and email and password):
        raise ValidationError("Please provide first name, last name, email, and a secure password.")

    if not EmailIdentityService.is_available(email):
        existing_user = User.objects.filter(email__iexact=email).first()
        if existing_user and existing_user.role != Role.APPLICANT:
            raise ValidationError("This email is already registered in the system. Please log in to continue your application.")

        # If user is already active with prior login or non-draft applications, force password login
        if existing_user and (
            existing_user.last_login is not None or
            Application.objects.filter(
                applicant_user=existing_user,
                status__in=[
                    Application.Status.SUBMITTED,
                    Application.Status.UNDER_REVIEW,
                    Application.Status.ACCEPTED,
                    Application.Status.ENROLLED,
                ],
            ).exists()
        ):
            raise ValidationError("An account with this email address already exists. Please sign in with your email and password.")

        # Re-send OTP for unverified applicant registration
        otp_code = generate_otp_code()
        request.session["applicant_pending_verification"] = {
            "email": email,
            "user_id": existing_user.id,
            "otp_code": otp_code,
            "generated_at": timezone.now().isoformat(),
            "attempts": 0,
        }
        request.session.modified = True
        _send_applicant_otp_email(email, first_name, otp_code)
        return existing_user, otp_code

    # Generate unique username
    base_username = email.split("@")[0].replace(".", "").lower()[:20]
    username = base_username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1

    user = User.objects.create_user(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        password=password,
        role=Role.APPLICANT,
        is_active=True,
    )

    otp_code = generate_otp_code()
    request.session["applicant_pending_verification"] = {
        "email": email,
        "user_id": user.id,
        "otp_code": otp_code,
        "generated_at": timezone.now().isoformat(),
        "attempts": 0,
    }
    request.session.modified = True

    _send_applicant_otp_email(email, first_name, otp_code)
    log_activity(
        request=request,
        user=user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.AUTH,
        entity="ApplicantUser",
        entity_id=user.pk,
        description=f"Applicant account registered for {email}.",
    )
    return user, otp_code


def _send_applicant_otp_email(email: str, name: str, otp_code: str):
    """Sends OTP verification code via email."""
    subject = "Verify Your Admissions Applicant Account - UMS"
    message = (
        f"Dear {name},\n\n"
        f"Thank you for expressing interest in applying to our University.\n\n"
        f"Your 6-digit verification code (OTP) is:\n\n"
        f"    {otp_code}\n\n"
        f"This code will expire in 15 minutes. Please enter it on the verification screen to proceed with your application.\n\n"
        f"Best regards,\n"
        f"Directorate of Admissions\n"
        f"University Management System"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL or get_institution_settings().get("admissions_email", "admissions@ums.ac.ke"),
            recipient_list=[email],
            fail_silently=True,
        )
    except Exception as e:
        logger.warning("Failed to send applicant OTP email to %s: %s", email, e)


def verify_applicant_otp(request, email: str, entered_otp: str):
    """
    Verifies the pending OTP for an applicant and authenticates them into the session.
    Enforces 15-minute expiration, max 5 attempts, and constant-time string comparison.
    """
    pending = request.session.get("applicant_pending_verification")
    if not pending or pending.get("email") != email.strip().lower():
        raise ValidationError("No pending verification found for this email. Please register again.")

    # Expiration check (15 minutes)
    gen_time_str = pending.get("generated_at")
    if gen_time_str:
        try:
            gen_time = datetime.fromisoformat(gen_time_str)
            if timezone.is_naive(gen_time):
                gen_time = timezone.make_aware(gen_time)
            if timezone.now() - gen_time > timedelta(minutes=15):
                request.session.pop("applicant_pending_verification", None)
                request.session.modified = True
                raise ValidationError("Verification code has expired. Please register or request a new code.")
        except ValidationError:
            raise
        except Exception:
            pass

    # Brute-force throttling (max 5 attempts)
    attempts = pending.get("attempts", 0) + 1
    pending["attempts"] = attempts
    request.session.modified = True
    if attempts > 5:
        request.session.pop("applicant_pending_verification", None)
        request.session.modified = True
        raise ValidationError("Too many incorrect verification attempts. Please register again.")

    stored_otp = str(pending.get("otp_code", "")).strip()
    if not stored_otp or not secrets.compare_digest(stored_otp, str(entered_otp).strip()):
        raise ValidationError("Invalid verification code. Please check your email and enter the correct 6-digit code.")

    user = User.objects.filter(pk=pending.get("user_id")).first()
    if not user:
        raise ValidationError("User account could not be found.")

    # Clean up verification session
    request.session.pop("applicant_pending_verification", None)
    request.session.modified = True

    # Log in user
    login(request, user)
    enforce_concurrent_session_policy(user, keep_session_key=request.session.session_key)
    return user


def get_applicant_active_application(user):
    """
    Returns the current active application for the authenticated applicant, if any.
    """
    if not user or not user.is_authenticated:
        return None
    return Application.objects.filter(
        applicant_user=user
    ).order_by("-created_at").first()
