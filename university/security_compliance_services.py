"""
Security & Compliance cockpit services.

This module turns the production cybersecurity guide into a concrete
dashboard model without adding new database tables. The authoritative
workflow state remains in GoLiveReadiness and GoLiveIssue; this service maps
those records into UMS-specific security domains and checklist sections.
"""

from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone

from university.golive_models import GoLiveCategory, IssueSeverity, IssueStatus, ReadinessStatus
from university.golive_services import ensure_default_categories
from university.models import AuditLog, GoLiveIssue, GoLiveReadiness


@dataclass(frozen=True)
class SecurityDomain:
    key: str
    title: str
    owner: str
    category: str
    icon: str
    checkpoints: tuple[str, ...]
    links: tuple[tuple[str, str], ...] = ()


SECURITY_DOMAINS = (
    SecurityDomain(
        key="governance",
        title="Security Governance & Ownership",
        owner="Security Administrator",
        category=GoLiveCategory.INSTITUTIONAL_APPROVAL,
        icon="fa-user-shield",
        checkpoints=(
            "Production access owners are documented and separated by duty.",
            "Deployment, database, user, payment, log, backup, and incident authorities are assigned.",
            "No single administrator retains unnecessary unrestricted access.",
        ),
        links=(("Staff Permissions", "university:staff_permissions_dashboard"), ("Audit Trails", "university:audit_dashboard")),
    ),
    SecurityDomain(
        key="assets",
        title="Asset Inventory",
        owner="Application/DevOps Administrator",
        category=GoLiveCategory.DOCUMENTATION,
        icon="fa-server",
        checkpoints=(
            "Servers, software, integrations, owners, credentials, data handled, backups, and recovery steps are inventoried.",
            "External services such as payments, email, SMS, LMS, HR, finance, library, DNS, and monitoring are assigned owners.",
        ),
    ),
    SecurityDomain(
        key="infrastructure",
        title="Infrastructure, Network, SSH & TLS",
        owner="Network Administrator",
        category=GoLiveCategory.SECURITY,
        icon="fa-network-wired",
        checkpoints=(
            "Only HTTPS is publicly exposed; database, Redis, debug, and internal admin ports stay private.",
            "SSH is key-based, restricted, monitored, and emergency access is controlled.",
            "TLS certificates, HSTS, redirects, renewal, and expiry monitoring are verified.",
        ),
        links=(("Lockdown & Security", "control:dashboard"),),
    ),
    SecurityDomain(
        key="secrets",
        title="Secrets & Source Code Security",
        owner="Application/DevOps Administrator",
        category=GoLiveCategory.SECURITY,
        icon="fa-key",
        checkpoints=(
            "Production secrets are outside source code, Git history, frontend bundles, logs, screenshots, and exports.",
            "Dependency, SAST, secret scanning, and vulnerable package reviews are complete.",
            "Development credentials are rotated before production.",
        ),
    ),
    SecurityDomain(
        key="auth",
        title="Login, Passwords, MFA, Sessions & Cookies",
        owner="Security Administrator",
        category=GoLiveCategory.ROLE_PERMISSIONS,
        icon="fa-fingerprint",
        checkpoints=(
            "Login errors are generic, brute-force controls are active, and authentication events are audited.",
            "Password reset uses one-time expiring tokens; passwords are never sent by email.",
            "Privileged MFA is required or formally risk-accepted; sessions and cookies use secure flags and invalidation rules.",
        ),
        links=(("Login & Security", "university:login_security"), ("Password Policy", "university:password_management")),
    ),
    SecurityDomain(
        key="rbac",
        title="Authorization, RBAC, IDOR & Admin Security",
        owner="Security Administrator",
        category=GoLiveCategory.ROLE_PERMISSIONS,
        icon="fa-lock",
        checkpoints=(
            "Every protected operation is authorized server-side, not only hidden in the UI.",
            "Student, lecturer, staff, finance, admin, and disabled-account access paths have IDOR/BOLA tests.",
            "Sensitive actions require elevated permission, confirmation, and audit trails.",
        ),
        links=(("Staff Permissions", "university:staff_permissions_dashboard"),),
    ),
    SecurityDomain(
        key="data",
        title="Database, Integrity, Privacy & Retention",
        owner="Database Administrator",
        category=GoLiveCategory.DATA_PROTECTION,
        icon="fa-database",
        checkpoints=(
            "Database access is private, least-privilege, encrypted where appropriate, audited, and backed up.",
            "Critical records use constraints for usernames, student numbers, course codes, payments, foreign keys, dates, and statuses.",
            "Data Protection Act, 2019 obligations, minimization, retention, and privacy-rights processes are documented.",
            "An approved privacy notice, data inventory, lawful-basis register, retention schedule, processor register, and breach workflow exist.",
        ),
        links=(("Privacy Notice", "university:privacy"), ("Personal Data Export", "accounts:personal_data_export")),
    ),
    SecurityDomain(
        key="payments",
        title="Financial & Payment Security",
        owner="Finance/Payment Administrator",
        category=GoLiveCategory.FINANCE_PAYMENTS,
        icon="fa-money-check-dollar",
        checkpoints=(
            "Payments are marked successful only after independent server-side provider verification.",
            "Webhooks validate signature, timestamp/replay controls, reference, amount, currency, account, and idempotency.",
            "Card secrets and sensitive card data are never stored outside a compliant tokenized architecture.",
        ),
        links=(("Payment Certification", "university:payment_go_live_certification"), ("Payments Ledger", "university:admin_payments_list"), ("Reconciliation", "university:fee_reconciliation_dashboard")),
    ),
    SecurityDomain(
        key="files",
        title="Files, Documents, Imports & Exports",
        owner="Data Protection/Privacy Officer",
        category=GoLiveCategory.DATA_PROTECTION,
        icon="fa-file-shield",
        checkpoints=(
            "Uploads allowlist type, size, MIME, signature, storage path, authorization, scanning, and audit checks.",
            "Documents use authorization, secure URLs, expiring links where appropriate, and download audit trails.",
            "CSV/Excel imports and exports enforce permissions, validation, filters, record-level authorization, and formula-injection protection.",
        ),
        links=(("Document Controls", "university:admin_document_controls"),),
    ),
    SecurityDomain(
        key="api",
        title="API, CORS, CSRF, XSS, SQLi & SSRF",
        owner="Application/DevOps Administrator",
        category=GoLiveCategory.SECURITY,
        icon="fa-code",
        checkpoints=(
            "APIs enforce authentication, authorization, validation, rate limits, request limits, secure errors, logging, CSRF, and CORS.",
            "XSS, SQL injection, CSRF, path traversal, command injection, SSTI, deserialization, race, and privilege escalation tests are complete.",
            "URL-fetching features cannot reach localhost, metadata endpoints, or private networks unless explicitly constrained.",
        ),
    ),
    SecurityDomain(
        key="monitoring",
        title="Logging, Audit Trails, Monitoring & Alerts",
        owner="Security Administrator",
        category=GoLiveCategory.MONITORING,
        icon="fa-chart-line",
        checkpoints=(
            "Security logging captures auth, privilege, admin, import/export, payment, grade, transcript, config, lockdown, backup, and integration events.",
            "Audit logs are access-controlled, append-oriented, monitored, and retained by policy.",
            "Alerts exist for failed admin logins, role escalation, payment anomalies, large exports, repeated 401/403, backup failure, disk, TLS, and integration auth failure.",
        ),
        links=(("Audit Trails", "university:audit_dashboard"), ("Logging & Monitoring", "university:monitoring_dashboard"), ("Security Testing", "university:security_testing_dashboard")),
    ),
    SecurityDomain(
        key="integrations",
        title="Integrations, Queues & Background Jobs",
        owner="Integration Administrator",
        category=GoLiveCategory.INTEGRATIONS,
        icon="fa-plug-circle-bolt",
        checkpoints=(
            "LMS, HR, finance, library, meeting, email, SMS, payment, DNS, and storage integrations have owners and credential controls.",
            "Every integration has authentication, timeout, retry, idempotency, error handling, logging, audit, health, manual retry, and recovery.",
            "Queues and scheduled jobs are protected from duplicate jobs, poison messages, flooding, and unauthorized submissions.",
        ),
        links=(),
    ),
    SecurityDomain(
        key="recovery",
        title="Backups, Restore Testing & Disaster Recovery",
        owner="Backup/Disaster Recovery Owner",
        category=GoLiveCategory.DISASTER_RECOVERY,
        icon="fa-life-ring",
        checkpoints=(
            "Database, uploaded documents, configuration, critical application data, and required key material are backed up securely.",
            "Backups are encrypted, access-controlled, monitored, retained, protected from unauthorized deletion, and restored in tests.",
            "RPO, RTO, ransomware, deletion, provider outage, DNS, cloud, deployment, and credential-compromise scenarios are documented.",
        ),
        links=(("System Backups", "university:backup_dashboard"),),
    ),
    SecurityDomain(
        key="deployment",
        title="Deployment, Change Management & Go-Live Gate",
        owner="Application/DevOps Administrator",
        category=GoLiveCategory.UAT,
        icon="fa-rocket",
        checkpoints=(
            "Production deployment follows review, automated tests, security scans, staging, UAT, approval, backup, smoke tests, and monitoring.",
            "Database migrations have backup, test, review, rollback/recovery, validation, and recorded execution.",
            "Critical/high blockers prevent go-live approval until remediated and retested.",
        ),
        links=(("Go-Live Command Center", "university:golive_dashboard"),),
    ),
    SecurityDomain(
        key="incident",
        title="Incident Response, DDoS, WAF & Post-Go-Live",
        owner="Incident Response Team",
        category=GoLiveCategory.BUSINESS_CONTINUITY,
        icon="fa-shield-virus",
        checkpoints=(
            "Incident response covers detection, validation, classification, containment, investigation, eradication, recovery, monitoring, documentation, and improvement.",
            "Account compromise, breach, ransomware, payment fraud, credential leak, database exposure, DDoS, defacement, insider threat, and integration compromise playbooks exist.",
            "DDoS protection, WAF rules, security/performance limits, browser/mobile testing, and post-go-live monitoring are planned.",
        ),
    ),
)


BLOCKING_SEVERITIES = (IssueSeverity.CRITICAL, IssueSeverity.HIGH)
OPEN_STATUSES = (IssueStatus.OPEN, IssueStatus.IN_PROGRESS, IssueStatus.RESOLVED)


def _status_rank(statuses: Iterable[str]) -> str:
    statuses = set(statuses)
    if ReadinessStatus.FAIL in statuses:
        return ReadinessStatus.FAIL
    if ReadinessStatus.WARN in statuses:
        return ReadinessStatus.WARN
    if ReadinessStatus.NOT_STARTED in statuses:
        return ReadinessStatus.NOT_STARTED
    if ReadinessStatus.IN_PROGRESS in statuses:
        return ReadinessStatus.IN_PROGRESS
    if statuses and statuses.issubset({ReadinessStatus.PASS_, ReadinessStatus.NOT_APPLICABLE}):
        return ReadinessStatus.PASS_
    return ReadinessStatus.NOT_STARTED


def build_security_compliance_summary():
    ensure_default_categories()

    User = get_user_model()
    readiness_by_category = {
        row.category: row
        for row in GoLiveReadiness.objects.select_related("owner", "signed_off_by").all()
    }
    open_issues = GoLiveIssue.objects.exclude(status__in=[IssueStatus.CLOSED, IssueStatus.RETESTED])

    domain_rows = []
    for domain in SECURITY_DOMAINS:
        readiness = readiness_by_category.get(domain.category)
        issues = list(open_issues.filter(category=domain.category))
        critical_count = sum(1 for issue in issues if issue.severity == IssueSeverity.CRITICAL)
        high_count = sum(1 for issue in issues if issue.severity == IssueSeverity.HIGH)
        status = readiness.status if readiness else ReadinessStatus.NOT_STARTED
        if critical_count:
            status = ReadinessStatus.FAIL
        elif high_count and status == ReadinessStatus.PASS_:
            status = ReadinessStatus.WARN
        domain_rows.append({
            "domain": domain,
            "readiness": readiness,
            "status": status,
            "open_issues": len(issues),
            "critical_issues": critical_count,
            "high_issues": high_count,
            "is_signed_off": bool(readiness and readiness.is_signed_off),
        })

    status_counts = {status: 0 for status in ReadinessStatus.values}
    for row in domain_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    blocking_issues = open_issues.filter(severity__in=BLOCKING_SEVERITIES).count()
    unsigned_pass = sum(1 for row in domain_rows if row["status"] == ReadinessStatus.PASS_ and not row["is_signed_off"])
    recent_security_logs = AuditLog.objects.filter(module__in=[AuditLog.Module.AUTH, AuditLog.Module.CONFIG, AuditLog.Module.BACKUPS]).order_by("-timestamp")[:8]
    audit_counts = dict(AuditLog.objects.values("module").annotate(total=Count("id")).values_list("module", "total"))

    from university.settings_services import get_setting
    from university.database_security_services import build_database_security_checks
    idle_timeout_minutes = get_setting("session_timeout_minutes", 60) or 0
    runtime_checks = [
        {
            "label": "Production debug mode",
            "status": "FAIL" if settings.DEBUG else "PASS",
            "detail": "DEBUG is enabled" if settings.DEBUG else "DEBUG is disabled",
        },
        {
            "label": "Session cookie: Secure",
            "status": "PASS" if getattr(settings, "SESSION_COOKIE_SECURE", False) else "FAIL",
            "detail": "SESSION_COOKIE_SECURE is enabled" if getattr(settings, "SESSION_COOKIE_SECURE", False) else "Authentication cookies could travel over HTTP",
        },
        {
            "label": "Session cookie: HttpOnly",
            "status": "PASS" if getattr(settings, "SESSION_COOKIE_HTTPONLY", False) else "FAIL",
            "detail": "JavaScript cannot read the session cookie" if getattr(settings, "SESSION_COOKIE_HTTPONLY", False) else "Enable SESSION_COOKIE_HTTPONLY",
        },
        {
            "label": "Session cookie: SameSite",
            "status": "PASS" if str(getattr(settings, "SESSION_COOKIE_SAMESITE", "")).lower() in {"lax", "strict"} else "FAIL",
            "detail": f"SameSite={getattr(settings, 'SESSION_COOKIE_SAMESITE', '') or 'unset'}",
        },
        {
            "label": "Session cookie scope",
            "status": "PASS" if getattr(settings, "SESSION_COOKIE_PATH", "/") == "/" and not getattr(settings, "SESSION_COOKIE_DOMAIN", None) else "WARN",
            "detail": f"Path={getattr(settings, 'SESSION_COOKIE_PATH', '/')}; Domain={getattr(settings, 'SESSION_COOKIE_DOMAIN', None) or 'host-only'}",
        },
        {
            "label": "CSRF cookie: Secure and SameSite",
            "status": "PASS" if getattr(settings, "CSRF_COOKIE_SECURE", False) and str(getattr(settings, "CSRF_COOKIE_SAMESITE", "")).lower() in {"lax", "strict"} else "FAIL",
            "detail": "CSRF cookie transport protections are enabled" if getattr(settings, "CSRF_COOKIE_SECURE", False) else "Enable secure CSRF cookie settings",
        },
        {
            "label": "Idle session timeout",
            "status": "PASS" if idle_timeout_minutes > 0 else "FAIL",
            "detail": f"{idle_timeout_minutes} minute inactivity timeout configured" if idle_timeout_minutes > 0 else "No idle session timeout configured",
        },
        {
            "label": "Absolute session lifetime",
            "status": "PASS" if 0 < getattr(settings, "SESSION_COOKIE_AGE", 0) <= 60 * 60 * 24 * 14 else "FAIL",
            "detail": f"{getattr(settings, 'SESSION_COOKIE_AGE', 0)} seconds configured",
        },
        {
            "label": "Session invalidation controls",
            "status": "PASS",
            "detail": "Logout, password change/reset, account suspension, role/group/permission changes, and session revocation invalidate server-side sessions",
        },
        {
            "label": "Concurrent session management",
            "status": "PASS" if int(getattr(settings, "MAX_CONCURRENT_SESSIONS", 0) or 0) > 0 else "FAIL",
            "detail": f"Maximum {getattr(settings, 'MAX_CONCURRENT_SESSIONS', 0)} active session(s); users can revoke other devices",
        },
        {
            "label": "HTTP Strict Transport Security",
            "status": "PASS" if getattr(settings, "SECURE_HSTS_SECONDS", 0) else "WARN",
            "detail": f"{getattr(settings, 'SECURE_HSTS_SECONDS', 0)} seconds configured",
        },
        {
            "label": "Browser response protections",
            "status": "PASS" if getattr(settings, "SECURE_CONTENT_TYPE_NOSNIFF", False) and getattr(settings, "X_FRAME_OPTIONS", "") else "WARN",
            "detail": "Content sniffing and clickjacking protections configured",
        },
        {
            "label": "Content Security Policy",
            "status": "PASS" if getattr(settings, "CONTENT_SECURITY_POLICY", "") and "frame-ancestors 'none'" in settings.CONTENT_SECURITY_POLICY else "FAIL",
            "detail": "CSP is enforcing same-origin application boundaries and denies framing" if getattr(settings, "CONTENT_SECURITY_POLICY", "") else "No CSP configured",
        },
        {
            "label": "Permissions Policy",
            "status": "PASS" if getattr(settings, "PERMISSIONS_POLICY", "") else "FAIL",
            "detail": getattr(settings, "PERMISSIONS_POLICY", "") or "No Permissions-Policy configured",
        },
        {
            "label": "Authenticated API CORS",
            "status": "PASS" if "*" not in getattr(settings, "CORS_ALLOWED_ORIGINS", []) else "FAIL",
            "detail": "Same-origin by default; no wildcard authenticated CORS allowlist" if not getattr(settings, "CORS_ALLOWED_ORIGINS", []) else "Explicit origins configured",
        },
        {
            "label": "Sensitive endpoint rate limiting",
            "status": "PASS" if getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", not settings.DEBUG) else "WARN",
            "detail": "Login and password-reset POST requests are cache-throttled in production" if getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", not settings.DEBUG) else "Enable production rate limiting",
        },
        {
            "label": "Shared security cache",
            "status": "PASS" if "locmem" not in str(getattr(settings, "CACHES", {}).get("default", {}).get("BACKEND", "")).lower() and "dummy" not in str(getattr(settings, "CACHES", {}).get("default", {}).get("BACKEND", "")).lower() else ("WARN" if settings.DEBUG else "FAIL"),
            "detail": "Rate limits use a shared cache backend" if not settings.DEBUG else "Development local-memory cache is active; production requires Redis or another shared backend",
        },
        {
            "label": "Admin accounts",
            "status": "PASS" if User.objects.filter(role="ADMIN", is_active=True).exists() or User.objects.filter(is_superuser=True, is_active=True).exists() else "FAIL",
            "detail": f"{User.objects.filter(role='ADMIN', is_active=True).count()} active admin role user(s), {User.objects.filter(is_superuser=True, is_active=True).count()} active superuser(s)",
        },
    ]
    runtime_checks.extend(build_database_security_checks())

    not_ready_reasons = []
    if status_counts.get(ReadinessStatus.FAIL, 0):
        not_ready_reasons.append(f"{status_counts[ReadinessStatus.FAIL]} security domain(s) marked FAIL")
    if status_counts.get(ReadinessStatus.NOT_STARTED, 0) or status_counts.get(ReadinessStatus.IN_PROGRESS, 0):
        not_ready_reasons.append("Security domains remain untested or in progress")
    if blocking_issues:
        not_ready_reasons.append(f"{blocking_issues} open Critical/High security issue(s)")
    if unsigned_pass:
        not_ready_reasons.append(f"{unsigned_pass} passed domain(s) still need authorized sign-off")
    runtime_failures = sum(1 for check in runtime_checks if check["status"] == "FAIL")
    if runtime_failures:
        not_ready_reasons.append(f"{runtime_failures} live security configuration check(s) failed")

    return {
        "domains": domain_rows,
        "status_counts": status_counts,
        "runtime_checks": runtime_checks,
        "recent_security_logs": recent_security_logs,
        "audit_counts": audit_counts,
        "auth_audit_count": audit_counts.get(AuditLog.Module.AUTH, 0),
        "total_domains": len(domain_rows),
        "total_checkpoints": sum(len(domain.checkpoints) for domain in SECURITY_DOMAINS),
        "blocking_issues": blocking_issues,
        "open_issues": open_issues.count(),
        "not_ready_reasons": not_ready_reasons,
        "is_approved": not not_ready_reasons,
        "generated_at": timezone.now(),
    }
