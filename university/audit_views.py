import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Role
from university.audit_services import (
    export_audit_csv, export_audit_excel, export_audit_pdf
)
from university.models import AuditLog


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


def _apply_audit_filters(request, qs):
    """Helper to apply query parameter filters to an AuditLog queryset."""
    q = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    module = request.GET.get("module", "").strip()
    user_role = request.GET.get("user_role", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if q:
        qs = qs.filter(
            Q(description__icontains=q) |
            Q(user_display__icontains=q) |
            Q(entity__icontains=q) |
            Q(entity_id__icontains=q) |
            Q(ip_address__icontains=q)
        )
    if action:
        qs = qs.filter(action=action)
    if module:
        qs = qs.filter(module=module)
    if user_role:
        qs = qs.filter(user_role=user_role)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    return qs


@login_required
@_admin_required
def audit_dashboard(request):
    """
    Central tamper-resistant Audit Trail log dashboard with filtering, search,
    real-time statistics, and direct exports to PDF, Excel, and CSV.
    """
    qs = AuditLog.objects.all().order_by("-timestamp")

    # Metrics
    total_logs = qs.count()
    today = timezone.now().date()
    today_logs = qs.filter(timestamp__date=today).count()
    security_events = qs.filter(action__in=[
        AuditLog.Action.LOGIN,
        AuditLog.Action.LOGOUT,
        AuditLog.Action.FAILED_LOGIN,
    ]).count()
    config_changes = qs.filter(action=AuditLog.Action.CONFIG_CHANGE).count()

    # Filtered queryset
    filtered_qs = _apply_audit_filters(request, qs)

    # Pagination
    paginator = Paginator(filtered_qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "total_logs": total_logs,
        "today_logs": today_logs,
        "security_events": security_events,
        "config_changes": config_changes,
        "actions": AuditLog.Action.choices,
        "modules": AuditLog.Module.choices,
        "roles": [
            (Role.ADMIN, "Admin"),
            (Role.FACULTY, "Faculty"),
            (Role.STUDENT, "Student"),
            ("SUPERUSER", "Superuser"),
        ],
        "q": request.GET.get("q", ""),
        "selected_action": request.GET.get("action", ""),
        "selected_module": request.GET.get("module", ""),
        "selected_role": request.GET.get("user_role", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
    }
    return render(request, "audit/dashboard.html", context)


@login_required
@_admin_required
def audit_detail(request, pk):
    """Detailed view of an individual audit entry showing before/after diffs."""
    log_entry = get_object_or_404(AuditLog, pk=pk)

    prev_json = json.dumps(log_entry.previous_state, indent=2) if log_entry.previous_state else None
    new_json = json.dumps(log_entry.new_state, indent=2) if log_entry.new_state else None

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "id": log_entry.id,
            "timestamp": log_entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "user_display": log_entry.user_display,
            "user_role": log_entry.user_role,
            "action": log_entry.action,
            "action_display": log_entry.get_action_display(),
            "module": log_entry.module,
            "entity": log_entry.entity,
            "entity_id": log_entry.entity_id,
            "description": log_entry.description,
            "ip_address": log_entry.ip_address,
            "device_type": log_entry.device_type,
            "user_agent": log_entry.user_agent,
            "previous_state": log_entry.previous_state,
            "new_state": log_entry.new_state,
        })

    return render(request, "audit/detail.html", {
        "log": log_entry,
        "prev_json": prev_json,
        "new_json": new_json,
    })


@login_required
@_admin_required
def audit_export(request, fmt):
    """Export filtered audit logs to CSV, Excel, or PDF."""
    qs = AuditLog.objects.all().order_by("-timestamp")
    filtered_qs = _apply_audit_filters(request, qs)

    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    fmt = fmt.lower()
    if fmt == "csv":
        data = export_audit_csv(filtered_qs)
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="audit_trail_{now_str}.csv"'
        return response
    elif fmt in ("excel", "xlsx"):
        data = export_audit_excel(filtered_qs)
        response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="audit_trail_{now_str}.xlsx"'
        return response
    elif fmt == "pdf":
        data = export_audit_pdf(filtered_qs)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="audit_trail_{now_str}.pdf"'
        return response
    else:
        raise Http404(f"Unsupported export format: {fmt}")
