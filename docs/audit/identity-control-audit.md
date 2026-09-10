# Identity and system control audit

Inventory captured before implementation changes on 2026-09-10.

| Module → feature | Routes / backend | Models / data owner | Roles and workflow |
|---|---|---|---|
| Identity → accounts, password, activation, sessions | accounts/; system-admin/users/; accounts.identity; identity_services | accounts.User (identity), UserAccount (lifecycle envelope), StudentProfile/FacultyProfile (domain profiles), PasswordResetToken/History | All three base roles; central provisioning → account state → authentication → session |
| RBAC → roles, assignment, overrides, inspector | manage/setups/permissions/; system-admin/users/roles redirects; permissions_services | SystemPermission, StaffRole, StaffRoleAssignment, UserPermissionOverride | ADMIN default, FACULTY defaults, explicit roles and grant/deny; control privileges require explicit grants |
| Module registry → modules/submodules/features/dependencies | manage/system/modules/ and system-admin/modules/ share module_views; module_services | SystemModule, SystemSubmodule, SystemFeature, ModuleDependency | Registry owner; availability enforced via module and control middleware |
| System control → maintenance/lockdown/read-only/schedule/status | system-control/; control_services.evaluate/tick | SystemRestriction, ControlHeartbeat | Explicit control permissions; scheduled incidents overlay registry status |
| Audit → event ledger/filter/detail/export | manage/audit-trails/; audit_services | AuditLog | Central audit owner; account LoginRecord is session/attempt telemetry, not a competing audit ledger |
| Recycle Bin → archive/restore/purge/bulk | manage/recycle-bin/; recycle_bin_services | RecycleBinItem | Central deletion snapshot owner; historical academic and financial records require entity-aware restore |
| Messaging → targeting/scheduling/delivery | control message routes; control_services | Notice (canonical message), MessageDelivery, ControlNotification (incident idempotency), MessageTemplate | Targeted users/base roles/staff roles/departments/programmes; publish → deliver → read/archive |

## Confirmed conflicts and proposed consolidation

1. **Duplicate permission rules:** backend `has_user_permission` and inspector `get_user_effective_permissions` independently implement defaults. Inspector treats `is_staff` as an operational administrator while backend does not. Keep backend precedence, share one decision function.
2. **Duplicate authorization wrappers:** permissions, module, audit and recycle views each implement `_admin_required`; these bypass central explicit denies and exclude explicitly granted non-admin users. Replace wrappers with central granular permission decorator, keeping URLs and data.
3. **Duplicate module routing:** control `route_scope` understands feature routes and module prefixes; module middleware's independent resolver uses only submodule prefixes and reads `resolver_match` before Django resolves requests. Control middleware delegates underlying module denial to this incomplete layer, permitting disabled features. Use central scope classifier for middleware; check all matched ancestors/features.
4. Identity role labels/UserType/groups are related classifications, not duplicate credential tables. UserGroup is organizational grouping; StaffRole is authorization. Preserve profiles/envelopes and historical login telemetry.
5. System restrictions are incident policy overlays, legitimately separate from permanent module enablement. Both must consume the one registry and cannot bypass its disabled status through incident bypass permissions.

No destructive changes or production database mutations are proposed. No tables need merging for these fixes. Existing records, redirects and public route names remain preserved.
