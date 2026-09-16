---
name: ums-ui-patterns
description: >-
  UI, CSS design system, pagination, server-side filtering, page-sizer, sorting,
  caching (Redis/LocMem), rate-limiting, and ORM query optimisation patterns for
  the UMS project. Activate when building or fixing any list view, filter, table,
  pagination widget, cache key, or rate-limited endpoint.
---

# UMS UI & Performance Patterns Skill

---

## 1. Design System — CSS Tokens

The system has **two CSS files** with distinct purposes:

| File | Scope | Primary color |
|---|---|---|
| `static/css/ums.css` | Authenticated dashboard | `#6C5CE7` (violet) |
| `static/css/public.css` | Public/unauthenticated pages | `#8a1a34` (maroon) |

### Dashboard CSS Variables (`ums.css`):
```css
:root {
  --primary:       #6C5CE7;   /* violet — main brand */
  --primary-dark:  #4834d4;   /* hover state */
  --accent:        #e84393;   /* pink — highlights, badges */
  --sidebar:       linear-gradient(180deg, #2d2a4a 0%, #3b3564 100%);
  --surface:       #f4f5fb;   /* page background */
  --gradient:      linear-gradient(135deg, #6C5CE7, #8f7bff);
  --card-radius:   18px;
  --shadow:        0 10px 30px rgba(26,26,64,.08);
  --shadow-sm:     0 4px 14px rgba(26,26,64,.06);
  --app-font:      'Quicksand', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}
```

### Public CSS Variables (`public.css`):
```css
body.public-theme {
  --primary:      #8a1a34;   /* maroon */
  --primary-dark: #6d1327;
  --maroon:       #8a1a34;
  --maroon-dark:  #6d1327;
}
```

### Typography:
- **Quicksand** is the ONLY application font — dashboard AND PDFs
- Applied via universal selector: `* { font-family: var(--app-font) }`
- Also embedded in PDFs via `document_design.document_fonts()`
- Font files live at: `static/fonts/quicksand/Quicksand.ttf` etc.

### Key Utility Classes (from `ums.css`):
```css
.btn-primary       /* uses --primary */
.btn-accent        /* uses --accent (#e84393) */
.badge-soft        /* rgba(108,92,231,.12) background, primary text */
.navbar-glass      /* backdrop-filter blur, glassmorphism nav */
.brand-gradient    /* animated gradient text: violet → pink → green */
.text-primary      /* --primary color */
```

---

## 2. Server-Side Pagination Pattern

The project uses **Django's built-in `Paginator`** — no third-party packages.

### Standard Allowed Page Sizes:
```python
ALLOWED_STUDENT_PAGE_SIZES  = [10, 25, 50, 100]
ALLOWED_FACULTY_PAGE_SIZES  = [10, 25, 50, 100]
ALLOWED_PROGRAM_PAGE_SIZES  = [10, 25, 50, 100]
ALLOWED_COURSE_PAGE_SIZES   = [10, 25, 50, 100]
ALLOWED_FEE_PAGE_SIZES      = [10, 25, 50, 100]
# All lists also support the special value "all"
```

### Canonical Pagination View Pattern:
```python
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

ALLOWED_SIZES = [10, 25, 50, 100]

def my_list_view(request):
    qs = MyModel.objects.select_related("fk").order_by("field")
    total_count = qs.count()

    # 1. Resolve page size — validate against allowlist, default 25
    per_page_param = request.GET.get("per_page", "25").strip().lower()
    if per_page_param == "all":
        per_page = max(total_count, 1)
        selected_per_page = "all"
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in ALLOWED_SIZES:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        selected_per_page = str(per_page)

    # 2. Paginate
    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # 3. Elided page range (shows ... for large page counts)
    page_range = (
        paginator.get_elided_page_range(number=page_obj.number, on_each_side=1, on_ends=1)
        if paginator.num_pages > 1
        else []
    )

    return render(request, "template.html", {
        "objects": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "page_range": page_range,
        "per_page": selected_per_page,
        "total_count": total_count,
        # also pass filter state so pagination links preserve filters
        "q": q,
        "sort_by": sort_by,
        "order": order,
    })
```

### Template Pagination Widget (Bootstrap 5):
```html
{% if paginator.num_pages > 1 %}
<nav aria-label="Page navigation">
  <ul class="pagination justify-content-center">
    {% if page_obj.has_previous %}
      <li class="page-item">
        <a class="page-link" href="?page={{ page_obj.previous_page_number }}&per_page={{ per_page }}&q={{ q }}&sort={{ sort_by }}&order={{ order }}">‹</a>
      </li>
    {% endif %}
    {% for num in page_range %}
      {% if num == page_obj.ELLIPSIS %}
        <li class="page-item disabled"><span class="page-link">…</span></li>
      {% else %}
        <li class="page-item {% if num == page_obj.number %}active{% endif %}">
          <a class="page-link" href="?page={{ num }}&per_page={{ per_page }}&q={{ q }}&sort={{ sort_by }}&order={{ order }}">{{ num }}</a>
        </li>
      {% endif %}
    {% endfor %}
    {% if page_obj.has_next %}
      <li class="page-item">
        <a class="page-link" href="?page={{ page_obj.next_page_number }}&per_page={{ per_page }}&q={{ q }}&sort={{ sort_by }}&order={{ order }}">›</a>
      </li>
    {% endif %}
  </ul>
</nav>
{% endif %}
```

### ⚠️ CRITICAL: Always preserve filter params in pagination links
Pagination links **must** carry `q`, `sort`, `order`, `per_page`, and any active filter params.
Failure to do so resets the filter on page change.

---

## 3. Server-Side Filter & Sort Pattern

### Canonical Filter Helper Function:
```python
def _get_filtered_students_queryset(request):
    """Common filtering and sorting logic shared by list, export, and preview views."""
    q = request.GET.get("q", "").strip()
    qs = StudentProfile.objects.select_related("user", "program")

    # Text search (multi-field Q filter)
    if q:
        qs = qs.filter(
            Q(roll_no__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__email__icontains=q) |
            Q(program__code__icontains=q) |
            Q(program__name__icontains=q)
        )

    # Validated sort — allowlist prevents ORM injection
    sort_by = request.GET.get("sort", "").strip()
    order = request.GET.get("order", "asc").strip().lower()
    SORT_MAP = {
        "roll_no":  "roll_no",
        "name":     "user__first_name",
        "program":  "program__code",
        "semester": "current_semester",
        "email":    "user__email",
    }
    if sort_by in SORT_MAP:
        prefix = "-" if order == "desc" else ""
        qs = qs.order_by(f"{prefix}{SORT_MAP[sort_by]}", "id")
    else:
        qs = qs.order_by("roll_no")

    return qs, q, sort_by, order
```

### Rules for Filters:
- **Always allowlist sort fields** — never pass `request.GET["sort"]` directly to `.order_by()`
- **Share the filter function** across list, export, and CSV preview views
- **Chain filters additively** — each active filter narrows the queryset
- **Faculty list** also accepts `dept_id` filter: `Q(department_id=dept_id)`

---

## 4. Cache Architecture

### Development (LocMem):
```python
# config/settings.py (auto-set when DEBUG=True)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ums-development",
    }
}
```

### Production (Redis — required):
```python
# Set via environment variables:
# DJANGO_CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
# DJANGO_CACHE_LOCATION=redis://redis-host:6379/1

CACHES = {
    "default": {
        "BACKEND": os.environ["DJANGO_CACHE_BACKEND"],
        "LOCATION": os.environ["DJANGO_CACHE_LOCATION"],
    }
}
# Raises RuntimeError if either env var is missing in production
```

### Named Cache Keys in Production:
| Key pattern | Purpose | TTL |
|---|---|---|
| `ums:rate:<scope>:<digest>` | Rate-limit counters per scope+IP | Per `window_seconds` |
| `ums_system_module_registry_v1` | System module registry | 3600s (1 hour) |
| `ums:security:*` | Security posture checks | Varies |

### Cache Invalidation:
```python
from university.module_services import invalidate_module_cache

# Call after any SystemModule / SystemFeature save
invalidate_module_cache()  # Deletes 'ums_system_module_registry_v1'
```

### Sensitive pages must disable caching:
```python
from django.views.decorators.cache import never_cache

@never_cache  # transcripts, control panel, documents
def transcript_view(request, pk):
    ...

# Or in middleware — system control panel adds:
response['Cache-Control'] = 'no-store, private'
```

---

## 5. Rate Limiting (`security_decorators.rate_limit`)

### Usage:
```python
from university.security_decorators import rate_limit

@rate_limit("my-endpoint", limit=10, window_seconds=60)
def my_view(request):
    ...

# For GET endpoints (default is POST-only):
@rate_limit("status-query", limit=30, window_seconds=60, methods=("GET",))
def status_view(request):
    ...
```

### How it works:
- Uses `django.core.cache` → **requires Redis in production** for shared state across processes
- Key format: `ums:rate:<scope>:sha256(<scope>:<REMOTE_ADDR>)[:32]`
- Respects `SECURITY_RATE_LIMIT_ENABLED` setting (defaults `True` when `DEBUG=False`)
- On cache backend failure: **fails closed** (returns HTTP 503) — never fails open
- On rate exceeded: returns HTTP 429 with `Retry-After` header

### Existing Rate-Limited Endpoints:
| Scope | Limit | Window | Method |
|---|---|---|---|
| `applicant-register` | 10 | 300s | POST |
| `applicant-verify-otp` | 10 | 300s | POST |
| `applicant-login` | 10 | 300s | POST |
| `app-submit-api` | 10 | 60s | POST |
| `admissions-apply` | 30 | 60s | POST |
| `app-doc-upload` | 30 | 60s | POST |
| `app-draft-save` | 60 | 60s | POST |
| `app-fee-pay` | 15 | 60s | POST |
| `app-status-query` | 30 | 60s | GET |

### ⚠️ Redis Required for Rate Limiting
LocMemCache rate limits are **process-local** — they don't work across Gunicorn workers.
In production, always point `DJANGO_CACHE_BACKEND` at Redis.

---

## 6. ORM Query Optimisation Rules

### N+1 Prevention — always use eager loading on list views:
```python
# Students list
StudentProfile.objects.select_related("user", "program").order_by("roll_no")

# Faculty list
FacultyProfile.objects.select_related("user", "department").order_by("user__last_name")

# Enrollments with course info
Enrollment.objects.select_related("course", "course__program", "student__user")

# Reverse FKs (e.g., courses with their exam count)
Course.objects.prefetch_related("exam_set").select_related("department")
```

### Aggregate queries — use `.count()` and `.annotate()` not `len()`:
```python
# ✗ Bad — loads all rows into memory
total = len(students_qs)

# ✓ Good — single COUNT(*) at DB level
total = students_qs.count()

# ✓ Good — annotate for dashboard stats
from django.db.models import Count, Sum, Avg
Course.objects.annotate(enrolled=Count("enrollment")).filter(enrolled__gt=0)
```

### Avoid re-evaluating querysets:
```python
# ✗ Bad — hits DB twice
count = qs.count()
page_obj = paginator.page(...)  # another query

# ✓ Good — let Paginator slice (one query per page)
# Call .count() once for display, then paginate the original qs
total_count = qs.count()
paginator = Paginator(qs, per_page)  # slices lazily
```

### `select_for_update()` for concurrent mutations:
```python
# Financial records that multiple processes might touch
with transaction.atomic():
    account = FeeAccount.objects.select_for_update().get(pk=pk)
    account.balance -= amount
    account.save()
```

---

## 7. Pre-Deployment Performance Checklist

- [ ] All list views use `select_related()` / `prefetch_related()`
- [ ] Pagination implemented with `ALLOWED_*_PAGE_SIZES` allowlist
- [ ] Sort fields validated against an allowlist map — no raw GET→order_by
- [ ] Filter state preserved in pagination links (`q`, `sort`, `order`, `per_page`)
- [ ] Redis configured in production (`DJANGO_CACHE_BACKEND` + `DJANGO_CACHE_LOCATION`)
- [ ] Sensitive pages decorated with `@never_cache` or `Cache-Control: no-store`
- [ ] Rate limiting on all public-facing POST endpoints (`@rate_limit`)
- [ ] `Cache-Control: no-store` on all financial and academic document responses
- [ ] Module registry invalidated after any module/feature save
