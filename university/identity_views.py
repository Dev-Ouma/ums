"""
System Admin → User Management views.

Every view is gated on a granular ``users.*`` permission through
``permission_required``; the sidebar and buttons are a convenience layer only,
and removing a permission blocks the route itself.
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university import identity_io
from university.audit_services import log_activity
from university.decorators import permission_required
from university.document_design import get_branding
from university.email_services import (Provider, get_email_config, mark_mailbox_active,
                                       provision_mailbox, send_test_email)
from university.identity_models import (AccountStatus, EmailDeliveryRecord, InstitutionalEmail,
                                        LoginRecord, PasswordResetToken, UserAccount,
                                        UserGroup, UserGroupMembership, UserImportBatch,
                                        UserType)
from university.identity_services import (active_sessions_for, assign_group,
                                          bulk_generate_emails, bulk_password_operation,
                                          commit_import, create_user_account, ensure_account,
                                          force_password_change, generate_password,
                                          generate_username, get_account, get_password_policy,
                                          invalidate_user_sessions, issue_temporary_password,
                                          provision_institutional_email, remove_group,
                                          search_users, send_reset_link, set_account_status,
                                          unlock_account, validate_import_rows)
from university.models import AuditLog, Department, Program, StaffRole, StaffRoleAssignment
from university.settings_services import get_setting, set_setting
from university.document_views import present_pdf
from university.views import _pdf_disposition

IMPORT_SESSION_KEY = "_user_import_preview"


def _paginate(request, queryset, default_per_page=25):
    per_page = request.GET.get("per_page", default_per_page)
    try:
        per_page = max(5, min(int(per_page), 200))
    except (TypeError, ValueError):
        per_page = default_per_page
    return Paginator(queryset, per_page).get_page(request.GET.get("page", 1))


def _filters(request):
    return {
        "q": request.GET.get("q", "").strip(),
        "user_type": request.GET.get("user_type", "").strip(),
        "status": request.GET.get("status", "").strip(),
        "role": request.GET.get("role", "").strip(),
        "department": request.GET.get("department", "").strip(),
        "program": request.GET.get("program", "").strip(),
        "group": request.GET.get("group", "").strip(),
        "campus": request.GET.get("campus", "").strip(),
    }


def _filtered_users(request, base=None):
    filters = _filters(request)
    qs = search_users(queryset=base, **filters)
    sort = request.GET.get("sort", "-date_joined")
    allowed_sorts = {
        "username", "-username", "first_name", "-first_name", "email", "-email",
        "last_login", "-last_login", "date_joined", "-date_joined",
        "account__status", "-account__status",
    }
    if sort not in allowed_sorts:
        sort = "-date_joined"
    return qs.order_by(sort).prefetch_related("group_memberships__group",
                                              "institutional_emails"), filters, sort


def _reference_data():
    return {
        "user_types": UserType.choices,
        "statuses": AccountStatus.choices,
        "roles": Role.choices,
        "departments": Department.objects.all().order_by("name"),
        "programs": Program.objects.all().order_by("code"),
        "groups": UserGroup.objects.all().order_by("precedence", "name"),
        "staff_roles": StaffRole.objects.all().order_by("name"),
    }


# ==============================================================================
# 1. DASHBOARD
# ==============================================================================

@login_required
@permission_required("users.view")
def user_dashboard(request):
    """Live identity metrics — every number is a database aggregate."""
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    accounts = UserAccount.objects.all()
    users = User.objects.all()

    status_counts = {row["status"]: row["n"] for row in
                     accounts.values("status").annotate(n=Count("id"))}
    type_counts = {row["user_type"]: row["n"] for row in
                   accounts.values("user_type").annotate(n=Count("id"))}
    role_counts = {row["role"]: row["n"] for row in users.values("role").annotate(n=Count("id"))}

    dept_counts = list(
        Department.objects.annotate(n=Count("faculty", distinct=True))
        .values("code", "name", "n").order_by("-n")[:8])
    program_counts = list(
        Program.objects.annotate(n=Count("students", distinct=True))
        .values("code", "name", "n").order_by("-n")[:8])

    login_activity = []
    for offset in range(13, -1, -1):
        day = (now - timedelta(days=offset)).date()
        day_qs = LoginRecord.objects.filter(login_at__date=day)
        login_activity.append({
            "day": day.strftime("%d %b"),
            "success": day_qs.filter(success=True).count(),
            "failed": day_qs.filter(success=False).count(),
        })

    reset_activity = []
    for offset in range(13, -1, -1):
        day = (now - timedelta(days=offset)).date()
        reset_activity.append({
            "day": day.strftime("%d %b"),
            "count": PasswordResetToken.objects.filter(created_at__date=day).count(),
        })

    context = {
        "total_users": users.count(),
        "active_users": status_counts.get(AccountStatus.ACTIVE, 0),
        "inactive_users": status_counts.get(AccountStatus.INACTIVE, 0),
        "pending_users": status_counts.get(AccountStatus.PENDING, 0),
        "locked_users": accounts.filter(locked_until__gt=now).count()
                        or status_counts.get(AccountStatus.LOCKED, 0),
        "suspended_users": status_counts.get(AccountStatus.SUSPENDED, 0),
        "disabled_users": status_counts.get(AccountStatus.DISABLED, 0),
        "student_accounts": type_counts.get(UserType.STUDENT, 0),
        "staff_accounts": type_counts.get(UserType.STAFF, 0),
        "admin_accounts": type_counts.get(UserType.ADMIN, 0),
        "students_without_accounts": StudentProfile.objects.filter(user__isnull=True).count(),
        "must_change_password": accounts.filter(must_change_password=True).count(),
        "unverified_email": users.filter(Q(email="") | Q(email__isnull=True)).count(),
        "never_logged_in": users.filter(last_login__isnull=True).count(),
        "no_institutional_email": users.exclude(
            id__in=InstitutionalEmail.objects.filter(is_primary=True).values("user_id")).count(),
        "recent_users": users.select_related("account").order_by("-date_joined")[:8],
        "recent_failed_logins": LoginRecord.objects.filter(success=False)
                                .select_related("user")[:8],
        "recent_resets": PasswordResetToken.objects.select_related("user")
                         .order_by("-created_at")[:8],
        "recent_account_changes": AuditLog.objects.filter(
            module=AuditLog.Module.AUTH).order_by("-timestamp")[:8],
        "new_users_this_week": users.filter(date_joined__gte=week_ago).count(),
        "failed_logins_this_week": LoginRecord.objects.filter(
            success=False, login_at__gte=week_ago).count(),
        "resets_this_week": PasswordResetToken.objects.filter(created_at__gte=week_ago).count(),
        # Passed as plain structures; the templates render them through
        # ``json_script``, which escapes them safely for the browser.
        "chart_types": [{"label": label, "value": type_counts.get(value, 0)}
                        for value, label in UserType.choices],
        "chart_status": [{"label": label, "value": status_counts.get(value, 0)}
                         for value, label in AccountStatus.choices if status_counts.get(value, 0)],
        "chart_roles": [{"label": label, "value": role_counts.get(value, 0)}
                        for value, label in Role.choices],
        "chart_departments": dept_counts,
        "chart_programs": program_counts,
        "chart_logins": login_activity,
        "chart_resets": reset_activity,
    }
    return render(request, "identity/dashboard.html", context)


# ==============================================================================
# 2. USER DIRECTORY
# ==============================================================================

@login_required
@permission_required("users.view")
def user_list(request):
    queryset, filters, sort = _filtered_users(request)
    page = _paginate(request, queryset)
    context = {"page_obj": page, "filters": filters, "sort": sort,
               "total_matched": queryset.count(), **_reference_data()}
    return render(request, "identity/user_list.html", context)


@login_required
@permission_required("users.view")
def student_accounts(request):
    base = User.objects.filter(role=Role.STUDENT)
    queryset, filters, sort = _filtered_users(request, base=base)
    page = _paginate(request, queryset)
    return render(request, "identity/student_accounts.html", {
        "page_obj": page, "filters": filters, "sort": sort,
        "unlinked_students": StudentProfile.objects.filter(user__isnull=True)[:50],
        "total_matched": queryset.count(), **_reference_data(),
    })


@login_required
@permission_required("users.view")
def staff_accounts(request):
    base = User.objects.filter(role__in=[Role.FACULTY, Role.ADMIN])
    queryset, filters, sort = _filtered_users(request, base=base)
    page = _paginate(request, queryset)
    return render(request, "identity/staff_accounts.html", {
        "page_obj": page, "filters": filters, "sort": sort,
        "unlinked_staff": FacultyProfile.objects.filter(user__isnull=True)[:50],
        "total_matched": queryset.count(), **_reference_data(),
    })


@login_required
@permission_required("users.view")
def user_detail(request, pk):
    user = get_object_or_404(
        User.objects.select_related("student_profile__program", "faculty_profile__department"),
        pk=pk)
    account = ensure_account(user)

    return render(request, "identity/user_detail.html", {
        "subject": user,
        "account": account,
        "student": getattr(user, "student_profile", None),
        "staff": getattr(user, "faculty_profile", None),
        "emails": user.institutional_emails.all(),
        "memberships": user.group_memberships.select_related("group"),
        "role_assignments": user.staff_role_assignments.select_related("role", "department"),
        "login_records": user.login_records.all()[:20],
        "sessions": active_sessions_for(user),
        "recent_activity": AuditLog.objects.filter(user=user).order_by("-timestamp")[:20],
        "account_events": AuditLog.objects.filter(
            module=AuditLog.Module.AUTH, entity_id=str(user.pk)).order_by("-timestamp")[:20],
        "policy": get_password_policy(),
        **_reference_data(),
    })


@login_required
@permission_required("users.create")
def user_create(request):
    """
    Create one central user and, optionally, link it to an existing academic record.

    Linking is preferred over re-keying: the form offers unlinked student and
    staff records so an administrator never re-types details the system holds.
    """
    if request.method == "POST":
        data = request.POST
        try:
            student_profile = faculty_profile = None
            if data.get("student_profile"):
                student_profile = StudentProfile.objects.filter(
                    pk=data["student_profile"], user__isnull=True).first()
            if data.get("faculty_profile"):
                faculty_profile = FacultyProfile.objects.filter(
                    pk=data["faculty_profile"], user__isnull=True).first()

            groups = list(UserGroup.objects.filter(pk__in=data.getlist("groups")))
            result = create_user_account(
                user_type=data.get("user_type") or UserType.STUDENT,
                first_name=data.get("first_name", "").strip(),
                last_name=data.get("last_name", "").strip(),
                email=data.get("email", "").strip(),
                username=data.get("username", "").strip(),
                phone=data.get("phone", "").strip(),
                role=data.get("role") or None,
                password=data.get("password") or None,
                password_mode=data.get("password_mode", "LINK"),
                status=data.get("status") or AccountStatus.PENDING,
                must_change_password=bool(data.get("must_change_password")),
                groups=groups,
                staff_role_codes=data.getlist("staff_roles"),
                student_profile=student_profile,
                faculty_profile=faculty_profile,
                campus=data.get("campus", "").strip(),
                activation_date=data.get("activation_date") or None,
                expiry_date=data.get("expiry_date") or None,
                generate_email=bool(data.get("generate_email")),
                notify=bool(data.get("send_notification")),
                actor=request.user, request=request,
            )
        except Exception as exc:
            messages.error(request, f"The account could not be created: {exc}")
        else:
            messages.success(
                request, f"Account '{result['username']}' created successfully.")
            if result["temporary_password"]:
                # Shown once, to the creating administrator only, for controlled
                # hand-over. It is never stored in plaintext or emailed.
                messages.warning(
                    request,
                    f"Temporary password for {result['username']}: "
                    f"{result['temporary_password']} — deliver it securely. The user must "
                    f"change it at first sign-in.")
            return redirect("university:user_detail", pk=result["user"].pk)

    return render(request, "identity/user_create.html", {
        "unlinked_students": StudentProfile.objects.filter(user__isnull=True).order_by("roll_no"),
        "unlinked_staff": FacultyProfile.objects.filter(user__isnull=True).order_by("employee_id"),
        "policy": get_password_policy(),
        **_reference_data(),
    })


@login_required
@permission_required("users.edit")
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    account = ensure_account(user)

    if request.method == "POST":
        data = request.POST
        before = {"username": user.username, "email": user.email, "role": user.role,
                  "user_type": account.user_type, "campus": account.campus}

        new_username = data.get("username", "").strip() or user.username
        if (new_username.lower() != user.username.lower()
                and User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists()):
            messages.error(request, f"Username '{new_username}' is already taken.")
            return redirect("university:user_edit", pk=user.pk)

        user.username = new_username
        user.first_name = data.get("first_name", "").strip()
        user.last_name = data.get("last_name", "").strip()
        user.email = data.get("email", "").strip()
        user.phone = data.get("phone", "").strip()
        if data.get("role") in dict(Role.choices):
            user.role = data["role"]
        user.save()

        account.user_type = data.get("user_type") or account.user_type
        account.campus = data.get("campus", "").strip()
        account.activation_date = data.get("activation_date") or None
        account.expiry_date = data.get("expiry_date") or None
        account.notes = data.get("notes", "").strip()
        account.save()

        log_activity(
            request=request, user=request.user, action=AuditLog.Action.UPDATE,
            module=AuditLog.Module.AUTH, entity="User", entity_id=user.pk,
            description=f"Updated account details for '{user.username}'.",
            previous_state=before,
            new_state={"username": user.username, "email": user.email, "role": user.role,
                       "user_type": account.user_type, "campus": account.campus})
        messages.success(request, "Account details updated.")
        return redirect("university:user_detail", pk=user.pk)

    return render(request, "identity/user_edit.html", {
        "subject": user, "account": account, **_reference_data()})


# ==============================================================================
# 3. ACCOUNT ACTIONS
# ==============================================================================

# Each action names the permission it needs, so a narrow grant (a service desk
# that may unlock but not disable) is genuinely enforceable.
ACTION_PERMISSIONS = {
    "activate": "users.manage_status",
    "deactivate": "users.manage_status",
    "suspend": "users.manage_status",
    "disable": "users.manage_status",
    "archive": "users.manage_status",
    "unlock": "users.manage_status",
    "send_reset_link": "users.reset_password",
    "force_password_change": "users.reset_password",
    "temporary_password": "users.reset_password",
    "revoke_sessions": "users.manage_sessions",
    "generate_email": "users.manage_emails",
    "assign_group": "users.manage_groups",
    "remove_group": "users.manage_groups",
    "assign_role": "users.manage_groups",
}

STATUS_ACTIONS = {
    "activate": AccountStatus.ACTIVE,
    "deactivate": AccountStatus.INACTIVE,
    "suspend": AccountStatus.SUSPENDED,
    "disable": AccountStatus.DISABLED,
    "archive": AccountStatus.ARCHIVED,
}


@login_required
@require_POST
def user_action(request, pk, action):
    """
    Single POST endpoint for every per-user administrative action.

    The permission check happens here, before anything is read or written, so an
    unauthorised AJAX call is refused exactly like an unauthorised page load.
    """
    from django.core.exceptions import PermissionDenied
    from university.permissions_services import has_user_permission

    required = ACTION_PERMISSIONS.get(action)
    if not required or not has_user_permission(request.user, required):
        raise PermissionDenied("You don't have permission for that action.")

    user = get_object_or_404(User, pk=pk)
    reason = request.POST.get("reason", "").strip()

    if action in STATUS_ACTIONS:
        if user == request.user and STATUS_ACTIONS[action] != AccountStatus.ACTIVE:
            messages.error(request, "You cannot change the status of your own account.")
        elif user.is_superuser and not request.user.is_superuser:
            messages.error(request, "This account is protected and cannot be modified.")
        else:
            ok, message, _account = set_account_status(
                user, STATUS_ACTIONS[action], actor=request.user, request=request, reason=reason)
            (messages.success if ok else messages.error)(request, message)

    elif action == "unlock":
        was_locked, _account = unlock_account(user, actor=request.user, request=request)
        messages.success(request, "Account unlocked." if was_locked
                         else "Account was not locked; counters were cleared.")

    elif action == "send_reset_link":
        ok, message, url = send_reset_link(user, actor=request.user, request=request)
        if ok:
            messages.success(request, f"A password reset link was emailed to {user.email}.")
        else:
            # Delivery failed, so hand the administrator the link for controlled
            # out-of-band delivery rather than leaving the user stuck.
            messages.warning(request, f"Reset link could not be emailed ({message}). "
                                      f"Secure link: {url}")

    elif action == "force_password_change":
        force_password_change(user, actor=request.user, request=request)
        messages.success(request, f"'{user.username}' must change their password at next sign-in.")

    elif action == "temporary_password":
        temporary = issue_temporary_password(user, actor=request.user, request=request)
        messages.warning(request, f"Temporary password for {user.username}: {temporary} — "
                                  f"deliver it securely. It expires and must be changed at "
                                  f"first sign-in.")

    elif action == "revoke_sessions":
        killed = invalidate_user_sessions(
            user, keep_session_key=request.session.session_key if user == request.user else None)
        log_activity(request=request, user=request.user, action=AuditLog.Action.UPDATE,
                     module=AuditLog.Module.AUTH, entity="User Sessions", entity_id=user.pk,
                     description=f"Revoked {killed} active sessions for '{user.username}'.")
        messages.success(request, f"Revoked {killed} active session(s).")

    elif action == "generate_email":
        record = provision_institutional_email(user, actor=request.user, request=request,
                                               notify=True)
        messages.success(request, f"Institutional email {record.address} recorded "
                                  f"({record.get_status_display()}).")

    elif action == "assign_group":
        group = get_object_or_404(UserGroup, pk=request.POST.get("group"))
        _membership, created = assign_group(user, group, actor=request.user, request=request)
        messages.success(request, f"Added to '{group.name}'." if created
                         else f"Already a member of '{group.name}'.")

    elif action == "remove_group":
        group = get_object_or_404(UserGroup, pk=request.POST.get("group"))
        remove_group(user, group, actor=request.user, request=request)
        messages.success(request, f"Removed from '{group.name}'.")

    elif action == "assign_role":
        role = get_object_or_404(StaffRole, pk=request.POST.get("staff_role"))
        StaffRoleAssignment.objects.get_or_create(
            user=user, role=role, department=None,
            defaults={"assigned_by": request.user, "is_active": True})
        log_activity(request=request, user=request.user, action=AuditLog.Action.UPDATE,
                     module=AuditLog.Module.AUTH, entity="Staff Role", entity_id=user.pk,
                     description=f"Assigned role '{role.name}' to '{user.username}'.")
        messages.success(request, f"Role '{role.name}' assigned.")

    next_url = request.POST.get("next")
    return redirect(next_url) if next_url else redirect("university:user_detail", pk=user.pk)


# ==============================================================================
# 4. PASSWORD MANAGEMENT
# ==============================================================================

@login_required
@permission_required("users.reset_password")
def password_management(request):
    """Search-driven reset console covering students, staff and administrators."""
    scope = request.GET.get("scope", "all")
    base = User.objects.all()
    if scope == "students":
        base = base.filter(role=Role.STUDENT)
    elif scope == "staff":
        base = base.filter(role__in=[Role.FACULTY, Role.ADMIN])

    queryset, filters, sort = _filtered_users(request, base=base)
    # An unfiltered console would list the whole institution; require a query
    # so an operator works from an intentional search.
    has_query = any(filters.values())
    page = _paginate(request, queryset if has_query else queryset.none())

    return render(request, "identity/password_management.html", {
        "page_obj": page, "filters": filters, "sort": sort, "scope": scope,
        "has_query": has_query, "policy": get_password_policy(),
        "total_matched": queryset.count() if has_query else 0,
        **_reference_data(),
    })


@login_required
@permission_required("users.manage_status")
def account_status_board(request):
    """Status overview with the levers to change it."""
    accounts = UserAccount.objects.select_related("user").order_by("-status_changed_at")
    status = request.GET.get("status", "").strip()
    if status:
        accounts = accounts.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        accounts = accounts.filter(Q(user__username__icontains=q)
                                   | Q(user__first_name__icontains=q)
                                   | Q(user__last_name__icontains=q))

    summary = {value: UserAccount.objects.filter(status=value).count()
               for value, _label in AccountStatus.choices}
    return render(request, "identity/account_status.html", {
        "page_obj": _paginate(request, accounts), "status": status, "q": q,
        "summary": summary, "statuses": AccountStatus.choices,
    })


# ==============================================================================
# 5. USERNAME & CREDENTIAL GENERATION
# ==============================================================================

@login_required
@permission_required("users.generate_credentials", "users.manage_settings")
def username_management(request):
    """Username rules plus a live generator that never saves without confirmation."""
    keys = ["username_student_format", "username_staff_format", "username_prefix",
            "username_suffix", "username_separator", "username_case",
            "username_max_length", "username_allowed_characters",
            "username_sequential_numbering", "username_sequence_padding"]

    if request.method == "POST":
        from university.permissions_services import has_user_permission
        if not has_user_permission(request.user, "users.manage_settings"):
            messages.error(request, "You don't have permission to change username rules.")
            return redirect("university:username_management")
        for key in keys:
            if key in request.POST:
                value = request.POST[key].strip()
                if key in ("username_sequential_numbering",):
                    value = key in request.POST and request.POST[key] in ("on", "true", "1")
                set_setting(key, value, user=request.user, request=request)
        log_activity(request=request, user=request.user, action=AuditLog.Action.CONFIG_CHANGE,
                     module=AuditLog.Module.AUTH, entity="Username Settings",
                     description="Updated username generation rules.")
        messages.success(request, "Username rules updated.")
        return redirect("university:username_management")

    return render(request, "identity/username_management.html", {
        "settings": {key: get_setting(key) for key in keys},
        "recent_usernames": User.objects.order_by("-date_joined")[:15],
    })


@login_required
@permission_required("users.generate_credentials")
def api_generate_username(request):
    """AJAX username generator. Returns a candidate; nothing is persisted."""
    username = generate_username(
        user_type=request.GET.get("user_type") or UserType.STUDENT,
        first_name=request.GET.get("first_name", ""),
        last_name=request.GET.get("last_name", ""),
        reg_no=request.GET.get("reg_no", ""),
        staff_id=request.GET.get("staff_id", ""),
        program_code=request.GET.get("program_code", ""),
    )
    return JsonResponse({"username": username, "available": True})


@login_required
@permission_required("users.generate_credentials")
def api_generate_password(request):
    """AJAX secure password generator for controlled hand-over workflows."""
    def flag(name, default=None):
        raw = request.GET.get(name)
        if raw is None:
            return default
        return raw.lower() in ("1", "true", "on", "yes")

    password = generate_password(
        length=request.GET.get("length") or None,
        use_upper=flag("upper"), use_lower=flag("lower"),
        use_digits=flag("digits"), use_special=flag("special"))
    return JsonResponse({"password": password, "policy": get_password_policy()})


@login_required
@permission_required("users.view")
def api_check_username(request):
    from university.identity_services import username_exists
    candidate = request.GET.get("username", "").strip()
    return JsonResponse({"username": candidate,
                         "available": bool(candidate) and not username_exists(candidate)})


# ==============================================================================
# 6. GROUPS
# ==============================================================================

@login_required
@permission_required("users.manage_groups")
def group_list(request):
    groups = UserGroup.objects.annotate(members=Count("memberships")).order_by(
        "precedence", "name").prefetch_related("roles")
    return render(request, "identity/groups.html", {
        "groups": groups, "user_types": UserType.choices,
        "staff_roles": StaffRole.objects.order_by("name"),
    })


@login_required
@permission_required("users.manage_groups")
def group_edit(request, pk=None):
    group = get_object_or_404(UserGroup, pk=pk) if pk else None

    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        name = request.POST.get("name", "").strip()
        if not code or not name:
            messages.error(request, "A group needs both a code and a name.")
        elif UserGroup.objects.filter(code=code).exclude(pk=getattr(group, "pk", None)).exists():
            messages.error(request, f"Group code '{code}' is already in use.")
        else:
            values = {
                "code": code, "name": name,
                "description": request.POST.get("description", "").strip(),
                "user_type": request.POST.get("user_type") or UserType.STAFF,
                "precedence": int(request.POST.get("precedence") or 100),
                "color": request.POST.get("color", "#6C5CE7"),
                "icon": request.POST.get("icon", "fa-solid fa-users-rectangle").strip(),
            }
            if group:
                for field, value in values.items():
                    setattr(group, field, value)
                group.save()
                action = AuditLog.Action.UPDATE
            else:
                group = UserGroup.objects.create(**values)
                action = AuditLog.Action.CREATE
            group.roles.set(StaffRole.objects.filter(pk__in=request.POST.getlist("roles")))
            log_activity(request=request, user=request.user, action=action,
                         module=AuditLog.Module.AUTH, entity="User Group", entity_id=group.pk,
                         description=f"Saved user group '{group.name}'.")
            messages.success(request, f"Group '{group.name}' saved.")
            return redirect("university:group_list")

    return render(request, "identity/group_form.html", {
        "group": group, "user_types": UserType.choices,
        "staff_roles": StaffRole.objects.order_by("name"),
        "selected_roles": list(group.roles.values_list("id", flat=True)) if group else [],
        "members": group.memberships.select_related("user")[:100] if group else [],
    })


@login_required
@permission_required("users.manage_groups")
@require_POST
def group_delete(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    if group.is_system_group:
        messages.error(request, "System groups cannot be deleted.")
    else:
        name = group.name
        group.delete()
        log_activity(request=request, user=request.user, action=AuditLog.Action.DELETE,
                     module=AuditLog.Module.AUTH, entity="User Group",
                     description=f"Deleted user group '{name}'.")
        messages.success(request, f"Group '{name}' deleted.")
    return redirect("university:group_list")


# ==============================================================================
# 7. EMAIL ACCOUNTS
# ==============================================================================

@login_required
@permission_required("users.manage_emails")
def email_accounts(request):
    emails = InstitutionalEmail.objects.select_related("user").order_by("-created_at")
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    q = request.GET.get("q", "").strip()
    if status:
        emails = emails.filter(status=status)
    if kind:
        emails = emails.filter(kind=kind)
    if q:
        emails = emails.filter(Q(address__icontains=q) | Q(user__username__icontains=q))

    without_email = User.objects.exclude(
        id__in=InstitutionalEmail.objects.filter(is_primary=True).values("user_id"))

    return render(request, "identity/email_accounts.html", {
        "page_obj": _paginate(request, emails),
        "statuses": InstitutionalEmail.Status.choices,
        "kinds": UserType.choices,
        "status": status, "kind": kind, "q": q,
        "config": {k: v for k, v in get_email_config().items() if k != "password"},
        "without_email_count": without_email.count(),
        "without_email": without_email.select_related("account")[:25],
        "summary": {value: InstitutionalEmail.objects.filter(status=value).count()
                    for value, _label in InstitutionalEmail.Status.choices},
        "recent_deliveries": EmailDeliveryRecord.objects.select_related("user")[:15],
    })


@login_required
@permission_required("users.manage_emails")
@require_POST
def email_action(request, pk, action):
    record = get_object_or_404(InstitutionalEmail, pk=pk)

    if action == "provision":
        ok, message = provision_mailbox(record, actor=request.user)
        (messages.success if ok else messages.warning)(request, message)
    elif action == "confirm":
        mark_mailbox_active(record, reference=request.POST.get("reference", "").strip(),
                            actor=request.user)
        messages.success(request, f"{record.address} marked as active.")
    elif action in ("suspend", "disable"):
        record.status = (InstitutionalEmail.Status.SUSPENDED if action == "suspend"
                         else InstitutionalEmail.Status.DISABLED)
        record.save(update_fields=["status"])
        messages.success(request, f"{record.address} is now {record.get_status_display()}.")
    elif action == "archive":
        # Historical addresses are preserved, never deleted, so old
        # correspondence and documents stay attributable.
        record.status = InstitutionalEmail.Status.ARCHIVED
        record.is_primary = False
        record.archived_at = timezone.now()
        record.save()
        messages.success(request, f"{record.address} archived.")
    else:
        raise Http404("Unknown email action.")

    log_activity(request=request, user=request.user, action=AuditLog.Action.UPDATE,
                 module=AuditLog.Module.AUTH, entity="Institutional Email",
                 entity_id=record.pk,
                 description=f"Email '{record.address}' action '{action}' → "
                             f"{record.get_status_display()}.")
    return redirect("university:email_accounts")


# ==============================================================================
# 8. LOGIN & SECURITY
# ==============================================================================

@login_required
@permission_required("users.view_login_history")
def login_history(request):
    records = LoginRecord.objects.select_related("user").all()
    outcome = request.GET.get("outcome", "").strip()
    q = request.GET.get("q", "").strip()
    user_type = request.GET.get("user_type", "").strip()
    days = request.GET.get("days", "").strip()

    if outcome == "success":
        records = records.filter(success=True)
    elif outcome == "failed":
        records = records.filter(success=False)
    if q:
        records = records.filter(Q(username_attempted__icontains=q)
                                 | Q(ip_address__icontains=q))
    if user_type:
        records = records.filter(user_type=user_type)
    if days.isdigit():
        records = records.filter(login_at__gte=timezone.now() - timedelta(days=int(days)))

    now = timezone.now()
    return render(request, "identity/login_history.html", {
        "page_obj": _paginate(request, records, default_per_page=50),
        "outcome": outcome, "q": q, "user_type": user_type, "days": days,
        "user_types": UserType.choices,
        "total_success": LoginRecord.objects.filter(success=True).count(),
        "total_failed": LoginRecord.objects.filter(success=False).count(),
        "failed_24h": LoginRecord.objects.filter(
            success=False, login_at__gte=now - timedelta(hours=24)).count(),
        "locked_now": UserAccount.objects.filter(locked_until__gt=now).count(),
    })


@login_required
@permission_required("users.view_login_history")
def login_security(request):
    """Lockouts, security events and live session overview in one console."""
    now = timezone.now()
    locked = UserAccount.objects.filter(
        Q(locked_until__gt=now) | Q(status=AccountStatus.LOCKED)).select_related("user")
    security_actions = AuditLog.objects.filter(module=AuditLog.Module.AUTH).order_by("-timestamp")

    return render(request, "identity/login_security.html", {
        "locked_accounts": locked,
        "page_obj": _paginate(request, security_actions, default_per_page=40),
        "policy": get_password_policy(),
        "recent_failures": LoginRecord.objects.filter(success=False)[:15],
        "flagged_password_change": UserAccount.objects.filter(
            must_change_password=True).select_related("user")[:25],
        "mfa_enabled_count": UserAccount.objects.filter(mfa_enabled=True).count(),
    })


@login_required
@permission_required("users.view_login_history")
def user_activity(request):
    """
    User activity, read from the existing central audit trail.

    This module deliberately does not keep a second activity log — it filters
    the one the whole system already writes to.
    """
    logs = AuditLog.objects.select_related("user").order_by("-timestamp")
    q = request.GET.get("q", "").strip()
    module = request.GET.get("module", "").strip()
    action = request.GET.get("action", "").strip()
    user_id = request.GET.get("user", "").strip()

    if q:
        logs = logs.filter(Q(user_display__icontains=q) | Q(description__icontains=q)
                           | Q(entity__icontains=q))
    if module:
        logs = logs.filter(module=module)
    if action:
        logs = logs.filter(action=action)
    if user_id.isdigit():
        logs = logs.filter(user_id=int(user_id))

    return render(request, "identity/user_activity.html", {
        "page_obj": _paginate(request, logs, default_per_page=50),
        "q": q, "module": module, "action": action, "user_id": user_id,
        "modules": AuditLog.Module.choices, "actions": AuditLog.Action.choices,
    })


# ==============================================================================
# 9. BULK OPERATIONS
# ==============================================================================

@login_required
@permission_required("users.bulk_operations")
def bulk_operations(request):
    return render(request, "identity/bulk_operations.html", {
        "recent_batches": UserImportBatch.objects.select_related("run_by")[:10],
        "student_columns": identity_io.STUDENT_IMPORT_COLUMNS,
        "staff_columns": identity_io.STAFF_IMPORT_COLUMNS,
        "user_types": UserType.choices,
        "groups": UserGroup.objects.order_by("precedence", "name"),
    })


@login_required
@permission_required("users.bulk_operations")
def bulk_import_template(request, user_type, fmt):
    if user_type not in (UserType.STUDENT, UserType.STAFF):
        raise Http404("Unknown template type.")
    stamp = timezone.now().strftime("%Y%m%d")
    if fmt == "csv":
        data = identity_io.import_template_csv(user_type)
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = \
            f'attachment; filename="{user_type.lower()}_user_import_{stamp}.csv"'
        return response
    if fmt in ("excel", "xlsx"):
        data = identity_io.import_template_excel(
            user_type, site_name=get_branding()["site_name"])
        response = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = \
            f'attachment; filename="{user_type.lower()}_user_import_{stamp}.xlsx"'
        return response
    raise Http404("Unsupported template format.")


@login_required
@permission_required("users.bulk_operations")
@require_POST
def bulk_import_preview(request):
    """Validate an upload and stage the result; nothing is written yet."""
    upload = request.FILES.get("file")
    user_type = request.POST.get("user_type") or UserType.STUDENT
    if not upload:
        messages.error(request, "Choose a CSV or Excel file to upload.")
        return redirect("university:bulk_operations")

    rows, error = identity_io.parse_import_file(upload, user_type)
    if error:
        messages.error(request, error)
        return redirect("university:bulk_operations")

    preview = validate_import_rows(rows, user_type)
    request.session[IMPORT_SESSION_KEY] = {
        "user_type": user_type, "filename": upload.name, "rows": preview,
    }
    return render(request, "identity/bulk_import_preview.html", {
        "preview": preview, "user_type": user_type, "filename": upload.name,
        "create_count": sum(1 for r in preview if r["outcome"] == "CREATE"),
        "skip_count": sum(1 for r in preview if r["outcome"] == "SKIP"),
        "error_count": sum(1 for r in preview if r["outcome"] == "ERROR"),
    })


@login_required
@permission_required("users.bulk_operations")
@require_POST
def bulk_import_commit(request):
    staged = request.session.get(IMPORT_SESSION_KEY)
    if not staged:
        messages.error(request, "The import preview has expired. Upload the file again.")
        return redirect("university:bulk_operations")

    batch = commit_import(staged["rows"], staged["user_type"], actor=request.user,
                          request=request, notify=bool(request.POST.get("send_notification")),
                          filename=staged.get("filename", ""))
    request.session.pop(IMPORT_SESSION_KEY, None)

    level = messages.success if batch.error_count == 0 else messages.warning
    level(request, f"Import finished: {batch.created_count} created, "
                   f"{batch.skipped_count} skipped, {batch.error_count} failed.")
    return redirect("university:bulk_import_report", pk=batch.pk)


@login_required
@permission_required("users.bulk_operations")
def bulk_import_report(request, pk):
    batch = get_object_or_404(UserImportBatch, pk=pk)
    return render(request, "identity/bulk_import_report.html", {
        "batch": batch, "rows": batch.report.get("rows", [])})


@login_required
@permission_required("users.bulk_operations")
@require_POST
def bulk_user_action(request):
    """Bulk password, email and status operations over a selected set of users."""
    ids = request.POST.getlist("user_ids")
    operation = request.POST.get("operation", "")
    users = list(User.objects.filter(pk__in=ids))

    if not users:
        messages.error(request, "Select at least one account.")
    elif operation in ("SEND_RESET_LINK", "FORCE_CHANGE", "UNLOCK"):
        succeeded, failed = bulk_password_operation(users, operation, actor=request.user,
                                                    request=request)
        messages.success(request, f"{operation.replace('_', ' ').title()}: "
                                  f"{len(succeeded)} succeeded, {len(failed)} failed.")
    elif operation == "GENERATE_EMAIL":
        created, skipped = bulk_generate_emails(users, actor=request.user, request=request,
                                                notify=bool(request.POST.get("send_notification")))
        messages.success(request, f"Generated {len(created)} institutional addresses "
                                  f"({len(skipped)} already had one).")
    elif operation in ("ACTIVATE", "SUSPEND", "DISABLE"):
        target = {"ACTIVATE": AccountStatus.ACTIVE, "SUSPEND": AccountStatus.SUSPENDED,
                  "DISABLE": AccountStatus.DISABLED}[operation]
        changed = 0
        for user in users:
            if user == request.user or (user.is_superuser and not request.user.is_superuser):
                continue
            ok, _message, _account = set_account_status(
                user, target, actor=request.user, request=request,
                reason=request.POST.get("reason", "Bulk status operation"))
            changed += int(ok)
        messages.success(request, f"Updated {changed} account(s) to {target}.")
    else:
        messages.error(request, "Unknown bulk operation.")

    return redirect(request.POST.get("next") or "university:user_list")


# ==============================================================================
# 10. EXPORT
# ==============================================================================

@login_required
@permission_required("users.export")
def user_export(request, fmt):
    """Export the current filtered directory. Never includes credentials."""
    queryset, filters, _sort = _filtered_users(request)
    branding = get_branding()
    stamp = timezone.now().strftime("%Y%m%d_%H%M")

    log_activity(request=request, user=request.user, action=AuditLog.Action.EXPORT,
                 module=AuditLog.Module.AUTH, entity="User Directory",
                 description=f"Exported {queryset.count()} user records as {fmt.upper()}.")

    fmt = fmt.lower()
    if fmt == "csv":
        response = HttpResponse(identity_io.export_users_csv(queryset),
                                content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="users_{stamp}.csv"'
        return response

    if fmt in ("excel", "xlsx"):
        response = HttpResponse(
            identity_io.export_users_excel(queryset, site_name=branding["site_name"]),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="users_{stamp}.xlsx"'
        return response

    if fmt == "pdf":
        active = [f"{k}={v}" for k, v in filters.items() if v]
        data = identity_io.export_users_pdf(
            queryset, site_name=branding["site_name"], logo_path=branding["logo_path"],
            filter_text=", ".join(active) if active else None)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = _pdf_disposition(request, f"users_{stamp}.pdf")
        return present_pdf(request, response)

    raise Http404(f"Unsupported export format: {fmt}")


# ==============================================================================
# 11. SETTINGS
# ==============================================================================

SETTING_GROUPS = {
    "account": ["auto_create_student_accounts", "auto_activate_student_accounts",
                "auto_create_staff_accounts", "auto_activate_staff_accounts",
                "auto_generate_email", "username_student_format", "username_staff_format",
                "username_prefix", "username_suffix", "username_separator", "username_case",
                "username_max_length", "username_allowed_characters",
                "username_sequential_numbering", "username_sequence_padding"],
    "password": ["password_min_length", "password_max_length", "password_require_uppercase",
                 "password_require_lowercase", "password_require_number",
                 "password_require_special", "password_history_depth", "password_expiry_days",
                 "temporary_password_expiry_hours", "password_reset_token_minutes",
                 "activation_token_minutes", "generated_password_length"],
    "email": ["email_enabled", "email_provider", "email_host", "email_port",
              "email_encryption", "email_host_user", "email_host_password",
              "email_from_name", "email_from_address", "email_reply_to",
              "email_timeout_seconds", "email_provisioning_enabled", "email_student_domain",
              "email_staff_domain", "email_admin_domain", "email_student_format",
              "email_staff_format", "email_admin_format", "email_case", "site_base_url",
              "notify_account_created", "notify_password_reset", "notify_password_changed",
              "notify_account_status"],
    "security": ["max_login_attempts", "account_lock_duration_minutes",
                 "login_progressive_delay", "session_timeout_minutes",
                 "force_logout_after_password_reset"],
}

BOOLEAN_SETTINGS = {
    "auto_create_student_accounts", "auto_activate_student_accounts",
    "auto_create_staff_accounts", "auto_activate_staff_accounts", "auto_generate_email",
    "username_sequential_numbering", "password_require_uppercase",
    "password_require_lowercase", "password_require_number", "password_require_special",
    "email_enabled", "email_provisioning_enabled", "notify_account_created",
    "notify_password_reset", "notify_password_changed", "notify_account_status",
    "login_progressive_delay", "force_logout_after_password_reset",
}


@login_required
@permission_required("users.manage_settings")
def user_settings(request):
    """
    User Management settings.

    Provider secrets are write-only: the stored value is never rendered, and an
    empty submission leaves the existing secret untouched.
    """
    from university.email_services import SECRET_SETTING_KEYS

    tab = request.GET.get("tab", "account")
    if tab not in SETTING_GROUPS:
        tab = "account"

    if request.method == "POST":
        section = request.POST.get("section", tab)
        for key in SETTING_GROUPS.get(section, []):
            if key in BOOLEAN_SETTINGS:
                set_setting(key, key in request.POST, user=request.user, request=request)
                continue
            if key not in request.POST:
                continue
            value = request.POST[key].strip()
            if key in SECRET_SETTING_KEYS and not value:
                continue  # blank means "leave the stored secret alone"
            set_setting(key, value, user=request.user, request=request)

        log_activity(request=request, user=request.user, action=AuditLog.Action.CONFIG_CHANGE,
                     module=AuditLog.Module.AUTH, entity="User Management Settings",
                     description=f"Updated '{section}' user management settings.")
        messages.success(request, "Settings saved.")
        return redirect(f"/system-admin/users/settings/?tab={section}")

    values = {}
    for group_keys in SETTING_GROUPS.values():
        for key in group_keys:
            values[key] = "" if key in SECRET_SETTING_KEYS else get_setting(key)

    return render(request, "identity/settings.html", {
        "tab": tab,
        "values": values,
        "has_email_secret": bool(get_setting("email_host_password")),
        "providers": Provider.CHOICES,
        "policy": get_password_policy(),
    })


@login_required
@permission_required("users.manage_settings")
@require_POST
def send_test_email_view(request):
    address = request.POST.get("test_email", "").strip()
    if not address:
        messages.error(request, "Enter an address to send the test message to.")
    else:
        ok, message = send_test_email(address, actor=request.user)
        (messages.success if ok else messages.error)(
            request, f"Test email to {address}: {message}")
        log_activity(request=request, user=request.user, action=AuditLog.Action.CONFIG_CHANGE,
                     module=AuditLog.Module.AUTH, entity="Email Configuration",
                     description=f"Sent test email to {address} — "
                                 f"{'delivered' if ok else 'failed'}.")
    return redirect("/system-admin/users/settings/?tab=email")


@login_required
@permission_required("users.view")
def roles_permissions_redirect(request):
    """
    Roles & Permissions live in the existing RBAC console.

    User Management links to it rather than shipping a second permissions editor,
    which is what keeps one permission model for the whole system.
    """
    return redirect("university:staff_permissions_dashboard")
