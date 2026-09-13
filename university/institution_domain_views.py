import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from university.models import DomainMigrationRecord
from university.institution_domain_services import (
    clean_domain,
    derive_subdomains,
    execute_domain_migration,
    get_institution_settings,
    preview_domain_migration,
    generate_staff_email,
    generate_student_email,
)


def _admin_required(view_func):
    """Ensure user is an active Administrator or Staff member."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        user_role = getattr(request.user, "role", "")
        if not (request.user.is_staff or request.user.is_superuser or user_role in (Role.ADMIN, "ADMIN")):
            messages.error(request, "Access restricted. System Administrator privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
@_admin_required
def institution_domain_settings(request):
    """
    Centralized Administrator Dashboard for Institution Identity & Domain Architecture.
    Allows administrators to configure university name, branding, root domain, staff domain,
    student email subdomains, preview migration impacts, and audit historical migrations.
    """
    settings_data = get_institution_settings()
    migration_history = DomainMigrationRecord.objects.all().order_by("-created_at")[:20]

    # Pre-calculate a live sample preview
    sample_preview = preview_domain_migration(settings_data["primary_domain"])

    context = {
        "settings": settings_data,
        "sample_preview": sample_preview,
        "migration_history": migration_history,
        "policy_choices": DomainMigrationRecord.Policy.choices,
    }
    return render(request, "setups/institution_domains.html", context)


@login_required
@_admin_required
def api_preview_domain_migration(request):
    """
    AJAX endpoint providing dynamic preview calculations and impact analysis
    when the administrator alters domains in the frontend before saving.
    """
    primary_domain = request.GET.get("primary_domain") or request.POST.get("primary_domain") or ""
    staff_domain = request.GET.get("staff_email_domain") or request.POST.get("staff_email_domain") or ""
    student_domain = request.GET.get("email_student_domain") or request.POST.get("email_student_domain") or ""
    student_prefix = request.GET.get("student_email_subdomain_prefix") or request.POST.get("student_email_subdomain_prefix") or "student"
    inst_name = request.GET.get("institution_name") or request.POST.get("institution_name") or ""
    short_name = request.GET.get("institution_short_name") or request.POST.get("institution_short_name") or ""

    preview = preview_domain_migration(
        new_primary_domain=primary_domain,
        new_staff_domain=staff_domain,
        new_student_domain=student_domain,
        new_student_prefix=student_prefix,
        new_institution_name=inst_name,
        new_short_name=short_name,
    )

    return JsonResponse({
        "success": True,
        "preview": preview,
    })


@login_required
@_admin_required
@require_POST
def execute_domain_migration_view(request):
    """
    Execute a confirmed institutional identity and domain update with full server-side validation,
    account email rotation, historical alias archiving, and audit logging.
    """
    inst_name = request.POST.get("institution_name", "").strip()
    short_name = request.POST.get("institution_short_name", "").strip()
    primary_domain = clean_domain(request.POST.get("primary_domain", "").strip())
    staff_domain = clean_domain(request.POST.get("staff_email_domain", "").strip())
    student_domain = clean_domain(request.POST.get("email_student_domain", "").strip())
    student_prefix = request.POST.get("student_email_subdomain_prefix", "student").strip().lower()
    website_url = request.POST.get("website_url", "").strip()
    portal_url = request.POST.get("portal_url", "").strip()
    admissions_email = request.POST.get("admissions_email", "").strip()
    finance_email = request.POST.get("finance_email", "").strip()
    migration_policy = request.POST.get("migration_policy", DomainMigrationRecord.Policy.MIGRATE_AND_ARCHIVE_ALIASES)

    if not primary_domain:
        messages.error(request, "Primary root domain is required (e.g. ums.ac.ke).")
        return redirect("university:admin_institution_settings")

    new_settings_data = {
        "institution_name": inst_name,
        "institution_short_name": short_name,
        "primary_domain": primary_domain,
        "staff_email_domain": staff_domain,
        "student_email_domain": student_domain,
        "student_email_subdomain_prefix": student_prefix,
        "website_url": website_url or f"https://{primary_domain}",
        "portal_url": portal_url or f"https://portal.{primary_domain}",
        "admissions_email": admissions_email or f"admissions@{staff_domain or primary_domain}",
        "finance_email": finance_email or f"finance@{staff_domain or primary_domain}",
    }

    try:
        record = execute_domain_migration(
            new_settings_data=new_settings_data,
            initiated_by=request.user,
            migration_policy=migration_policy,
            request=request,
        )
        messages.success(
            request,
            f"Institutional identity & domains successfully updated! Primary domain set to '{primary_domain}'. "
            f"Processed {record.total_emails_migrated} account email migrations."
        )
    except Exception as exc:
        messages.error(request, f"Error applying domain migration: {exc}")

    return redirect("university:admin_institution_settings")
