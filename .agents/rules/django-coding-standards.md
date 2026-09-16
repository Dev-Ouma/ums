---
trigger: always_on
---

# Django Coding Standards — UMS Project

## Non-Negotiable Rules

1. **Service layer for all mutations** — Views call services, services call models.
   No business logic in views, templates, or signals.

2. **Two-layer permission defence** — Every protected view must have BOTH:
   - `@login_required` (or `LoginRequiredMixin`)
   - `@permission_required("app.codename")` (or `PermissionRequiredMixin`)

3. **`@transaction.atomic` on all multi-step writes** — Any service method that
   touches more than one model must be wrapped in a transaction.

4. **No direct `.get()` in views** — Always use `get_object_or_404()`.

5. **No `SELECT *` anti-patterns** — Use `select_related()` and
   `prefetch_related()` for any view that renders related objects in a loop.

6. **Management commands for data seeding** — Never seed data via a standalone
   script. Use `university/management/commands/`.

7. **Python executable** — Always use `.venv/bin/python`, never `python` directly.

8. **Migrations** — Run `.venv/bin/python manage.py makemigrations` after any
   model change. Never hand-edit migration files.

9. **Templates** — Public pages extend `base_public.html`.
   Authenticated portals extend `base_dashboard.html`.

10. **No placeholder data in client pages** — Any page visible to Wigot School
    of Hospitality must use real contact/course data, not `hello@example.com`.
