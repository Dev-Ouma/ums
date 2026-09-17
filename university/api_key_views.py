"""Self-service API key management -- any authenticated user manages their
own keys, the same way GitHub/Stripe let a user issue their own PATs."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from university.api.models import ApiKey
from university.audit_services import log_activity
from university.models import AuditLog


@login_required
def api_key_list(request):
    keys = ApiKey.objects.filter(user=request.user)
    return render(request, "system/api_keys/list.html", {"keys": keys})


@login_required
@require_POST
def api_key_create(request):
    name = request.POST.get("name", "").strip() or "Unnamed key"
    api_key, raw_key = ApiKey.generate(request.user, name)
    log_activity(request=request, user=request.user, action=AuditLog.Action.CREATE,
                 module=AuditLog.Module.AUTH, entity="ApiKey", entity_id=api_key.pk,
                 description=f"Created API key '{name}'.")
    messages.success(request, f"API key created: {raw_key} — copy it now, it won't be shown again.")
    return redirect("university:api_key_list")


@login_required
@require_POST
def api_key_revoke(request, pk):
    api_key = get_object_or_404(ApiKey, pk=pk, user=request.user)
    api_key.revoke()
    log_activity(request=request, user=request.user, action=AuditLog.Action.UPDATE,
                 module=AuditLog.Module.AUTH, entity="ApiKey", entity_id=api_key.pk,
                 description=f"Revoked API key '{api_key.name}'.")
    messages.success(request, f"API key '{api_key.name}' revoked.")
    return redirect("university:api_key_list")
