"""
Module Management Administration Views.
Provides full control over module availability, submodules, features,
maintenance modes, and cross-module dependencies.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
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
