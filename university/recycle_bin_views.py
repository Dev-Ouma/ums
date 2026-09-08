import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from university.models import RecycleBinItem
from university.recycle_bin_services import (
    bulk_purge, bulk_restore, purge_recycle_item, restore_from_recycle_bin
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
def recycle_bin_dashboard(request):
    """
    Central Recycle Bin dashboard listing soft-deleted records with filters,
    search, stats, individual and bulk actions.
    """
    items_qs = RecycleBinItem.objects.select_related("deleted_by", "restored_by").all().order_by("-deleted_at")

    # Metrics
    total_count = items_qs.count()
    active_in_bin = items_qs.filter(is_restored=False).count()
    restored_count = items_qs.filter(is_restored=True).count()
    protected_count = items_qs.filter(is_protected=True, is_restored=False).count()

    # Filters
    q = request.GET.get("q", "").strip()
    module_filter = request.GET.get("module", "").strip()
    status_filter = request.GET.get("status", "in_bin").strip()
    deleted_by = request.GET.get("deleted_by", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if q:
        items_qs = items_qs.filter(
            Q(object_repr__icontains=q) |
            Q(object_id__icontains=q) |
            Q(content_type__icontains=q) |
            Q(ip_address__icontains=q) |
            Q(deleted_by__username__icontains=q)
        )

    if module_filter:
        items_qs = items_qs.filter(module=module_filter)

    if status_filter == "in_bin":
        items_qs = items_qs.filter(is_restored=False)
    elif status_filter == "restored":
        items_qs = items_qs.filter(is_restored=True)

    if deleted_by:
        items_qs = items_qs.filter(deleted_by_id=deleted_by)

    if date_from:
        items_qs = items_qs.filter(deleted_at__date__gte=date_from)
    if date_to:
        items_qs = items_qs.filter(deleted_at__date__lte=date_to)

    # Distinct modules for dropdown
    modules = RecycleBinItem.objects.values_list("module", flat=True).distinct()

    # Pagination
    paginator = Paginator(items_qs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "total_count": total_count,
        "active_in_bin": active_in_bin,
        "restored_count": restored_count,
        "protected_count": protected_count,
        "modules": sorted(list(filter(None, modules))),
        "q": q,
        "selected_module": module_filter,
        "selected_status": status_filter,
        "date_from": date_from,
        "date_to": date_to,
        "is_superuser": request.user.is_superuser,
    }
    return render(request, "recycle_bin/dashboard.html", context)


@login_required
@_admin_required
def recycle_bin_detail(request, pk):
    """
    Detailed inspection of a deleted record, displaying its JSON snapshot,
    actor metadata, IP, and device.
    """
    item = get_object_or_404(RecycleBinItem, pk=pk)
    formatted_json = json.dumps(item.serialized_data, indent=2) if item.serialized_data else "{}"

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "id": item.id,
            "content_type": item.content_type,
            "object_id": item.object_id,
            "object_repr": item.object_repr,
            "module": item.module,
            "deleted_by": str(item.deleted_by) if item.deleted_by else "System",
            "deleted_at": item.deleted_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ip_address": item.ip_address,
            "device_type": item.device_type,
            "user_agent": item.user_agent,
            "is_restored": item.is_restored,
            "is_protected": item.is_protected,
            "serialized_data": item.serialized_data,
        })

    return render(request, "recycle_bin/detail.html", {
        "item": item,
        "formatted_json": formatted_json,
    })


@login_required
@_admin_required
@require_POST
def recycle_bin_restore(request, pk):
    """Restore a single deleted record."""
    success, message = restore_from_recycle_bin(pk, user=request.user, request=request)
    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("university:recycle_bin_dashboard")


@login_required
@_admin_required
@require_POST
def recycle_bin_bulk_restore(request):
    """Bulk restore selected records."""
    raw_ids = request.POST.get("item_ids", "")
    if not raw_ids:
        # Check if checkboxes were posted
        id_list = request.POST.getlist("selected_ids")
    else:
        id_list = [i.strip() for i in raw_ids.split(",") if i.strip()]

    if not id_list:
        messages.warning(request, "No items were selected for restoration.")
        return redirect("university:recycle_bin_dashboard")

    success_count, fail_count, errors = bulk_restore(id_list, user=request.user, request=request)
    if success_count > 0:
        messages.success(request, f"Successfully restored {success_count} item(s).")
    if fail_count > 0:
        err_msg = "; ".join(errors[:3])
        messages.error(request, f"Failed to restore {fail_count} item(s). {err_msg}")
    return redirect("university:recycle_bin_dashboard")


@login_required
@_admin_required
@require_POST
def recycle_bin_purge(request, pk):
    """Permanently delete a record from the recycle bin."""
    from django.core.exceptions import PermissionDenied
    try:
        success, message = purge_recycle_item(pk, user=request.user, request=request)
        if success:
            messages.success(request, message)
        else:
            messages.error(request, message)
    except PermissionDenied as e:
        messages.error(request, str(e))
    return redirect("university:recycle_bin_dashboard")


@login_required
@_admin_required
@require_POST
def recycle_bin_bulk_purge(request):
    """Bulk permanently delete selected records."""
    raw_ids = request.POST.get("item_ids", "")
    if not raw_ids:
        id_list = request.POST.getlist("selected_ids")
    else:
        id_list = [i.strip() for i in raw_ids.split(",") if i.strip()]

    if not id_list:
        messages.warning(request, "No items were selected for permanent purge.")
        return redirect("university:recycle_bin_dashboard")

    success_count, fail_count, errors = bulk_purge(id_list, user=request.user, request=request)
    if success_count > 0:
        messages.success(request, f"Permanently deleted {success_count} item(s) from the system.")
    if fail_count > 0:
        err_msg = "; ".join(errors[:3])
        messages.error(request, f"Could not purge {fail_count} item(s). {err_msg}")
    return redirect("university:recycle_bin_dashboard")
