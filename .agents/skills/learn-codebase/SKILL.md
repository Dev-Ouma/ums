---
name: learn-codebase
description: >-
  Scoped codebase reconnaissance skill for the UMS Django monolith.
  Provides a staged exploration strategy — entry points first, then
  domain seams, then deep reading — so large codebases are absorbed
  accurately without pretending full coverage in one pass.
  Activate at the start of any new session involving unfamiliar modules.
---

# Learn Codebase Skill — UMS Project

## Core Observation (from log #4)
> Codebase learning should start with source-of-truth topology and workflow
> seams, then deepen selectively — never claim full absorption in one pass.

---

## Stage 1: Entry Point Reconnaissance (Always First)

Read these files BEFORE touching any domain code:

```bash
# 1. Root URL routing
cat config/urls.py

# 2. Settings — understand env, apps, middleware, CSP
cat config/settings.py

# 3. Requirements — understand installed packages
cat requirements.txt

# 4. Gitignore — understand what's excluded
cat .gitignore
```

### Key Settings to Note:
- `INSTALLED_APPS` → which apps are active
- `MIDDLEWARE` → request/response pipeline
- `AUTH_USER_MODEL` → custom user model location
- `_CSP_DIRECTIVES` → current Content Security Policy
- `DEBUG` → development vs production mode

---

## Stage 2: Domain Topology (Apps and Models)

For each app in `INSTALLED_APPS`, read:
1. `<app>/models.py` — domain entities
2. `<app>/urls.py` — URL surface
3. `<app>/admin.py` — registered admin views

### UMS App Map (full service file landscape):
```
accounts/
  models.py             ← CustomUser, StudentProfile, FacultyProfile, AdminProfile
  identity.py           ← Role constants (STUDENT/FACULTY/ADMIN)
  activity.py           ← Login tracking
  email_identity_service.py ← Institutional email provisioning
  signature_services.py ← Digital signature configuration

university/  (130+ source files — read selectively by domain)
  Core Domain:
    services.py              models.py         forms.py
  Identity & Access:
    identity_services.py     identity_models.py  identity_views.py
    permissions_services.py  permissions_views.py
    decorators.py            security_decorators.py
  Examinations:
    examination_services.py  examination_views.py  examination_forms.py
    examination_operations.py examination_urls.py
  Finance:
    payment_services.py      financial_services.py
    payment_providers/       fee_account_views.py  fee_payment_views.py
    payment_certification_services.py
  Academics:
    admissions_services.py   academics_views.py    academic_calendar_services.py
    graduation_services.py   attachment_services.py attachment_views.py
    library_services.py      hostel_services.py
    evaluation_services.py   evaluation_views.py
  PDFs & Exports:
    document_design.py       transcript_io.py  progressive_report_io.py
    transcript_views.py      document_views.py
    student_io.py faculty_io.py fee_io.py marks_io.py
    course_io.py program_io.py timetable_io.py
  System Control:
    control_services.py      control_views.py   control_urls.py
    golive_services.py       golive_models.py   golive_views.py
    module_services.py       module_models.py   module_views.py
    backup_services.py       backup_models.py   backup_views.py
    monitoring_services.py   monitoring_views.py
    audit_services.py        audit_views.py
    security_compliance_services.py security_compliance_views.py
    recycle_bin_services.py  recycle_bin_views.py
  Integrations:
    integrations/            email_services.py  sms_services.py
    integration_views.py     institution_domain_services.py
  CMS:
cms/
  services.py  models.py  views.py  sanitizer.py
  management/commands/seed_cms.py
```

### Quick model count:
```bash
.venv/bin/python manage.py shell -c "
from django.apps import apps
for app in apps.get_app_configs():
    models = list(app.get_models())
    if models:
        print(f'{app.label}: {len(models)} models — {[m.__name__ for m in models]}')
"
```

---

## Stage 3: Workflow Seams (Views and Services)

After understanding models, map the primary workflows:

```bash
# Count views per app
wc -l accounts/views.py university/views.py cms/views.py

# Find all view function definitions
grep -n "^def \|^class " university/views.py | head -50

# Find all URL patterns
grep -n "path(" university/urls.py | head -30
```

### Known Major Workflow Areas (UMS):
| Area | Views file | URL prefix |
|---|---|---|
| Authentication | `accounts/views.py` | `/accounts/` |
| Student Portal | `university/views.py` | `/me/` |
| Faculty Portal | `university/views.py` | `/teach/` |
| Admin Portal | `university/views.py` | `/manage/` |
| Identity Admin | `university/views.py` | `/system-admin/users/` |
| Examinations | `university/examination_urls.py` | `/manage/academics/examinations/` |
| System Control | `university/control_urls.py` | `/system-control/` |
| CMS/Public | `cms/views.py` | `/<slug>/` |

---

## Stage 4: Template Structure

```bash
# List all templates
find templates/ -name "*.html" | sort

# Understand base templates
cat templates/base_public.html
cat templates/base_dashboard.html

# Find which templates extend which base
grep -rn "extends" templates/ | grep -v ".DS_Store"
```

### Template Hierarchy:
```
templates/
├── base_public.html           ← Public/unauthenticated pages
├── base_dashboard.html        ← Authenticated role portals
├── public/                    ← home, about, contact, courses
├── student/                   ← Student portal views
├── faculty/                   ← Faculty/teaching staff views
├── admin/                     ← Administration views
└── emails/                    ← Email templates
```

---

## Stage 5: Test Coverage Map

```bash
# List all test files
find . -name "test*.py" | grep -v ".venv"

# Run full test suite
.venv/bin/python manage.py test --verbosity=2

# Run tests for a specific app
.venv/bin/python manage.py test accounts --verbosity=2
```

### Known Test Files:
- `accounts/tests_login_security.py` — authentication and security
- `accounts/tests_password_policy.py` — password validation rules
- `accounts/tests_profile.py` — profile management

---

## Stage 6: What to Record After Reconnaissance

After completing stages 1–5, document:
- [ ] Which apps/modules are well-tested vs untested
- [ ] Which views lack `@login_required` or `@permission_required`
- [ ] Which models lack migrations or have schema drift
- [ ] Which workflows span multiple apps (cross-cutting concerns)
- [ ] What is NOT yet understood (flag for deeper follow-up)

---

## Quick Reference Commands

```bash
# Run server
.venv/bin/python manage.py runserver

# Interactive shell
.venv/bin/python manage.py shell

# Show all URLs
.venv/bin/python manage.py show_urls  # (if django-extensions installed)

# Database stats
.venv/bin/python manage.py dbshell
.tables          # SQLite: list all tables
.schema <table>  # SQLite: show table schema

# Check for issues
.venv/bin/python manage.py check
.venv/bin/python manage.py check --deploy  # Production checks
```
