---
trigger: always_on
---

# Git Hygiene Rules — UMS Project

## Remote
- Repository: https://github.com/Dev-Ouma/ums.git
- Default branch: `main`

## Commit Rules

1. **Commit message format:** `<type>(<scope>): <summary>`
   Types: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `data`, `security`
   Example: `feat(courses): add seed command for Wigot hospitality courses`

2. **Atomic commits** — One logical change per commit. Don't mix feature
   code with unrelated fixes in the same commit.

3. **Never commit secrets** — Run `git diff --cached | grep -iE "(password|secret|key|token)"`
   before every push.

4. **Include `.gitignore` exclusions** — `db.sqlite3`, `.venv/`, `*.pyc`,
   `.env`, `.secret_key` must never be committed.

## Branch and Push Rules

5. **No direct push to `main`** without review for major changes.

6. **No force-push to `main`** — Requires explicit team approval.
   Use `git push --force-with-lease` if force is unavoidable.

7. **Run Django checks before pushing:**
   ```bash
   .venv/bin/python manage.py check
   .venv/bin/python manage.py migrate --check
   ```

## History Modification

8. **Use `.mailmap` for contributor attribution** — Never rewrite history just
   to fix a display name. Use `.mailmap` first.

9. **History rewrites require explicit approval** — Never use `git filter-repo`
   or interactive rebase on shared branches without team sign-off.
