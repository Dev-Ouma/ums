"""
Granular Roles, Permissions & User-Level Overrides Engine for University Management System.
Supports role definitions, permission catalogs, and explicit user-level Grant/Deny overrides.
"""

from django.db import transaction
from django.db.models import Q
from university.models import (
    Department,
    School,
    SystemPermission,
    StaffRole,
    StaffRoleAssignment,
    UserPermissionOverride,
    AuditLog,
)
from accounts.models import User, Role
from university.audit_services import log_activity


# ==============================================================================
# 1. PERMISSION CATALOG DEFINITIONS
# ==============================================================================

DEFAULT_PERMISSIONS = [
    # --- Student Records ---
    ("students.view_all", "View Student Nominal Registers", "Students", "Access student directory, profiles, and cohort rolls."),
    ("students.create", "Admit & Register Students", "Students", "Create new student profiles and assign admission numbers."),
    ("students.edit", "Modify Student Details", "Students", "Edit biodata, address, program enrollment, and academic year."),
    ("students.delete", "Deactivate / Archive Students", "Students", "Soft-delete or suspend student records."),
    ("students.clearance", "Student Clearance Authority", "Students", "Grant graduation and exit clearances."),

    # --- Academic Curriculum & Programs ---
    ("academics.view_curriculum", "View Curriculum & Courses", "Academics", "Inspect degree programs, course units, and credit requirements."),
    ("academics.manage_programs", "Manage Programs & Degrees", "Academics", "Create or edit academic programs, seat capacities, and departments."),
    ("academics.manage_courses", "Manage Course Units", "Academics", "Create, edit, or assign lecturers to course units."),
    ("academics.manage_terms", "Manage Academic Terms & Sessions", "Academics", "Open, close, or configure semester academic terms."),
    ("academics.unit_registration", "Approve Unit Registrations", "Academics", "Authorize and approve student semester course registrations."),
    ("academics.manage_requests", "Manage Student Academic Requests", "Academics", "Review deferment, withdrawal, leave, and return-to-study requests."),
    ("academics.manage_transfers", "Manage Programme Transfers", "Academics", "Review and decide student programme transfer applications."),
    ("academics.manage_attachments", "Manage Industrial Attachments", "Academics", "Approve placements, assign supervisors, and oversee attachment completion."),
    ("academics.manage_evaluations", "Manage Course Evaluations", "Academics", "Manage evaluation windows and institution-level teaching evaluation analytics."),
    ("academics.manage_graduation", "Manage Graduation & Clearance", "Academics", "Manage graduation applications, departmental clearance, ceremonies, and conferment."),

    # --- Examinations & Senate ---
    ("exams.create_exam", "Create & Schedule Examinations", "Examinations", "Create examination sessions, supplementary exams, and schedules (HOD, Dean, and Admin only)."),
    ("exams.view_marks", "View Examination Marks & Grades", "Examinations", "Access CAT and final examination marks and grade summaries."),
    ("exams.enter_cat", "Enter CAT / Coursework Marks", "Examinations", "Submit continuous assessment marks (30%) for assigned units."),
    ("exams.enter_exam", "Enter Final Examination Marks", "Examinations", "Submit final written exam marks (70%)."),
    ("exams.moderate_marks", "Moderate & Edit Marks", "Examinations", "Departmental marks moderation, adjustments, and review."),
    ("exams.approve_senate", "Approve Senate Results", "Examinations", "Official Board of Examiners & Senate pass/fail confirmation."),
    ("exams.publish_results", "Publish & Release Transcripts", "Examinations", "Release verified grades to student portals and issue transcripts."),

    # --- Finance & Fee Accounts ---
    ("finance.view_invoices", "View Student Fee Accounts", "Finance", "Inspect fee structures, invoices, and student account statements."),
    ("finance.create_invoices", "Issue Fee Invoices", "Finance", "Post semester tuition, exam, and accommodation invoices."),
    ("finance.record_payments", "Record Fee Receipts & Payments", "Finance", "Post bank transfers, receipts, and reconcile student ledgers."),
    ("finance.financial_clearance", "Grant Financial Clearance", "Finance", "Approve student fee clearance for examinations and graduation."),
    ("finance.manage_fee_accounts", "Manage Payment Accounts & Gateways", "Finance", "Configure M-Pesa Paybill, Till, Card Gateways, and Bank channels."),
    ("finance.reconcile_payments", "Reconcile Provider Transactions", "Finance", "Cross-check provider statement batches against internal ledger."),
    ("finance.manage_refunds", "Reverse & Refund Payments", "Finance", "Authorize and process fee refunds, reversals, and adjustments."),

    # --- Hostels & Accommodation ---
    ("hostels.view_allocation", "View Hostel Allocations", "Accommodation", "View hostel blocks, room occupancy, and resident nominal lists."),
    ("hostels.allocate_room", "Allocate & Reassign Rooms", "Accommodation", "Assign students to hostel blocks, rooms, and beds."),
    ("hostels.clear_student", "Hostel Exit Clearance", "Accommodation", "Authorize room check-out and hostel clearance."),

    # --- Library & Repository ---
    ("library.view", "View Library Operations", "Library", "View the catalog, circulation ledger, past papers, and borrower status."),
    ("library.circulate", "Issue & Return Books", "Library", "Issue catalog copies, process returns, and assess overdue fines."),
    ("library.manage_catalog", "Manage Library Catalog", "Library", "Register books, copies, call numbers, shelves, and digital past papers."),

    # --- Reports & Analytics ---
    ("reports.view_catalog", "Access Reports Catalog", "Reports", "Browse standard university report catalogues and web previews."),
    ("reports.generate_official", "Generate Official Reports", "Reports", "Run full institution-wide operational and demographic reports."),
    ("reports.senate_marksheet", "Generate Senate Consolidated Marksheets", "Reports", "Compile and export official Board of Examiners master sheets."),
    ("reports.export_files", "Export Reports (PDF/Excel/CSV)", "Reports", "Download printable PDF, Excel, and CSV datasets."),

    ("admin.manage_settings", "Manage System Settings & Setups", "Administration", "Configure university branding, academic rules, and system toggles."),
    ("admin.view_audit_logs", "Inspect Audit Trail Ledgers", "Administration", "Forensic analysis of user activity, state changes, and logins."),
    ("admin.manage_recycle_bin", "Manage Recycle Bin & Restore", "Administration", "Inspect and restore soft-deleted university records."),
    ("admin.manage_roles_permissions", "Manage Roles & Permissions", "Administration", "Configure staff roles, assign permissions, and set user overrides."),

    # --- System Backups & Disaster Recovery ---
    ("backups.view", "View Backups & Storage Dashboard", "Backups", "Access backup dashboard, history, schedules, and health metrics."),
    ("backups.create", "Create & Run On-Demand Backups", "Backups", "Execute manual database, files, or full system backups."),
    ("backups.manage_schedules", "Manage Automated Backup Schedules", "Backups", "Create, edit, pause, and delete recurring backup schedules."),
    ("backups.download", "Download Backup Archives", "Backups", "Download encrypted or compressed backup archives."),
    ("backups.verify", "Verify Backup Integrity", "Backups", "Run cryptographic and database verification checks on backup archives."),
    ("backups.restore", "Restore System from Backup", "Backups", "Execute full or selective system restorations with elevated safety locks."),
    ("backups.delete", "Delete Backup Archives", "Backups", "Permanently remove backup archives and storage artifacts."),
    ("backups.manage_storage", "Configure Backup Storage Destinations", "Backups", "Configure local, network, and cloud storage targets."),
    ("backups.manage_settings", "Manage Backup Policies & Settings", "Backups", "Configure retention rules, health thresholds, and storage alerts."),

    # --- Go-Live Command Center ---
    ("golive.view", "View Go-Live Readiness Dashboard", "Go-Live", "Access the pre-launch readiness scoreboard and issue ledger."),
    ("golive.manage", "Manage Go-Live Readiness & Issues", "Go-Live", "Update category status/sign-off and create, edit, or close readiness issues."),
]

# System control grants never inherit the ADMIN base-role default.
DEFAULT_PERMISSIONS += [
    (f"control.{group}.{action}", f"{action.title()} {group.title()}", f"System {group.title()}", "Explicit system-control permission.")
    for group, actions in {
        "maintenance": ["view", "create", "edit", "schedule", "activate", "deactivate", "bypass"],
        "lockdown": ["view", "activate", "deactivate", "emergency", "bypass"],
        "messages": ["view", "create", "edit", "publish", "unpublish", "delete"],
        "health": ["view"],
    }.items() for action in actions
]


# ==============================================================================
# 2. DEFAULT SYSTEM ROLES DEFINITION
# ==============================================================================

DEFAULT_ROLES = [
    {
        "name": "Academic Registrar",
        "code": "academic_registrar",
        "color": "#6C5CE7",
        "description": "Executive authority overseeing all student admissions, curriculum, Senate results, transcripts, and clearances.",
        "permissions": [
            "students.view_all", "students.create", "students.edit", "students.clearance",
            "academics.view_curriculum", "academics.manage_programs", "academics.manage_courses", "academics.manage_terms", "academics.unit_registration",
            "academics.manage_requests", "academics.manage_transfers", "academics.manage_attachments", "academics.manage_evaluations", "academics.manage_graduation",
            "exams.create_exam", "exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "exams.publish_results",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
            "admin.view_audit_logs",
        ]
    },
    {
        "name": "Dean of Faculty / School",
        "code": "dean",
        "color": "#00b894",
        "description": "Academic head of school overseeing departmental curriculum, faculty workload, exam results, and Senate approvals.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum", "academics.manage_courses", "academics.unit_registration",
            "academics.manage_attachments", "academics.manage_evaluations", "academics.manage_graduation",
            "exams.create_exam", "exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "exams.publish_results",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
        ]
    },
    {
        "name": "Head of Department (HOD)",
        "code": "hod",
        "color": "#0984e3",
        "description": "Departmental chair managing course offerings, teaching assignments, mark moderation, and unit registrations.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum", "academics.manage_courses", "academics.unit_registration",
            "exams.create_exam", "exams.view_marks", "exams.enter_cat", "exams.enter_exam", "exams.moderate_marks",
            "reports.view_catalog", "reports.generate_official", "reports.export_files",
        ]
    },
    {
        "name": "Examinations Officer",
        "code": "exam_officer",
        "color": "#e84393",
        "description": "Central officer responsible for exam session scheduling, mark verification, Senate sheets, and official transcripts.",
        "permissions": [
            "students.view_all",
            "exams.create_exam", "exams.view_marks", "exams.enter_cat", "exams.enter_exam", "exams.moderate_marks", "exams.publish_results",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
            "admin.view_audit_logs",
        ]
    },
    {
        "name": "Finance & Accounts Officer",
        "code": "finance_officer",
        "color": "#fdcb6e",
        "description": "Bursary officer handling fee structures, invoicing, payment receipts, fee clearances, and financial statements.",
        "permissions": [
            "students.view_all",
            "finance.view_invoices", "finance.create_invoices", "finance.record_payments", "finance.financial_clearance",
            "finance.manage_fee_accounts", "finance.reconcile_payments", "finance.manage_refunds",
            "reports.view_catalog", "reports.generate_official", "reports.export_files",
        ]
    },
    {
        "name": "Hostel Warden / Accommodation Officer",
        "code": "hostel_warden",
        "color": "#e17055",
        "description": "Accommodation administrator managing hall allocations, room check-in/out, and damage clearances.",
        "permissions": [
            "students.view_all",
            "hostels.view_allocation", "hostels.allocate_room", "hostels.clear_student",
            "reports.view_catalog", "reports.export_files",
        ]
    },
    {
        "name": "Librarian / Repository Officer",
        "code": "librarian",
        "color": "#0984e3",
        "description": "Library officer managing catalog records, circulation, fines, and the digital past-paper repository.",
        "permissions": [
            "students.view_all",
            "library.view", "library.circulate", "library.manage_catalog",
            "reports.view_catalog", "reports.export_files",
        ]
    },
    {
        "name": "Lecturer / Teaching Staff",
        "code": "lecturer",
        "color": "#00cec9",
        "description": "Academic staff delivering course lectures, continuous assessment, exam grading, and viewing evaluation analytics.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum",
            "exams.view_marks", "exams.enter_cat", "exams.enter_exam",
            "reports.view_catalog", "reports.export_files",
        ]
    },
    {
        "name": "Admissions & Records Officer",
        "code": "admissions_officer",
        "color": "#a29bfe",
        "description": "Staff in charge of student admissions, enrollment registers, biodata maintenance, and demographic reporting.",
        "permissions": [
            "students.view_all", "students.create", "students.edit",
            "academics.view_curriculum", "academics.unit_registration",
            "reports.view_catalog", "reports.generate_official", "reports.export_files",
        ]
    },
    {
        "name": "System & Compliance Auditor",
        "code": "auditor",
        "color": "#2d3436",
        "description": "Internal auditor with institution-wide read-only visibility, audit trail inspection, and compliance reporting.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum",
            "exams.view_marks",
            "finance.view_invoices",
            "hostels.view_allocation",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
            "admin.view_audit_logs", "admin.manage_recycle_bin",
        ]
    },
    {
        "name": "Emergency Response & Campus Security",
        "code": "emergency_security",
        "color": "#c0392b",
        "description": "Emergency security, health and disaster coordinators authorized for critical operations and lockdown bypass.",
        "permissions": [
            "students.view_all",
            "hostels.view_allocation",
            "admin.view_audit_logs",
            "control.lockdown.view",
            "control.lockdown.bypass",
            "control.maintenance.view",
            "control.maintenance.bypass",
        ]
    },
    {
        "name": "Vice Chancellor",
        "code": "vc",
        "color": "#1e293b",
        "description": "Chief Executive and Academic Head of the University with executive oversight over all governance, academics, and administration.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum", "academics.manage_programs", "academics.manage_courses", "academics.manage_terms", "academics.unit_registration",
            "academics.manage_requests", "academics.manage_transfers", "academics.manage_attachments", "academics.manage_evaluations", "academics.manage_graduation",
            "exams.create_exam", "exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "exams.publish_results",
            "finance.view_invoices",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
            "admin.view_audit_logs",
        ]
    },
    {
        "name": "Deputy Vice Chancellor (Academic Affairs)",
        "code": "dvcaa",
        "color": "#4338ca",
        "description": "Executive oversight of all academic faculties, programs, curriculum development, examination approvals, and Senate operations.",
        "permissions": [
            "students.view_all",
            "academics.view_curriculum", "academics.manage_programs", "academics.manage_courses", "academics.manage_terms", "academics.unit_registration",
            "academics.manage_requests", "academics.manage_transfers", "academics.manage_attachments", "academics.manage_evaluations", "academics.manage_graduation",
            "exams.create_exam", "exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "exams.publish_results",
            "reports.view_catalog", "reports.generate_official", "reports.senate_marksheet", "reports.export_files",
            "admin.view_audit_logs",
        ]
    },
]


# ==============================================================================
# 3. SEEDING & INITIALIZATION
# ==============================================================================

@transaction.atomic
def seed_default_permissions_and_roles():
    """
    Idempotently seeds all system permissions and default staff roles.
    """
    perm_objs = {}
    for code, name, module, desc in DEFAULT_PERMISSIONS:
        obj, _ = SystemPermission.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "module": module,
                "description": desc,
            }
        )
        perm_objs[code] = obj

    for role_data in DEFAULT_ROLES:
        role_obj, _ = StaffRole.objects.update_or_create(
            code=role_data["code"],
            defaults={
                "name": role_data["name"],
                "color": role_data["color"],
                "description": role_data["description"],
                "is_system_role": True,
            }
        )
        assigned_perms = [perm_objs[p] for p in role_data["permissions"] if p in perm_objs]
        role_obj.permissions.set(assigned_perms)

    return len(perm_objs), len(DEFAULT_ROLES)


# ==============================================================================
# 4. PERMISSION RESOLUTION ENGINE
# ==============================================================================

def has_user_permission(user, permission_code):
    """
    Evaluates whether a user has a specific permission taking into account:
    1. Superuser Status -> Always True
    2. User-level Explicit Overrides (GRANT / DENY) -> Highest Specificity Priority
    3. Assigned Active Staff Roles & their permission sets
    4. Base Role Defaults (ADMIN broad access, FACULTY teaching access)
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # Check Explicit User-Level Override
    override = UserPermissionOverride.objects.filter(
        user=user,
        permission__code=permission_code
    ).first()

    if override:
        if override.override_type == UserPermissionOverride.OverrideType.DENY:
            return False
        if override.override_type == UserPermissionOverride.OverrideType.GRANT:
            return True

    # Check Assigned Active Staff Roles
    role_filter = {"user": user, "is_active": True}
    if getattr(user, "_active_role_code", None):
        role_filter["role__code"] = user._active_role_code
    has_role_perm = StaffRoleAssignment.objects.filter(
        **role_filter,
        role__permissions__code=permission_code
    ).exists()

    if has_role_perm:
        return True

    # Check User Groups permissions
    if hasattr(user, 'group_memberships'):
        has_group_perm = user.group_memberships.filter(
            group__roles__permissions__code=permission_code
        ).exists() or user.group_memberships.filter(
            group__permissions__code=permission_code
        ).exists()
        if has_group_perm:
            return True

    # Security controls require explicit grants; ordinary admin labels never bypass.
    if permission_code.startswith("control."):
        return False

    # Check Base System Role Defaults
    user_role = getattr(user, "role", "")
    if getattr(user, "_active_role_code", None):
        # Assigned custom roles are authoritative for this request.
        user_role = "__active_custom_role__"
    if user_role == Role.ADMIN or getattr(user, "is_admin_role", False):
        # Admin has default access to all operational permissions unless explicitly denied
        return True

    if user_role == Role.FACULTY or getattr(user, "is_faculty", False):
        # Default faculty basic permissions
        faculty_defaults = {
            "exams.view_marks", "exams.enter_cat", "exams.enter_exam",
            "academics.view_curriculum", "reports.view_catalog", "reports.export_files"
        }
        if permission_code in faculty_defaults:
            return True

    return False


user_has_permission = has_user_permission


def has_scoped_permission(user, permission_code, department=None, school=None):
    """
    Like has_user_permission(), but honours the department/school scope on a
    StaffRoleAssignment instead of ignoring it.

    StaffRoleAssignment.department/school are optional: an assignment with
    both null is institution-wide (grants everywhere, same as today's
    unscoped has_user_permission behaviour). A department-scoped assignment
    (e.g. "HOD of Computer Science") only grants the permission when checked
    against that department, or a department that belongs to the school a
    school-scoped assignment (e.g. "Dean of Engineering") covers — a Dean
    oversees every department in their school, an HOD does not oversee other
    departments or the school itself.

    Pass neither `department` nor `school` to fall back to institution-wide
    evaluation identical to has_user_permission (useful when the caller
    doesn't yet know the scope, e.g. a generic list view before filtering).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    override = UserPermissionOverride.objects.filter(
        user=user, permission__code=permission_code
    ).first()
    if override:
        if override.override_type == UserPermissionOverride.OverrideType.DENY:
            return False
        if override.override_type == UserPermissionOverride.OverrideType.GRANT:
            return True

    if department is None and school is None:
        return has_user_permission(user, permission_code)

    scope_match = Q(department__isnull=True, school__isnull=True)
    if department is not None:
        scope_match |= Q(department=department)
        if getattr(department, "school_id", None):
            scope_match |= Q(school_id=department.school_id)
    if school is not None:
        scope_match |= Q(school=school)

    role_filter = {"user": user, "is_active": True}
    if getattr(user, "_active_role_code", None):
        role_filter["role__code"] = user._active_role_code
    has_role_perm = StaffRoleAssignment.objects.filter(
        scope_match, **role_filter, role__permissions__code=permission_code
    ).exists()
    if has_role_perm:
        return True

    if permission_code.startswith("control."):
        return False

    # Base role defaults (ADMIN/FACULTY) are institution-wide by definition —
    # they carry no StaffRoleAssignment scope to check, so fall back to the
    # unscoped evaluation for these rather than denying every scoped check.
    user_role = getattr(user, "role", "")
    if not getattr(user, "_active_role_code", None) and (
        user_role == Role.ADMIN or getattr(user, "is_admin_role", False)
    ):
        return True
    if not getattr(user, "_active_role_code", None) and (
        user_role == Role.FACULTY or getattr(user, "is_faculty", False)
    ):
        faculty_defaults = {
            "exams.view_marks", "exams.enter_cat", "exams.enter_exam",
            "academics.view_curriculum", "reports.view_catalog", "reports.export_files"
        }
        if permission_code in faculty_defaults:
            return True

    return False


def get_user_effective_permissions(user):
    """
    Returns a comprehensive dict of all permissions mapped to their active status and origin:
    {
       'students.view_all': {
           'permission': <SystemPermission>,
           'granted': True,
           'source': 'Override (Grant)' | 'Override (Deny)' | 'Role: Academic Registrar' | 'Superuser' | 'Admin Default' | 'Denied'
       }
    }
    """
    if SystemPermission.objects.count() == 0:
        seed_default_permissions_and_roles()

    all_perms = SystemPermission.objects.all().order_by("module", "name")
    
    # Pre-fetch user overrides
    user_overrides = {
        ov.permission_id: ov 
        for ov in UserPermissionOverride.objects.filter(user=user).select_related("permission")
    }

    # Pre-fetch assigned role permissions
    assigned_roles = StaffRole.objects.filter(
        assignments__user=user,
        assignments__is_active=True
    ).prefetch_related("permissions")

    role_perm_map = {}
    for r in assigned_roles:
        for p in r.permissions.all():
            if p.id not in role_perm_map:
                role_perm_map[p.id] = []
            role_perm_map[p.id].append(r.name)

    results = {}
    is_admin = getattr(user, "is_admin_role", False) or user.is_staff or user.role == Role.ADMIN
    is_faculty = getattr(user, "is_faculty", False) or user.role == Role.FACULTY
    faculty_defaults = {
        "exams.view_marks", "exams.enter_cat", "exams.enter_exam",
        "academics.view_curriculum", "reports.view_catalog", "reports.export_files"
    }

    for perm in all_perms:
        granted = False
        source = "No Access"

        if user.is_superuser:
            granted = True
            source = "Superuser (Full System Access)"
        elif perm.id in user_overrides:
            ov = user_overrides[perm.id]
            if ov.override_type == UserPermissionOverride.OverrideType.GRANT:
                granted = True
                source = f"Override: Explicit Grant ({ov.reason or 'Authorized'})"
            else:
                granted = False
                source = f"Override: Explicit Deny ({ov.reason or 'Blocked'})"
        elif perm.id in role_perm_map:
            granted = True
            source = f"Role: {', '.join(role_perm_map[perm.id])}"
        elif is_admin and not perm.code.startswith("control."):
            granted = True
            source = "Administrator Base Access"
        elif is_faculty and perm.code in faculty_defaults:
            granted = True
            source = "Faculty Base Access"

        results[perm.code] = {
            "permission": perm,
            "granted": granted,
            "source": source,
            "has_override": perm.id in user_overrides,
            "override_type": user_overrides[perm.id].override_type if perm.id in user_overrides else None,
            "override_reason": user_overrides[perm.id].reason if perm.id in user_overrides else "",
        }

    return results


# ==============================================================================
# 5. ASSIGNMENT & OVERRIDE MANAGEMENT
# ==============================================================================

@transaction.atomic
def set_user_permission_override(user, permission_code, override_type, reason="", granted_by=None, request=None):
    """
    Creates or updates a user-level permission override and logs to AuditLog.
    """
    perm = SystemPermission.objects.get(code=permission_code)
    override, created = UserPermissionOverride.objects.update_or_create(
        user=user,
        permission=perm,
        defaults={
            "override_type": override_type,
            "reason": reason,
            "granted_by": granted_by,
        }
    )
    from university.identity_services import invalidate_user_sessions
    invalidate_user_sessions(user)

    action_label = "GRANTED" if override_type == UserPermissionOverride.OverrideType.GRANT else "DENIED"
    log_activity(
        request=request,
        user=granted_by or user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.CONFIG,
        entity=f"Permission Override: {user.username}",
        description=f"Explicitly {action_label} permission '{perm.name}' ({perm.code}) for user {user.username}. Reason: {reason or 'N/A'}",
    )
    return override


@transaction.atomic
def remove_user_permission_override(user, permission_code, actor=None, request=None):
    """
    Removes an explicit user override, restoring role-derived permissions.
    """
    perm = SystemPermission.objects.get(code=permission_code)
    deleted, _ = UserPermissionOverride.objects.filter(user=user, permission=perm).delete()
    if deleted:
        from university.identity_services import invalidate_user_sessions
        invalidate_user_sessions(user)
        log_activity(
            request=request,
            user=actor or user,
            action=AuditLog.Action.DELETE,
            module=AuditLog.Module.CONFIG,
            entity=f"Permission Override: {user.username}",
            description=f"Removed permission override '{perm.name}' ({perm.code}) for user {user.username}.",
        )
    return bool(deleted)


@transaction.atomic
def assign_staff_role(user, role_id_or_code, department_id=None, school_id=None, actor=None, request=None):
    """
    Assigns a StaffRole to a user, with an optional Department or School/Faculty
    scope (e.g. HOD of a Department, Dean of a School).
    """
    if isinstance(role_id_or_code, int) or str(role_id_or_code).isdigit():
        role = StaffRole.objects.get(id=int(role_id_or_code))
    else:
        role = StaffRole.objects.get(code=role_id_or_code)

    dept = Department.objects.filter(id=department_id).first() if department_id else None
    school = School.objects.filter(id=school_id).first() if school_id else None

    assignment, created = StaffRoleAssignment.objects.update_or_create(
        user=user,
        role=role,
        department=dept,
        school=school,
        defaults={
            "assigned_by": actor,
            "is_active": True,
        }
    )
    from university.identity_services import invalidate_user_sessions
    invalidate_user_sessions(user)

    scope_str = f" in {dept.code}" if dept else (f" for {school.code}" if school else "")
    log_activity(
        request=request,
        user=actor or user,
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        module=AuditLog.Module.CONFIG,
        entity=f"Staff Role: {user.username}",
        description=f"Assigned role '{role.name}'{scope_str} to staff user {user.username}.",
    )
    return assignment


# ==============================================================================
# 6. USER MANAGEMENT & IDENTITY ADMINISTRATION PERMISSIONS
# ==============================================================================
# Identity work is split into narrow grants so that, for example, a registry
# clerk can reset a student's password without also being able to change roles.

USER_MANAGEMENT_PERMISSIONS = [
    ("users.view", "View User Accounts", "User Management",
     "Browse the user directory, account profiles, and status information."),
    ("users.create", "Create User Accounts", "User Management",
     "Provision new student, staff, and administrator accounts."),
    ("users.edit", "Edit User Accounts", "User Management",
     "Modify identity details, organisation links, and account metadata."),
    ("users.manage_status", "Manage Account Status", "User Management",
     "Activate, deactivate, suspend, disable, unlock, and archive accounts."),
    ("users.reset_password", "Reset User Passwords", "User Management",
     "Send reset links, force password changes, and issue temporary credentials."),
    ("users.generate_credentials", "Generate Usernames & Passwords", "User Management",
     "Run the username and secure password generators."),
    ("users.manage_emails", "Manage Institutional Email Accounts", "User Management",
     "Generate, provision, suspend, and archive institutional email identities."),
    ("users.manage_groups", "Manage User Groups", "User Management",
     "Create user groups and manage group membership and inherited roles."),
    ("users.assign_staff_role", "Assign Staff Roles", "User Management",
     "Grant or revoke an individual StaffRole (with optional department/school "
     "scope) on a user account, independent of group membership."),
    ("users.bulk_operations", "Run Bulk User Operations", "User Management",
     "Import users in bulk and run bulk password, email, and status operations."),
    ("users.view_login_history", "View Login & Security History", "User Management",
     "Inspect login history, failed attempts, and account security events."),
    ("users.manage_sessions", "Manage User Sessions", "User Management",
     "View active sessions and force-revoke a user's authenticated sessions."),
    ("users.export", "Export User Data", "User Management",
     "Export filtered user lists to PDF, Excel, and CSV."),
    ("users.manage_settings", "Manage User Management Settings", "User Management",
     "Configure username rules, password policy, email domains, and providers."),
]

DEFAULT_PERMISSIONS += USER_MANAGEMENT_PERMISSIONS

DEFAULT_ROLES.append({
    "name": "ICT & Identity Administrator",
    "code": "identity_admin",
    "color": "#00b894",
    "description": "Custodian of the central user directory, credentials, institutional "
                   "email identities, and account security.",
    "permissions": [code for code, _n, _m, _d in USER_MANAGEMENT_PERMISSIONS] + [
        "admin.view_audit_logs", "admin.manage_roles_permissions",
    ],
})

DEFAULT_ROLES.append({
    "name": "Registry Service Desk",
    "code": "registry_service_desk",
    "color": "#0984e3",
    "description": "Front-line desk that helps students and staff recover access without "
                   "holding role-escalation or configuration authority.",
    "permissions": [
        "users.view", "users.reset_password", "users.manage_status",
        "users.view_login_history", "students.view_all",
    ],
})


# ==============================================================================
# 7. DEFAULT USER GROUPS
# ==============================================================================

DEFAULT_USER_GROUPS = [
    {"code": "students", "name": "Students", "user_type": "STUDENT", "precedence": 90,
     "icon": "fa-solid fa-user-graduate", "color": "#0984e3", "roles": [],
     "description": "All enrolled students."},
    {"code": "faculty", "name": "Teaching Staff", "user_type": "STAFF", "precedence": 60,
     "icon": "fa-solid fa-chalkboard-user", "color": "#00cec9", "roles": ["lecturer"],
     "description": "Teaching staff delivering course units."},
    {"code": "finance_staff", "name": "Finance Staff", "user_type": "STAFF", "precedence": 50,
     "icon": "fa-solid fa-coins", "color": "#fdcb6e", "roles": ["finance_officer"],
     "description": "Bursary and fee accounts staff."},
    {"code": "registry_staff", "name": "Registry Staff", "user_type": "STAFF", "precedence": 50,
     "icon": "fa-solid fa-folder-open", "color": "#a29bfe", "roles": ["admissions_officer"],
     "description": "Admissions and student records officers."},
    {"code": "department_heads", "name": "Department Heads", "user_type": "STAFF", "precedence": 30,
     "icon": "fa-solid fa-sitemap", "color": "#6C5CE7", "roles": ["hod"],
     "description": "Heads of department and academic chairs."},
    {"code": "examinations_staff", "name": "Examinations Staff", "user_type": "STAFF",
     "precedence": 40, "icon": "fa-solid fa-file-pen", "color": "#e84393",
     "roles": ["exam_officer"], "description": "Examinations office and Senate secretariat."},
    {"code": "system_administrators", "name": "System Administrators", "user_type": "ADMIN",
     "precedence": 10, "icon": "fa-solid fa-user-shield", "color": "#2d3436",
     "roles": ["identity_admin"], "description": "ICT and system administration team."},
]


@transaction.atomic
def seed_default_user_groups():
    """
    Idempotently create the standard user groups.

    Group roles resolve to the same ``StaffRole`` objects the permission engine
    already evaluates, so groups never become a second source of authority.
    """
    from university.identity_models import UserGroup

    created = 0
    for spec in DEFAULT_USER_GROUPS:
        group, was_created = UserGroup.objects.update_or_create(
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "user_type": spec["user_type"],
                "precedence": spec["precedence"],
                "icon": spec["icon"],
                "color": spec["color"],
                "is_system_group": True,
            },
        )
        group.roles.set(StaffRole.objects.filter(code__in=spec["roles"]))
        created += int(was_created)
    return created
