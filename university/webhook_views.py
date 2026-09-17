"""Admin console for outbound webhook endpoints (see webhook_services.py)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from university.audit_services import log_activity
from university.decorators import permission_required
from university.models import AuditLog
from university.webhook_models import WebhookDelivery, WebhookEndpoint

_admin_required = permission_required("admin.manage_settings")


@login_required
@_admin_required
def webhook_list(request):
    endpoints = WebhookEndpoint.objects.all()
    return render(request, "system/webhooks/list.html", {
        "endpoints": endpoints,
        "event_kind_choices": WebhookEndpoint.EVENT_KIND_CHOICES,
    })


@login_required
@_admin_required
def webhook_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        url = request.POST.get("url", "").strip()
        event_kinds = request.POST.getlist("event_kinds")
        if not name or not url:
            messages.error(request, "A name and URL are required.")
        else:
            endpoint = WebhookEndpoint.objects.create(
                name=name, url=url, event_kinds=event_kinds, created_by=request.user)
            log_activity(request=request, user=request.user, action=AuditLog.Action.CREATE,
                         module=AuditLog.Module.CONFIG, entity="WebhookEndpoint", entity_id=endpoint.pk,
                         description=f"Created webhook endpoint '{endpoint.name}' -> {endpoint.url}.")
            messages.success(request, f"Webhook endpoint '{endpoint.name}' created. "
                                      f"Secret: {endpoint.secret} (shown once, save it now).")
            return redirect("university:webhook_list")
    return render(request, "system/webhooks/form.html", {
        "event_kind_choices": WebhookEndpoint.EVENT_KIND_CHOICES,
    })


@login_required
@_admin_required
def webhook_edit(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    if request.method == "POST":
        endpoint.name = request.POST.get("name", endpoint.name).strip()
        endpoint.url = request.POST.get("url", endpoint.url).strip()
        endpoint.event_kinds = request.POST.getlist("event_kinds")
        endpoint.is_active = bool(request.POST.get("is_active"))
        endpoint.save()
        log_activity(request=request, user=request.user, action=AuditLog.Action.UPDATE,
                     module=AuditLog.Module.CONFIG, entity="WebhookEndpoint", entity_id=endpoint.pk,
                     description=f"Updated webhook endpoint '{endpoint.name}'.")
        messages.success(request, f"Webhook endpoint '{endpoint.name}' updated.")
        return redirect("university:webhook_list")
    return render(request, "system/webhooks/form.html", {
        "endpoint": endpoint,
        "event_kind_choices": WebhookEndpoint.EVENT_KIND_CHOICES,
        "selected_kinds": set(endpoint.event_kinds or []),
    })


@login_required
@_admin_required
@require_POST
def webhook_delete(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    name = endpoint.name
    endpoint.delete()
    log_activity(request=request, user=request.user, action=AuditLog.Action.DELETE,
                 module=AuditLog.Module.CONFIG, entity="WebhookEndpoint",
                 description=f"Deleted webhook endpoint '{name}'.")
    messages.success(request, f"Webhook endpoint '{name}' deleted.")
    return redirect("university:webhook_list")


@login_required
@_admin_required
def webhook_deliveries(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    deliveries = endpoint.deliveries.all()[:200]
    return render(request, "system/webhooks/deliveries.html", {
        "endpoint": endpoint, "deliveries": deliveries,
    })
