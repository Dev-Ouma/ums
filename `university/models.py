import re
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
from django.utils import timezone

from .module_models import (
    ModuleStatus,
    SystemModule,
    SystemSubmodule,
    SystemFeature,
    ModuleDependency,
)


class School(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=15, unique=True)
    description = models.TextField(blank=True, default="")
    dean_name = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Faculty / School"
        verbose_name_plural = "Faculties & Schools"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=10, unique=True)
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="departments",
        verbose_name="Faculty / School",
        help_text="Faculty or School this department belongs to"
    )
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=40, default="fa-building-columns",
                            help_text="Font Awesome icon name")
    color = models.CharField(max_length=20, default="#6C5CE7")
    image_url = models.URLField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def get_absolute_url(self):
        return reverse("university:department_detail", args=[self.pk])

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def faculty_name(self):
        return self.school.name if self.school else "Unassigned School/Faculty"


class Program(models.Model):
    class Level(models.TextChoices):
        CERTIFICATE = "CERT", "Certificate (TVET/Vocational)"
        DIPLOMA = "DIP", "Diploma (TVET/Higher Diploma)"
        UNDERGRADUATE = "UG", "Undergraduate (Degree)"
        POSTGRADUATE = "PG", "Postgraduate Diploma"
        MASTERS = "MS", "Master's Degree"
        DOCTORATE = "PHD", "Doctorate (Ph.D.)"

    LEVELS = [
        ("CERT", "Certificate"),
        ("DIP", "Diploma"),
        ("UG", "Undergraduate"),
        ("PG", "Postgraduate"),
        ("MS", "Master's"),
        ("PHD", "Doctorate"),
    ]

    class ProgramType(models.TextChoices):
        DEGREE = "Degree", "University Bachelor Degree"
        DIPLOMA = "Diploma", "TVET / Higher National Diploma"
        CERTIFICATE = "Certificate", "TVET / Professional Certificate"
        MASTERS = "Masters", "Master's Degree"
        DOCTORATE = "Doctorate", "Doctor of Philosophy / Ph.D."
        POSTGRAD_DIP = "PostgradDip", "Postgraduate Diploma"

    class StudyMode(models.TextChoices):
        FULL_TIME = "Full-Time", "Full-Time Regular"
        PART_TIME = "Part-Time", "Part-Time"
        EVENING = "Evening", "Evening Classes"
        WEEKEND = "Weekend", "Weekend Intensive"
        ONLINE = "Online", "Distance / E-Learning"

    class DurationUnit(models.TextChoices):
        YEARS = "Years", "Years"
        SEMESTERS = "Semesters", "Semesters"
        TRIMESTERS = "Trimesters", "Trimesters"
        MONTHS = "Months", "Months"

    class Status(models.TextChoices):
        ACTIVE = "Active", "Active & Admitting"
        INACTIVE = "Inactive", "Inactive / Suspended"
        PHASED_OUT = "Phased Out", "Phased Out / Teach-Out Only"

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True, db_index=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programs")
    program_type = models.CharField(max_length=30, choices=ProgramType.choices, default=ProgramType.DEGREE)
    level = models.CharField(max_length=6, choices=LEVELS, default="UG")
    award_title = models.CharField(max_length=160, blank=True, default="", help_text="Official designation awarded upon completion")
    study_mode = models.CharField(max_length=30, choices=StudyMode.choices, default=StudyMode.FULL_TIME)
    duration_value = models.PositiveSmallIntegerField(default=4, help_text="Duration in specified units")
    duration_unit = models.CharField(max_length=20, choices=DurationUnit.choices, default=DurationUnit.YEARS)
    duration_years = models.PositiveSmallIntegerField(default=4, help_text="Duration in years (for backward compatibility)")
    semesters_per_year = models.PositiveSmallIntegerField(default=2)
    total_semesters = models.PositiveSmallIntegerField(default=8)
    min_credits = models.PositiveIntegerField(default=120)
    max_credits = models.PositiveIntegerField(null=True, blank=True)
    total_seats = models.PositiveIntegerField(default=120)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    description = models.TextField(blank=True, default="")
    career_prospects = models.TextField(blank=True, default="", help_text="Career prospects and learning outcomes")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_absolute_url(self):
        return reverse("university:program_detail", args=[self.pk])

    @property
    def school(self):
        return self.department.school if self.department else None

    @property
    def faculty_name(self):
        return self.department.school.name if (self.department and self.department.school) else "Unassigned School/Faculty"

    @property
    def enrolled_students_count(self):
        return self.students.count()

    @property
    def active_courses_count(self):
        return self.courses.filter(status="Active").count()

    def save(self, *args, **kwargs):
        sem_per_yr = max(1, self.semesters_per_year or 2)
        if self.duration_unit == self.DurationUnit.YEARS:
            self.duration_years = max(1, self.duration_value)
            self.total_semesters = self.duration_years * sem_per_yr
        elif self.duration_unit in [self.DurationUnit.SEMESTERS, self.DurationUnit.TRIMESTERS]:
            self.total_semesters = max(1, self.duration_value)
            self.duration_years = max(1, round(self.total_semesters / sem_per_yr))
        elif self.duration_unit == self.DurationUnit.MONTHS:
            self.duration_years = max(1, round(self.duration_value / 12))
            self.total_semesters = max(1, round((self.duration_value / 12) * sem_per_yr))
        super().save(*args, **kwargs)


class AcademicYear(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        CURRENT = "CURRENT", "Current"
        CLOSED = "CLOSED", "Closed"
        ARCHIVED = "ARCHIVED", "Archived"

    name = models.CharField(max_length=30, unique=True, help_text="e.g. 2026/2027")
    code = models.CharField(max_length=30, unique=True, blank=True, help_text="e.g. AY-2026-2027")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_current = models.BooleanField(default=False, db_index=True)
    description = models.TextField(blank=True, default="")
    reference_no = models.CharField(max_length=60, blank=True, default="", help_text="Institutional reference / Gazette notice")
    max_programmes_allowed = models.PositiveIntegerField(default=1, help_text="Max concurrent programmes a student can accept")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_academic_years")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date", "name"]
        verbose_name = "Academic Year"
        verbose_name_plural = "Academic Years"

    def __str__(self):
        cur = " (Current)" if self.is_current else ""
        return f"{self.name}{cur}"

    def clean(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("Academic Year start date must be strictly before end date.")

    def save(self, *args, **kwargs):
        if not self.code and self.name:
            clean_name = re.sub(r"[^0-9A-Za-z]+", "-", self.name.strip())
            self.code = f"AY-{clean_name}".upper()
        if self.is_current:
            self.status = self.Status.CURRENT
            AcademicYear.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)


class AcademicTerm(models.Model):
    class TermType(models.TextChoices):
        SEMESTER = "SEMESTER", "Semester"
        TRIMESTER = "TRIMESTER", "Trimester"
        TERM = "TERM", "Term"
        SUMMER = "SUMMER", "Summer / Special Session"

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="semesters",
        null=True, blank=True
    )
    name = models.CharField(max_length=40, unique=True)
    term_type = models.CharField(max_length=20, choices=TermType.choices, default=TermType.SEMESTER)
    semester_number = models.PositiveSmallIntegerField(default=1, help_text="e.g. 1, 2, 3")
    start_date = models.DateField()
    end_date = models.DateField()
    registration_start_date = models.DateField(null=True, blank=True)
    registration_end_date = models.DateField(null=True, blank=True)
    exam_start_date = models.DateField(null=True, blank=True)
    exam_end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AcademicYear.Status.choices,
        default=AcademicYear.Status.PUBLISHED,
        db_index=True
    )
    is_current = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_semesters"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Academic Semester / Term"
        verbose_name_plural = "Academic Semesters & Terms"

    def __str__(self):
        cur = " (Current)" if self.is_current else ""
        if self.academic_year:
            return f"{self.name} · {self.academic_year.name}{cur}"
        return f"{self.name}{cur}"

    def clean(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("Semester start date must be strictly before end date.")
        if self.academic_year:
            if self.start_date < self.academic_year.start_date or self.end_date > self.academic_year.end_date:
                raise ValidationError(
                    f"Semester dates ({self.start_date} to {self.end_date}) must fall within parent Academic Year dates "
                    f"({self.academic_year.start_date} to {self.academic_year.end_date})."
                )

    def save(self, *args, **kwargs):
        if self.is_current:
            self.status = AcademicYear.Status.CURRENT
            AcademicTerm.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)


Semester = AcademicTerm


class Course(models.Model):
    STATUS_ACTIVE = "Active"
    STATUS_ARCHIVED = "Archived"
    STATUS_UPCOMING = "Upcoming"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
        (STATUS_UPCOMING, "Upcoming"),
    ]

    code = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=150)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="courses")
    program = models.ForeignKey(Program, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="courses")
    faculty = models.ForeignKey("accounts.FacultyProfile", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="courses")
    credits = models.PositiveSmallIntegerField(default=4)
    semester_no = models.PositiveSmallIntegerField(default=1)
    description = models.TextField(blank=True, default="")
    image_url = models.URLField(blank=True, default="")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        ordering = ["code"]

    def get_absolute_url(self):
        return reverse("university:course_detail", args=[self.pk])

    @property
    def enrolled_count(self):
        return self.enrollments.filter(status=Enrollment.ACTIVE).count()

    def __str__(self):
        return f"{self.code} — {self.title}"


class Exam(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SCHEDULED = "SCHEDULED", "Scheduled"
        MARKING = "MARKING", "Marks entry"
        INTERNAL_REVIEW = "INTERNAL_REVIEW", "Internal review"
        EXTERNAL_REVIEW = "EXTERNAL_REVIEW", "External review"
        SUBMITTED = "SUBMITTED", "Awaiting approval"
        APPROVED = "APPROVED", "Approved"
        PUBLISHED = "PUBLISHED", "Published"
        CANCELLED = "CANCELLED", "Cancelled"

    class Kind(models.TextChoices):
        CAT = "CAT", "Continuous assessment"
        FINAL = "FINAL", "Final examination"
        PRACTICAL = "PRACTICAL", "Practical"
        SUPPLEMENTARY = "SUPPLEMENTARY", "Supplementary"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.FINAL)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    room = models.ForeignKey("ExamRoom", null=True, blank=True, on_delete=models.PROTECT, related_name="exams")
    invigilator = models.ForeignKey("accounts.FacultyProfile", null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="invigilated_exams")
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=100,
                                validators=[MinValueValidator(0.01), MaxValueValidator(100)])
    pass_mark = models.DecimalField(max_digits=5, decimal_places=2, default=40,
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    grade_bands = models.JSONField(default=default_grade_bands)
    instructions = models.TextField(blank=True)
    original_exam = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="supplementary_exams")
    published_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="exams")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=80, default="Mid Term")
    date = models.DateField(default=timezone.now)
    max_marks = models.PositiveSmallIntegerField(default=100, validators=[MinValueValidator(1), MaxValueValidator(999)])

    cat_max_marks = models.DecimalField(max_digits=5, decimal_places=2, default=30,
                                      validators=[MinValueValidator(0), MaxValueValidator(999)])
    exam_max_marks = models.DecimalField(max_digits=5, decimal_places=2, default=70,
                                       validators=[MinValueValidator(0), MaxValueValidator(999)])
    internal_examiner = models.ForeignKey("accounts.FacultyProfile", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="internal_examined_exams")
    external_examiner = models.ForeignKey("accounts.FacultyProfile", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="external_examined_exams")
    external_examiner_name = models.CharField(max_length=150, blank=True, default="",
                                             help_text="External examiner name and affiliation")
    internal_reviewed_at = models.DateTimeField(null=True, blank=True)
    internal_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name="internal_exam_reviews")
    external_reviewed_at = models.DateTimeField(null=True, blank=True)
    external_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name="external_exam_reviews")
    examiner_remarks = models.TextField(blank=True, default="")

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError("End time must be after start time; exams must finish on the same day.")
        if self.term_id and not self.term.start_date <= self.date <= self.term.end_date:
            raise ValidationError("Exam date must fall within the selected academic term.")

        if not self.internal_examiner_id and self.course_id and self.course.faculty_id:
            self.internal_examiner = self.course.faculty

        if self.cat_max_marks is not None and self.exam_max_marks is not None:
            calc_max = int(self.cat_max_marks + self.exam_max_marks)
            if calc_max > 0:
                self.max_marks = calc_max

    def grade_for(self, percentage):
        for band in sorted(self.grade_bands, key=lambda b: b["minimum"], reverse=True):
            if percentage >= band["minimum"]:
                return band["grade"]
        return "F"

    class Meta:
        ordering = ["-date"]
        constraints = [models.CheckConstraint(condition=models.Q(max_marks__gt=0), name="exam_positive_max_marks")]

    def __str__(self):
        return f"{self.name} — {self.course.code}"


class Result(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="results")
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE,
                                related_name="results")
    cat_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    exam_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    marks_obtained = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    attendance = models.CharField(max_length=7, choices=[("PENDING", "Not recorded"), ("PRESENT", "Present"), ("ABSENT", "Absent")], default="PENDING")
    seat_number = models.PositiveIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=250, blank=True)

    def clean(self):
        super().clean()
        cat_max = self.exam.cat_max_marks if hasattr(self, "exam") and self.exam_id else Decimal(30)
        exam_max = self.exam.exam_max_marks if hasattr(self, "exam") and self.exam_id else Decimal(70)
        total_max = self.exam.max_marks if hasattr(self, "exam") and self.exam_id else Decimal(100)

        if self.cat_marks is not None and not 0 <= self.cat_marks <= cat_max:
            raise ValidationError(f"CAT marks must be between 0 and {cat_max}.")
        if self.exam_marks is not None and not 0 <= self.exam_marks <= exam_max:
            raise ValidationError(f"Exam marks must be between 0 and {exam_max}.")

        if self.cat_marks is not None or self.exam_marks is not None:
            self.marks_obtained = round((self.cat_marks or Decimal(0)) + (self.exam_marks or Decimal(0)), 2)

        if self.marks_obtained is not None and not 0 <= self.marks_obtained <= total_max:
            raise ValidationError(f"Marks must be between zero and maximum ({total_max}).")

    @property
    def outcome(self):
        if self.attendance == "ABSENT":
            return "Absent"
        if self.marks_obtained in (None, ""):
            return "Pending"
        try:
            return "Pass" if (self.marks_obtained * 100 / self.exam.max_marks) >= self.exam.pass_mark else "Fail"
        except (ValueError, TypeError, ZeroDivisionError):
            return "Pass" if self.percentage >= 40.0 else "Fail"

    class Meta:
        unique_together = ("exam", "student")
        ordering = ["-exam__date"]

    @property
    def percentage(self):
        if not self.exam.max_marks or self.marks_obtained in (None, ""):
            return 0
        try:
            return round(float(self.marks_obtained) / float(self.exam.max_marks) * 100, 1)
        except (ValueError, TypeError, ZeroDivisionError):
            return 0

    @property
    def grade(self):
        if self.attendance == "ABSENT":
            return "ABS"
        if self.marks_obtained in (None, ""):
            return "—"
        return self.exam.grade_for(self.marks_obtained * 100 / self.exam.max_marks)

    def __str__(self):
        return f"{self.student.roll_no} {self.exam.course.code}: {self.marks_obtained}"


class FeeInvoice(models.Model):
    PAID, PARTIAL, UNPAID, OVERPAID = "PAID", "PARTIAL", "UNPAID", "OVERPAID"
    STATUS = [(PAID, "Paid"), (PARTIAL, "Partial"), (UNPAID, "Unpaid"), (OVERPAID, "Overpaid")]
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="invoices")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=120, default="Semester Tuition Fee")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    issued_on = models.DateField(default=timezone.now)
    due_date = models.DateField()

    class Meta:
        ordering = ["-issued_on"]

    @property
    def balance(self):
        return self.amount - self.amount_paid

    @property
    def credit(self):
        bal = self.balance
        return abs(bal) if bal < 0 else Decimal("0.00")

    @property
    def status(self):
        if self.amount_paid > self.amount:
            return self.OVERPAID
        if self.amount_paid >= self.amount:
            return self.PAID
        if self.amount_paid > 0:
            return self.PARTIAL
        return self.UNPAID

    def __str__(self):
        return f"{self.title} · {self.student.roll_no}"
