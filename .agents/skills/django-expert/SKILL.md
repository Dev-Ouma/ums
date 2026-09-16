---
name: django-expert
description: >-
  Comprehensive Django development skill for the UMS project. Covers the service
  layer pattern, permission/authorization architecture, model design, view
  patterns, atomic transactions, and end-to-end permission catalog auditing.
  Activate for any Django model, view, service, or permission work.
---

# Django Expert Skill — UMS Project

## Project Architecture at a Glance

```
ums/
├── accounts/       → CustomUser, StudentProfile, FacultyProfile, AdminProfile
│   ├── identity.py                 ← Role constants, profile helpers
│   ├── activity.py                 ← Login activity tracking
│   ├── email_identity_service.py   ← Institutional email management
│   └── signature_services.py       ← Digital signature config
├── university/     → Core domain — 130+ service files
│   ├── identity_services.py        ← SINGLE AUTHORITY for user/account creation
│   ├── identity_models.py          ← UserAccount, AccountStatus, LoginRecord
│   ├── permissions_services.py     ← Granular RBAC + user-level overrides
│   ├── payment_services.py         ← Fee allocation, reconciliation, reversal
│   ├── payment_providers/          ← Adapters: mpesa.py, bank.py, card.py
│   ├── examination_services.py     ← Exam workflow, marks, grading, appeals
│   ├── financial_services.py       ← Fee accounts, invoices, receipts
│   ├── audit_services.py           ← log_activity() — ALL mutations log here
│   ├── golive_services.py          ← Go-Live readiness board (PASS/WARN/FAIL)
│   ├── module_services.py          ← System module registry + feature toggles
│   ├── backup_services.py          ← Atomic DB snapshots, SHA-256, retention
│   ├── transcript_io.py            ← PDF transcript generation (ReportLab)
│   ├── document_design.py          ← Shared PDF brand system (Quicksand font)
│   ├── security_compliance_services.py ← Security posture tracking
│   └── monitoring_services.py      ← System health and uptime checks
├── cms/            → CMS pages, public-facing content
├── config/         → Settings, URLs, CSP, middleware
├── templates/      → HTML templates
└── static/         → CSS, JS, fonts (Quicksand TTF embedded in PDFs)
```

**Python:** `.venv/bin/python`  
**Django management:** `.venv/bin/python manage.py <command>`  
**DB:** SQLite (dev) — `db.sqlite3`

### Management Commands (all in `university/management/commands/`):
| Command | Purpose |
|---|---|
| `seed_wigot_courses` | Seed Wigot School of Hospitality courses |
| `seed_demo` | Full demo dataset for development |
| `seed_demo_users` | Demo users (student/faculty/admin roles) |
| `seed_exam_schedules` | Sample examination timetables |
| `align_assessments` | Repair assessment–enrollment alignment |
| `run_background_jobs` | Trigger background job processing |
| `security_preflight` | Pre-deployment security checks |
| `start_maintenance` | Enable maintenance lockdown mode |
| `system_control_tick` | Heartbeat for system control panel |

---

## 1. Service Layer Pattern (MANDATORY)

### Rule: Keep Views Thin, Services Fat
Never place business logic directly in views. All multi-step operations, 
cross-model mutations, and permission-gated actions belong in a service module.

**Structure:**
```
university/
├── services/
│   ├── __init__.py
│   ├── enrollment_service.py
│   ├── fee_service.py
│   └── report_service.py
```

**Canonical service method pattern:**
```python
from django.core.exceptions import PermissionDenied
from django.db import transaction

class EnrollmentService:
    @transaction.atomic
    def enroll_student(self, actor, student_profile, course):
        # 1. ALWAYS authorize first
        if not actor.has_perm("university.add_enrollment"):
            raise PermissionDenied("You do not have permission to enroll students.")
        
        # 2. Validate domain rules
        if Enrollment.objects.filter(student=student_profile, course=course).exists():
            raise ValueError("Student is already enrolled in this course.")
        
        # 3. Mutate — wrapped in atomic transaction
        enrollment = Enrollment.objects.create(
            student=student_profile,
            course=course,
            status="ACTIVE",
            enrolled_by=actor,
        )
        
        # 4. Side effects (emails, notifications) inside atomic block
        self._send_enrollment_confirmation(student_profile, course)
        
        return enrollment
```

### What NEVER belongs in a view:
- Multi-model saves without `@transaction.atomic`
- Permission checks duplicated across views
- Business rule validation (use the service or model's `clean()`)
- Email/notification dispatch logic

---

## 2. Permission Architecture

### Two-Layer Defence

**Layer 1 — View Level (coarse):**
```python
from django.contrib.auth.decorators import login_required, permission_required

@login_required
@permission_required("university.view_course", raise_exception=True)
def course_detail(request, pk):
    ...
```

**Layer 2 — Service Level (granular):**
```python
# Inside service method — always check before mutating
if not actor.has_perm("university.change_course"):
    raise PermissionDenied(...)
```

### Permission Catalog Audit Checklist
Before marking any feature complete, trace every catalog permission through:

```
Permission → Navigation link → Read endpoint → Mutation endpoint
          → Role assignment → Positive HTTP test → Negative HTTP test
```

**Audit command (run to list all permissions):**
```bash
.venv/bin/python manage.py shell -c "
from django.contrib.auth.models import Permission
for p in Permission.objects.select_related('content_type').order_by('content_type__app_label', 'codename'):
    print(f'{p.content_type.app_label}.{p.codename} — {p.name}')
"
```

---

## 3. Model Design Rules

### Do:
- Use `get_object_or_404()` in views, never raw `.get()` without try/except
- Define `__str__`, `get_absolute_url`, and `class Meta: ordering` on every model
- Use `select_related()` / `prefetch_related()` to prevent N+1 queries
- Add `db_index=True` on foreign keys used in frequent lookups
- Use Custom Managers for reusable querysets: `Course.objects.active()`

### Don't:
- Put business logic in `save()` overrides — use services
- Use signals for core workflows — they hide logic and are hard to debug
- Duplicate filtering logic in multiple views — use a custom Manager

### Custom Manager Pattern:
```python
class CourseManager(models.Manager):
    def active(self):
        return self.filter(status=Course.STATUS_ACTIVE)
    
    def for_department(self, dept):
        return self.filter(department=dept).select_related("faculty")

class Course(models.Model):
    objects = CourseManager()
    ...
```

---

## 4. Migration Safety Rules

Before running any migration:
```bash
# 1. Check for pending migrations
.venv/bin/python manage.py showmigrations

# 2. Preview the SQL
.venv/bin/python manage.py sqlmigrate <app> <migration_number>

# 3. Run with --check in CI (exits non-zero if migrations pending)
.venv/bin/python manage.py migrate --check
```

**NEVER:**
- Delete migrations — squash them instead (`squashmigrations`)
- Rename fields directly — use a 2-step migration (add → copy → remove)
- Run migrations on production without a tested rollback plan

---

## 5. Template Patterns

This project uses two base templates:
- `base_public.html` — for public-facing pages (unauthenticated)
- `base_dashboard.html` — for role-specific authenticated portals

Always extend the correct base. Block structure:
```html
{% extends 'base_dashboard.html' %}
{% block title %}Page Title · UMS{% endblock %}
{% block content %}
  <!-- Your content here -->
{% endblock %}
```

---

## 6. End-to-End Feature Completion Checklist

Before calling any Django feature "done":

- [ ] Model has `__str__`, `Meta.ordering`, and proper field types
- [ ] Service method exists for all mutations (with `@transaction.atomic`)
- [ ] View uses `@login_required` + `@permission_required`
- [ ] Permission exists in catalog and is assigned to at least one group
- [ ] Template extends the correct base, uses `{% csrf_token %}` in all forms
- [ ] N+1 query check: use Django Debug Toolbar or `connection.queries`
- [ ] At least one positive and one negative unit test for the permission
- [ ] Management command added if data seeding is needed (see `seed_wigot_courses`)

---

## 7. Common Pitfalls (from skill-observations log)

- **Observation #7:** Never embed adjacent workflow permissions in the same view.  
  Example: Invigilators and examiners both touch `CandidateResult` — expose  
  separate service methods with different permission codenames.

- **Observation #8:** A permission is NOT implemented until:  
  1. The authorized role can reach the endpoint  
  2. An unauthorized role receives a 403  
  3. The mutation preserves domain invariants on repeat calls

---

## 8. Examination Workflow (examination_services.py)

The examination module has a strict state machine. Key rules:

- **Role check before any write** — use `is_admin(user)`, `can_create_exams(user)` etc.
- **StaffRoleAssignment** controls exam creation: only `hod`, `dean`, `academic_registrar`, `exam_officer`, `vc`, `dvcaa`
- **MarksWorkflowEvent** + **MarksVersion** — every marks change is versioned
- **GradingScale** is exam-specific — never assume a single global scale
- Repeat/supplementary assessments replace original grades (do not average)

```python
# Pattern: always verify role before exam mutation
from university.examination_services import is_admin, can_create_exams

if not can_create_exams(request.user):
    raise PermissionDenied("Only HOD/Dean/Registrar/Exam Officer may create exams.")
```

---

## 9. Identity Service (identity_services.py) — THE Single Authority

**Rule:** NOTHING else in the system is permitted to:
- Hash a password (`make_password`)
- Mint a token
- Invent a username
- Create a `UserAccount` directly

All of the above go through `identity_services.py`:

```python
from university.identity_services import ensure_account, resolve_user_type

# Always use ensure_account() — creates lazily, never duplicates
account = ensure_account(user, user_type=UserType.STUDENT)
```

### AccountStatus transitions are controlled:
```python
from university.identity_models import ALLOWED_STATUS_TRANSITIONS
# Check before changing status — invalid transitions raise ValidationError
```

---

## 10. Permissions Engine (permissions_services.py)

The UMS uses a **three-tier authorization system**:

1. **Base role** (`accounts.Role`) — STUDENT / FACULTY / ADMIN
2. **StaffRoleAssignment** — granular institutional roles (hod, dean, etc.)
3. **UserPermissionOverride** — explicit per-user GRANT or DENY

```python
from university.permissions_services import has_user_permission

# Checks all three tiers in order
if has_user_permission(user, 'students.edit'):
    ...
```

### Department/School scoping:
All permission checks must respect department scope:
```python
# Wrong: checking permission without scope
if user.has_perm('university.view_student'):
    ...

# Right: check scope via StaffRoleAssignment
assignment = StaffRoleAssignment.objects.filter(
    user=user, is_active=True,
    department=student.department  # scope to their department
).exists()
```

---

## 11. Go-Live Readiness System

The system has a full deployment readiness board at `/system-control/`.
Sequence defined in `golive_services.GO_LIVE_SEQUENCE`:

- Categories: `SECURITY`, `ROLE_PERMISSIONS`, `DATA_PROTECTION`, `FINANCE_PAYMENTS`, `BACKUP`, `MONITORING`, `PERFORMANCE`, `UAT`, `INSTITUTIONAL_APPROVAL`
- Each has `GoLiveReadiness` (PASS/WARN/FAIL/IN_PROGRESS) + `GoLiveIssue` blockers
- **FAIL on any Critical/High issue blocks deployment**

---

## 12. Module Management System

The system supports a full feature-toggle registry (`module_services.py`):

```python
from university.module_services import is_module_enabled

# Always guard optional features with module check
if is_module_enabled('hostel'):
    # show hostel UI
```

Cache key: `ums_system_module_registry_v1` (1 hour TTL, invalidated on edit)

---

## 13. Backup & Disaster Recovery

`backup_services.py` handles:
- Atomic SQLite snapshots
- SHA-256 verification of backup archives  
- Media packaging into tar/zip
- Sanitized config dumps (no secrets)
- Multi-tier retention pruning
- Integration with maintenance lockdown (`start_maintenance` command)

```bash
# Trigger a manual backup via management command
.venv/bin/python manage.py run_background_jobs --job=backup
```
