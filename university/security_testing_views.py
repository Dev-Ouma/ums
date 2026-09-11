from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from university.security_compliance_views import _security_admin_required
from university.security_testing_services import MANUAL_TESTS, run_automated_checks


@login_required
@_security_admin_required
def security_testing_dashboard(request):
    return render(request, "system/security_testing.html", {
        "automated_tests": run_automated_checks(),
        "manual_tests": MANUAL_TESTS,
    })
