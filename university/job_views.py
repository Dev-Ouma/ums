from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from university.job_services import build_job_snapshot
from university.security_compliance_views import _security_admin_required


@login_required
@_security_admin_required
def job_dashboard(request):
    return render(request, "system/job_dashboard.html", {"snapshot": build_job_snapshot()})
