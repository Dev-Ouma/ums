"""
Granular Roles, Permissions & User-Level Overrides Engine for University Management System.
Supports role definitions, permission catalogs, and explicit user-level Grant/Deny overrides.
"""

from django.db import transaction
from university.models import (
    Department,
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

    # --- Examinations & Senate ---
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

    # --- Hostels & Accommodation ---
    ("hostels.view_allocation", "View Hostel Allocations", "Accommodation", "View hostel blocks, room occupancy, and resident nominal lists."),
    ("hostels.allocate_room", "Allocate & Reassign Rooms", "Accommodation", "Assign students to hostel blocks, rooms, and beds."),
    ("hostels.clear_student", "Hostel Exit Clearance", "Accommodation", "Authorize room check-out and hostel clearance."),

    # --- Reports & Analytics ---
    ("reports.view_catalog", "Access Reports Catalog", "Reports", "Browse standard university report catalogues and web previews."),
    ("reports.generate_official", "Generate Official Reports", "Reports", "Run full institution-wide operational and demographic reports."),
    ("reports.senate_marksheet", "Generate Senate Consolidated Marksheets", "Reports", "Compile and export official Board of Examiners master sheets."),
    ("reports.export_files", "Export Reports (PDF/Excel/CSV)", "Reports", "Download printable PDF, Excel, and CSV datasets."),

    # --- System Administration & Governance ---
    ("admin.manage_settings", "Manage System Settings & Setups", "Administration", "Configure university branding, academic rules, and system toggles."),
    ("admin.view_audit_logs", "Inspect Audit Trail Ledgers", "Administration", "Forensic analysis of user activity, state changes, and logins."),
    ("admin.manage_recycle_bin", "Manage Recycle Bin & Restore", "Administration", "Inspect and restore soft-deleted university records."),
    ("admin.manage_roles_permissions", "Manage Roles & Permissions", "Administration", "Configure staff roles, assign permissions, and set user overrides."),
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
            "exams.view_marks", "exams.moderate_marks", "exams.approve_senate", "exams.publish_results",
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
            "exams.view_marks", "exams.moderate_marks", "exams.approve_senate",
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
            "exams.view_marks", "exams.enter_cat", "exams.enter_exam", "exams.moderate_marks",
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
            "exams.view_marks", "exams.enter_cat", "exams.enter_exam", "exams.moderate_marks", "exams.publish_results",
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
    has_role_perm = StaffRoleAssignment.objects.filter(
        user=user,
        is_active=True,
        role__permissions__code=permission_code
    ).exists()

    if has_role_perm:
        return True

    # Check Base System Role Defaults
    user_role = getattr(user, "role", "")
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
        elif is_admin:
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
def assign_staff_role(user, role_id_or_code, department_id=None, actor=None, request=None):
    """
    Assigns a StaffRole to a user.
    """
    if isinstance(role_id_or_code, int) or str(role_id_or_code).isdigit():
        role = StaffRole.objects.get(id=int(role_id_or_code))
    else:
        role = StaffRole.objects.get(code=role_id_or_code)

    dept = Department.objects.filter(id=department_id).first() if department_id else None

    assignment, created = StaffRoleAssignment.objects.update_or_create(
        user=user,
        role=role,
        department=dept,
        defaults={
            "assigned_by": actor,
            "is_active": True,
        }
    )

    dept_str = f" in {dept.code}" if dept else ""
    log_activity(
        request=request,
        user=actor or user,
        action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        module=AuditLog.Module.CONFIG,
        entity=f"Staff Role: {user.username}",
        description=f"Assigned role '{role.name}'{dept_str} to staff user {user.username}.",
    )
    return assignment
