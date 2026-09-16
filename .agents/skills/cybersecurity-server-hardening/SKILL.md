---
name: cybersecurity-server-hardening
description: >-
  Security hardening skill for the UMS Django production deployment.
  Covers CSP management, production checklist, HTTPS/SSL enforcement,
  dependency auditing, operational security dashboard patterns, and
  converting security guides into tracked, owned operational items.
  Also covers the full Go-Live Command Center — all 20 sequence steps,
  all security domains, and all runtime checks. Activate when working
  on security settings, CSP, middleware, deployment, or go-live readiness.
---

# Cybersecurity & Server Hardening Skill — UMS Project

---

## 1. Go-Live Command Center Architecture

The system has a full production launch readiness board at `/system-control/`.

### Key Files:
```
university/golive_models.py              ← GoLiveReadiness, GoLiveIssue models
university/golive_services.py            ← compute_readiness_summary(), build_go_live_sequence()
university/golive_views.py               ← Dashboard views
university/security_compliance_services.py ← SECURITY_DOMAINS checklist definitions
university/database_security_services.py   ← build_database_security_checks() runtime checks
```

### Gate Status Logic (3-colour):
| Gate | Condition | Meaning |
|---|---|---|
| 🟢 **GREEN** | No open Critical/High issues, all areas signed off | Ready to launch |
| 🟡 **AMBER** | No Critical/High blockers but gaps remain | Conditional launch with approved mitigation |
| 🔴 **RED** | Open Critical/High issue OR any area marked FAIL | **DO NOT LAUNCH** |

### Deployment Blocker Rules:
- Any `GoLiveIssue` with severity `CRITICAL` or `HIGH` and status not `CLOSED`/`RETESTED` → **blocks gate**
- Any `GoLiveReadiness` row with status `FAIL` → **blocks gate**
- Any `PASS` row that lacks `signed_off_at` → **blocks gate**
- Open `MEDIUM`/`LOW` issues → AMBER only (not RED), require documented mitigation

---

## 2. Go-Live Sequence (All 22 Steps)

Defined in `golive_services.GO_LIVE_SEQUENCE`:

| # | Step | Category |
|---|---|---|
| 1 | Infrastructure Hardening | SECURITY |
| 2 | HTTPS/TLS/Security Headers | SECURITY |
| 3 | Authentication | SECURITY |
| 4 | Session & Cookie Security | SECURITY |
| 5 | RBAC/Authorization | ROLE_PERMISSIONS |
| 6 | Data Protection/GDPR | DATA_PROTECTION |
| 7 | Database Security | SECURITY |
| 8 | Payment Security | FINANCE_PAYMENTS |
| 9 | Backup & Disaster Recovery | BACKUP + DISASTER_RECOVERY |
| 10 | Email/SMS | INTEGRATIONS |
| 11 | LMS | INTEGRATIONS |
| 12 | HR/SMHR | INTEGRATIONS |
| 13 | Finance & Procurement | FINANCE_PAYMENTS |
| 14 | Library | INTEGRATIONS |
| 15 | Zoom/Teams/Blackboard/Meetings | INTEGRATIONS |
| 16 | Integration Synchronization | INTEGRATIONS |
| 17 | Monitoring & Logging | MONITORING |
| 18 | Load/Performance Testing | PERFORMANCE |
| 19 | Penetration/Security Testing | SECURITY |
| 20 | Disaster-Recovery Test | DISASTER_RECOVERY |
| 21 | Final User Acceptance Testing | UAT |
| 22 | GO-LIVE APPROVAL | INSTITUTIONAL_APPROVAL |

### Sequence Status Resolution:
Most restrictive status wins across all categories in a step:
`FAIL > WARN > IN_PROGRESS > NOT_STARTED > PASS > NOT_APPLICABLE`

---

## 3. All GoLive Categories (GoLiveCategory)

```python
FUNCTIONAL_TESTING      # Feature/workflow testing
UI_UX                   # UI/UX quality review
ACCESSIBILITY           # Accessibility compliance
SECURITY                # Security & Compliance
DATA_PROTECTION         # Data Protection & Privacy
DATA_MIGRATION          # Data Migration & Quality
ACADEMIC_RULES          # Academic Rules & Business Logic
FINANCE_PAYMENTS        # Finance & Payments Reconciliation
ROLE_PERMISSIONS        # Role & Permission Matrix
INSTITUTIONAL_APPROVAL  # Final institutional sign-off
INTEGRATIONS            # Third-party dependencies
PERFORMANCE             # Performance & Scalability
BACKUP                  # Backup
DISASTER_RECOVERY       # Disaster Recovery
BUSINESS_CONTINUITY     # Business Continuity
MONITORING              # Monitoring & Observability
UAT                     # User Acceptance Testing
TRAINING                # Training
DOCUMENTATION           # Documentation
SUPPORT                 # Support & Helpdesk
```

---

## 4. Security Domains Checklist (from security_compliance_services.py)

These are the authoritative checkpoints used in the Security & Compliance cockpit.

### Domain 1: Security Governance & Ownership
**Owner:** Security Administrator
- [ ] Production access owners are documented and separated by duty
- [ ] Deployment, database, user, payment, log, backup, and incident authorities are assigned
- [ ] No single administrator retains unnecessary unrestricted access

### Domain 2: Asset Inventory
**Owner:** Application/DevOps Administrator
- [ ] Servers, software, integrations, owners, credentials, data handled, backups, and recovery steps are inventoried
- [ ] External services (payments, email, SMS, LMS, HR, finance, library, DNS, monitoring) are assigned owners

### Domain 3: Infrastructure, Network, SSH & TLS
**Owner:** Network Administrator
- [ ] Only HTTPS is publicly exposed; database, Redis, debug, and internal admin ports stay private
- [ ] SSH is key-based, restricted, monitored, and emergency access is controlled
- [ ] TLS certificates, HSTS, redirects, renewal, and expiry monitoring are verified

### Domain 4: Secrets & Source Code Security
**Owner:** Application/DevOps Administrator
- [ ] Production secrets are outside source code, Git history, frontend bundles, logs, screenshots, and exports
- [ ] Dependency, SAST, secret scanning, and vulnerable package reviews are complete
- [ ] Development credentials are rotated before production

### Domain 5: Login, Passwords, MFA, Sessions & Cookies
**Owner:** Security Administrator
- [ ] Login errors are generic, brute-force controls are active, authentication events audited
- [ ] Password reset uses one-time expiring tokens; passwords are NEVER sent by email
- [ ] Privileged MFA is required or formally risk-accepted
- [ ] Sessions and cookies use secure flags and invalidation rules

### Domain 6: Authorization, RBAC, IDOR & Admin Security
**Owner:** Security Administrator
- [ ] Every protected operation is authorized server-side (not only hidden in UI)
- [ ] Student, lecturer, staff, finance, admin, and disabled-account access paths have IDOR/BOLA tests
- [ ] Sensitive actions require elevated permission, confirmation, and audit trails

### Domain 7: Database, Integrity, Privacy & Retention
**Owner:** Database Administrator
- [ ] Database access is private, least-privilege, encrypted, audited, and backed up
- [ ] Critical records use constraints: usernames, student numbers, course codes, payments, FK, dates, statuses
- [ ] Data Protection Act 2019 obligations: minimization, retention, privacy-rights processes documented
- [ ] Approved privacy notice, data inventory, lawful-basis register, retention schedule, processor register, breach workflow

### Domain 8: Financial & Payment Security
**Owner:** Finance/Payment Administrator
- [ ] Payments marked successful only after independent server-side provider verification
- [ ] Webhooks validate: signature, timestamp/replay controls, reference, amount, currency, account, idempotency
- [ ] Card secrets and sensitive card data are NEVER stored outside a compliant tokenized architecture

### Domain 9: Files, Documents, Imports & Exports
**Owner:** Data Protection/Privacy Officer
- [ ] Uploads allowlist: type, size, MIME, signature, storage path, authorization, scanning, audit checks
- [ ] Documents use authorization, secure URLs, expiring links where appropriate, download audit trails
- [ ] CSV/Excel imports/exports enforce permissions, validation, filters, record-level auth, formula-injection protection

### Domain 10: API, CORS, CSRF, XSS, SQLi & SSRF
**Owner:** Application/DevOps Administrator
- [ ] APIs enforce: authentication, authorization, validation, rate limits, request limits, secure errors, logging, CSRF, CORS
- [ ] XSS, SQL injection, CSRF, path traversal, command injection, SSTI, deserialization, race, privilege escalation tests done
- [ ] URL-fetching features cannot reach localhost, metadata endpoints, or private networks

### Domain 11: Logging, Audit Trails, Monitoring & Alerts
**Owner:** Security Administrator
- [ ] Security logging captures: auth, privilege, admin, import/export, payment, grade, transcript, config, lockdown, backup, integration events
- [ ] Audit logs are access-controlled, append-oriented, monitored, and retained by policy
- [ ] Alerts exist for: failed admin logins, role escalation, payment anomalies, large exports, repeated 401/403, backup failure, disk, TLS, integration auth failure

### Domain 12: Integrations, Queues & Background Jobs
**Owner:** Integration Administrator
- [ ] LMS, HR, finance, library, meeting, email, SMS, payment, DNS, and storage integrations have owners and credential controls
- [ ] Every integration has: authentication, timeout, retry, idempotency, error handling, logging, audit, health, manual retry, recovery
- [ ] Queues/scheduled jobs are protected from: duplicate jobs, poison messages, flooding, unauthorized submissions

### Domain 13: Backups, Restore Testing & Disaster Recovery
**Owner:** Backup/Disaster Recovery Owner
- [ ] DB, uploaded documents, configuration, critical application data, and required key material are backed up securely
- [ ] Backups are encrypted, access-controlled, monitored, retained, protected from unauthorized deletion, and restore-tested
- [ ] RPO, RTO, ransomware, deletion, provider outage, DNS, cloud, deployment, credential-compromise scenarios documented

---

## 5. Live Runtime Security Checks (from security_compliance_services.py)

These checks run live from settings at `/system-control/security/`:

```python
# PASS/FAIL/WARN determined in real-time from settings
Checks:
  "Production debug mode"          → DEBUG must be False
  "Session cookie: Secure"         → SESSION_COOKIE_SECURE = True
  "Session cookie: HttpOnly"       → SESSION_COOKIE_HTTPONLY = True
  "Session cookie: SameSite"       → SESSION_COOKIE_SAMESITE in {'Lax','Strict'}
  "Session cookie scope"           → Path='/', no cross-domain DOMAIN set
  "CSRF cookie: Secure and SameSite" → CSRF_COOKIE_SECURE + SameSite Lax/Strict
  "Idle session timeout"           → session_timeout_minutes > 0
  "Absolute session lifetime"      → 0 < SESSION_COOKIE_AGE <= 1,209,600 (14d)
  "Session invalidation controls"  → Logout/password-change/suspension revoke sessions
  "Concurrent session management"  → MAX_CONCURRENT_SESSIONS > 0
  "HTTP Strict Transport Security" → SECURE_HSTS_SECONDS > 0
  "Browser response protections"   → SECURE_CONTENT_TYPE_NOSNIFF + X_FRAME_OPTIONS
  "Content Security Policy"        → CONTENT_SECURITY_POLICY set + frame-ancestors 'none'
  "Permissions Policy"             → PERMISSIONS_POLICY set
  "Authenticated API CORS"         → '*' NOT in CORS_ALLOWED_ORIGINS
  "Sensitive endpoint rate limiting" → SECURITY_RATE_LIMIT_ENABLED
  "Shared security cache"          → Cache backend is NOT locmem/dummy (requires Redis)
  "Admin accounts"                 → At least one active ADMIN user or superuser
```

### Database Security Checks (from database_security_services.py):
```python
  "Production database backend"    → Not SQLite in production
  "Database credentials"           → USER + PASSWORD configured outside source
  "Database encryption in transit" → OPTIONS.sslmode in {'require','verify-ca','verify-full'}
  "Private database network"       → DATABASE_PRIVATE_NETWORK = True (firewall evidence)
  "Migration state"                → No unapplied migrations
  "Foreign-key and database constraints" → connection.check_constraints() passes
  "Password storage"               → All users use pbkdf2_ / argon2 / bcrypt hashes
  "Sensitive credential fields"    → encrypted_credentials used for payment providers
  "Audit record protection"        → AuditLog has no edit endpoint
  "Encrypted database backups"     → Default BackupStorage.encryption_at_rest = True
  "Backup restoration evidence"    → At least one BackupJob.Status.RESTORE_SUCCESSFUL
```

---

## 6. Content Security Policy (CSP) Management

### Current CSP Location:
`config/settings.py` → `_CSP_DIRECTIVES` list → `CONTENT_SECURITY_POLICY`

### Required for Go-Live Approval:
The live runtime check verifies:
```python
"frame-ancestors 'none'" in settings.CONTENT_SECURITY_POLICY
```
This must be present or the CSP check **fails**.

### Adding a New External Source — Procedure:
1. Identify the blocked resource from browser console
2. Add the minimum required domain to the correct directive

| Resource Type | Directive |
|---|---|
| JavaScript | `script-src` |
| CSS | `style-src` |
| Fonts | `font-src` |
| Images | `img-src` |
| iframes/embeds | `frame-src` |
| XHR/fetch | `connect-src` |

3. Test in `CSP_REPORT_ONLY` mode first before enforcing
4. **Never add `unsafe-eval`** or wildcards

---

## 7. Production Settings Audit

Run before every deployment:
```bash
.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py migrate --check
```

### Required Production Settings:
```python
DEBUG = False
SECRET_KEY = os.environ["SECRET_KEY"]       # env var, never hardcoded
ALLOWED_HOSTS = ["yourdomain.com"]           # never ['*']
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 3600                   # 1 hour idle
MAX_CONCURRENT_SESSIONS = 3                  # per user
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURITY_RATE_LIMIT_ENABLED = True
DATABASE_PRIVATE_NETWORK = True              # evidence flag
# Redis required:
DJANGO_CACHE_BACKEND = "django.core.cache.backends.redis.RedisCache"
DJANGO_CACHE_LOCATION = "redis://redis-host:6379/1"
```

---

## 8. Dependency Auditing (Monthly + Pre-Release)

```bash
.venv/bin/pip install pip-audit
.venv/bin/pip-audit                    # known vulnerabilities
.venv/bin/pip list --outdated          # outdated packages
```

---

## 9. Incident Response Quick Reference

| Scenario | Immediate Action |
|---|---|
| Suspected breach | Rotate `SECRET_KEY` (invalidates all sessions), rotate DB password |
| CSP violation spike | Check if new feature or XSS — block at firewall if malicious |
| Payment anomaly alert | Freeze affected `FeeAccount`, audit `PaymentReconciliation` |
| Failed admin logins spike | Lock account, audit `AuditLog.Module.AUTH` |
| Backup failure | Alert backup owner, verify storage, attempt manual backup |
| Open Critical/High issue | Gate becomes RED — no deployment until `CLOSED`/`RETESTED` |

---

## 10. Go-Live Readiness Quick Commands

```bash
# Check Django deploy warnings
.venv/bin/python manage.py check --deploy

# Verify no unapplied migrations
.venv/bin/python manage.py migrate --check

# Run security preflight (custom command)
.venv/bin/python manage.py security_preflight

# Trigger backup
.venv/bin/python manage.py run_background_jobs --job=backup

# Enable maintenance lockdown during deployments
.venv/bin/python manage.py start_maintenance
```

### Admin Panel Links:
| Area | URL name |
|---|---|
| Go-Live Command Center | `/system-control/` |
| Security Compliance | `university:security_compliance_dashboard` |
| Audit Trails | `university:audit_dashboard` |
| System Backups | `university:backup_dashboard` |
| Staff Permissions | `university:staff_permissions_dashboard` |
| Payment Certification | `university:payment_go_live_certification` |
| Monitoring | `university:monitoring_dashboard` |
| Security Testing | `university:security_testing_dashboard` |
