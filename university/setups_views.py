import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from university.models import SystemSetting
from university.settings_services import seed_default_settings, set_setting


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
def admin_setups_dashboard(request):
    """
    Centralized Administrative Setup and System Configuration hub.
    Organized into 7 strategic categories:
    - Academic Setup
    - University Structure
    - Course & Examination Setup
    - Timetable Setup
    - Student & Registration Setup
    - System & Organization Settings (Finance/Branding)
    - User & Security Setup
    """
    # Ensure baseline settings exist
    if SystemSetting.objects.count() == 0:
        seed_default_settings()

    active_tab = request.GET.get("tab", "academic").lower()
    valid_tabs = [
        ("academic", "Academic Setup", "bi-mortarboard", SystemSetting.Category.ACADEMIC),
        ("university", "University Structure", "bi-building", SystemSetting.Category.UNIVERSITY),
        ("examination", "Course & Examination", "bi-journal-check", SystemSetting.Category.EXAMINATION),
        ("timetable", "Timetable Setup", "bi-calendar3", SystemSetting.Category.TIMETABLE),
        ("registration", "Student & Registration", "bi-person-badge", SystemSetting.Category.REGISTRATION),
        ("finance", "System & Branding", "bi-sliders", SystemSetting.Category.FINANCE),
        ("security", "User & Security", "bi-shield-lock", SystemSetting.Category.SECURITY),
    ]

    tab_keys = [t[0] for t in valid_tabs]
    if active_tab not in tab_keys:
        active_tab = "academic"

    # Group settings by category
    categorized_settings = {}
    for tab_key, tab_label, tab_icon, cat_enum in valid_tabs:
        categorized_settings[tab_key] = {
            "label": tab_label,
            "icon": tab_icon,
            "category": cat_enum,
            "settings": SystemSetting.objects.filter(category=cat_enum).order_by("key"),
        }

    context = {
        "active_tab": active_tab,
        "valid_tabs": valid_tabs,
        "categorized_settings": categorized_settings,
        "current_group": categorized_settings[active_tab],
        "total_settings": SystemSetting.objects.count(),
    }
    return render(request, "setups/dashboard.html", context)


@login_required
@_admin_required
@require_POST
def admin_setups_update(request, category):
    """
    Handle batch update for settings within a specific configuration category.
    Validates types, persists changes, and logs to Audit Trail.
    """
    category_upper = category.upper()
    settings_qs = SystemSetting.objects.filter(category=category_upper)

    if not settings_qs.exists():
        messages.error(request, f"Unknown setup category '{category}'.")
        return redirect("university:admin_setups_dashboard")

    updated_count = 0
    errors = []

    for setting in settings_qs:
        field_key = f"setting_{setting.key}"
        if setting.value_type == SystemSetting.ValueType.BOOLEAN:
            # Checkbox: if present in POST -> True, otherwise False
            val_bool = field_key in request.POST
            if str(setting.value).lower() != ("true" if val_bool else "false"):
                set_setting(setting.key, val_bool, user=request.user, request=request)
                updated_count += 1
        elif field_key in request.POST:
            raw_val = request.POST[field_key].strip()

            # Validation per type
            if setting.value_type == SystemSetting.ValueType.INTEGER:
                try:
                    int_val = int(raw_val)
                    if str(setting.value) != str(int_val):
                        set_setting(setting.key, int_val, user=request.user, request=request)
                        updated_count += 1
                except ValueError:
                    errors.append(f"Value for '{setting.label}' must be an integer.")
            elif setting.value_type == SystemSetting.ValueType.DECIMAL:
                try:
                    from decimal import Decimal
                    dec_val = Decimal(raw_val)
                    if str(setting.value) != str(dec_val):
                        set_setting(setting.key, dec_val, user=request.user, request=request)
                        updated_count += 1
                except Exception:
                    errors.append(f"Value for '{setting.label}' must be a valid number.")
            elif setting.value_type == SystemSetting.ValueType.JSON:
                try:
                    parsed_json = json.loads(raw_val)
                    # Verify changes
                    if json.dumps(parsed_json, sort_keys=True) != json.dumps(json.loads(setting.value), sort_keys=True):
                        set_setting(setting.key, parsed_json, user=request.user, request=request)
                        updated_count += 1
                except json.JSONDecodeError as exc:
                    errors.append(f"Invalid JSON for '{setting.label}': {exc}")
            else:
                # String
                if setting.value != raw_val:
                    set_setting(setting.key, raw_val, user=request.user, request=request)
                    updated_count += 1

    if updated_count > 0:
        messages.success(request, f"Successfully updated {updated_count} setting(s) in {category.capitalize()} Setup.")
    elif not errors:
        messages.info(request, "No configuration changes were detected.")

    if errors:
        for err in errors:
            messages.error(request, err)

    return redirect(f"/manage/setups/?tab={category.lower()}")
