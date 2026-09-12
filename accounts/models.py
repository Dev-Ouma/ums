import re
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Administrator"
    FACULTY = "FACULTY", "Teaching Staff"
    STUDENT = "STUDENT", "Student"


# Per-role UI theme (drives the colour scheme / design language of each portal).
ROLE_THEMES = {
    Role.ADMIN: {
        "key": "admin",
        "name": "Administrator",
        "primary": "#6C5CE7",
        "primary_dark": "#4834d4",
        "accent": "#e84393",
        "sidebar": "linear-gradient(180deg,#2d2a4a 0%,#3b3564 100%)",
        "surface": "#f4f5fb",
        "gradient": "linear-gradient(135deg,#6C5CE7 0%,#8f7bff 100%)",
        "icon": "fa-user-shield",
    },
    Role.FACULTY: {
        "key": "faculty",
        "name": "Teaching Staff",
        "primary": "#009688",
        "primary_dark": "#00695c",
        "accent": "#ff7043",
        "sidebar": "linear-gradient(180deg,#08312c 0%,#0b4f45 100%)",
        "surface": "#eef7f5",
        "gradient": "linear-gradient(135deg,#009688 0%,#26c6a6 100%)",
        "icon": "fa-chalkboard-user",
    },
    Role.STUDENT: {
        "key": "student",
        "name": "Student",
        "primary": "#0984e3",
        "primary_dark": "#0652a5",
        "accent": "#e17055",
        "sidebar": "linear-gradient(180deg,#0a2540 0%,#123a63 100%)",
        "surface": "#eef4fb",
        "gradient": "linear-gradient(135deg,#0984e3 0%,#48b1f3 100%)",
        "icon": "fa-user-graduate",
    },
}


class User(AbstractUser):
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)
    phone = models.CharField(max_length=20, blank=True, default="0000")
    avatar_url = models.URLField(blank=True)
    # Uploaded avatar takes precedence over avatar_url, which in turn wins over
    # the generated ui-avatars fallback. Keeping all three means existing seeded
    # accounts (which only set avatar_url) keep working untouched.
    avatar_image = models.ImageField(upload_to="avatars/", blank=True, null=True)

    # -- login activity ----------------------------------------------------
    # last_login is maintained by Django itself; these three add the "first
    # access / last access / last IP" picture shown on the account menu.
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    @property
    def theme(self):
        return ROLE_THEMES.get(self.role, ROLE_THEMES[Role.STUDENT])

    @property
    def is_admin_role(self):
        return self.role == Role.ADMIN

    @property
    def is_faculty(self):
        return self.role == Role.FACULTY

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def avatar(self):
        if self.avatar_image:
            return self.avatar_image.url
        if self.avatar_url:
            return self.avatar_url
        seed = (self.display_name or self.username).replace(" ", "+")
        colors = {"ADMIN": "6C5CE7", "FACULTY": "009688", "STUDENT": "0984e3"}
        bg = colors.get(self.role, "6C5CE7")
        return f"https://ui-avatars.com/api/?name={seed}&background={bg}&color=fff&bold=true"

    @property
    def initials(self):
        """Two-letter monogram for the header chip (e.g. "SA")."""
        parts = [p for p in (self.first_name, self.last_name) if p]
        if not parts:
            parts = [p for p in self.username.replace(".", " ").split() if p]
        letters = "".join(p[0] for p in parts[:2])
        return (letters or self.username[:2]).upper()

    @property
    def has_custom_avatar(self):
        return bool(self.avatar_image or self.avatar_url)

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"


class StudentProfile(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DEFERRED = "DEFERRED", "Deferred"
        ON_LEAVE = "ON_LEAVE", "On Sick Leave"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    GENDER = [("M", "Male"), ("F", "Female"), ("O", "Other")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    roll_no = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    program = models.ForeignKey("university.Program", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="students")
    current_semester = models.PositiveSmallIntegerField(default=1)
    gender = models.CharField(max_length=1, choices=GENDER, default="O")
    date_of_birth = models.DateField(null=True, blank=True)
    admission_date = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True, default="Campus Hostel Block")
    guardian_name = models.CharField(max_length=120, blank=True, default="Guardian")
    guardian_relationship = models.CharField(max_length=60, blank=True, default="")
    guardian_phone = models.CharField(max_length=30, blank=True, default="")
    guardian_email = models.EmailField(blank=True, default="")
    guardian_address = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["roll_no"]

    @property
    def year_of_study(self):
        return (self.current_semester + 1) // 2 if self.current_semester else 1

    @property
    def semester(self):
        return ((self.current_semester - 1) % 2) + 1 if self.current_semester else 1

    @property
    def clean_roll_no(self):
        """Alphanumeric registration number without slashes, dashes, or whitespace."""
        return re.sub(r"[^A-Za-z0-9]", "", self.roll_no or "").upper()

    @property
    def virtual_account_number(self):
        """Deterministic unique virtual fee account identifier."""
        return f"ACC{self.id:06d}"

    @property
    def is_active_student(self):
        return self.status == self.Status.ACTIVE

    def get_absolute_url(self):
        return reverse("university:student_detail", args=[self.pk])

    def __str__(self):
        return f"{self.roll_no} - {self.user.display_name}"


class FacultyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="faculty_profile")
    employee_id = models.CharField(max_length=20, unique=True)
    department = models.ForeignKey("university.Department", on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="faculty")
    designation = models.CharField(max_length=80, default="Assistant Professor")
    specialization = models.CharField(max_length=120, blank=True, default="")
    joining_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["employee_id"]
        verbose_name = "Teaching Staff"
        verbose_name_plural = "Teaching Staff"

    def get_absolute_url(self):
        return reverse("university:faculty_detail", args=[self.pk])

    def __str__(self):
        return f"{self.employee_id} - {self.user.display_name}"


class UserSignature(models.Model):
    """
    Centralized official signature profile for a user.
    Maintains active signature asset, title, department, verification status, and versioning.
    """
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active / Approved"
        PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending Verification"
        INACTIVE = "INACTIVE", "Inactive"
        REVOKED = "REVOKED", "Revoked"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="signature")
    signature_image = models.ImageField(upload_to="signatures/users/", null=True, blank=True)
    title = models.CharField(max_length=150, blank=True, default="", help_text="Official designation (e.g. Academic Registrar, Dean)")
    department_or_office = models.CharField(max_length=150, blank=True, default="", help_text="Office/Faculty/Department (e.g. Office of the Registrar)")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_signatures")

    class Meta:
        verbose_name = "User Signature"
        verbose_name_plural = "User Signatures"

    def __str__(self):
        return f"{self.user.display_name} - Signature (v{self.version}) [{self.status}]"

    @property
    def is_usable(self):
        return bool(self.is_active and self.status == self.Status.ACTIVE and self.signature_image)


class UserSignatureHistory(models.Model):
    """
    Immutable archive of historical user signature versions.
    Ensures that past issued documents retain exact historical signature assets.
    """
    signature = models.ForeignKey(UserSignature, on_delete=models.CASCADE, related_name="history")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="signature_history")
    version = models.PositiveIntegerField()
    signature_image = models.ImageField(upload_to="signatures/users/history/")
    title = models.CharField(max_length=150, blank=True, default="")
    department_or_office = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=25)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    change_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        verbose_name = "User Signature History"
        verbose_name_plural = "User Signature Histories"

    def __str__(self):
        return f"{self.user.display_name} - Signature v{self.version} ({self.valid_from.date()} to {self.valid_until.date() if self.valid_until else 'present'})"
