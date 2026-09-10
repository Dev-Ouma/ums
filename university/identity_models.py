"""
Central Identity models for the University Management System.

These extend the single ``accounts.User`` entity rather than introducing a
parallel account store: every model here hangs off ``settings.AUTH_USER_MODEL``
so that students, staff and administrators share one authentication surface.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserType(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    STAFF = "STAFF", "Staff / Faculty"
    ADMIN = "ADMIN", "Administrator"
    OTHER = "OTHER", "Other"


class AccountStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    PENDING = "PENDING", "Pending Activation"
    SUSPENDED = "SUSPENDED", "Suspended"
    LOCKED = "LOCKED", "Locked"
    DISABLED = "DISABLED", "Disabled"
    EXPIRED = "EXPIRED", "Expired"
    ARCHIVED = "ARCHIVED", "Archived"


# Statuses that permit authentication. Everything else is a hard stop enforced
# by the authentication backend, not by hiding the login form.
AUTHENTICABLE_STATUSES = {AccountStatus.ACTIVE}

# Administrators may only move an account between states that make sense; this
# prevents e.g. resurrecting an archived identity straight into ACTIVE without
# an explicit restore.
ALLOWED_STATUS_TRANSITIONS = {
    AccountStatus.PENDING: {AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.ARCHIVED, AccountStatus.EXPIRED},
    AccountStatus.ACTIVE: {AccountStatus.INACTIVE, AccountStatus.SUSPENDED, AccountStatus.LOCKED,
                           AccountStatus.DISABLED, AccountStatus.EXPIRED, AccountStatus.ARCHIVED},
    AccountStatus.INACTIVE: {AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.ARCHIVED, AccountStatus.EXPIRED},
    AccountStatus.SUSPENDED: {AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.ARCHIVED},
    AccountStatus.LOCKED: {AccountStatus.ACTIVE, AccountStatus.SUSPENDED, AccountStatus.DISABLED, AccountStatus.ARCHIVED},
    AccountStatus.DISABLED: {AccountStatus.ACTIVE, AccountStatus.ARCHIVED},
    AccountStatus.EXPIRED: {AccountStatus.ACTIVE, AccountStatus.ARCHIVED, AccountStatus.DISABLED},
    AccountStatus.ARCHIVED: {AccountStatus.ACTIVE, AccountStatus.DISABLED},
}


class UserGroup(models.Model):
    """
    A scalable administration bucket that confers StaffRoles and permissions.

    Groups never replace the RBAC engine — they are a convenience layer that
    resolves down to the same ``StaffRole`` / ``SystemPermission`` objects, so
    permission evaluation stays in one place.
    """
    code = models.SlugField(max_length=60, unique=True, db_index=True)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, default="")
    user_type = models.CharField(max_length=15, choices=UserType.choices, default=UserType.STAFF)
    color = models.CharField(max_length=20, default="#6C5CE7")
    icon = models.CharField(max_length=60, default="fa-solid fa-users-rectangle")
    # Lower number wins when two groups disagree. Documented precedence keeps
    # multi-group membership from producing undefined access.
    precedence = models.PositiveSmallIntegerField(
        default=100, help_text="Lower value = higher priority when groups conflict.")
    roles = models.ManyToManyField("university.StaffRole", blank=True, related_name="user_groups")
    permissions = models.ManyToManyField("university.SystemPermission", blank=True, related_name="user_groups")
    is_system_group = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["precedence", "name"]

    @property
    def member_count(self):
        return self.memberships.count()

    def __str__(self):
        return self.name


class UserGroupMembership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="group_memberships")
    group = models.ForeignKey(UserGroup, on_delete=models.CASCADE, related_name="memberships")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["group__precedence", "group__name"]
        unique_together = [("user", "group")]

    def __str__(self):
        return f"{self.user.username} ∈ {self.group.name}"


class UserAccount(models.Model):
    """
    The identity/security envelope around a ``User``.

    ``User`` keeps authentication credentials and profile fields; this record
    owns lifecycle status, lockout counters and password metadata so those
    concerns are not smeared across every module.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="account")
    user_type = models.CharField(max_length=15, choices=UserType.choices,
                                 default=UserType.STUDENT, db_index=True)
    status = models.CharField(max_length=15, choices=AccountStatus.choices,
                              default=AccountStatus.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=255, blank=True, default="")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name="+")

    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    password_expires_at = models.DateTimeField(null=True, blank=True)

    failed_login_attempts = models.PositiveIntegerField(default=0)
    last_failed_login_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    lock_reason = models.CharField(max_length=255, blank=True, default="")

    activation_date = models.DateField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    mfa_enabled = models.BooleanField(default=False)
    mfa_method = models.CharField(max_length=30, blank=True, default="",
                                  help_text="e.g. TOTP, EMAIL, SMS — reserved for future MFA rollout.")

    campus = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def effective_status(self):
        """Status after applying time-based lock and expiry, without a write."""
        if self.is_expired and self.status not in (AccountStatus.ARCHIVED, AccountStatus.DISABLED):
            return AccountStatus.EXPIRED
        if self.is_locked:
            return AccountStatus.LOCKED
        return self.status

    @property
    def can_authenticate(self):
        return self.effective_status in AUTHENTICABLE_STATUSES

    @property
    def status_color(self):
        return {
            AccountStatus.ACTIVE: "success",
            AccountStatus.PENDING: "info",
            AccountStatus.INACTIVE: "secondary",
            AccountStatus.SUSPENDED: "warning",
            AccountStatus.LOCKED: "danger",
            AccountStatus.DISABLED: "dark",
            AccountStatus.EXPIRED: "warning",
            AccountStatus.ARCHIVED: "secondary",
        }.get(self.effective_status, "secondary")

    def __str__(self):
        return f"{self.user.username} [{self.effective_status}]"


class PasswordHistoryEntry(models.Model):
    """One-way hashes of previously used passwords — never plaintext."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="password_history")
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Password history entries"

    def __str__(self):
        return f"{self.user.username} @ {self.created_at:%Y-%m-%d %H:%M}"


class PasswordResetToken(models.Model):
    """
    Single-use, expiring credential-recovery token.

    Only the SHA-256 digest is stored; the plaintext token exists just long
    enough to be placed in an email link, so a database read cannot be replayed
    as a password reset.
    """
    class Purpose(models.TextChoices):
        RESET = "RESET", "Password Reset"
        ACTIVATION = "ACTIVATION", "Account Activation"
        ADMIN_RESET = "ADMIN_RESET", "Administrator-Initiated Reset"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="reset_tokens")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    purpose = models.CharField(max_length=15, choices=Purpose.choices, default=Purpose.RESET)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_valid(self):
        return not (self.used_at or self.invalidated_at or self.is_expired)

    def __str__(self):
        return f"{self.purpose} token for {self.user.username}"


class LoginRecord(models.Model):
    """
    Authentication attempt ledger. Never stores passwords — only the outcome,
    a coarse failure category, and request provenance.
    """
    class Failure(models.TextChoices):
        BAD_CREDENTIALS = "BAD_CREDENTIALS", "Invalid username or password"
        INACTIVE = "INACTIVE", "Account not active"
        LOCKED = "LOCKED", "Account locked"
        SUSPENDED = "SUSPENDED", "Account suspended"
        DISABLED = "DISABLED", "Account disabled"
        EXPIRED = "EXPIRED", "Account expired"
        UNKNOWN_USER = "UNKNOWN_USER", "No such account"
        BLOCKED = "BLOCKED", "Blocked by system control"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="login_records")
    username_attempted = models.CharField(max_length=150, db_index=True)
    user_type = models.CharField(max_length=15, choices=UserType.choices, blank=True, default="")
    success = models.BooleanField(default=False, db_index=True)
    failure_reason = models.CharField(max_length=20, choices=Failure.choices, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    device_type = models.CharField(max_length=30, blank=True, default="")
    session_key = models.CharField(max_length=64, blank=True, default="")
    mfa_used = models.BooleanField(default=False)
    login_at = models.DateTimeField(default=timezone.now, db_index=True)
    logout_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-login_at"]
        indexes = [models.Index(fields=["-login_at", "success"])]

    @property
    def duration(self):
        if self.logout_at:
            return self.logout_at - self.login_at
        return None

    def __str__(self):
        outcome = "OK" if self.success else f"FAIL/{self.failure_reason}"
        return f"{self.username_attempted} {outcome} @ {self.login_at:%Y-%m-%d %H:%M}"


class InstitutionalEmail(models.Model):
    """
    The authoritative institutional email identity for a user.

    Modules must read the address from here rather than keeping their own copy,
    which is why historical addresses are archived instead of overwritten.
    """
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROVISIONING = "PROVISIONING", "Provisioning"
        ACTIVE = "ACTIVE", "Active"
        FAILED = "FAILED", "Provisioning Failed"
        SUSPENDED = "SUSPENDED", "Suspended"
        DISABLED = "DISABLED", "Disabled"
        ARCHIVED = "ARCHIVED", "Archived"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="institutional_emails")
    address = models.EmailField(unique=True, db_index=True)
    kind = models.CharField(max_length=15, choices=UserType.choices, default=UserType.STUDENT)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    provider = models.CharField(max_length=40, blank=True, default="")
    provider_reference = models.CharField(max_length=120, blank=True, default="")
    provider_message = models.TextField(blank=True, default="")
    provisioned_at = models.DateTimeField(null=True, blank=True)
    is_primary = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "-created_at"]

    @property
    def status_color(self):
        return {
            self.Status.ACTIVE: "success",
            self.Status.PENDING: "info",
            self.Status.PROVISIONING: "info",
            self.Status.FAILED: "danger",
            self.Status.SUSPENDED: "warning",
            self.Status.DISABLED: "dark",
            self.Status.ARCHIVED: "secondary",
        }.get(self.status, "secondary")

    def __str__(self):
        return self.address


class EmailDeliveryRecord(models.Model):
    """Outbound notification ledger — proof of what the system tried to send."""
    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        SKIPPED = "SKIPPED", "Skipped"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="email_deliveries")
    to_address = models.EmailField()
    subject = models.CharField(max_length=255)
    template_code = models.CharField(max_length=60, blank=True, default="")
    provider = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.to_address} · {self.subject} [{self.status}]"


class UserImportBatch(models.Model):
    """Audit envelope for a bulk user import run."""
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Previewed"
        COMPLETED = "COMPLETED", "Completed"
        PARTIAL = "PARTIAL", "Completed With Errors"
        FAILED = "FAILED", "Failed"

    user_type = models.CharField(max_length=15, choices=UserType.choices, default=UserType.STUDENT)
    filename = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREVIEW)
    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=dict, blank=True)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_user_type_display()} import · {self.created_count}/{self.total_rows}"
