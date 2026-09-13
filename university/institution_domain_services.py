"""
Centralized Institutional Domain & Identity Authority.

Provides a single source of truth for university branding, root internet domain,
staff and student email domains, dynamic email generation, server-side validation,
domain migration previews, and safe migration execution with historical alias preservation.
"""

import logging
import re
import unicodedata
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from university.models import AuditLog, DomainMigrationRecord, SystemSetting
from university.audit_services import log_activity
from university.settings_services import get_setting, set_setting

logger = logging.getLogger(__name__)

AUDIT_MODULE = AuditLog.Module.CONFIG


# ==============================================================================
# 1. DOMAIN CONFIGURATION & SETTINGS RESOLUTION
# ==============================================================================

def clean_domain(domain_str):
    """Normalize a domain string by stripping leading @, protocols, trailing slashes, and spaces."""
    if not domain_str:
        return ""
    d = str(domain_str).strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.lstrip("@").rstrip("/")
    return d


def derive_subdomains(primary_domain, student_prefix="students"):
    """
    Derive default staff and student domains from the primary root domain.
    Staff domain defaults to root domain (@domain.ac.ke).
    Student domain defaults to @{student_prefix}.{domain.ac.ke}.
    """
    cleaned_root = clean_domain(primary_domain) or "ums.ac.ke"
    cleaned_prefix = (student_prefix or "").strip().lower().strip(".")

    staff_domain = cleaned_root
    student_domain = f"{cleaned_prefix}.{cleaned_root}" if cleaned_prefix else cleaned_root

    return {
        "primary_domain": cleaned_root,
        "staff_domain": staff_domain,
        "student_domain": student_domain,
        "student_prefix": cleaned_prefix or "students",
    }


def get_institution_settings():
    """
    Retrieve current institutional branding, domains, and contact configurations.
    Single source of truth used across all modules, templates, and services.
    """
    primary_domain = clean_domain(get_setting("primary_domain", "ums.ac.ke")) or "ums.ac.ke"
    student_prefix = (get_setting("student_email_subdomain_prefix", "students") or "students").strip().lower()
    derived = derive_subdomains(primary_domain, student_prefix)

    # Allow custom explicit override of staff and student email domains
    staff_domain = clean_domain(get_setting("email_staff_domain", derived["staff_domain"])) or derived["staff_domain"]
    student_domain = clean_domain(get_setting("email_student_domain", derived["student_domain"])) or derived["student_domain"]
    admin_domain = clean_domain(get_setting("email_admin_domain", staff_domain)) or staff_domain

    inst_name = get_setting("institution_name", "Meru University of Science and Technology")
    short_name = get_setting("institution_code", get_setting("institution_short_name", "UMS"))

    website_url = get_setting("institution_website_url", f"https://{primary_domain}")
    portal_url = get_setting("institution_portal_url", f"https://portal.{primary_domain}")
    admissions_email = get_setting("institution_admissions_email", f"admissions@{staff_domain}")
    finance_email = get_setting("institution_finance_email", f"finance@{staff_domain}")
    support_email = get_setting("institution_support_email", f"support@{staff_domain}")

    staff_format = get_setting("email_staff_format", "{FIRST}.{LAST}")
    student_format = get_setting("email_student_format", "{REGNO}")

    return {
        "institution_name": inst_name,
        "institution_short_name": short_name,
        "primary_domain": primary_domain,
        "staff_email_domain": staff_domain,
        "student_email_domain": student_domain,
        "admin_email_domain": admin_domain,
        "student_email_subdomain_prefix": student_prefix,
        "staff_email_format": staff_format,
        "student_email_format": student_format,
        "website_url": website_url,
        "portal_url": portal_url,
        "admissions_email": admissions_email,
        "finance_email": finance_email,
        "support_email": support_email,
        "institution_address": get_setting("institution_address", "P.O. Box 972-60200, Meru, Kenya"),
        "institution_phone": get_setting("institution_phone", "+254 700 000 000"),
        "institution_motto": get_setting("institution_motto", "Excellence in Knowledge, Integrity in Leadership"),
    }


def get_primary_domain():
    return get_institution_settings()["primary_domain"]


def get_staff_email_domain():
    return get_institution_settings()["staff_email_domain"]


def get_student_email_domain():
    return get_institution_settings()["student_email_domain"]


def get_student_subdomain_prefix():
    return get_institution_settings()["student_email_subdomain_prefix"]


# ==============================================================================
# 2. EMAIL ADDRESS GENERATION & VALIDATION
# ==============================================================================

def _clean_slug(value):
    """ASCII-fold and strip characters that are invalid in an email local part."""
    val = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    val = re.sub(r"[^A-Za-z0-9._-]+", "", val.replace(" ", ".").replace("/", "."))
    return val.strip("._-").lower()


def generate_staff_email(first_name="", last_name="", username="", staff_id=""):
    """
    Generate an official institutional staff email address based on active settings.
    Example: m.wabs@ums.ac.ke or firstname.lastname@university.ac.ke
    """
    cfg = get_institution_settings()
    domain = cfg["staff_email_domain"]
    fmt = cfg["staff_email_format"]

    first = _clean_slug(first_name)
    last = _clean_slug(last_name)
    initial = first[:1] if first else ""
    uname = _clean_slug(username)
    sid = _clean_slug(staff_id)

    local_part = fmt
    local_part = local_part.replace("{FIRST}", first).replace("{LAST}", last)
    local_part = local_part.replace("{INITIAL}", initial).replace("{USERNAME}", uname)
    local_part = local_part.replace("{STAFFID}", sid)
    local_part = re.sub(r"\{[A-Za-z0-9_]+\}", "", local_part)
    local_part = re.sub(r"\.{2,}", ".", local_part).strip("._-")

    if not local_part:
        local_part = uname or (f"{first}.{last}" if first and last else first or "staff")

    return f"{local_part}@{domain}"


def generate_student_email(reg_number="", username=""):
    """
    Generate an official student institutional email matching:
    {reg_number}@{student_subdomain}.{root_domain}
    Example: cs012026@students.ums.ac.ke
    """
    cfg = get_institution_settings()
    domain = cfg["student_email_domain"]
    fmt = cfg["student_email_format"]

    cleaned_reg = re.sub(r"[^A-Za-z0-9]", "", str(reg_number or "")).lower()
    uname = _clean_slug(username)

    local_part = fmt.replace("{REGNO}", cleaned_reg).replace("{STUDENTID}", cleaned_reg).replace("{USERNAME}", uname)
    local_part = re.sub(r"\{[A-Za-z0-9_]+\}", "", local_part).strip("._-")

    if not local_part:
        local_part = cleaned_reg or uname or "student"

    return f"{local_part}@{domain}"


def validate_staff_email(email):
    """
    Validate that the email address adheres to the active institutional staff domain.
    Returns (is_valid: bool, error_message: str)
    """
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    
    cfg = get_institution_settings()
    expected_domain = cfg["staff_email_domain"].lower()
    actual_domain = email.strip().split("@")[-1].lower()

    if actual_domain != expected_domain:
        return False, f"Staff email must belong to the official domain '@{expected_domain}'."
    return True, ""


def validate_student_email(email, reg_no=None):
    """
    Validate that the student email adheres to the active student domain and registration format.
    Returns (is_valid: bool, error_message: str)
    """
    if not email or "@" not in email:
        return False, "Enter a valid email address."

    cfg = get_institution_settings()
    expected_domain = cfg["student_email_domain"].lower()
    parts = email.strip().split("@")
    actual_local, actual_domain = parts[0].lower(), parts[-1].lower()

    if actual_domain != expected_domain:
        return False, f"Student email must belong to the official student domain '@{expected_domain}'."

    if reg_no:
        cleaned_reg = re.sub(r"[^A-Za-z0-9]", "", str(reg_no)).lower()
        if cleaned_reg and cleaned_reg not in actual_local:
            return False, f"Student email local part must contain registration number '{cleaned_reg}'."

    return True, ""


def is_institutional_email(email):
    """Check if an email address belongs to either the configured staff or student domain."""
    if not email or "@" not in email:
        return False
    actual_domain = email.strip().split("@")[-1].lower()
    cfg = get_institution_settings()
    return actual_domain in (cfg["staff_email_domain"].lower(), cfg["student_email_domain"].lower())


# ==============================================================================
# 3. DOMAIN MIGRATION PREVIEW & EXECUTION ENGINE
# ==============================================================================

def preview_domain_migration(new_primary_domain, new_staff_domain=None, new_student_domain=None,
                             new_student_prefix="students", new_institution_name=None, new_short_name=None):
    """
    Calculate a dry-run migration preview without altering any database records.
    Returns counts of affected accounts, sample email transformations, and migration policies.
    """
    from university.identity_models import InstitutionalEmail, UserType
    from accounts.models import User

    curr = get_institution_settings()
    clean_root = clean_domain(new_primary_domain) or curr["primary_domain"]
    clean_prefix = (new_student_prefix or "").strip().lower()
    derived = derive_subdomains(clean_root, clean_prefix)

    target_staff_domain = clean_domain(new_staff_domain) or derived["staff_domain"]
    target_student_domain = clean_domain(new_student_domain) or derived["student_domain"]
    target_inst_name = new_institution_name.strip() if new_institution_name else curr["institution_name"]
    target_short_name = new_short_name.strip() if new_short_name else curr["institution_short_name"]

    # Affected staff accounts (primary institutional emails matching old staff domain)
    staff_emails_qs = InstitutionalEmail.objects.filter(
        is_primary=True,
        kind__in=[UserType.STAFF, UserType.ADMIN],
        address__icontains=f"@{curr['staff_email_domain']}",
    )
    staff_affected_count = staff_emails_qs.count()

    # Affected student accounts (primary institutional emails matching old student domain)
    student_emails_qs = InstitutionalEmail.objects.filter(
        is_primary=True,
        kind=UserType.STUDENT,
        address__icontains=f"@{curr['student_email_domain']}",
    )
    student_affected_count = student_emails_qs.count()

    # If institutional emails aren't populated yet, fallback to User accounts matching domains
    if staff_affected_count == 0:
        staff_affected_count = User.objects.filter(
            email__iendswith=f"@{curr['staff_email_domain']}"
        ).count()

    if student_affected_count == 0:
        student_affected_count = User.objects.filter(
            email__iendswith=f"@{curr['student_email_domain']}"
        ).count()

    # Build sample transformations for review modal
    sample_staff = []
    for em in staff_emails_qs.select_related("user")[:5]:
        local_p = em.address.split("@")[0]
        sample_staff.append({
            "name": em.user.get_full_name() or em.user.username,
            "current": em.address,
            "new": f"{local_p}@{target_staff_domain}",
        })
    if not sample_staff:
        sample_staff.append({
            "name": "Dr. Michael Wabs (Dean)",
            "current": f"m.wabs@{curr['staff_email_domain']}",
            "new": f"m.wabs@{target_staff_domain}",
        })

    sample_students = []
    for em in student_emails_qs.select_related("user")[:5]:
        local_p = em.address.split("@")[0]
        sample_students.append({
            "name": em.user.get_full_name() or em.user.username,
            "current": em.address,
            "new": f"{local_p}@{target_student_domain}",
        })
    if not sample_students:
        sample_students.append({
            "name": "Amina Ouma (BSc Nursing)",
            "current": f"cs012026@{curr['student_email_domain']}",
            "new": f"cs012026@{target_student_domain}",
        })

    is_domain_changing = (
        clean_root != curr["primary_domain"]
        or target_staff_domain != curr["staff_email_domain"]
        or target_student_domain != curr["student_email_domain"]
    )

    return {
        "current": curr,
        "target": {
            "institution_name": target_inst_name,
            "institution_short_name": target_short_name,
            "primary_domain": clean_root,
            "staff_email_domain": target_staff_domain,
            "student_email_domain": target_student_domain,
            "student_email_subdomain_prefix": clean_prefix,
            "website_url": f"https://{clean_root}",
            "portal_url": f"https://portal.{clean_root}",
            "admissions_email": f"admissions@{target_staff_domain}",
            "finance_email": f"finance@{target_staff_domain}",
        },
        "is_domain_changing": is_domain_changing,
        "staff_affected_count": staff_affected_count,
        "student_affected_count": student_affected_count,
        "total_affected_count": staff_affected_count + student_affected_count,
        "sample_staff": sample_staff,
        "sample_students": sample_students,
    }


@transaction.atomic
def execute_domain_migration(new_settings_data, initiated_by=None,
                             migration_policy=DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES,
                             request=None):
    """
    Execute a full institutional domain and identity migration:
    1. Persists updated institution & domain settings in SystemSetting.
    2. Rotates active user InstitutionalEmail records while preserving historical aliases as ARCHIVED.
    3. Retains historical receipts, invoices, transcripts, and audit logs untouched.
    4. Generates a formal DomainMigrationRecord and logs to AuditLog.
    """
    from university.identity_models import InstitutionalEmail, UserType
    from accounts.models import User

    current_settings = get_institution_settings()

    target_name = (new_settings_data.get("institution_name") or current_settings["institution_name"]).strip()
    target_short = (new_settings_data.get("institution_short_name") or current_settings["institution_short_name"]).strip()
    target_primary = clean_domain(new_settings_data.get("primary_domain") or current_settings["primary_domain"])
    target_prefix = (new_settings_data.get("student_email_subdomain_prefix") or current_settings["student_email_subdomain_prefix"]).strip().lower()

    derived = derive_subdomains(target_primary, target_prefix)
    target_staff = clean_domain(new_settings_data.get("staff_email_domain") or derived["staff_domain"])
    target_student = clean_domain(new_settings_data.get("student_email_domain") or derived["student_domain"])

    # Update SystemSetting values
    set_setting("institution_name", target_name, user=initiated_by, request=request)
    set_setting("institution_code", target_short, user=initiated_by, request=request)
    set_setting("institution_short_name", target_short, user=initiated_by, request=request)
    set_setting("primary_domain", target_primary, user=initiated_by, request=request)
    set_setting("email_staff_domain", target_staff, user=initiated_by, request=request)
    set_setting("email_student_domain", target_student, user=initiated_by, request=request)
    set_setting("email_admin_domain", target_staff, user=initiated_by, request=request)
    set_setting("student_email_subdomain_prefix", target_prefix, user=initiated_by, request=request)

    if new_settings_data.get("website_url"):
        set_setting("institution_website_url", new_settings_data["website_url"].strip(), user=initiated_by, request=request)
    if new_settings_data.get("portal_url"):
        set_setting("institution_portal_url", new_settings_data["portal_url"].strip(), user=initiated_by, request=request)
    if new_settings_data.get("admissions_email"):
        set_setting("institution_admissions_email", new_settings_data["admissions_email"].strip(), user=initiated_by, request=request)
    if new_settings_data.get("finance_email"):
        set_setting("institution_finance_email", new_settings_data["finance_email"].strip(), user=initiated_by, request=request)

    staff_migrated = 0
    student_migrated = 0
    total_migrated = 0
    migration_logs = []

    old_staff_domain = current_settings["staff_email_domain"]
    old_student_domain = current_settings["student_email_domain"]

    # Process account migrations if policy calls for it
    if migration_policy in (
        DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES,
        DomainMigrationRecord.Policy.FULL_REPLACE,
    ):
        # 1. Staff Emails Migration
        if old_staff_domain != target_staff:
            staff_emails = list(InstitutionalEmail.objects.filter(
                is_primary=True,
                kind__in=[UserType.STAFF, UserType.ADMIN],
                address__icontains=f"@{old_staff_domain}",
            ).select_related("user"))

            for email_rec in staff_emails:
                user = email_rec.user
                local_part = email_rec.address.split("@")[0]
                new_address = f"{local_part}@{target_staff}"

                if migration_policy == DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES:
                    # Archive current address as alias
                    email_rec.is_primary = False
                    email_rec.status = InstitutionalEmail.Status.ARCHIVED
                    email_rec.archived_at = timezone.now()
                    email_rec.save(update_fields=["is_primary", "status", "archived_at"])

                    # Create new primary address
                    if not InstitutionalEmail.objects.filter(address__iexact=new_address).exists():
                        InstitutionalEmail.objects.create(
                            user=user,
                            address=new_address,
                            kind=email_rec.kind,
                            status=InstitutionalEmail.Status.ACTIVE,
                            is_primary=True,
                            created_by=initiated_by,
                        )
                else:
                    # In-place replace
                    email_rec.address = new_address
                    email_rec.save(update_fields=["address"])

                # Update User.email if it matches old address
                if user.email and user.email.lower() == email_rec.address.lower():
                    user.email = new_address
                    user.save(update_fields=["email"])

                staff_migrated += 1
                total_migrated += 1

        # 2. Student Emails Migration
        if old_student_domain != target_student:
            student_emails = list(InstitutionalEmail.objects.filter(
                is_primary=True,
                kind=UserType.STUDENT,
                address__icontains=f"@{old_student_domain}",
            ).select_related("user"))

            for email_rec in student_emails:
                user = email_rec.user
                local_part = email_rec.address.split("@")[0]
                new_address = f"{local_part}@{target_student}"

                if migration_policy == DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES:
                    # Archive current address as alias
                    email_rec.is_primary = False
                    email_rec.status = InstitutionalEmail.Status.ARCHIVED
                    email_rec.archived_at = timezone.now()
                    email_rec.save(update_fields=["is_primary", "status", "archived_at"])

                    # Create new primary address
                    if not InstitutionalEmail.objects.filter(address__iexact=new_address).exists():
                        InstitutionalEmail.objects.create(
                            user=user,
                            address=new_address,
                            kind=UserType.STUDENT,
                            status=InstitutionalEmail.Status.ACTIVE,
                            is_primary=True,
                            created_by=initiated_by,
                        )
                else:
                    email_rec.address = new_address
                    email_rec.save(update_fields=["address"])

                if user.email and user.email.lower() == email_rec.address.lower():
                    user.email = new_address
                    user.save(update_fields=["email"])

                student_migrated += 1
                total_migrated += 1

    migration_logs.append(
        f"Domain migrated: {current_settings['primary_domain']} -> {target_primary}. "
        f"Staff domain: {old_staff_domain} -> {target_staff} ({staff_migrated} migrated). "
        f"Student domain: {old_student_domain} -> {target_student} ({student_migrated} migrated)."
    )

    # Create DomainMigrationRecord
    record = DomainMigrationRecord.objects.create(
        initiated_by=initiated_by,
        previous_institution_name=current_settings["institution_name"],
        new_institution_name=target_name,
        previous_short_name=current_settings["institution_short_name"],
        new_short_name=target_short,
        previous_primary_domain=current_settings["primary_domain"],
        new_primary_domain=target_primary,
        previous_staff_domain=old_staff_domain,
        new_staff_domain=target_staff,
        previous_student_domain=old_student_domain,
        new_student_domain=target_student,
        previous_student_prefix=current_settings["student_email_subdomain_prefix"],
        new_student_prefix=target_prefix,
        migration_policy=migration_policy,
        staff_accounts_affected=staff_migrated,
        student_accounts_affected=student_migrated,
        total_emails_migrated=total_migrated,
        status=DomainMigrationRecord.Status.COMPLETED,
        logs="\n".join(migration_logs),
        details={
            "updated_settings": {
                "institution_name": target_name,
                "primary_domain": target_primary,
                "staff_domain": target_staff,
                "student_domain": target_student,
            },
            "migrated_counts": {
                "staff": staff_migrated,
                "students": student_migrated,
                "total": total_migrated,
            }
        },
    )

    log_activity(
        request=request,
        user=initiated_by,
        action=AuditLog.Action.UPDATE,
        module=AUDIT_MODULE,
        entity="InstitutionalDomainSettings",
        entity_id=record.pk,
        description=(
            f"Institutional domain migrated from '{current_settings['primary_domain']}' to '{target_primary}'. "
            f"Migrated {total_migrated} institutional accounts ({staff_migrated} staff, {student_migrated} students)."
        ),
        previous_state=current_settings,
        new_state=get_institution_settings(),
    )

    return record
