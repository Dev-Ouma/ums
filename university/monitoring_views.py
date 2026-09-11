from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from university.monitoring_services import build_monitoring_snapshot
from university.security_compliance_views import _security_admin_required


@login_required
@_security_admin_required
def monitoring_dashboard(request):
    return render(request, "system/monitoring_dashboard.html", {
        "snapshot": build_monitoring_snapshot(),
    })
