"""
Go-Live Command Center: readiness tracking for production launch.

Two models:
- GoLiveReadiness: one row per readiness area (Data Migration, Academic
  Rules, Finance, Security, UAT, ...), each carrying a current status,
  owner, notes and sign-off trail.
- GoLiveIssue: the warning/issue ledger. Every issue is tracked through its
  full lifecycle (Severity -> Description -> Impact -> Owner -> Due Date ->
  Resolution -> Evidence -> Retest -> Final Status) so nothing raised here
  can be silently ignored.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class GoLiveCategory(models.TextChoices):
    FUNCTIONAL_TESTING = "FUNCTIONAL_TESTING", "Functional Testing"
    UI_UX = "UI_UX", "UI/UX Quality"
    ACCESSIBILITY = "ACCESSIBILITY", "Accessibility"
    SECURITY = "SECURITY", "Security & Compliance"
    DATA_PROTECTION = "DATA_PROTECTION", "Data Protection & Privacy"
    DATA_MIGRATION = "DATA_MIGRATION", "Data Migration & Quality"
    ACADEMIC_RULES = "ACADEMIC_RULES", "Academic Rules & Business Logic"
    FINANCE_PAYMENTS = "FINANCE_PAYMENTS", "Finance & Payments Reconciliation"
    ROLE_PERMISSIONS = "ROLE_PERMISSIONS", "Role & Permission Matrix"
    INSTITUTIONAL_APPROVAL = "INSTITUTIONAL_APPROVAL", "Institutional Approval"
    INTEGRATIONS = "INTEGRATIONS", "Integrations & Third-Party Dependencies"
    PERFORMANCE = "PERFORMANCE", "Performance & Scalability"
    BACKUP = "BACKUP", "Backup"
    DISASTER_RECOVERY = "DISASTER_RECOVERY", "Disaster Recovery"
    BUSINESS_CONTINUITY = "BUSINESS_CONTINUITY", "Business Continuity"
    MONITORING = "MONITORING", "Monitoring & Observability"
    UAT = "UAT", "User Acceptance Testing"
    TRAINING = "TRAINING", "Training"
    DOCUMENTATION = "DOCUMENTATION", "Documentation"
    SUPPORT = "SUPPORT", "Support & Helpdesk"


class ReadinessStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not Started"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    PASS_ = "PASS", "Pass"
    WARN = "WARN", "Warning"
    FAIL = "FAIL", "Fail"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Not Applicable"


class GoLiveReadiness(models.Model):
    """One row per readiness category -- the command center's status board."""

    category = models.CharField(max_length=40, choices=GoLiveCategory.choices, unique=True)
    status = models.CharField(max_length=20, choices=ReadinessStatus.choices, default=ReadinessStatus.NOT_STARTED)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="The person/office accountable for this area signing off.",
    )
    notes = models.TextField(blank=True)
    evidence_url = models.CharField(max_length=500, blank=True, help_text="Link/reference to the sign-off document, test report, or reconciliation record.")
    signed_off_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    signed_off_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["category"]

    def __str__(self):
        return self.get_category_display()

    @property
    def is_signed_off(self):
        return bool(self.signed_off_at)


class IssueSeverity(models.TextChoices):
    CRITICAL = "CRITICAL", "Critical"
    HIGH = "HIGH", "High"
    MEDIUM = "MEDIUM", "Medium"
    LOW = "LOW", "Low / Warning"


class IssueStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    RESOLVED = "RESOLVED", "Resolved"
    RETESTED = "RETESTED", "Retested"
    CLOSED = "CLOSED", "Closed"


class GoLiveIssue(models.Model):
    """A single readiness issue/warning, tracked through its full lifecycle."""

    category = models.CharField(max_length=40, choices=GoLiveCategory.choices)
    severity = models.CharField(max_length=10, choices=IssueSeverity.choices, default=IssueSeverity.MEDIUM)
    status = models.CharField(max_length=15, choices=IssueStatus.choices, default=IssueStatus.OPEN)

    description = models.TextField()
    impact = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="golive_issues_owned",
    )
    due_date = models.DateField(null=True, blank=True)

    resolution = models.TextField(blank=True)
    evidence_url = models.CharField(max_length=500, blank=True)
    retest_result = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="golive_issues_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.description[:60]}"

    @property
    def is_overdue(self):
        return bool(
            self.due_date
            and self.due_date < timezone.now().date()
            and self.status not in (IssueStatus.CLOSED, IssueStatus.RETESTED)
        )

    @property
    def is_blocking(self):
        """Critical/High issues that are not yet closed block go-live sign-off."""
        return self.severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH) and self.status not in (
            IssueStatus.CLOSED, IssueStatus.RETESTED,
        )
