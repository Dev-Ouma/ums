---
name: cybersecurity-server-hardening
description: >-
  Security hardening skill for the UMS Django production deployment.
  Covers CSP management, production checklist, HTTPS/SSL enforcement,
  dependency auditing, operational security dashboard patterns, and
  converting security guides into tracked, owned operational items.
  Activate when working on security settings, CSP, middleware, or deployment.
---

# Cybersecurity & Server Hardening Skill — UMS Project

## Core Observation (from log #3)
> Security documentation is strongest when converted into tracked ownership,
> evidence, live status, blockers, and operational links — not static prose.

---

## 1. Content Security Policy (CSP) Management

### Current CSP Location
`config/settings.py` → `_CSP_DIRECTIVES` list

### Adding a New External Source — Standard Procedure

**Step 1: Identify the blocked resource**
```bash
# Check browser console for CSP violation messages like:
# "Refused to load the script 'https://example.com/...' because it violates the CSP"
# OR check Django response headers:
curl -I http://127.0.0.1:8000/your-page/ | grep -i "content-security"
```

**Step 2: Identify the correct directive**
| Resource Type | CSP Directive |
|---|---|
| JavaScript files | `script-src` |
| CSS files | `style-src` |
| Fonts | `font-src` |
| Images | `img-src` |
| iframes / embeds | `frame-src` |
| XHR / fetch calls | `connect-src` |
| Web fonts served via CDN | `font-src` |

**Step 3: Add the minimal required domain**
```python
# In config/settings.py — add only the specific domain, not wildcards
"frame-src 'self' https://www.openstreetmap.org",  # ✓ Specific
"frame-src *",                                       # ✗ Never use wildcard
```

**Step 4: Test in Report-Only mode first (production)**
```python
# Use report-only to detect violations without breaking the site
CSP_REPORT_ONLY = True  # Set via env: DJANGO_CSP_REPORT_ONLY=true
```

### Current Approved External Sources
```python
# scripts
https://cdn.jsdelivr.net
https://unpkg.com            # Leaflet.js maps

# styles  
https://fonts.googleapis.com
https://cdnjs.cloudflare.com
https://unpkg.com

# fonts
https://fonts.gstatic.com

# frames
https://maps.google.com
https://www.openstreetmap.org

# tile connections
https://*.tile.openstreetmap.org
```

---

## 2. Production Security Checklist

Run this before every deployment:
```bash
.venv/bin/python manage.py check --deploy
```

### Critical Settings Audit
```python
# These MUST be set for production:
DEBUG = False                          # ← NEVER True in production
SECRET_KEY = os.environ["SECRET_KEY"] # ← From env, never hardcoded
ALLOWED_HOSTS = ["yourdomain.com"]    # ← Never ['*']
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
```

### Check current settings safely:
```bash
.venv/bin/python manage.py shell -c "
from django.conf import settings
checks = [
    ('DEBUG', settings.DEBUG, False),
    ('ALLOWED_HOSTS', settings.ALLOWED_HOSTS, ['your-domain']),
    ('SECURE_SSL_REDIRECT', getattr(settings, 'SECURE_SSL_REDIRECT', False), True),
    ('SESSION_COOKIE_SECURE', settings.SESSION_COOKIE_SECURE, True),
    ('CSRF_COOKIE_SECURE', settings.CSRF_COOKIE_SECURE, True),
]
for name, val, expected in checks:
    status = '✓' if val == expected else '✗ FAIL'
    print(f'{status} {name} = {val}')
"
```

---

## 3. Dependency Auditing

Run monthly and before every production release:
```bash
# Audit for known vulnerabilities
.venv/bin/pip install pip-audit
.venv/bin/pip-audit

# Check outdated packages
.venv/bin/pip list --outdated

# Update safely (one at a time, test after each)
.venv/bin/pip install --upgrade <package>
.venv/bin/python manage.py check
```

---

## 4. Security Findings → Operational Dashboard Pattern

When implementing a security guide or audit, convert findings into:

| Field | Description |
|---|---|
| **Domain** | Which area (Auth, CSP, Payments, Dependencies) |
| **Owner** | Who is responsible for this item |
| **Status** | Not Started / In Progress / Resolved / Accepted Risk |
| **Evidence** | Link to commit, test, or config showing it's fixed |
| **Blocker** | What is blocking resolution (if any) |
| **Severity** | Critical / High / Medium / Low |
| **Admin Link** | URL to the relevant admin/management screen |

### Severity Escalation Rules:
- **Critical/High findings block production deployment** — no exceptions
- **Medium findings** must have a ticket/issue with an owner and due date
- **Low findings** logged for next sprint review

---

## 5. Authentication & Session Security

```python
# Recommended session settings
SESSION_COOKIE_AGE = 3600         # 1 hour idle timeout
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Password validation (in AUTH_PASSWORD_VALIDATORS)
# Ensure these are active:
# - UserAttributeSimilarityValidator
# - MinimumLengthValidator (min_length=10)
# - CommonPasswordValidator
# - NumericPasswordValidator
```

---

## 6. Incident Response Quick Reference

**Suspected breach:**
1. Immediately set `DJANGO_LOCKDOWN=true` (if lockdown middleware exists)
2. Rotate `SECRET_KEY` (invalidates all sessions)
3. Rotate database password
4. Audit `last_login` and `date_joined` for anomalous accounts
5. Check server logs for unusual IP activity

**CSP violation spike:**
1. Check `CSP_REPORT_ONLY` reports
2. Identify the new external resource being loaded
3. Determine if it's legitimate (new feature) or malicious (XSS injection)
4. If malicious: block at firewall + audit affected sessions
5. If legitimate: add to approved CSP list via standard procedure above
