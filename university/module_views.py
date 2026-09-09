"""
Module Management Administration Views.
Provides full control over module availability, submodules, features,
maintenance modes, and cross-module dependencies.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from university.models import AuditLog
from university.module_models import (
    ModuleStatus,
    SystemModule,
    SystemSubmodule,
    SystemFeature,
    ModuleDependency,
)
from university.module_services import (
    set_module_status,
    set_submodule_status,
    set_feature_status,
    bulk_set_modules_status,
    enable_all_modules,
    disable_all_configurable_modules,
    check_module_dependencies,
    seed_system_modules,
    invalidate_module_cache,
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
def admin_modules(request):
    """
    Central Module Management Cockpit.
    Displays complete system module hierarchy, submodules, features, statuses,
    dependency metrics, and administrative bulk controls.
    """
    # Ensure modules catalog is populated
    if SystemModule.objects.count() == 0:
        seed_system_modules()

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()
    category_filter = request.GET.get("category", "").strip()

    modules_qs = SystemModule.objects.prefetch_related(
        "submodules",
        "submodules__features",
        "dependencies_as_source__target_module",
        "dependencies_as_target__source_module",
    ).order_by("sort_order", "name")

    if q:
        modules_qs = modules_qs.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q) |
            Q(submodules__name__icontains=q) | Q(submodules__code__icontains=q)
        ).distinct()

    if status_filter and status_filter in dict(ModuleStatus.choices):
        modules_qs = modules_qs.filter(status=status_filter)

    if category_filter:
        modules_qs = modules_qs.filter(category=category_filter)

    # Compute global stats
    all_modules = SystemModule.objects.all()
    stats = {
        "total": all_modules.count(),
        "active": all_modules.filter(status=ModuleStatus.ENABLED).count(),
        "maintenance": all_modules.filter(status=ModuleStatus.MAINTENANCE).count(),
        "coming_soon": all_modules.filter(status=ModuleStatus.COMING_SOON).count(),
        "disabled": all_modules.filter(status=ModuleStatus.DISABLED).count(),
        "critical": all_modules.filter(is_critical=True).count(),
        "total_submodules": SystemSubmodule.objects.count(),
        "total_features": SystemFeature.objects.count(),
    }

    categories = list(SystemModule.objects.values_list("category", flat=True).distinct())

    return render(request, "system/admin_modules.html", {
        "modules": modules_qs,
        "stats": stats,
        "categories": categories,
        "status_choices": ModuleStatus.choices,
        "search_query": q,
        "selected_status": status_filter,
        "selected_category": category_filter,
    })


@login_required
@_admin_required
@require_POST
def admin_module_toggle(request, pk):
    """
    Quick status toggle between Active and Disabled/Maintenance.
    """
    mod = get_object_or_404(SystemModule, pk=pk)
    target_status = request.POST.get("status")

    if not target_status:
        # Default toggle logic
        target_status = ModuleStatus.DISABLED if mod.status == ModuleStatus.ENABLED else ModuleStatus.ENABLED

    status_message = request.POST.get("status_message", "")
    success, msg = set_module_status(
        module_code=mod.code,
        new_status=target_status,
        status_message=status_message,
        user=request.user,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": success, "message": msg, "new_status": target_status})

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:admin_modules")


@login_required
@_admin_required
@require_POST
def admin_module_update(request, pk):
    """
    Detailed module configuration: update status, custom user message,
    and optional submodule cascade.
    """
    mod = get_object_or_404(SystemModule, pk=pk)
    new_status = request.POST.get("status", mod.status)
    status_message = request.POST.get("status_message", "").strip()
    cascade = request.POST.get("cascade_submodules") == "1"

    success, msg = set_module_status(
        module_code=mod.code,
        new_status=new_status,
        status_message=status_message,
        user=request.user,
    )

    if success and cascade:
        mod.submodules.all().update(status=new_status)
        SystemFeature.objects.filter(submodule__module=mod).update(status=new_status)
        msg += " All child submodules & features updated to match."

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:admin_modules")


@login_required
@_admin_required
@require_POST
def admin_submodule_update(request, pk):
    """
    Update status and notice message of a specific Submodule.
    """
    sub = get_object_or_404(SystemSubmodule, pk=pk)
    new_status = request.POST.get("status", sub.status)
    status_message = request.POST.get("status_message", "").strip()

    success, msg = set_submodule_status(
        submodule_code=sub.code,
        new_status=new_status,
        status_message=status_message,
        user=request.user,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": success, "message": msg, "new_status": new_status})

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:admin_modules")


@login_required
@_admin_required
@require_POST
def admin_feature_toggle(request, pk):
    """
    Toggle status of an individual granular Feature.
    """
    feat = get_object_or_404(SystemFeature, pk=pk)
    target_status = ModuleStatus.DISABLED if feat.status == ModuleStatus.ENABLED else ModuleStatus.ENABLED

    success, msg = set_feature_status(
        feature_code=feat.code,
        new_status=target_status,
        user=request.user,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": success, "message": msg, "new_status": target_status})

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:admin_modules")


@login_required
@_admin_required
@require_POST
def admin_modules_bulk(request):
    """
    Execute bulk operations across multiple modules (Enable All, Disable All, or Bulk Selection).
    """
    action = request.POST.get("bulk_action", "").strip()
    status_message = request.POST.get("status_message", "").strip()

    if action == "enable_all":
        success, msg = enable_all_modules(user=request.user)
        messages.success(request, msg)
        return redirect("university:admin_modules")

    elif action == "disable_all":
        success, msg = disable_all_configurable_modules(user=request.user, status_message=status_message)
        messages.warning(request, msg)
        return redirect("university:admin_modules")

    # Selected modules
    selected_codes = request.POST.getlist("selected_modules")
    if not selected_codes:
        messages.error(request, "No modules were selected for bulk action.")
        return redirect("university:admin_modules")

    target_status = ModuleStatus.ENABLED
    if action == "disable_selected":
        target_status = ModuleStatus.DISABLED
    elif action == "maintenance_selected":
        target_status = ModuleStatus.MAINTENANCE
    elif action == "coming_soon_selected":
        target_status = ModuleStatus.COMING_SOON

    success, msg = bulk_set_modules_status(
        module_codes=selected_codes,
        new_status=target_status,
        user=request.user,
        status_message=status_message,
    )

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:admin_modules")


@login_required
@_admin_required
def admin_module_dependencies_api(request, pk):
    """
    Returns dependency graph details for a specific module.
    """
    mod = get_object_or_404(SystemModule, pk=pk)
    requires = [
        {
            "code": d.target_module.code,
            "name": d.target_module.name,
            "status": d.target_module.status,
            "type": d.dependency_type,
            "description": d.description,
        }
        for d in mod.dependencies_as_source.select_related("target_module").all()
    ]
    required_by = [
        {
            "code": d.source_module.code,
            "name": d.source_module.name,
            "status": d.source_module.status,
            "type": d.dependency_type,
            "description": d.description,
        }
        for d in mod.dependencies_as_target.select_related("source_module").all()
    ]

    return JsonResponse({
        "module_code": mod.code,
        "module_name": mod.name,
        "requires": requires,
        "required_by": required_by,
    })


@login_required
@_admin_required
def admin_modules_export_json(request):
    """
    Exports the current module, submodule, feature, and dependency configuration as JSON.
    """
    import json
    from django.http import HttpResponse

    data = {
        "exported_at": timezone.now().isoformat(),
        "exported_by": request.user.username,
        "modules": [],
    }

    modules = SystemModule.objects.prefetch_related("submodules", "submodules__features").all()
    for mod in modules:
        mod_dict = {
            "code": mod.code,
            "name": mod.name,
            "category": mod.category,
            "status": mod.status,
            "status_message": mod.status_message,
            "is_critical": mod.is_critical,
            "submodules": [],
        }
        for sub in mod.submodules.all():
            sub_dict = {
                "code": sub.code,
                "name": sub.name,
                "status": sub.status,
                "status_message": sub.status_message,
                "is_critical": sub.is_critical,
                "features": [],
            }
            for feat in sub.features.all():
                feat_dict = {
                    "code": feat.code,
                    "name": feat.name,
                    "status": feat.status,
                    "status_message": feat.status_message,
                }
                sub_dict["features"].append(feat_dict)
            mod_dict["submodules"].append(sub_dict)
        data["modules"].append(mod_dict)

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(json.dumps(data, indent=2), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="ums_module_config_{timestamp}.json"'
    return response


@login_required
@_admin_required
@require_POST
def admin_modules_import_json(request):
    """
    Imports and applies module, submodule, and feature statuses from an uploaded JSON configuration.
    """
    import json
    uploaded_file = request.FILES.get("config_file")
    if not uploaded_file:
        messages.error(request, "Please select a valid JSON configuration file to import.")
        return redirect("university:admin_modules")

    try:
        data = json.loads(uploaded_file.read().decode("utf-8"))
    except Exception as e:
        messages.error(request, f"Failed to parse JSON file: {str(e)}")
        return redirect("university:admin_modules")

    modules_data = data.get("modules", [])
    if not modules_data:
        messages.error(request, "Uploaded JSON does not contain valid module configuration.")
        return redirect("university:admin_modules")

    updated_count = 0
    with transaction.atomic():
        for mod_item in modules_data:
            mod_code = mod_item.get("code")
            mod_status = mod_item.get("status")
            mod_msg = mod_item.get("status_message", "")
            if mod_code and mod_status:
                m = SystemModule.objects.filter(code=mod_code).first()
                if m and (not m.is_critical or mod_status == ModuleStatus.ENABLED):
                    m.status = mod_status
                    m.status_message = mod_msg
                    m.save(update_fields=["status", "status_message", "updated_at"])
                    updated_count += 1

            for sub_item in mod_item.get("submodules", []):
                sub_code = sub_item.get("code")
                sub_status = sub_item.get("status")
                sub_msg = sub_item.get("status_message", "")
                if sub_code and sub_status:
                    s = SystemSubmodule.objects.filter(code=sub_code).first()
                    if s and (not s.is_critical or sub_status == ModuleStatus.ENABLED):
                        s.status = sub_status
                        s.status_message = sub_msg
                        s.save(update_fields=["status", "status_message", "updated_at"])

                for feat_item in sub_item.get("features", []):
                    feat_code = feat_item.get("code")
                    feat_status = feat_item.get("status")
                    feat_msg = feat_item.get("status_message", "")
                    if feat_code and feat_status:
                        f = SystemFeature.objects.filter(code=feat_code).first()
                        if f:
                            f.status = feat_status
                            f.status_message = feat_msg
                            f.save(update_fields=["status", "status_message", "updated_at"])

        invalidate_module_cache()
        from .audit_services import log_activity
        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.MODULES_BULK_UPDATE,
            module=AuditLog.Module.MODULE_MGMT,
            entity="SystemModule",
            entity_id="IMPORT_JSON",
            description=f"Imported module availability configuration ({updated_count} modules updated).",
        )

    messages.success(request, f"Successfully imported module configuration ({updated_count} modules synchronized).")
    return redirect("university:admin_modules")
