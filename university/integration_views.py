"""Contextual status pages for integrations that are not yet connected."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from university.security_compliance_views import _security_admin_required


INTEGRATION_FEATURES = {
    "lms": {
        "submodule_code": "lms_integration",
        "name": "LMS Integration",
        "description": "This integration will connect student and staff identity, programmes, courses, academic periods, enrolment, and withdrawals with an approved LMS.",
        "status_label": "Integration Pending",
        "dependencies": "Approved LMS provider, SSO or API credentials, role mapping, and synchronization ownership.",
    },
    "hr-smhr": {
        "submodule_code": "hr_smhr",
        "name": "HR / SMHR",
        "description": "This integration will synchronize staff identity, departments, designations, employment status, account lifecycle, and permissions from the authoritative HR system.",
        "status_label": "Integration Pending",
        "dependencies": "Authoritative HR source, staff data mapping, lifecycle rules, and secure service credentials.",
    },
    "finance-procurement": {
        "submodule_code": "finance_procurement",
        "name": "Finance & Procurement",
        "description": "This integration will connect financial accounts, invoices, payments, receipts, reconciliation, vendors, approvals, refunds, and procurement workflows.",
        "status_label": "Integration Pending",
        "dependencies": "Finance ownership matrix, transaction reconciliation, idempotency, and provider verification.",
    },
    "library": {
        "submodule_code": "library_integration",
        "name": "Library Integration",
        "description": "This integration will connect student and staff library accounts, borrowing, returns, fines, clearance, and account status.",
        "status_label": "Integration Pending",
        "dependencies": "Library account mapping, fine ownership decision, clearance workflow, and secure API access.",
    },
    "meetings": {
        "submodule_code": "meeting_integrations",
        "name": "Zoom / Teams / Blackboard",
        "description": "This integration will connect courses, lecturers, students, timetables, meeting creation, meeting links, cancellations, and provider lifecycle events.",
        "status_label": "Integration Pending",
        "dependencies": "Approved meeting provider, OAuth application, token storage, course mapping, and outage handling.",
    },
    "plugin-security": {
        "submodule_code": "plugin_security",
        "name": "Plugin / Integration Security",
        "description": "This area will record security requirements for every integration, including credentials, encryption, webhooks, retries, audit, and failure recovery.",
        "status_label": "Under Development",
        "dependencies": "Integration inventory, data owners, credential register, webhook policy, and security review evidence.",
    },
    "health": {
        "submodule_code": "integration_health",
        "name": "Integration Health",
        "description": "This dashboard will show connection status, last sync, last success, failures, provider availability, and safe operational actions.",
        "status_label": "Under Development",
        "dependencies": "Integration health checks, sync telemetry, alerting, and administrator permissions.",
    },
    "sync-recovery": {
        "submodule_code": "sync_recovery",
        "name": "Sync & Failure Recovery",
        "description": "This workflow will manage retries, duplicate protection, idempotency, reconciliation, manual retry, and recovery when an integration fails.",
        "status_label": "Under Development",
        "dependencies": "Protected queues, retry policy, idempotency keys, reconciliation rules, and recovery audit trails.",
    },
    "mapping": {
        "submodule_code": "integration_mapping",
        "name": "Integration Mapping",
        "description": "This register will define the authoritative system, approved data fields, synchronization direction, retention, and ownership for every integration.",
        "status_label": "Under Development",
        "dependencies": "Institution-approved data ownership matrix, privacy review, retention policy, and processor register.",
    },
}


@login_required
@_security_admin_required
def integration_feature(request, integration):
    feature = INTEGRATION_FEATURES.get(integration)
    if feature is None:
        from django.http import Http404
        raise Http404("Integration feature not found")

    feature = dict(feature)
    # Module Management can override the presentation for maintenance or
    # disabled states. ENABLED still remains pending until a provider adapter
    # is actually implemented and approved.
    from university.module_models import ModuleStatus, SystemSubmodule
    submodule = SystemSubmodule.objects.filter(code=feature["submodule_code"]).first()
    if submodule and submodule.status == ModuleStatus.MAINTENANCE:
        feature["status_label"] = "Maintenance"
        feature["description"] = submodule.status_message or "This integration is temporarily under maintenance."
    elif submodule and submodule.status == ModuleStatus.DISABLED:
        feature["status_label"] = "Disabled"
        feature["description"] = submodule.status_message or "This integration has been disabled by system administration."
    elif submodule and submodule.status == ModuleStatus.COMING_SOON and submodule.status_message:
        feature["description"] = submodule.status_message

    return render(request, "system/integration_pending.html", {
        "module_name": "Integrations",
        "module_code": "integrations",
        "module_icon": "fa-solid fa-plug-circle-bolt",
        "feature": feature,
        "integration_key": integration,
    })
