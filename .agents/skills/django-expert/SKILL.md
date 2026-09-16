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
├── accounts/       → Custom user model, profiles, identity & auth services
├── university/     → Core domain: courses, departments, programs, enrollments
├── cms/            → CMS pages, public-facing content management
├── config/         → Settings, URLs, CSP, middleware
├── templates/      → HTML templates (base_public.html, base_dashboard.html)
└── static/         → CSS, JS, images
```

**Python:** `.venv/bin/python`  
**Django management:** `.venv/bin/python manage.py <command>`  
**DB:** SQLite (dev) — `db.sqlite3`

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
