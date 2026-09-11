"""Privacy-rights helpers with a deliberately narrow, user-owned export scope."""

from django.utils import timezone

from .identity_models import LoginRecord


def _date(value):
    return value.isoformat() if value else None


def build_personal_data_export(user):
    """Return data the signed-in subject can download about their account.

    Credentials, password hashes, reset digests, session keys, internal notes,
    and other users' records are intentionally excluded from this export.
    Institutional records that cannot be deleted for legal or academic reasons
    remain subject to the DPO request workflow and retention schedule.
    """
    data = {
        "exported_at": timezone.now().isoformat(),
        "scope": "personal account and identity data",
        "account": {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "role": user.get_role_display(),
            "date_joined": _date(user.date_joined),
            "first_seen_at": _date(user.first_seen_at),
            "last_seen_at": _date(user.last_seen_at),
        },
        "institutional_emails": list(user.institutional_emails.values(
            "address", "kind", "status", "provider", "is_primary", "archived_at"
        )),
        "groups": list(user.group_memberships.select_related("group").values_list(
            "group__name", flat=True
        )),
        "login_activity": [
            {
                "username_attempted": record.username_attempted,
                "success": record.success,
                "failure_reason": record.failure_reason,
                "ip_address": record.ip_address,
                "user_agent": record.user_agent,
                "device_type": record.device_type,
                "mfa_used": record.mfa_used,
                "login_at": _date(record.login_at),
                "logout_at": _date(record.logout_at),
            }
            for record in LoginRecord.objects.filter(user=user)[:100]
        ],
    }

    if hasattr(user, "account"):
        data["account_security"] = {
            "user_type": user.account.get_user_type_display(),
            "status": user.account.get_effective_status_display()
            if hasattr(user.account, "get_effective_status_display")
            else user.account.effective_status,
            "password_changed_at": _date(user.account.password_changed_at),
            "password_expires_at": _date(user.account.password_expires_at),
            "mfa_enabled": user.account.mfa_enabled,
        }

    if hasattr(user, "student_profile"):
        profile = user.student_profile
        data["student_profile"] = {
            "roll_no": profile.roll_no,
            "status": profile.get_status_display(),
            "program": str(profile.program) if profile.program else None,
            "current_semester": profile.current_semester,
            "gender": profile.get_gender_display(),
            "date_of_birth": _date(profile.date_of_birth),
            "admission_date": _date(profile.admission_date),
            "address": profile.address,
            "guardian_name": profile.guardian_name,
            "guardian_relationship": profile.guardian_relationship,
            "guardian_phone": profile.guardian_phone,
            "guardian_email": profile.guardian_email,
            "guardian_address": profile.guardian_address,
        }

    if hasattr(user, "faculty_profile"):
        profile = user.faculty_profile
        data["faculty_profile"] = {
            "employee_id": profile.employee_id,
            "department": str(profile.department) if profile.department else None,
            "designation": profile.designation,
            "specialization": profile.specialization,
            "joining_date": _date(profile.joining_date),
        }

    return data
