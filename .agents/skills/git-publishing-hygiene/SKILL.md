---
name: git-publishing-hygiene
description: >-
  Git workflow hygiene skill for the UMS project. Covers commit message
  standards, branch strategy, .mailmap contributor attribution, safe history
  cleanup, and force-push governance. Activate when working on git history,
  contributor cleanup, release branching, or any destructive git operation.
---

# Git Publishing Hygiene Skill — UMS Project

## Core Observation (from log #5)
> Contributor cleanup should start with attribution mapping and verified
> shortlog output before considering destructive history rewrites.

---

## 1. Commit Message Standards

### Format
```
<type>(<scope>): <imperative summary under 72 chars>

[Optional body — explain the WHY, not the WHAT]
[Blank line before body]

[Optional footer: Closes #123, Breaking Change: ...]
```

### Types
| Type | Use for |
|---|---|
| `feat` | New feature or user-facing capability |
| `fix` | Bug fix |
| `chore` | Build, CI, dependencies, tooling |
| `refactor` | Code restructure without behavior change |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `style` | Formatting, whitespace (no logic change) |
| `data` | Data seeding, migrations, fixtures |
| `security` | Security-related changes |

### Examples
```bash
# ✓ Good
feat(courses): seed Wigot School of Hospitality courses from newsletter PDF
fix(csp): allow openstreetmap.org in frame-src for embedded maps
security(csp): replace unsafe-inline with nonce-based script policy
data(courses): add management command to seed 14 hospitality courses

# ✗ Bad
fixed stuff
update
wip
```

---

## 2. Branch Strategy

```
main          ← Production-ready. Protected. No direct pushes.
feature/*     ← New features: feature/student-portal-map
fix/*         ← Bug fixes: fix/csp-iframe-blocked
data/*        ← Data/migration work: data/seed-wigot-courses
chore/*       ← Tooling/deps: chore/update-leaflet
```

### Branch Rules
- **Never push directly to `main`** without review
- **Always rebase** feature branches before merging: `git rebase main`
- **Squash fixup commits** before pushing: `git rebase -i HEAD~N`
- **Delete branches** after merging: `git branch -d feature/branch-name`

---

## 3. Contributor Attribution with `.mailmap`

### When to use `.mailmap` (NON-DESTRUCTIVE)
Use when a contributor appears multiple times in `git shortlog` due to:
- Using different machines with different git configs
- Changing email addresses over time
- Typos in name/email

### Check current contributor list:
```bash
git shortlog -sne --all
```

### Create `.mailmap` in project root:
```
# Format: Canonical Name <canonical@email.com> <commit-email@other.com>
Dev Ouma <dev@example.com> <dev.ouma@gmail.com>
Dev Ouma <dev@example.com> <douma@stackgee.com>
```

### Verify the mapping worked:
```bash
git shortlog -sne --all  # Should now show consolidated names
git log --format="%aN <%aE>" | sort -u  # List all unique author identities
```

### ⚠️ Important: `.mailmap` limits
- Works locally and in `git log`, `git shortlog`, `git blame`
- **GitHub does NOT use `.mailmap`** for contribution graphs
- To fix GitHub attribution → requires history rewrite (see section 4)

---

## 4. History Rewrite — Only with Explicit Approval

### STOP — ask before proceeding if:
- The branch has been pushed to a shared remote
- Other team members have cloned/branched off it
- It's the `main` branch

### If approved — use `git filter-repo` (NOT `git filter-branch`):
```bash
# Install
pip install git-filter-repo

# Rewrite author email across all history
git filter-repo --email-callback '
    return email if email != b"old@email.com" else b"new@email.com"
'

# After rewrite — force push (ONLY with team buy-in)
git push origin main --force-with-lease  # Safer than --force
```

### After force push — notify all contributors:
```bash
# Every team member must do:
git fetch origin
git reset --hard origin/main
```

---

## 5. Pre-Push Checklist

Before every `git push`:
```bash
# 1. Review what you're pushing
git log origin/main..HEAD --oneline

# 2. Check for secrets accidentally staged
git diff --cached | grep -iE "(password|secret|api_key|token)"

# 3. Verify Django still runs
.venv/bin/python manage.py check

# 4. Ensure no migration conflicts
.venv/bin/python manage.py migrate --check
```

---

## 6. Current Remote Reference

```bash
git remote -v
# origin  https://github.com/Dev-Ouma/ums.git (fetch)
# origin  https://github.com/Dev-Ouma/ums.git (push)

# Default branch: main
```

### Emergency: Undo last push
```bash
# Revert the last commit (safe — creates a new commit)
git revert HEAD
git push origin main

# Reset last local commit (before pushing only)
git reset --soft HEAD~1  # Keep changes staged
git reset --hard HEAD~1  # Discard changes (IRREVERSIBLE)
```
