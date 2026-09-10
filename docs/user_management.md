# User Management & Identity Administration

This is the single source of identity for the whole system. Every account —
student, staff or administrator — is one row in `accounts.User` plus one
`UserAccount` envelope in `university.identity_models`. Nothing else in the
codebase should create a `User` directly; it should go through
`university.identity_services`.

Console: **System Admin → User Management**, `/system-admin/users/`.

## Why a central service

Before this module, student, staff and bulk-import code paths each created
`User` rows and set passwords independently, which meant several different
places had to be trusted to get it right. `identity_services.py` is now the
one place that:

- generates usernames from a configurable pattern, with collision handling
- generates or validates passwords against the configured policy
- issues single-use, expiring reset/activation tokens instead of shipping a
  password by email
- creates the `UserAccount` envelope, institutional email and audit entry
  together, so an account is never half-provisioned
- enforces account status (`Active`/`Inactive`/`Pending`/`Suspended`/
  `Locked`/`Disabled`/`Expired`/`Archived`) at the authentication layer,
  not just in the UI

Every module that used to mint its own accounts now calls into it:

| Caller | Function |
|---|---|
| Admissions matriculation (`admissions_services.matriculate_applicant`) | `provision_student_account` via `create_user_account` |
| Staff/faculty creation | `provision_staff_account` |
| Student bulk import (`student_io.execute_student_import`) | `create_user_account` + `provision_student_account` |
| Faculty bulk import (`faculty_io.execute_faculty_import`) | `create_user_account` + `provision_staff_account` |
| Admin "Add Student"/"Add Faculty" forms (`forms.py`) | `create_user_account` / `record_password_change` |
| Recycle Bin restore (`recycle_bin_services.restore_from_recycle_bin`) | `ensure_account` (restores with an unusable password, status `Pending`) |

None of these paths sets a hard-coded or predictable password anymore. A
password field left blank always means "send an activation link," never
"fall back to a default."

## Key modules

- `university/identity_models.py` — `UserAccount`, `UserGroup`,
  `UserGroupMembership`, `PasswordHistoryEntry`, `PasswordResetToken`,
  `LoginRecord`, `InstitutionalEmail`, `UserImportBatch`.
- `university/identity_services.py` — the service layer described above:
  username/password generation, policy, tokens, sessions, account status,
  groups, provisioning, search, bulk import/export helpers.
- `university/identity_views.py` — the admin console (dashboard, directory,
  create/edit, password consoles, status board, groups, email accounts,
  login security/history, activity, bulk operations, settings). Every view
  is gated by `permission_required("users.*")` from `university/decorators.py`;
  the sidebar is a convenience layer, not the access control.
- `university/identity_io.py` — CSV/Excel/PDF export and import-template
  generation for the user directory. Exports never include passwords, reset
  tokens or provider secrets.
- `accounts/identity.py` — the authentication backend
  (`IdentityModelBackend`) that refuses to authenticate a blocked account,
  the login-ledger signal receivers, and `PasswordChangeRequiredMiddleware`.

## Security invariants

These are enforced by `university/tests_identity.py` (46 tests) and must
keep passing:

- No plaintext or predictable passwords anywhere (`demo1234`, `password123`,
  a fabricated default, etc.) — passwords are either administrator-supplied
  and policy-validated, cryptographically generated, or replaced by an
  activation link.
- Passwords are one-way hashed; no view or export ever renders one.
- Reset/activation tokens are stored as hashes, are single-use, expire, and
  are invalidated once a new one is issued for the same user.
- A blocked account (`Inactive`/`Suspended`/`Disabled`/`Expired`/
  `Archived`/`Pending`) cannot authenticate even with the correct password —
  this is enforced in `IdentityModelBackend`, not just hidden in the UI.
- Every console view and every `user_action` POST is checked against
  `has_user_permission` server-side; removing a permission blocks the route,
  not just the button.
- Deleting a user means archiving it via the Recycle Bin, never a hard
  delete — historical marks, results, documents and audit entries stay
  attributable.
- Provider secrets (SMTP/API passwords) are write-only in Settings: the
  stored value is never rendered, and a blank submission keeps it unchanged.

## Running the acceptance tests

```bash
python manage.py test university.tests_identity
```

## Seeding reference data

The module depends on the existing settings/permissions/module-catalog
seeders, which are idempotent and safe to re-run:

```bash
python manage.py shell -c "
from university.module_services import seed_system_modules
from university.permissions_services import seed_default_permissions_and_roles, seed_default_user_groups
from university.settings_services import seed_default_settings
seed_system_modules()
seed_default_settings()
seed_default_permissions_and_roles()
seed_default_user_groups()
"
```
