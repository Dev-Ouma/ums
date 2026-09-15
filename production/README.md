# UMS Production Runbook

This folder is the living deployment guide for the University Management System. Update it whenever a production dependency, migration, security control, or operational process changes.

## Production architecture

Use Django behind Nginx and Gunicorn, with PostgreSQL as the database, Redis as the shared cache/task broker, and S3-compatible object storage for uploaded documents and generated PDFs. Run at least two application workers or instances for resilience.

This is a Django/Python application. PHP-FPM is not required. Gunicorn serves Django web requests; Celery workers process long-running background jobs such as PDF generation, email/SMS, reports, imports, and reconciliation.

```text
Browser -> HTTPS/Nginx -> Gunicorn/Django -> PostgreSQL
                                      -> Redis
                                      -> Object storage
                                      -> Celery workers
```

## Phased path to go-live

Follow these phases in order. Do not begin user handover while an earlier phase has unresolved blockers.

### Phase 0 — Ownership and decisions

- Name the university product owner, System Administrator, DBA, security lead, DPO, finance owner, integrations owner, support lead, and incident commander.
- Confirm the domain, institution identity, data residency requirements, user-volume target, support hours, recovery targets, retention policy, and budget.
- Decide which integrations are required at launch: email, SMS, payments, LMS, SMHR/HR, finance/procurement, library, and meetings.
- Create the permissions matrix and module ownership register.

Exit gate: owners, scope, launch date, privacy responsibilities, and required integrations are approved.

### Phase 1 — Domain, hosting, and network foundation

- Register the domain with a reputable registrar; keep registrar ownership with the institution and enable MFA/2FA and registrar lock.
- Use a managed VPS/cloud provider or managed platform with a private network, encrypted disks, snapshots, monitoring, and support. Keep PostgreSQL and Redis private.
- Put Cloudflare in front for DNS, CDN, WAF, DDoS protection, rate limiting, and origin shielding. Proxy only public web hostnames; keep database and Redis DNS records unproxied/private.
- Prefer Cloudflare Tunnel when inbound origin exposure is undesirable. Otherwise firewall the origin to HTTPS and trusted administration paths only.
- Use Cloudflare SSL/TLS `Full (strict)` with a valid origin certificate; never use Flexible mode for an authenticated university system.
- Use a dedicated IP only when the host, email reputation, allow-listing, or a provider explicitly requires it. HTTPS certificates no longer generally require a dedicated IP.

Exit gate: domain resolves, TLS is valid, origin is protected, SSH is restricted, backups have a destination, and the hosting owner can recover access.

### Phase 2 — Application and database production setup

- Set `DJANGO_DEBUG=false`, production secrets, explicit hosts, PostgreSQL, Redis, email, storage, and payment settings.
- Install dependencies, run `check --deploy`, migrate, collect static files, and start Gunicorn behind Nginx. Do not use `runserver`.
- Configure private media delivery, upload limits, database backups, connection limits, indexes, and log rotation.
- Run the existing scheduled background command using a single systemd timer/cron owner; introduce Celery when jobs become long-running or numerous.

Exit gate: the application starts after restart, migrations are recorded, static/media behavior is correct, and a restore test succeeds.

### Phase 3 — Security and identity hardening

- Verify authentication, password policy, brute-force throttling, secure cookies, CSRF, session expiry, logout, session revocation, role switching, scoped permissions, and audit trails.
- Run browser console/network inspection, dependency review, upload tests, authorization tests, TLS/header checks, and an independent penetration test before launch.
- Configure SMTP/SMS secrets in a secret manager and validate SPF, DKIM, DMARC, sender identity, delivery, retries, and consent.
- Approve the privacy notice, data inventory, lawful-basis register, retention/deletion schedule, rights-request process, breach process, processor agreements, and DPO contact.

Exit gate: no critical/high security issues remain open and the security/DPO owners sign off.

### Phase 4 — Functional and integration readiness

- Test every role and module end to end: public portal, admissions, cohorts, student records, registration, examinations, marks, results, transcripts, fees, payments, finance, library, graduation, reports, notifications, maintenance, audit, backups, and system administration.
- For each external integration, test authentication, field mapping, retries, duplicate prevention, outages, reconciliation, audit logging, privacy, and sandbox/production separation.
- Mark unsupported integrations explicitly `NOT_APPLICABLE` or `IN_PROGRESS` in Go-Live; do not present a menu item as a working integration.

Exit gate: role-based UAT passes using realistic but safe data and all launch-critical workflows have evidence.

### Phase 5 — Performance, operations, and training

- Load-test peak logins, admissions, registration, marks submission, PDF generation, fee payment, and reporting.
- Tune PostgreSQL, Gunicorn workers, Redis, background workers, static/media delivery, and upload limits from measurements.
- Configure uptime, error, resource, queue, backup, payment, and security alerts. Test intentional failures.
- Train administrators, finance, admissions, faculty, support, and students; publish quick-start guides and escalation contacts.

Exit gate: performance targets, monitoring alerts, support procedures, and training acceptance are signed off.

### Phase 6 — Controlled launch and handover

- Freeze non-essential changes, take verified backups, record the release and migration list, and run the Go-Live Command Center at `/system-admin/go-live/`.
- Launch with a named on-call team and a rollback window. Monitor authentication, errors, payments, queues, database health, and user support continuously.
- Handover domain/Cloudflare, hosting, SSH, secret-manager, database, backups, email/SMS, payment, monitoring, source-control, and vendor ownership to named institutional accounts.
- Deliver the permissions matrix, architecture diagram, runbooks, privacy records, integration register, test evidence, release notes, backup-restore evidence, and support SLA.

Exit gate: institutional owner signs acceptance and the first post-launch review is scheduled.

## First deployment

1. Provision PostgreSQL, Redis, object storage, DNS, and TLS.
2. Create a dedicated operating-system/service account and a private application directory.
3. Install the pinned Python dependencies:

   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

4. Set the required environment variables. Use `production/.env.example` as a checklist. Store real secrets in the hosting secret manager; do not commit `.env` files.
5. Run deployment checks and migrations:

   ```bash
   DJANGO_DEBUG=false .venv/bin/python manage.py check --deploy
   .venv/bin/python manage.py migrate --noinput
   .venv/bin/python manage.py collectstatic --noinput
   ```

6. Create or verify the initial administrator through the approved account-provisioning process.
7. Start Gunicorn using the project WSGI module (`config.wsgi:application`). Start Celery workers separately when background jobs are enabled.
8. Configure Nginx to proxy HTTPS traffic and serve static files. Do not serve private media directly without an authenticated document view.
9. Run a smoke test of login, role switching, admissions, registration, marks, fees, transcripts, uploads, and logout.

## Required production settings

The application must run with `DJANGO_DEBUG=false`, a long unique `DJANGO_SECRET_KEY`, explicit `DJANGO_ALLOWED_HOSTS`, a non-SQLite database, and a shared non-local cache. Database SSL should remain enabled. See `config/settings.py` for the enforced checks.

Minimum database variables:

```text
DJANGO_DB_ENGINE=django.db.backends.postgresql
DJANGO_DB_NAME=ums
DJANGO_DB_USER=ums_app
DJANGO_DB_PASSWORD=<secret>
DJANGO_DB_HOST=<private-postgres-host>
DJANGO_DB_PORT=5432
DJANGO_DB_SSL_REQUIRE=true
```

## Release procedure

1. Review the diff and migration files.
2. Run `check --deploy`, `git diff --check`, and the relevant Django tests.
3. Take a database and media backup.
4. Deploy the application code and install dependencies.
5. Run migrations before enabling new application code that depends on them.
6. Run `collectstatic`.
7. Restart workers using a graceful reload.
8. Verify health, authentication, permissions, payments, document generation, and error logs.
9. Record the release, migration names, and rollback decision in the change log.

## Backups and recovery

Back up PostgreSQL and uploaded media independently. Test restoration regularly in an isolated environment. Keep encrypted, access-controlled backups with a documented retention period. A backup is not considered valid until a restore has been verified.

## Scaling checklist

- PostgreSQL connection limits and slow-query monitoring configured.
- Redis shared by all workers.
- Gunicorn web-worker count sized from load tests; begin with 2–4 workers for a small production deployment.
- Celery worker count and queue monitoring sized separately from web workers.
- Background job runner added for large PDFs, reports, notifications, imports, and reconciliation.
- Object storage used for media and generated documents.
- Worker timeouts and upload limits reviewed.
- Error tracking, uptime checks, logs, and alerting enabled.
- Load testing completed against expected peak login, registration, examination, and fee-payment traffic.

## Security checklist

- HTTPS, secure cookies, HSTS, CSRF protection, and explicit hosts enabled.
- `DEBUG` disabled and secret values kept outside source control.
- Least-privilege database user used; no public database exposure.
- Role assignments scoped to the correct school or department.
- Audit logs retained for role, admissions, marks, fee, and document actions.
- Payment webhooks require signature verification.
- Upload validation, private media access, rate limiting, and session controls verified.
- Development database files are never copied to production.

## Go-Live Command Center

The authoritative in-app readiness board is available to authorized administrators at:

`/system-admin/go-live/`

Use it as the approval record, not as a static checklist. Each readiness area should have a status, accountable owner, notes, evidence reference, and authorized sign-off. Record unresolved risks in the issue ledger with severity, impact, owner, due date, resolution, evidence, retest result, and final status. Critical and high unresolved issues block the final gate.

The current readiness sequence captures these areas:

| Area | What must be evidenced |
|---|---|
| Infrastructure, domain, IP, server, SSH, hosting | Provider, DNS, TLS, firewall, SSH keys/MFA, patching, backups, and recovery owner |
| Login, authentication, cookies, sessions | Password policy, brute-force controls, session expiry, secure cookie flags, logout/session revocation, hijacking checks |
| Security and APIs | Security headers, CSRF, upload controls, authorization tests, API inventory, authentication, rate limits, and no secrets in browser code |
| Email and SMS | Provider, sender/domain verification, templates, delivery/retry logs, opt-in basis, failure handling, and test evidence |
| GDPR/data protection | Data inventory, lawful basis, privacy notice, retention/deletion, access/export/correction requests, breach response, DPO, processor agreements, and international-transfer assessment |
| Payments | Production credentials, signed webhooks, idempotency, reconciliation, refunds/reversals, duplicate/replay tests, and finance sign-off |
| Backend, database, frontend, UI/UX | Functional tests, migrations, PostgreSQL, indexes, accessibility, responsive screens, error handling, and browser smoke tests |
| Caching, deployment, monitoring | Redis/shared cache, worker capacity, queues, logs, alerts, health checks, and rollback procedure |
| LMS, SMHR/HR, finance/procurement, library, meetings | Data owner, fields exchanged, direction, sync/retry behavior, sandbox/production separation, access controls, processor/DPA, and failure recovery |
| User acceptance, training, support | Role-based UAT, test accounts, training material, helpdesk owner, escalation path, SLAs, and signed acceptance |

## SSL, email, scheduled jobs, and monitoring status

| Capability | Current project support | Production action |
|---|---|---|
| SSL/TLS certificate | Django enforces HTTPS redirects, secure cookies, HSTS, and security headers when `DJANGO_DEBUG=false`. Database TLS is separately configurable. | Obtain a certificate through the hosting provider or Certbot/Let's Encrypt, configure renewal, redirect HTTP to HTTPS at Nginx, and test renewal before go-live. |
| SMTP/email gateway | Supported through the in-app email configuration, including SMTP, TLS/SSL modes, sender settings, and a test-email flow. | Use a reputable transactional provider or authenticated institutional Microsoft 365/Google Workspace relay. Verify SPF, DKIM, DMARC, sender reputation, bounce handling, retries, and secret-manager storage. |
| Scheduled jobs/cron | The project includes `run_background_jobs`, which runs scheduled maintenance, backup, retention, and notification work. | Schedule it with systemd timers, cron, or a managed scheduler. Ensure only one scheduler runs each job, capture exit codes, and alert on failures. Move heavy work to Celery when traffic grows. |
| Telegram monitoring bot | No Telegram bot integration is currently implemented or configured. | Do not mark Telegram monitoring as complete. Add it only through an approved outbound-notification design with a bot token in the secret manager, allow-listed chat IDs, redaction, rate limits, audit events, and a fallback alert channel. |

Example cron entry for the existing management command (adapt the path and frequency to the hosting environment):

```cron
*/5 * * * * cd /srv/ums && /srv/ums/.venv/bin/python manage.py run_background_jobs >> /var/log/ums/background-jobs.log 2>&1
```

Prefer a systemd timer or managed scheduler when available because it provides clearer service ownership, restart behavior, and failure monitoring. Never place SMTP passwords, API keys, bot tokens, or database credentials in the crontab itself.

## Easy setup recipes

### SSL certificate

1. Point the domain DNS records to the server and allow ports 80 and 443.
2. Configure Nginx for the domain and proxy traffic to Gunicorn.
3. Use a managed certificate or Certbot/Let’s Encrypt; enable automatic renewal.
4. Test HTTPS, HTTP-to-HTTPS redirects, the certificate chain, all subdomains, and renewal.

Done when the browser shows a valid certificate and cookies are Secure.

### SMTP email gateway

1. Choose a transactional provider or authenticated Microsoft 365/Google Workspace relay.
2. Verify the sending domain and publish SPF, DKIM, and DMARC records.
3. Configure the provider details in System Administrator email settings.
4. Test internal/external delivery, failure handling, retries, and template rendering.

Done when delivery is auditable and credentials never appear in the UI or logs.

### Cron or systemd jobs

1. Create a dedicated service user and log location.
2. Schedule `manage.py run_background_jobs` with absolute paths.
3. Prevent overlapping executions with a systemd timer or scheduler lock.
4. Capture exit codes and alert on failures or missed runs.

Done when a successful run, deliberate failure alert, and duplicate-run protection have been tested.

### Telegram monitoring (optional)

Telegram is not currently implemented in UMS. If approved later, store the bot token in a secret manager, allow-list private chat IDs, send only redacted operational alerts, rate-limit and deduplicate messages, log delivery outcomes, and maintain an email/SMS fallback. Test token rotation, bot outage, unauthorized chat IDs, and message redaction before marking the Go-Live integration complete.

### Monitoring and browser/server errors

Monitor HTTPS uptime, Gunicorn, PostgreSQL, Redis, disk, memory, queues, backups, and scheduled jobs. During UAT, inspect browser Console and Network panels in a clean private session. Record uncaught exceptions, failed requests, mixed content, blocked assets, exposed data, server errors, and correlation IDs in the Go-Live issue ledger.

Done when an intentional test alert is received, logs correlate to requests, and no critical browser or server errors remain unresolved.

## Web and browser console error inspection

This is a required part of Functional Testing, Security Testing, UI/UX Testing, and Monitoring sign-off. Test the main workflows in a clean browser session and inspect both the server logs and browser developer tools.

For each critical screen, verify:

- No uncaught JavaScript exceptions in the Console.
- No failed requests, unexpected redirects, 4xx responses, 5xx responses, mixed-content warnings, or blocked resources in Network.
- No exposed passwords, tokens, session identifiers, payment secrets, personal data, or stack traces in the console, page source, URLs, or responses.
- No failed static assets, missing fonts/icons, layout overflow, broken links, or accessibility errors.
- POST requests include CSRF protection and unauthorized requests return the expected denial/redirect.
- Slow requests and repeated requests are recorded for performance review.
- Errors reproduce consistently and have an owner, severity, fix, and retest evidence.

Required evidence for Go-Live:

1. Browser and device used, browser version, URL, date/time, and test account role.
2. Sanitized screenshots or exported console/network logs for any issue.
3. Correlation or request ID from the server log where available.
4. Expected result, actual result, severity, impact, and reproduction steps.
5. Go-Live issue-ledger reference and retest result.

Use the browser’s Incognito/Private mode for clean-session checks. Do not attach raw console exports if they contain authorization headers, cookies, query-string tokens, student records, payment details, or email addresses; redact them before storing evidence. Console errors must be fixed or formally accepted with a documented mitigation before final approval.

Suggested smoke-test paths include login/logout, role switching, dashboard loading, admissions, documents, registration, marks capture, fee payment/reconciliation, transcript/fee PDF generation, uploads, reports, search/filter/pagination, maintenance mode, and mobile layouts.

## System Administrator module map

The main administrative areas to review before handover are:

- User and staff administration: `/system-admin/users/`
- Roles and permissions: `/system-admin/users/roles/` and `/manage/setups/permissions/`
- Login security and sessions: `/system-admin/users/security/`, `/system-admin/users/login-history/`, and `/system-admin/users/activity/`
- Modules and feature availability: `/manage/system/modules/`
- System control and maintenance: `/system-control/`
- Audit trails: `/manage/audit-trails/`
- Backups and recovery: `/system-control/` backup controls and the documented restore test
- Go-Live approval: `/system-admin/go-live/`

System Administrator handover must include a permissions matrix, named owners, emergency contacts, provider credentials held in a secret manager, DNS/hosting access, backup restoration evidence, and a signed acceptance record. Never hand over shared passwords or private keys through email or chat.

## Integration decision record

An integration is not production-ready merely because its menu item exists. Before enabling LMS, SMHR, finance/procurement, library, or Zoom/Teams/Blackboard/meeting connectivity, record the provider, environment, API scope, data fields, sync schedule, retry/dead-letter behavior, audit event, timeout, outage fallback, data retention, and contract/DPA. If no approved connector exists, mark the Go-Live area `NOT_APPLICABLE` or `WARN` with an owner and documented decision rather than claiming it is complete.

## Ownership and evidence

Go-Live records should link to evidence stored in the approved document repository: test reports, screenshots, configuration exports with secrets removed, provider verification, restore logs, UAT sign-off, and release notes. Evidence URLs must not expose credentials, session keys, payment secrets, or personal data.

## Rollback

Stop the rollout if migrations fail, authentication breaks, data scopes leak, payments duplicate, or documents render incorrectly. Preserve logs and the backup, return traffic to the last known-good release, and only reverse migrations after reviewing data safety. Never use destructive database reset commands in production.

## Living change log

Add an entry here for each production-impacting change:

| Date | Change | Migration | Verification | Owner |
|---|---|---|---|---|
| 2026-09-15 | Initial production runbook | — | Settings and deployment requirements reviewed | UMS team |
