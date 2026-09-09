from django.conf import settings
from django.db import models


class ModuleStatus(models.TextChoices):
    ENABLED = "ENABLED", "Active / Enabled"
    MAINTENANCE = "MAINTENANCE", "Under Maintenance"
    COMING_SOON = "COMING_SOON", "Coming Soon..."
    DISABLED = "DISABLED", "Disabled / Inactive"


class SystemModule(models.Model):
    """
    Top-level operational subsystem in the University Management System (Level 1 Control).
    E.g. Academics, Examinations, Finance, Admissions, Students, System Admin.
    """
    code = models.SlugField(max_length=60, unique=True, db_index=True,
                            help_text="Unique machine identifier e.g. 'examinations', 'finance'")
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=60, default="Academic",
                                help_text="Grouping e.g. 'Academic', 'Administrative', 'Finance', 'Student Services', 'Governance & Security'")
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=60, default="fa-solid fa-cube",
                            help_text="FontAwesome icon class e.g. 'fa-solid fa-file-pen'")
    status = models.CharField(max_length=20, choices=ModuleStatus.choices, default=ModuleStatus.ENABLED, db_index=True)
    status_message = models.TextField(blank=True, default="",
                                      help_text="Custom message displayed to users when inactive, under maintenance, or coming soon")
    is_critical = models.BooleanField(default=False,
                                      help_text="Protected core system module that cannot be disabled (e.g. Auth, System Admin)")
    sort_order = models.IntegerField(default=0)
    target_roles = models.JSONField(default=list, blank=True,
                                    help_text="List of target user roles allowed access (e.g. ['ADMIN', 'FACULTY', 'STUDENT']) or empty for all")
    route_prefixes = models.JSONField(default=list, blank=True,
                                      help_text="List of URL namespaces or path prefixes associated with this module")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "System Module"
        verbose_name_plural = "System Modules"

    def __str__(self):
        return f"{self.name} ({self.code}) [{self.get_status_display()}]"

    @property
    def is_active(self):
        return self.status == ModuleStatus.ENABLED

    @property
    def submodules_count(self):
        return self.submodules.count()

    @property
    def active_submodules_count(self):
        return self.submodules.filter(status=ModuleStatus.ENABLED).count()

    @property
    def features_count(self):
        return SystemFeature.objects.filter(submodule__module=self).count()

    @property
    def active_features_count(self):
        return SystemFeature.objects.filter(submodule__module=self, status=ModuleStatus.ENABLED).count()


class SystemSubmodule(models.Model):
    """
    Subsystem operational unit under a SystemModule (Level 2 Control).
    E.g. Unit Registration under Academics, CAT/Marks Entry under Examinations.
    """
    module = models.ForeignKey(SystemModule, on_delete=models.CASCADE, related_name="submodules")
    code = models.SlugField(max_length=60, unique=True, db_index=True,
                            help_text="Unique machine identifier e.g. 'unit_registration', 'marks_entry'")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=60, default="fa-solid fa-circle-dot")
    status = models.CharField(max_length=20, choices=ModuleStatus.choices, default=ModuleStatus.ENABLED, db_index=True)
    status_message = models.TextField(blank=True, default="")
    is_critical = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    route_names = models.JSONField(default=list, blank=True,
                                   help_text="List of Django url_names (e.g. ['admin_unit_registrations', 'student_register_units'])")
    path_patterns = models.JSONField(default=list, blank=True,
                                     help_text="Path prefixes (e.g. ['/academics/register/', '/manage/academics/registrations/'])")
    target_roles = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["module", "sort_order", "name"]
        verbose_name = "System Submodule"
        verbose_name_plural = "System Submodules"

    def __str__(self):
        return f"{self.module.name} → {self.name} ({self.code})"

    @property
    def is_active(self):
        return self.module.is_active and self.status == ModuleStatus.ENABLED

    @property
    def features_count(self):
        return self.features.count()

    @property
    def active_features_count(self):
        return self.features.filter(status=ModuleStatus.ENABLED).count()


class SystemFeature(models.Model):
    """
    Granular action or workflow capability within a Submodule (Level 3 Control).
    E.g. Deferment Request under Student Requests, CAT Marks Entry under Examinations.
    """
    submodule = models.ForeignKey(SystemSubmodule, on_delete=models.CASCADE, related_name="features")
    code = models.SlugField(max_length=60, unique=True, db_index=True,
                            help_text="Unique machine identifier e.g. 'deferment', 'sick_leave', 'publish_results'")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=ModuleStatus.choices, default=ModuleStatus.ENABLED, db_index=True)
    status_message = models.TextField(blank=True, default="")
    is_critical = models.BooleanField(default=False)
    action_code = models.CharField(max_length=60, blank=True, default="",
                                   help_text="Optional identifier checked in views/services e.g. 'DEFERMENT'")
    route_names = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["submodule", "name"]
        verbose_name = "System Feature"
        verbose_name_plural = "System Features"

    def __str__(self):
        return f"{self.submodule.module.name} → {self.submodule.name} → {self.name}"

    @property
    def is_active(self):
        return self.submodule.is_active and self.status == ModuleStatus.ENABLED


class ModuleDependency(models.Model):
    """
    Expresses dependencies between modules.
    If source_module depends on target_module, target_module cannot be disabled while source_module is active.
    """
    class DependencyType(models.TextChoices):
        REQUIRED = "REQUIRED", "Strict Prerequisite (Required)"
        RECOMMENDED = "RECOMMENDED", "Recommended Dependency"

    source_module = models.ForeignKey(SystemModule, on_delete=models.CASCADE, related_name="dependencies_as_source")
    target_module = models.ForeignKey(SystemModule, on_delete=models.CASCADE, related_name="dependencies_as_target")
    dependency_type = models.CharField(max_length=20, choices=DependencyType.choices, default=DependencyType.REQUIRED)
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("source_module", "target_module")]
        verbose_name = "Module Dependency"
        verbose_name_plural = "Module Dependencies"

    def __str__(self):
        return f"{self.source_module.name} requires {self.target_module.name} ({self.dependency_type})"
