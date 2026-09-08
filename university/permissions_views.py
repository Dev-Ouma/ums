"""
Roles, Permissions & Staff Access Overrides Views for Academic Setups & System Administration.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User, Role, FacultyProfile
from university.models import (
    Department,
    SystemPermission,
    StaffRole,
    StaffRoleAssignment,
    UserPermissionOverride,
    AuditLog,
)
from university.permissions_services import (
    seed_default_permissions_and_roles,
    get_user_effective_permissions,
    set_user_permission_override,
    remove_user_permission_override,
    assign_staff_role,
    has_user_permission,
)
from university.audit_services import log_activity


def _admin_required(view_func):
    """Ensure user is an active Administrator or Staff member."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        user_role = getattr(request.user, "role", "")
        if not (request.user.is_staff or request.user.is_superuser or user_role in (Role.ADMIN, "ADMIN")):
            messages.error(request, "Access restricted. System Administrator privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ==============================================================================
# 1. ROLES & PERMISSIONS HUB (ADMIN SETUPS)
# ==============================================================================

@login_required
@_admin_required
def staff_permissions_dashboard(request):
    """
    Main hub for All Staff Access Control, Roles Directory, and Permission Overrides.
    """
    # Ensure default permissions and roles exist
    if SystemPermission.objects.count() == 0 or StaffRole.objects.count() == 0:
        seed_default_permissions_and_roles()

    sub_tab = request.GET.get("sub", "staff").lower()
    q = request.GET.get("q", "").strip().lower()
    dept_filter = request.GET.get("dept", "").strip()
    role_filter = request.GET.get("role", "").strip()

    # 1. Staff Members (Faculty + Admins + Staff)
    staff_qs = User.objects.filter(role__in=[Role.ADMIN, Role.FACULTY]).prefetch_related(
        "staff_role_assignments__role",
        "staff_role_assignments__department",
        "permission_overrides__permission",
        "faculty_profile__department",
    ).order_by("first_name", "last_name", "username")

    if q:
        staff_qs = staff_qs.filter(
            username__icontains=q
        ) | staff_qs.filter(
            first_name__icontains=q
        ) | staff_qs.filter(
            last_name__icontains=q
        ) | staff_qs.filter(
            email__icontains=q
        )

    if dept_filter:
        staff_qs = staff_qs.filter(faculty_profile__department_id=dept_filter)

    if role_filter:
        staff_qs = staff_qs.filter(staff_role_assignments__role__code=role_filter, staff_role_assignments__is_active=True)

    # Server-side pagination for staff list
    per_page = int(request.GET.get("per_page", 20))
    paginator = Paginator(staff_qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Pre-calculate override counts and display badges for paginated staff
    staff_items = []
    for staff in page_obj:
        assignments = staff.staff_role_assignments.filter(is_active=True)
        overrides = staff.permission_overrides.all()
        grants = [o for o in overrides if o.override_type == UserPermissionOverride.OverrideType.GRANT]
        denies = [o for o in overrides if o.override_type == UserPermissionOverride.OverrideType.DENY]
        
        dept = None
        if hasattr(staff, "faculty_profile") and staff.faculty_profile.department:
            dept = staff.faculty_profile.department
        elif assignments.filter(department__isnull=False).exists():
            dept = assignments.filter(department__isnull=False).first().department

        staff_items.append({
            "user": staff,
            "department": dept,
            "assignments": assignments,
            "grants_count": len(grants),
            "denies_count": len(denies),
            "total_overrides": len(overrides),
        })

    # 2. Roles Catalog
    roles = StaffRole.objects.prefetch_related("permissions").all().order_by("name")

    # 3. Permissions grouped by module
    all_permissions = SystemPermission.objects.all().order_by("module", "name")
    modules = {}
    for p in all_permissions:
        if p.module not in modules:
            modules[p.module] = []
        modules[p.module].append(p)

    # Departments for filter dropdown
    departments = Department.objects.all().order_by("name")

    # KPI Summary Counts
    total_staff = User.objects.filter(role__in=[Role.ADMIN, Role.FACULTY]).count()
    total_roles = StaffRole.objects.count()
    total_permissions = SystemPermission.objects.count()
    total_overrides = UserPermissionOverride.objects.count()

    context = {
        "sub_tab": sub_tab,
        "staff_items": staff_items,
        "page_obj": page_obj,
        "total_staff_count": staff_qs.count(),
        "per_page": per_page,
        "roles": roles,
        "modules": modules,
        "departments": departments,
        "total_staff": total_staff,
        "total_roles": total_roles,
        "total_permissions": total_permissions,
        "total_overrides": total_overrides,
        "search_query": request.GET.get("q", ""),
        "selected_dept": dept_filter,
        "selected_role": role_filter,
    }
    return render(request, "setups/staff_permissions_dashboard.html", context)


# ==============================================================================
# 2. STAFF USER ACCESS & PERMISSION OVERRIDES DETAIL
# ==============================================================================

@login_required
@_admin_required
def staff_user_access_detail(request, user_id):
    """
    Detailed inspector & editor for a single staff member:
    - View active role assignments
    - Assign/remove roles
    - View effective permissions breakdown
    - Configure granular User-Level Overrides (GRANT / DENY) with reason
    """
    staff_user = get_object_or_404(User, id=user_id)
    if staff_user.role == Role.STUDENT and not staff_user.is_staff:
        messages.warning(request, f"User {staff_user.username} is registered as a student.")

    effective_perms = get_user_effective_permissions(staff_user)

    # Group effective permissions by module for the UI
    grouped_perms = {}
    for code, data in effective_perms.items():
        mod = data["permission"].module
        if mod not in grouped_perms:
            grouped_perms[mod] = []
        grouped_perms[mod].append(data)

    all_roles = StaffRole.objects.all().order_by("name")
    departments = Department.objects.all().order_by("name")
    active_assignments = staff_user.staff_role_assignments.filter(is_active=True).select_related("role", "department")

    context = {
        "staff_user": staff_user,
        "effective_perms": effective_perms,
        "grouped_perms": grouped_perms,
        "all_roles": all_roles,
        "departments": departments,
        "active_assignments": active_assignments,
        "overrides_count": staff_user.permission_overrides.count(),
    }
    return render(request, "setups/staff_user_access_detail.html", context)


# ==============================================================================
# 3. SET / TOGGLE PERMISSION OVERRIDE (AJAX / POST)
# ==============================================================================

@login_required
@_admin_required
@require_POST
def set_permission_override_action(request, user_id):
    """
    Sets an explicit GRANT or DENY override, or REMOVES an override for a specific permission.
    """
    staff_user = get_object_or_404(User, id=user_id)
    perm_code = request.POST.get("perm_code", "").strip()
    override_type = request.POST.get("override_type", "").strip().upper()  # 'GRANT', 'DENY', 'REMOVE'
    reason = request.POST.get("reason", "").strip()

    if not perm_code:
        messages.error(request, "Invalid permission code provided.")
        return redirect("university:staff_user_access_detail", user_id=user_id)

    try:
        if override_type == "REMOVE":
            remove_user_permission_override(staff_user, perm_code, actor=request.user, request=request)
            messages.success(request, f"Override removed for '{perm_code}'. Standard role rules now apply.")
        elif override_type in [UserPermissionOverride.OverrideType.GRANT, UserPermissionOverride.OverrideType.DENY]:
            set_user_permission_override(
                user=staff_user,
                permission_code=perm_code,
                override_type=override_type,
                reason=reason,
                granted_by=request.user,
                request=request
            )
            action_str = "explicitly GRANTED" if override_type == "GRANT" else "explicitly DENIED"
            messages.success(request, f"Permission '{perm_code}' was {action_str} for {staff_user.username}.")
        else:
            messages.error(request, f"Unknown override action '{override_type}'.")
    except Exception as e:
        messages.error(request, f"Failed to update permission override: {str(e)}")

    return redirect("university:staff_user_access_detail", user_id=user_id)


# ==============================================================================
# 4. ASSIGN / REMOVE STAFF ROLES (POST)
# ==============================================================================

@login_required
@_admin_required
@require_POST
def staff_role_assignment_action(request, user_id):
    """
    Assign a StaffRole to a user or remove an existing assignment.
    """
    staff_user = get_object_or_404(User, id=user_id)
    action = request.POST.get("action", "assign")
    role_id = request.POST.get("role_id")
    dept_id = request.POST.get("department_id") or None

    if action == "assign":
        if not role_id:
            messages.error(request, "Please select a staff role to assign.")
            return redirect("university:staff_user_access_detail", user_id=user_id)

        try:
            assign_staff_role(
                user=staff_user,
                role_id_or_code=role_id,
                department_id=dept_id,
                actor=request.user,
                request=request
            )
            messages.success(request, f"Role successfully assigned to {staff_user.username}.")
        except Exception as e:
            messages.error(request, f"Error assigning role: {str(e)}")

    elif action == "remove":
        assignment_id = request.POST.get("assignment_id")
        assignment = get_object_or_404(StaffRoleAssignment, id=assignment_id, user=staff_user)
        role_name = assignment.role.name
        assignment.delete()
        log_activity(
            request=request,
            user=request.user,
            action=AuditLog.Action.DELETE,
            module=AuditLog.Module.CONFIG,
            entity=f"Staff Role: {staff_user.username}",
            description=f"Removed role '{role_name}' from staff user {staff_user.username}.",
        )
        messages.success(request, f"Role '{role_name}' removed from {staff_user.username}.")

    return redirect("university:staff_user_access_detail", user_id=user_id)


# ==============================================================================
# 5. ROLE CREATE / EDIT & PERMISSION SETTINGS
# ==============================================================================

@login_required
@_admin_required
def role_create_edit(request, role_id=None):
    """
    Create a new custom StaffRole or update permissions on an existing role.
    """
    role_obj = get_object_or_404(StaffRole, id=role_id) if role_id else None

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip().lower().replace(" ", "_")
        description = request.POST.get("description", "").strip()
        color = request.POST.get("color", "#6C5CE7").strip()
        selected_perms = request.POST.getlist("permissions")

        if not name or not code:
            messages.error(request, "Role Name and Code identifier are required.")
            return redirect(request.path)

        with transaction.atomic():
            if role_obj:
                role_obj.name = name
                role_obj.description = description
                role_obj.color = color
                role_obj.save()
            else:
                if StaffRole.objects.filter(code=code).exists():
                    messages.error(request, f"Role with code '{code}' already exists.")
                    return redirect(request.path)
                role_obj = StaffRole.objects.create(
                    name=name,
                    code=code,
                    description=description,
                    color=color,
                    is_system_role=False,
                )

            # Update assigned permissions
            perm_objs = SystemPermission.objects.filter(code__in=selected_perms)
            role_obj.permissions.set(perm_objs)

            log_activity(
                request=request,
                user=request.user,
                action=AuditLog.Action.UPDATE if role_id else AuditLog.Action.CREATE,
                module=AuditLog.Module.CONFIG,
                entity=f"Staff Role: {role_obj.name}",
                description=f"{'Updated' if role_id else 'Created'} role '{role_obj.name}' with {len(perm_objs)} permissions.",
            )

        messages.success(request, f"Role '{role_obj.name}' successfully saved.")
        return redirect("university:staff_permissions_dashboard")

    # Group permissions for matrix checkboxes
    all_permissions = SystemPermission.objects.all().order_by("module", "name")
    modules = {}
    for p in all_permissions:
        if p.module not in modules:
            modules[p.module] = []
        modules[p.module].append(p)

    current_perm_codes = set(role_obj.permissions.values_list("code", flat=True)) if role_obj else set()

    context = {
        "role_obj": role_obj,
        "modules": modules,
        "current_perm_codes": current_perm_codes,
    }
    return render(request, "setups/role_form.html", context)


# ==============================================================================
# 6. ROLE DELETE
# ==============================================================================

@login_required
@_admin_required
@require_POST
def role_delete(request, role_id):
    """
    Deletes a custom staff role (system roles cannot be deleted).
    """
    role_obj = get_object_or_404(StaffRole, id=role_id)
    if role_obj.is_system_role:
        messages.error(request, "Built-in system roles cannot be deleted.")
        return redirect("university:staff_permissions_dashboard")

    role_name = role_obj.name
    role_obj.delete()

    log_activity(
        request=request,
        user=request.user,
        action=AuditLog.Action.DELETE,
        module=AuditLog.Module.SETTINGS,
        entity=f"Staff Role: {role_name}",
        description=f"Deleted custom role '{role_name}'.",
    )
    messages.success(request, f"Role '{role_name}' was deleted.")
    return redirect("university:staff_permissions_dashboard")
