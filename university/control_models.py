"""Persistent control state. Restrictions overlay module settings; never overwrite them."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class SystemRestriction(models.Model):
    class Kind(models.TextChoices):
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'
        EMERGENCY_MAINTENANCE = 'EMERGENCY_MAINTENANCE', 'Emergency maintenance'
        MODULE = 'MODULE', 'Module lockdown'
        ROLE = 'ROLE', 'Role lockdown'
        READ_ONLY = 'READ_ONLY', 'Read only'
        LOCKDOWN = 'LOCKDOWN', 'Full lockdown'
        EMERGENCY = 'EMERGENCY', 'Emergency lockdown'
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    reason = models.TextField(help_text='Internal reason; visible only to authorized administrators.')
    public_message = models.TextField(default='The system is temporarily unavailable. Please try again later.')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    starts_at = models.DateTimeField(default=timezone.now, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    time_zone = models.CharField(max_length=64, default='Africa/Nairobi')
    modules = models.ManyToManyField('SystemModule', blank=True)
    submodules = models.ManyToManyField('SystemSubmodule', blank=True)
    features = models.ManyToManyField('SystemFeature', blank=True)
    roles = models.JSONField(default=list, blank=True)
    allow_bypass = models.BooleanField(default=True, help_text='Allow explicitly permitted bypass users. Incident managers always retain recovery access.')
    session_policy = models.CharField(max_length=20, default='BLOCK', choices=[('BLOCK', 'Block requests'), ('LOGOUT', 'Force logout'), ('READ_ONLY', 'Existing sessions read only')])
    warning_minutes = models.JSONField(default=list, blank=True)
    notification_message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    activated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notes = models.TextField(blank=True)
    affected_user_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F('starts_at')), name='restriction_valid_window')]
    def __str__(self):
        return self.title
    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'Completion must be after the start.'})
        if self.status == self.Status.SCHEDULED and not self.ends_at:
            raise ValidationError({'ends_at': 'Scheduled maintenance requires an end time.'})
    @property
    def duration(self):
        return (self.completed_at or timezone.now()) - self.activated_at if self.activated_at else None


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=150)
    body = models.TextField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return self.name


class MessageDelivery(models.Model):
    message = models.ForeignKey('Notice', on_delete=models.CASCADE, related_name='deliveries')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    method = models.CharField(max_length=12, default='IN_APP')
    status = models.CharField(max_length=12, default='PENDING', choices=[('PENDING','Pending'),('SENT','Sent'),('DELIVERED','Delivered'),('FAILED','Failed'),('EXPIRED','Expired')])
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['message','recipient','method'], name='unique_system_message_delivery')]


class ControlNotification(models.Model):
    restriction = models.ForeignKey(SystemRestriction, on_delete=models.PROTECT)
    event = models.CharField(max_length=40)
    message = models.OneToOneField('Notice', null=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['restriction','event'], name='unique_control_notification')]


class ControlHeartbeat(models.Model):
    key = models.CharField(max_length=30, primary_key=True)
    last_success_at = models.DateTimeField(default=timezone.now)
