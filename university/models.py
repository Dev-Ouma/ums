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


def default_grade_bands():
    return [
        {"grade": "A", "minimum": 70, "gp": 4.0, "description": "Excellent"},
        {"grade": "B", "minimum": 60, "gp": 3.0, "description": "Good"},
        {"grade": "C", "minimum": 50, "gp": 2.0, "description": "Satisfactory"},
        {"grade": "D", "minimum": 40, "gp": 1.0, "description": "Pass"},
        {"grade": "F", "minimum": 0, "gp": 0.0, "description": "Fail"},
    ]


def grade_point_for(grade):
    points = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}
    return points.get(grade, 0.0)


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
    award_title = models.CharField(max_length=160, blank=True, default="", help_text="Official designation awarded upon completion (e.g. Bachelor of Science in Computer Science)")
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
        if self.duration_unit == self.DurationUnit.YEARS:
            self.duration_years = self.duration_value
            self.total_semesters = self.duration_value * (self.semesters_per_year or 2)
        elif self.duration_unit in [self.DurationUnit.SEMESTERS, self.DurationUnit.TRIMESTERS]:
            self.total_semesters = self.duration_value
            self.duration_years = max(1, round(self.duration_value / (self.semesters_per_year or 2)))
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

    @property
    def semesters_count(self):
        return self.semesters.count()

    @property
    def active_semesters(self):
        return self.semesters.filter(status__in=[self.Status.PUBLISHED, self.Status.CURRENT])


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
    name = models.CharField(max_length=80)
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
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "term_type", "semester_number"],
                name="unique_semester_number_per_academic_year",
            ),
            models.UniqueConstraint(
                fields=["academic_year", "name"],
                name="unique_semester_name_per_academic_year",
            ),
        ]

    def __str__(self):
        cur = " (Current)" if self.is_current else ""
        if self.academic_year:
            return f"{self.name} · {self.academic_year.name}{cur}"
        return f"{self.name}{cur}"

    def clean(self):
        if not self.academic_year_id and not self.academic_year:
            raise ValidationError("Semester must be linked to an Academic Year.")
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("Semester start date must be strictly before end date.")
        if self.academic_year:
            if self.start_date < self.academic_year.start_date or self.end_date > self.academic_year.end_date:
                raise ValidationError(
                    f"Semester dates ({self.start_date} to {self.end_date}) must fall within parent Academic Year dates "
                    f"({self.academic_year.start_date} to {self.academic_year.end_date})."
                )
        if self.registration_start_date and self.registration_end_date:
            if self.registration_start_date > self.registration_end_date:
                raise ValidationError("Registration start date must be on or before registration end date.")
        if self.exam_start_date and self.exam_end_date:
            if self.exam_start_date > self.exam_end_date:
                raise ValidationError("Exam start date must be on or before exam end date.")

    def save(self, *args, **kwargs):
        if self.is_current:
            self.status = AcademicYear.Status.CURRENT
            # Ensure single current semester
            AcademicTerm.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)

    @property
    def is_registration_open(self):
        if self.status not in [AcademicYear.Status.PUBLISHED, AcademicYear.Status.CURRENT]:
            return False
        today = timezone.now().date()
        if self.registration_start_date and today < self.registration_start_date:
            return False
        if self.registration_end_date and today > self.registration_end_date:
            return False
        return True

    @property
    def is_exam_period(self):
        today = timezone.now().date()
        if self.exam_start_date and self.exam_end_date:
            return self.exam_start_date <= today <= self.exam_end_date
        return False


# Semantic alias
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
                                null=True, blank=True, related_name="courses",
                                verbose_name="Teaching Staff")
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
        return self.enrollments.filter(status="ACTIVE").count()

    @property
    def level_display(self):
        if self.program:
            return self.program.get_level_display()
        return "Undergraduate"

    @property
    def year_display(self):
        if self.semester_no:
            return f"Year {(self.semester_no + 1) // 2}"
        return "Year 1"

    def __str__(self):
        return f"{self.code} — {self.title}"


class SemesterRegistration(models.Model):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REGISTERED = "REGISTERED"
    FINAL = "FINAL"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (APPROVED, "Approved"),
        (REGISTERED, "Registered"),
        (FINAL, "Final"),
        (REJECTED, "Rejected"),
    ]

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE,
                                related_name="academic_registrations")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE,
                             related_name="academic_registrations")
    semester_no = models.PositiveSmallIntegerField(default=1)
    academic_year = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=DRAFT)
    total_credits = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="approved_registrations")
    admin_remarks = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("student", "term")
        ordering = ["-term__start_date", "-created_at"]

    def __str__(self):
        return f"{self.student.roll_no} — {self.term.name} ({self.get_status_display()})"

    def recalculate_credits(self, save=True):
        self.total_credits = sum(e.course.credits for e in self.enrollments.exclude(status=Enrollment.DROPPED))
        if save:
            self.save(update_fields=["total_credits"])


class Enrollment(models.Model):
    ACTIVE, COMPLETED, DROPPED = "ACTIVE", "COMPLETED", "DROPPED"
    DRAFT, SUBMITTED, APPROVED = "DRAFT", "SUBMITTED", "APPROVED"
    STATUS = [
        (ACTIVE, "Active"),
        (COMPLETED, "Completed"),
        (DROPPED, "Dropped"),
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (APPROVED, "Approved"),
    ]
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE,
                                related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)
    registration = models.ForeignKey(SemesterRegistration, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="enrollments")
    enrolled_on = models.DateField(default=timezone.now)
    status = models.CharField(max_length=12, choices=STATUS, default=ACTIVE)

    class Meta:
        unique_together = ("student", "course")
        ordering = ["-enrolled_on"]

    def __str__(self):
        return f"{self.student.roll_no} → {self.course.code}"


class Attendance(models.Model):
    PRESENT, ABSENT, LATE = "P", "A", "L"
    STATUS = [(PRESENT, "Present"), (ABSENT, "Absent"), (LATE, "Late")]
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=1, choices=STATUS, default=PRESENT)

    class Meta:
        unique_together = ("enrollment", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.enrollment} {self.date} {self.status}"


class Assignment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    max_marks = models.PositiveSmallIntegerField(default=100)
    assigned_on = models.DateField(default=timezone.now)
    due_date = models.DateField()

    class Meta:
        ordering = ["-due_date"]

    def get_absolute_url(self):
        return reverse("university:assignment_detail", args=[self.pk])

    @property
    def is_open(self):
        return self.due_date >= timezone.now().date()

    def __str__(self):
        return f"{self.title} ({self.course.code})"


class Submission(models.Model):
    PENDING, SUBMITTED, GRADED = "PENDING", "SUBMITTED", "GRADED"
    STATUS = [(PENDING, "Pending"), (SUBMITTED, "Submitted"), (GRADED, "Graded")]
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE,
                                related_name="submissions")
    content = models.TextField(blank=True, default="")
    submitted_on = models.DateTimeField(null=True, blank=True)
    marks = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING)

    class Meta:
        unique_together = ("assignment", "student")
        ordering = ["-submitted_on"]

    def __str__(self):
        return f"{self.student.roll_no} · {self.assignment.title}"


class GradingScale(models.Model):
    grade = models.CharField(max_length=5, unique=True)
    min_mark = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    max_mark = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    grade_point = models.DecimalField(max_digits=3, decimal_places=2, default=0.0)
    description = models.CharField(max_length=60, blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "-min_mark"]

    def __str__(self):
        return f"Grade {self.grade} ({self.min_mark}% – {self.max_mark}%, {self.grade_point} GP)"


class ExamRoom(models.Model):
    name = models.CharField(max_length=100, unique=True)
    capacity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    location = models.CharField(max_length=150, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=models.Q(capacity__gt=0), name="exam_room_positive_capacity")]

    def __str__(self):
        return f"{self.name} ({self.capacity} seats)"


class ClassSchedule(models.Model):
    class Day(models.TextChoices):
        MON = "MON", "Monday"
        TUE = "TUE", "Tuesday"
        WED = "WED", "Wednesday"
        THU = "THU", "Thursday"
        FRI = "FRI", "Friday"
        SAT = "SAT", "Saturday"

    class SessionType(models.TextChoices):
        LECTURE = "LECTURE", "Lecture"
        TUTORIAL = "TUTORIAL", "Tutorial"
        LAB = "LAB", "Lab / Practical"
        SEMINAR = "SEMINAR", "Seminar"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="schedules")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE, related_name="schedules")
    room = models.ForeignKey(ExamRoom, on_delete=models.PROTECT, related_name="class_schedules")
    day = models.CharField(max_length=3, choices=Day.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    session_type = models.CharField(max_length=10, choices=SessionType.choices, default=SessionType.LECTURE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)

    class Meta:
        ordering = ["day", "start_time"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")),
                                   name="schedule_end_after_start"),
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError("End time must be after start time.")
        if self.term_id and self.day and self.start_time and self.end_time:
            overlapping = ClassSchedule.objects.filter(
                term_id=self.term_id, day=self.day,
                start_time__lt=self.end_time, end_time__gt=self.start_time,
            ).exclude(pk=self.pk).select_related("course", "course__faculty")
            if self.room_id:
                room_clash = overlapping.filter(room_id=self.room_id).first()
                if room_clash:
                    raise ValidationError(
                        f"{self.room} is already booked for {room_clash.course.code} "
                        f"({room_clash.start_time.strftime('%H:%M')}–{room_clash.end_time.strftime('%H:%M')}).")
            if self.course_id and self.course.faculty_id:
                faculty_clash = overlapping.filter(course__faculty_id=self.course.faculty_id).first()
                if faculty_clash:
                    raise ValidationError(
                        f"{self.course.faculty} already teaches {faculty_clash.course.code} "
                        f"({faculty_clash.start_time.strftime('%H:%M')}–{faculty_clash.end_time.strftime('%H:%M')}) then.")
            if self.course_id and self.course.program_id:
                cohort_clash = overlapping.filter(
                    course__program_id=self.course.program_id,
                    course__semester_no=self.course.semester_no,
                ).exclude(course_id=self.course_id).first()
                if cohort_clash:
                    raise ValidationError(
                        f"{self.course.program} (Sem {self.course.semester_no}) already has "
                        f"{cohort_clash.course.code} scheduled "
                        f"({cohort_clash.start_time.strftime('%H:%M')}–{cohort_clash.end_time.strftime('%H:%M')}) then.")

    def __str__(self):
        return (f"{self.course.code} · {self.get_day_display()} "
               f"{self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')}")


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
    room = models.ForeignKey(ExamRoom, null=True, blank=True, on_delete=models.PROTECT, related_name="exams")
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
        if self.original_exam_id:
            original = self.original_exam
            if original.pk == self.pk or original.original_exam_id or original.status != self.Status.PUBLISHED:
                raise ValidationError("Select a published regular exam for the supplementary sitting.")
            if original.course_id != self.course_id or original.term_id != self.term_id:
                raise ValidationError("A supplementary exam must use the original course and term.")
            if self.kind != self.Kind.SUPPLEMENTARY:
                raise ValidationError("An original exam is only allowed for supplementary sittings.")
        elif self.kind == self.Kind.SUPPLEMENTARY:
            raise ValidationError("A supplementary sitting requires an original exam.")

        # Default internal examiner to course faculty if not assigned
        if not self.internal_examiner_id and self.course_id and self.course.faculty_id:
            self.internal_examiner = self.course.faculty

        # Sync max marks with components when appropriate
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

        if self.cat_marks is not None:
            if not 0 <= self.cat_marks <= cat_max:
                raise ValidationError(f"CAT marks must be between 0 and {cat_max}.")
        if self.exam_marks is not None:
            if not 0 <= self.exam_marks <= exam_max:
                raise ValidationError(f"Exam marks must be between 0 and {exam_max}.")

        # Auto-compute total marks from CAT + Exam if provided
        if self.cat_marks is not None or self.exam_marks is not None:
            self.marks_obtained = round((self.cat_marks or Decimal(0)) + (self.exam_marks or Decimal(0)), 2)

        if self.marks_obtained is not None and not 0 <= self.marks_obtained <= total_max:
            raise ValidationError(f"Marks must be between zero and the exam maximum ({total_max}).")
        if self.attendance != "PRESENT" and (self.marks_obtained is not None or self.cat_marks is not None or self.exam_marks is not None):
            raise ValidationError("Only present candidates may receive marks.")

    @property
    def outcome(self):
        if self.attendance == "ABSENT":
            return "Absent"
        if self.marks_obtained in (None, ""):
            return "Pending"
        try:
            return "Pass" if self.marks_obtained * 100 / self.exam.max_marks >= self.exam.pass_mark else "Fail"
        except (ValueError, TypeError):
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
        except (ValueError, TypeError):
            return 0

    @property
    def grade(self):
        if self.attendance == "ABSENT":
            return "ABS"
        if self.marks_obtained in (None, ""):
            return "—"
        return self.exam.grade_for(self.marks_obtained * 100 / self.exam.max_marks)

    @property
    def grade_point(self):
        if self.attendance == "ABSENT" or self.marks_obtained in (None, ""):
            return 0.0
        band = next((b for b in self.exam.grade_bands if b["grade"] == self.grade), {})
        return float(band.get("gp", grade_point_for(self.grade)))

    def __str__(self):
        return f"{self.student.roll_no} {self.exam.course.code}: {self.marks_obtained}"


class FeeInvoice(models.Model):
    PAID, PARTIAL, UNPAID, OVERPAID = "PAID", "PARTIAL", "UNPAID", "OVERPAID"
    STATUS = [(PAID, "Paid"), (PARTIAL, "Partial"), (UNPAID, "Unpaid"), (OVERPAID, "Overpaid")]
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE,
                                related_name="invoices")
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
        """Positive = amount owed, negative = credit/overpayment on this invoice."""
        return self.amount - self.amount_paid

    @property
    def credit(self):
        """Returns the overpayment credit amount (always >= 0)."""
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


class FeeAccount(models.Model):
    class AccountType(models.TextChoices):
        MPESA_PAYBILL = "MPESA_PAYBILL", "M-Pesa Paybill"
        MPESA_TILL = "MPESA_TILL", "M-Pesa Buy Goods / Till"
        CARD_GATEWAY = "CARD_GATEWAY", "Card Payment Gateway"
        BANK_ACCOUNT = "BANK_ACCOUNT", "Bank Account Transfer"
        OTHER = "OTHER", "Other Payment Channel"

    class Provider(models.TextChoices):
        SAFARICOM = "SAFARICOM", "Safaricom M-Pesa"
        STRIPE = "STRIPE", "Stripe"
        PESAPAL = "PESAPAL", "Pesapal"
        EQUITY = "EQUITY", "Equity Bank"
        KCB = "KCB", "KCB Bank"
        COOP = "COOP", "Co-operative Bank"
        STANDARD_CHARTERED = "STANDARD_CHARTERED", "Standard Chartered"
        GENERIC = "GENERIC", "Generic Payment Gateway"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        SUSPENDED = "SUSPENDED", "Suspended"
        EXPIRED = "EXPIRED", "Expired"

    class Environment(models.TextChoices):
        SANDBOX = "SANDBOX", "Sandbox / Test"
        PRODUCTION = "PRODUCTION", "Production / Live"

    name = models.CharField(max_length=120, help_text="Human-readable account label e.g. Main Tuition M-Pesa Paybill")
    account_type = models.CharField(max_length=30, choices=AccountType.choices, default=AccountType.MPESA_PAYBILL, db_index=True)
    provider = models.CharField(max_length=40, choices=Provider.choices, default=Provider.SAFARICOM)
    account_identifier = models.CharField(max_length=80, help_text="Paybill Number, Till Number, Merchant ID, or Bank Account Number")
    account_name = models.CharField(max_length=120, blank=True, default="", help_text="Business name, paybill account name, or bank account title")
    currency = models.CharField(max_length=10, default="KES")
    description = models.TextField(blank=True, default="")
    environment = models.CharField(max_length=20, choices=Environment.choices, default=Environment.SANDBOX)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    is_default = models.BooleanField(default=False, help_text="Default account for this payment type")
    supported_methods = models.JSONField(default=list, blank=True, help_text="List of supported methods e.g. ['MPESA_PAYBILL'], ['VISA', 'MASTERCARD']")
    configuration = models.JSONField(default=dict, blank=True, help_text="Public configuration such as callback URL, confirmation URL, branch, bank code")
    encrypted_credentials = models.TextField(blank=True, default="", help_text="Encrypted API keys, consumer secrets, passkeys")

    # Optional routing rules
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_accounts")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_accounts")
    program = models.ForeignKey(Program, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_accounts")

    # Diagnostics & Health
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(max_length=40, blank=True, default="")
    last_test_message = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_fee_accounts")
    created_at = models.DateTimeField(default=timezone.now)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="updated_fee_accounts")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()}) - {self.account_identifier}"

    @property
    def paybill_account_number_display(self):
        """Human-readable description of how the M-Pesa Paybill Account No is resolved in Kenya."""
        if self.account_type != self.AccountType.MPESA_PAYBILL:
            return ""
        config = self.configuration or {}
        fmt = config.get("account_ref_format", "STUDENT_REG_NO")
        if fmt == "FIXED_ACCOUNT":
            return config.get("fixed_account_number") or "Fixed Account"
        elif fmt == "CLEAN_REG_NO":
            return "Alphanumeric Reg No (e.g. BTCSE0012027)"
        elif fmt == "UNIQUE_STUDENT_ID":
            prefix = config.get("account_ref_prefix", "ACC")
            return f"Unique Virtual Account (e.g. {prefix}000001)"
        elif fmt == "PREFIX_REG_NO":
            prefix = config.get("account_ref_prefix", "FEES-")
            return f"{prefix}[Student Reg No]"
        elif fmt == "INVOICE_NUMBER":
            return "[Invoice Number]"
        elif fmt == "PAYMENT_REFERENCE":
            return "[Payment Ref]"
        return "Student Reg No (e.g. BT-CSE/001/2027)"

    def get_student_account_number(self, student=None, invoice=None, payment=None):
        """
        Returns the exact Paybill Account Number to be entered by a student based on this FeeAccount's configured rule.
        """
        config = self.configuration or {}
        fmt = config.get("account_ref_format", "STUDENT_REG_NO")

        if fmt == "FIXED_ACCOUNT":
            return config.get("fixed_account_number") or self.account_identifier
        elif fmt == "INVOICE_NUMBER" and invoice:
            return getattr(invoice, "invoice_number", "")
        elif fmt == "PAYMENT_REFERENCE" and payment:
            return getattr(payment, "internal_reference", "")

        if not student:
            return config.get("fixed_account_number") or self.account_identifier

        roll_no = getattr(student, "roll_no", "") or ""
        clean_roll = re.sub(r"[^A-Za-z0-9]", "", roll_no).upper()

        if fmt == "CLEAN_REG_NO":
            return clean_roll or roll_no
        elif fmt == "UNIQUE_STUDENT_ID":
            prefix = config.get("account_ref_prefix", "ACC").strip().upper()
            student_id = getattr(student, "id", 0) or 0
            return f"{prefix}{student_id:06d}"
        elif fmt == "PREFIX_REG_NO":
            prefix = config.get("account_ref_prefix", "FEES-").strip()
            return f"{prefix}{roll_no}"

        # Default standard STUDENT_REG_NO
        return roll_no


class Payment(models.Model):
    class Status(models.TextChoices):
        INITIATED = "INITIATED", "Initiated"
        PENDING = "PENDING", "Pending Confirmation"
        PROCESSING = "PROCESSING", "Processing"
        SUCCESSFUL = "SUCCESSFUL", "Successful"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"
        REVERSED = "REVERSED", "Reversed"
        REFUNDED = "REFUNDED", "Refunded"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially Refunded"

    invoice = models.ForeignKey(FeeInvoice, on_delete=models.CASCADE, related_name="payments", null=True, blank=True)
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="fee_payments", null=True, blank=True)
    fee_account = models.ForeignKey(FeeAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.SET_NULL, null=True, blank=True)
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")
    paid_on = models.DateField(default=timezone.now)
    method = models.CharField(max_length=40, default="Online")
    reference = models.CharField(max_length=64, default="TXN-0000", db_index=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.SUCCESSFUL, db_index=True)
    internal_reference = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)
    provider_reference = models.CharField(max_length=100, blank=True, default="", db_index=True)

    payer_phone = models.CharField(max_length=25, blank=True, default="")
    payer_name = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    raw_callback_payload = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-paid_on"]

    def save(self, *args, **kwargs):
        if not self.student and self.invoice:
            self.student = self.invoice.student
        if not self.internal_reference:
            import uuid
            self.internal_reference = f"PAY-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.internal_reference or self.reference} · {self.currency} {self.amount} ({self.status})"


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(FeeInvoice, on_delete=models.CASCADE, related_name="allocations")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    allocated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-allocated_at"]

    def __str__(self):
        return f"Alloc {self.amount} from {self.payment} to {self.invoice}"


class FeeReceipt(models.Model):
    receipt_number = models.CharField(max_length=64, unique=True, db_index=True)
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="fee_receipt")
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="fee_receipts")
    issued_at = models.DateTimeField(default=timezone.now)
    previous_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    remaining_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.receipt_number} · {self.student.roll_no} ({self.amount_paid})"


class PaymentReconciliation(models.Model):
    class Status(models.TextChoices):
        MATCHED = "MATCHED", "Matched"
        UNMATCHED = "UNMATCHED", "Unmatched"
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount Mismatch"
        DUPLICATE = "DUPLICATE", "Duplicate Transaction"
        REQUIRES_REVIEW = "REQUIRES_REVIEW", "Requires Review"

    fee_account = models.ForeignKey(FeeAccount, on_delete=models.CASCADE, related_name="reconciliations")
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="reconciliations")
    provider_reference = models.CharField(max_length=100, db_index=True)
    internal_reference = models.CharField(max_length=64, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNMATCHED, db_index=True)
    transaction_date = models.DateTimeField(default=timezone.now)
    reconciled_at = models.DateTimeField(default=timezone.now)
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-transaction_date"]

    def __str__(self):
        return f"Recon {self.provider_reference} - {self.status}"


class PaymentReversal(models.Model):
    class ReversalType(models.TextChoices):
        REVERSAL = "REVERSAL", "Full Reversal"
        REFUND = "REFUND", "Full Refund"
        PARTIAL_REFUND = "PARTIAL_REFUND", "Partial Refund"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Approval"
        APPROVED = "APPROVED", "Approved & Applied"
        REJECTED = "REJECTED", "Rejected"

    original_payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="reversals")
    reversal_type = models.CharField(max_length=20, choices=ReversalType.choices, default=ReversalType.REVERSAL)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    provider_reference = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="requested_reversals")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_reversals")
    created_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reversal_type} on {self.original_payment.internal_reference or self.original_payment.reference} ({self.amount})"


class FeeAccountLog(models.Model):
    class EventType(models.TextChoices):
        TEST_CONNECTION = "TEST_CONNECTION", "Connection Test"
        INITIATE_PAYMENT = "INITIATE_PAYMENT", "Payment Initiated"
        CALLBACK_RECEIVED = "CALLBACK_RECEIVED", "Callback Received"
        VERIFICATION = "VERIFICATION", "Payment Verification"
        ERROR = "ERROR", "Provider Error"
        CONFIG_CHANGE = "CONFIG_CHANGE", "Configuration Change"

    fee_account = models.ForeignKey(FeeAccount, on_delete=models.CASCADE, related_name="logs")
    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    message = models.TextField()
    payload_preview = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.event_type}] {self.fee_account.name} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class Event(models.Model):
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=40, default="Campus")
    location = models.CharField(max_length=120, default="Main Auditorium")
    date = models.DateField(default=timezone.now)
    image_url = models.URLField(blank=True, default="")
    icon = models.CharField(max_length=40, default="fa-calendar-star")

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return self.title


class Notice(models.Model):
    AUDIENCE = [("ALL", "Everyone"), ("STUDENT", "Students"),
                ("FACULTY", "Teaching Staff"), ("ADMIN", "Admins")]
    message_type = models.CharField(max_length=20, default="INFORMATION", choices=[(v, v.replace('_',' ').title()) for v in ['INFORMATION','ANNOUNCEMENT','WARNING','MAINTENANCE','EMERGENCY','SUCCESS','IMPORTANT_NOTICE']])
    priority = models.CharField(max_length=10, default="NORMAL", choices=[(v,v.title()) for v in ['LOW','NORMAL','HIGH','CRITICAL']])
    status = models.CharField(max_length=12, default="PUBLISHED", db_index=True, choices=[(v,v.title()) for v in ['DRAFT','SCHEDULED','PUBLISHED','PAUSED','EXPIRED','ARCHIVED','DELETED']])
    starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locations = models.JSONField(default=list, blank=True)
    target_roles = models.ManyToManyField("StaffRole", blank=True)
    departments = models.ManyToManyField("Department", blank=True)
    programmes = models.ManyToManyField("Program", blank=True)
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="targeted_notices")
    modules = models.ManyToManyField("SystemModule", blank=True)
    template = models.ForeignKey("MessageTemplate", null=True, blank=True, on_delete=models.SET_NULL)
    restriction = models.ForeignKey("SystemRestriction", null=True, blank=True, on_delete=models.SET_NULL)
    banner_mode = models.CharField(max_length=12, default="TICKER", choices=[("STATIC", "Static"), ("TICKER", "Ticker / Live News")])
    animation_enabled = models.BooleanField(default=True)
    animation_speed = models.PositiveSmallIntegerField(default=28, help_text="Ticker duration in seconds. Higher is slower.")
    animation_direction = models.CharField(max_length=8, default="LEFT", choices=[("LEFT", "Right to left"), ("RIGHT", "Left to right")])
    dismissible = models.BooleanField(default=True)
    persistent = models.BooleanField(default=False)
    action_url = models.CharField(max_length=255, blank=True, default="")
    action_label = models.CharField(max_length=80, blank=True, default="")
    display_order = models.PositiveSmallIntegerField(default=100)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    title = models.CharField(max_length=150)
    body = models.TextField()
    audience = models.CharField(max_length=10, choices=AUDIENCE, default="ALL")
    is_pinned = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["display_order", "-is_pinned", "-created_at"]

    def __str__(self):
        return self.title


class ExamAudit(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="audit_entries")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class ExamAppeal(models.Model):
    result = models.ForeignKey(Result, on_delete=models.PROTECT, related_name="appeals")
    reason = models.TextField(max_length=2000)
    status = models.CharField(max_length=10, choices=[("OPEN", "Open"), ("ACCEPTED", "Accepted"), ("REJECTED", "Rejected")], default="OPEN", db_index=True)
    resolution = models.TextField(blank=True, max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["result"], condition=models.Q(status="OPEN"), name="one_open_exam_appeal")]


class Intake(models.Model):
    name = models.CharField(max_length=120)  # e.g., "September 2026 Regular Intake"
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="intakes",
        null=True, blank=True
    )
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.name} ({self.academic_year.name if self.academic_year else 'Unassigned'})"


class Application(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        READY_FOR_PAYMENT = "READY_FOR_PAYMENT", "Ready For Payment"
        PAYMENT_PENDING = "PAYMENT_PENDING", "Payment Pending"
        PAID = "PAID", "Application Fee Paid"
        READY_FOR_SUBMISSION = "READY_FOR_SUBMISSION", "Ready For Submission"
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        ACCEPTED = "ACCEPTED", "Accepted (Admitted)"
        REJECTED = "REJECTED", "Rejected"
        ENROLLED = "ENROLLED", "Enrolled / Matriculated"

    GUARDIAN_RELATIONSHIPS = [
        ("Parent", "Parent"),
        ("Father", "Father"),
        ("Mother", "Mother"),
        ("Legal Guardian", "Legal Guardian"),
        ("Spouse", "Spouse"),
        ("Sibling", "Sibling"),
        ("Sponsor", "Sponsor"),
        ("Relative", "Relative"),
        ("Other", "Other"),
    ]

    application_number = models.CharField(max_length=40, unique=True, db_index=True)
    applicant_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_applications")
    intake = models.ForeignKey(Intake, on_delete=models.SET_NULL, null=True, blank=True, related_name="applications")
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="applications")

    # Personal details
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField()
    phone = models.CharField(max_length=30)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=20, choices=[("MALE", "Male"), ("FEMALE", "Female"), ("OTHER", "Other")])
    national_id = models.CharField(max_length=50, verbose_name="National ID / Passport No.")
    address = models.TextField(blank=True, default="")

    # Guardian & Emergency Contact details
    guardian_name = models.CharField(max_length=120, blank=True, default="", verbose_name="Guardian Full Name")
    guardian_relationship = models.CharField(max_length=60, blank=True, default="Parent", choices=GUARDIAN_RELATIONSHIPS, verbose_name="Relationship to Applicant")
    guardian_phone = models.CharField(max_length=30, blank=True, default="", verbose_name="Guardian Primary Phone")
    guardian_alternative_phone = models.CharField(max_length=30, blank=True, default="", verbose_name="Guardian Alternative Phone")
    guardian_email = models.EmailField(blank=True, default="", verbose_name="Guardian Email")
    guardian_address = models.CharField(max_length=255, blank=True, default="", verbose_name="Guardian Physical / Postal Address")
    guardian_country = models.CharField(max_length=80, blank=True, default="Kenya", verbose_name="Guardian Country")
    guardian_occupation = models.CharField(max_length=120, blank=True, default="", verbose_name="Guardian Occupation")
    guardian_employer = models.CharField(max_length=150, blank=True, default="", verbose_name="Guardian Employer / Organization")
    is_guardian_emergency_contact = models.BooleanField(default=True, verbose_name="Is Next of Kin / Emergency Contact")

    # Academic qualifications
    secondary_school = models.CharField(max_length=160, blank=True, default="")
    kcse_index_number = models.CharField(max_length=60, blank=True, default="")
    kcse_mean_grade = models.CharField(max_length=10, blank=True, default="C+")
    kcse_year = models.PositiveIntegerField(default=2025)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    admitted_reg_no = models.CharField(max_length=50, blank=True, default="")
    reporting_date = models.DateField(null=True, blank=True)
    review_notes = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_applications")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    student = models.OneToOneField("accounts.StudentProfile", on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_application")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.application_number} · {self.first_name} {self.last_name} ({self.program.code})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def fee_paid(self):
        return self.fee_payments.filter(status=ApplicationFeePayment.Status.CONFIRMED).exists()

    @property
    def confirmed_fee_payment(self):
        return self.fee_payments.filter(status=ApplicationFeePayment.Status.CONFIRMED).first()

    @property
    def active_admission_document(self):
        return self.issued_documents.filter(is_current_version=True).exclude(status="REVOKED").first()

    @property
    def verified_attachments_count(self):
        return self.attachments.filter(verification_status="VERIFIED").count()


class ApplicationFeePayment(models.Model):
    """Records payment of the non-refundable application processing fee."""

    class Method(models.TextChoices):
        MPESA = "MPESA", "M-Pesa"
        CARD = "CARD", "Debit / Credit Card"
        BANK = "BANK", "Bank Transfer"
        ECITIZEN = "ECITIZEN", "eCitizen / Government Gateway"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Confirmation"
        CONFIRMED = "CONFIRMED", "Confirmed"
        FAILED = "FAILED", "Failed"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="fee_payments")
    applicant_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="application_fee_payments")
    receipt_number = models.CharField(max_length=60, unique=True, null=True, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.MPESA)
    reference = models.CharField(max_length=60, help_text="Transaction / reference number from the payment channel")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    paid_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.application.application_number} · KES {self.amount} ({self.get_status_display()})"


class FeeStructure(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="fee_structures")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_structures")
    year_of_study = models.PositiveSmallIntegerField(default=1)  # Year 1, 2, 3, 4
    semester = models.PositiveSmallIntegerField(default=1)       # Semester 1, 2, 3
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=45000.00)
    registration_fee = models.DecimalField(max_digits=10, decimal_places=2, default=1500.00)
    examination_fee = models.DecimalField(max_digits=10, decimal_places=2, default=3000.00)
    library_fee = models.DecimalField(max_digits=10, decimal_places=2, default=1000.00)
    activity_fee = models.DecimalField(max_digits=10, decimal_places=2, default=1000.00)
    medical_fee = models.DecimalField(max_digits=10, decimal_places=2, default=1500.00)
    ict_fee = models.DecimalField(max_digits=10, decimal_places=2, default=2000.00)
    student_union_fee = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)

    class Meta:
        ordering = ["program", "year_of_study", "semester"]
        unique_together = ("program", "term", "year_of_study", "semester")

    @property
    def total_fee(self):
        return (
            self.tuition_fee + self.registration_fee + self.examination_fee +
            self.library_fee + self.activity_fee + self.medical_fee +
            self.ict_fee + self.student_union_fee
        )

    def __str__(self):
        return f"{self.program.code} · Y{self.year_of_study}S{self.semester} (Total: KSh {self.total_fee:,.2f})"


class SupplementaryExamRegistration(models.Model):
    class ExamType(models.TextChoices):
        SUPPLEMENTARY = "SUPPLEMENTARY", "Supplementary Examination"
        SPECIAL = "SPECIAL", "Special Examination"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        COMPLETED = "COMPLETED", "Completed"

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="supplementary_registrations")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="supplementary_registrations")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)
    exam_type = models.CharField(max_length=20, choices=ExamType.choices, default=ExamType.SUPPLEMENTARY)
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    fee_invoice = models.ForeignKey(FeeInvoice, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("student", "course", "term", "exam_type")

    def __str__(self):
        return f"{self.student.roll_no} - {self.course.code} ({self.exam_type})"


class StudentRequest(models.Model):
    """Student-initiated Deferment / Withdrawal / Sick Leave requests, reviewed by staff."""

    class Type(models.TextChoices):
        DEFERMENT = "DEFERMENT", "Deferment"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
        SICK_LEAVE = "SICK_LEAVE", "Sick Leave"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="requests")
    request_type = models.CharField(max_length=15, choices=Type.choices)
    reason = models.TextField()
    supporting_document = models.FileField(upload_to="student_requests/%Y/%m/", blank=True, null=True)
    start_date = models.DateField(null=True, blank=True,
                                  help_text="Deferment / leave start date")
    end_date = models.DateField(null=True, blank=True,
                                help_text="Expected deferment / leave end date (resumption date)")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING, db_index=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="reviewed_student_requests")
    review_comments = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["student"], condition=models.Q(status="PENDING"),
                                    name="one_pending_request_per_student"),
        ]

    def __str__(self):
        return f"{self.student.roll_no} · {self.get_request_type_display()} ({self.get_status_display()})"


class RecycleBinItem(models.Model):
    class Module(models.TextChoices):
        STUDENTS = "Students", "Students"
        FACULTY = "Faculty", "Teaching Staff"
        COURSES = "Courses", "Courses"
        PROGRAMMES = "Programmes", "Programmes"
        SCHOOLS = "Schools", "Schools & Faculties"
        DEPARTMENTS = "Departments", "Departments"
        TIMETABLE = "Timetable", "Timetable Schedules"
        FEES = "Fees", "Fee Invoices & Structures"
        ADMISSIONS = "Admissions", "Admissions & Applications"
        NOTICES = "Notices", "Campus Notices"
        EVENTS = "Events", "Campus Events"
        EXAMINATIONS = "Examinations", "Examinations & Marks"
        CALENDAR = "Calendar", "Academic Calendar & Terms"
        OTHER = "Other", "Other Records"

    content_type = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=255)
    module = models.CharField(max_length=40, choices=Module.choices, default=Module.OTHER, db_index=True)
    serialized_data = models.JSONField(help_text="Complete JSON snapshot of model fields and relationships")
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="deleted_recycle_items")
    deleted_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.CharField(max_length=50, blank=True, null=True)
    user_agent = models.TextField(blank=True, default="")
    device_type = models.CharField(max_length=30, default="Desktop")
    is_restored = models.BooleanField(default=False, db_index=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="restored_recycle_items")
    is_protected = models.BooleanField(default=False, help_text="Protected academic/final records requiring superuser to purge")

    class Meta:
        ordering = ["-deleted_at"]

    def __str__(self):
        status = " (Restored)" if self.is_restored else ""
        return f"[{self.module}] {self.object_repr}{status}"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = "LOGIN", "User Login"
        LOGOUT = "LOGOUT", "User Logout"
        FAILED_LOGIN = "FAILED_LOGIN", "Failed Login Attempt"
        CREATE = "CREATE", "Record Created"
        UPDATE = "UPDATE", "Record Updated"
        DELETE = "DELETE", "Record Deleted (Soft Delete)"
        RESTORE = "RESTORE", "Record Restored"
        PERMANENT_DELETE = "PERMANENT_DELETE", "Record Permanently Purged"
        IMPORT = "IMPORT", "Data Imported"
        EXPORT = "EXPORT", "Data Exported"
        MARKS_SUBMISSION = "MARKS_SUBMISSION", "Marks Submitted"
        MARKS_APPROVAL = "MARKS_APPROVAL", "Marks Approved / Moderated"
        GRADE_CHANGE = "GRADE_CHANGE", "Grade Modified"
        UNIT_REGISTRATION = "UNIT_REGISTRATION", "Unit Registration Changed"
        TRANSCRIPT_GENERATION = "TRANSCRIPT_GENERATION", "Transcript Generated"
        CONFIG_CHANGE = "CONFIG_CHANGE", "System Configuration Modified"
        PUBLISH = "PUBLISH", "Record Published"
        UNPUBLISH = "UNPUBLISH", "Record Unpublished"
        CLOSE = "CLOSE", "Academic Period Closed"
        REOPEN = "REOPEN", "Academic Period Reopened"
        SET_CURRENT = "SET_CURRENT", "Set as Current / Active"
        GENERATE_DOCUMENT = "GENERATE_DOCUMENT", "Document Generated"
        REGENERATE_DOCUMENT = "REGENERATE_DOCUMENT", "Document Regenerated"
        RESEND_DOCUMENT = "RESEND_DOCUMENT", "Document Resent"
        VERIFY_DOCUMENT = "VERIFY_DOCUMENT", "Document Verified"
        REVOKE_DOCUMENT = "REVOKE_DOCUMENT", "Document Revoked"
        MODULE_ENABLE = "MODULE_ENABLE", "Module Enabled"
        MODULE_DISABLE = "MODULE_DISABLE", "Module Disabled"
        MODULE_STATUS_CHANGE = "MODULE_STATUS_CHANGE", "Module Status Changed"
        SUBMODULE_STATUS_CHANGE = "SUBMODULE_STATUS_CHANGE", "Submodule Status Changed"
        FEATURE_STATUS_CHANGE = "FEATURE_STATUS_CHANGE", "Feature Status Changed"
        MODULES_BULK_UPDATE = "MODULES_BULK_UPDATE", "Modules Bulk Status Updated"
        BACKUP_CREATE = "BACKUP_CREATE", "Backup Created"
        BACKUP_RESTORE = "BACKUP_RESTORE", "Backup Restored"
        BACKUP_VERIFY = "BACKUP_VERIFY", "Backup Verified"
        BACKUP_DELETE = "BACKUP_DELETE", "Backup Deleted"
        BACKUP_SCHEDULE = "BACKUP_SCHEDULE", "Backup Scheduled"
        SIGNATURE_UPLOAD = "SIGNATURE_UPLOAD", "Signature Uploaded"
        SIGNATURE_UPDATE = "SIGNATURE_UPDATE", "Signature Replaced / Updated"
        SIGNATURE_REMOVE = "SIGNATURE_REMOVE", "Signature Removed"
        SIGNATURE_ACTIVATE = "SIGNATURE_ACTIVATE", "Signature Activated"
        SIGNATURE_DEACTIVATE = "SIGNATURE_DEACTIVATE", "Signature Deactivated"
        SIGNATURE_APPROVE = "SIGNATURE_APPROVE", "Signature Approved"
        SIGNATURE_REVOKE = "SIGNATURE_REVOKE", "Signature Revoked"
        DOCUMENT_SIGNED = "DOCUMENT_SIGNED", "Document Signed & Finalized"
        SIGNATORY_CHANGED = "SIGNATORY_CHANGED", "Document Signatory Changed"

    class Module(models.TextChoices):
        STUDENTS = "Students", "Students"
        FACULTY = "Faculty", "Teaching Staff"
        COURSES = "Courses", "Courses"
        PROGRAMMES = "Programmes", "Programmes"
        DEPARTMENTS = "Departments", "Departments"
        ACADEMICS = "Academics", "Academics & Registrations"
        CALENDAR = "Academic Calendar", "Academic Years & Semesters"
        EXAMINATIONS = "Examinations", "Examinations & Marks"
        FEES = "Fees", "Fees & Finance"
        ADMISSIONS = "Admissions", "Admissions & Applications"
        TIMETABLE = "Timetable", "Timetable"
        AUTH = "Security & Auth", "Security & Authentication"
        CONFIG = "System Configuration", "System Configuration"
        NOTICES = "Notices & Events", "Notices & Events"
        MODULE_MGMT = "Module Management", "Module Management"
        BACKUPS = "System Backups", "System Backups & Recovery"
        SIGNATURES = "Signature Management", "Signature Management"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    user_display = models.CharField(max_length=150, blank=True, default="System")
    user_role = models.CharField(max_length=30, blank=True, default="Anonymous")
    action = models.CharField(max_length=30, choices=Action.choices, db_index=True)
    module = models.CharField(max_length=40, choices=Module.choices, db_index=True)
    entity = models.CharField(max_length=80, blank=True, default="")
    entity_id = models.CharField(max_length=60, blank=True, default="")
    description = models.TextField()
    previous_state = models.JSONField(null=True, blank=True)
    new_state = models.JSONField(null=True, blank=True)
    ip_address = models.CharField(max_length=50, blank=True, null=True)
    user_agent = models.TextField(blank=True, default="")
    device_type = models.CharField(max_length=30, default="Desktop")

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp.strftime('%Y-%m-%d %H:%M')} · {self.user_display} · {self.action} ({self.module})"


class SystemSetting(models.Model):
    class Category(models.TextChoices):
        ACADEMIC = "ACADEMIC", "Academic Setup"
        UNIVERSITY = "UNIVERSITY", "University Structure & Info"
        EXAMINATION = "EXAMINATION", "Course & Examination Setup"
        TIMETABLE = "TIMETABLE", "Timetable Setup"
        REGISTRATION = "REGISTRATION", "Student & Registration Setup"
        FINANCE = "FINANCE", "Finance & Fees Setup"
        SECURITY = "SECURITY", "User & Security Setup"

    class ValueType(models.TextChoices):
        STRING = "STRING", "Text String"
        INTEGER = "INTEGER", "Integer Number"
        DECIMAL = "DECIMAL", "Decimal Number"
        BOOLEAN = "BOOLEAN", "Boolean (True/False)"
        JSON = "JSON", "Structured JSON"

    category = models.CharField(max_length=30, choices=Category.choices, db_index=True)
    key = models.CharField(max_length=80, unique=True, db_index=True)
    label = models.CharField(max_length=150)
    value_type = models.CharField(max_length=20, choices=ValueType.choices, default=ValueType.STRING)
    value = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_public = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["category", "key"]

    def __str__(self):
        return f"{self.category} · {self.key} = {self.value}"


class DomainMigrationRecord(models.Model):
    """
    Audit ledger tracking institutional domain & identity changes over time.
    Preserves before/after states, account migration counts, and migration policies.
    """
    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Preview"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    class Policy(models.TextChoices):
        MIGRATE_AND_ARCHIVE_ALIASES = "MIGRATE_AND_ARCHIVE_ALIASES", "Migrate Active Accounts & Retain Old Emails as Aliases"
        APPLY_TO_NEW_ONLY = "APPLY_TO_NEW_ONLY", "Apply to New Accounts Only"
        FULL_REPLACE = "FULL_REPLACE", "Full Replacement"

    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="domain_migrations")
    created_at = models.DateTimeField(auto_now_add=True)

    previous_institution_name = models.CharField(max_length=200, blank=True, default="")
    new_institution_name = models.CharField(max_length=200, blank=True, default="")
    previous_short_name = models.CharField(max_length=50, blank=True, default="")
    new_short_name = models.CharField(max_length=50, blank=True, default="")

    previous_primary_domain = models.CharField(max_length=120, blank=True, default="")
    new_primary_domain = models.CharField(max_length=120, blank=True, default="")
    previous_staff_domain = models.CharField(max_length=120, blank=True, default="")
    new_staff_domain = models.CharField(max_length=120, blank=True, default="")
    previous_student_domain = models.CharField(max_length=120, blank=True, default="")
    new_student_domain = models.CharField(max_length=120, blank=True, default="")
    previous_student_prefix = models.CharField(max_length=50, blank=True, default="students")
    new_student_prefix = models.CharField(max_length=50, blank=True, default="students")

    migration_policy = models.CharField(max_length=40, choices=Policy.choices, default=Policy.MIGRATE_AND_ARCHIVE_ALIASES)
    staff_accounts_affected = models.PositiveIntegerField(default=0)
    student_accounts_affected = models.PositiveIntegerField(default=0)
    total_emails_migrated = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    logs = models.TextField(blank=True, default="")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Domain Migration Record"
        verbose_name_plural = "Domain Migration Records"

    def __str__(self):
        return f"Domain Migration: {self.previous_primary_domain} -> {self.new_primary_domain} ({self.created_at:%Y-%m-%d %H:%M})"


# ==============================================================================
# MODULE: GRADUATION & MULTI-DEPARTMENT CLEARANCE
# ==============================================================================

class GraduationCeremony(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        SENATE_APPROVED = "SENATE_APPROVED", "Senate Approved"
        COMPLETED = "COMPLETED", "Completed"

    academic_year = models.CharField(max_length=20, default="2025/2026")
    title = models.CharField(max_length=150)
    ceremony_date = models.DateField()
    venue = models.CharField(max_length=150, default="Main University Pavilion")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.PLANNED)
    chief_guest = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ceremony_date"]
        verbose_name_plural = "Graduation Ceremonies"

    def __str__(self):
        return f"{self.title} ({self.academic_year})"


class GraduationApplication(models.Model):
    class Classification(models.TextChoices):
        FIRST_CLASS = "FIRST_CLASS", "First Class Honours"
        SECOND_UPPER = "SECOND_UPPER", "Second Class Honours (Upper Division)"
        SECOND_LOWER = "SECOND_LOWER", "Second Class Honours (Lower Division)"
        PASS = "PASS", "Pass"
        NOT_APPLICABLE = "NOT_APPLICABLE", "Not Applicable"

    class Status(models.TextChoices):
        APPLIED = "APPLIED", "Application Submitted"
        CLEARANCE_IN_PROGRESS = "CLEARANCE_IN_PROGRESS", "Clearance in Progress"
        CLEARED = "CLEARED", "Fully Cleared"
        SENATE_APPROVED = "SENATE_APPROVED", "Senate Approved"
        GRADUATED = "GRADUATED", "Conferred / Graduated"
        REJECTED = "REJECTED", "Rejected / On Academic Hold"

    student = models.OneToOneField("accounts.StudentProfile", on_delete=models.CASCADE, related_name="graduation_record")
    ceremony = models.ForeignKey(GraduationCeremony, on_delete=models.SET_NULL, null=True, blank=True, related_name="graduands")
    applied_at = models.DateTimeField(auto_now_add=True)
    total_credits_earned = models.IntegerField(default=0)
    final_cgpa = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.00"))
    classification = models.CharField(max_length=30, choices=Classification.choices, default=Classification.SECOND_UPPER)
    certificate_serial = models.CharField(max_length=80, blank=True, unique=True, null=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.APPLIED)
    senate_approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"Graduation: {self.student.roll_no} - {self.student.user.display_name} ({self.get_classification_display()})"


class DepartmentClearance(models.Model):
    class DepartmentType(models.TextChoices):
        FINANCE = "FINANCE", "Finance Office"
        LIBRARY = "LIBRARY", "University Library"
        ACADEMIC_DEAN = "ACADEMIC_DEAN", "Academic Dean / HOD"
        STUDENT_AFFAIRS = "STUDENT_AFFAIRS", "Hostel & Student Affairs"
        REGISTRAR = "REGISTRAR", "Academic Registrar"

    class ClearanceStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Review"
        CLEARED = "CLEARED", "Cleared"
        REJECTED = "REJECTED", "Hold / Uncleared"

    application = models.ForeignKey(GraduationApplication, on_delete=models.CASCADE, related_name="clearances")
    department = models.CharField(max_length=30, choices=DepartmentType.choices)
    status = models.CharField(max_length=20, choices=ClearanceStatus.choices, default=ClearanceStatus.PENDING)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("application", "department")
        ordering = ["id"]

    def __str__(self):
        return f"{self.get_department_display()}: {self.application.student.roll_no} -> {self.status}"


# ==============================================================================
# MODULE: HOSTEL & CAMPUS ACCOMMODATION
# ==============================================================================

class HostelBlock(models.Model):
    class Gender(models.TextChoices):
        MALE = "MALE", "Male Students Only"
        FEMALE = "FEMALE", "Female Students Only"
        MIXED = "MIXED", "Co-educational"

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    campus = models.CharField(max_length=100, default="Main Campus")
    gender = models.CharField(max_length=20, choices=Gender.choices, default=Gender.MIXED)
    warden_name = models.CharField(max_length=100, blank=True, default="Hostel Warden")
    warden_phone = models.CharField(max_length=40, blank=True, default="")
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class HostelRoom(models.Model):
    class RoomType(models.TextChoices):
        SINGLE = "SINGLE", "Single Room"
        DOUBLE = "DOUBLE", "Double Occupancy"
        QUAD = "QUAD", "Quad (4-Beds)"

    block = models.ForeignKey(HostelBlock, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=30)
    floor = models.IntegerField(default=1)
    room_type = models.CharField(max_length=20, choices=RoomType.choices, default=RoomType.DOUBLE)
    capacity = models.IntegerField(default=2)
    occupied_beds = models.IntegerField(default=0)
    fee_per_semester = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("8000.00"))
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("block", "room_number")
        ordering = ["block", "room_number"]

    @property
    def available_beds(self):
        return max(0, self.capacity - self.occupied_beds)

    def __str__(self):
        return f"{self.block.code} - Room {self.room_number} ({self.available_beds}/{self.capacity} beds free)"


class HostelAllocation(models.Model):
    class Status(models.TextChoices):
        APPLIED = "APPLIED", "Applied"
        ALLOCATED = "ALLOCATED", "Allocated"
        CHECKED_IN = "CHECKED_IN", "Checked In"
        CHECKED_OUT = "CHECKED_OUT", "Checked Out"
        CANCELLED = "CANCELLED", "Cancelled"

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="hostel_allocations")
    room = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name="allocations")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE)
    applied_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    allocated_at = models.DateTimeField(null=True, blank=True)
    check_in_date = models.DateField(null=True, blank=True)
    check_out_date = models.DateField(null=True, blank=True)
    room_key_number = models.CharField(max_length=50, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"Hostel: {self.student.roll_no} -> {self.room} ({self.status})"


# ==============================================================================
# MODULE: LIBRARY MANAGEMENT & DIGITAL PAST PAPERS
# ==============================================================================

class Book(models.Model):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255)
    isbn = models.CharField(max_length=30, blank=True, default="")
    category = models.CharField(max_length=100, default="Computer Science")
    call_number = models.CharField(max_length=50, blank=True, default="")
    publisher = models.CharField(max_length=150, blank=True, default="")
    year_published = models.IntegerField(null=True, blank=True)
    total_copies = models.IntegerField(default=1)
    available_copies = models.IntegerField(default=1)
    shelf_location = models.CharField(max_length=100, default="Stack 3, Shelf B")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} by {self.author} ({self.available_copies}/{self.total_copies} available)"


class BookLoan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active Loan"
        RETURNED = "RETURNED", "Returned"
        OVERDUE = "OVERDUE", "Overdue"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="loans")
    borrower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_loans")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="issued_loans")
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    fine_accrued = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    fine_paid = models.BooleanField(default=False)

    class Meta:
        ordering = ["-issue_date"]

    def __str__(self):
        return f"Loan: {self.book.title} to {self.borrower.username} ({self.status})"


class PastExamPaper(models.Model):
    class ExamType(models.TextChoices):
        MAIN = "MAIN", "Main University Exam"
        CAT = "CAT", "Continuous Assessment Test"
        SUPPLEMENTARY = "SUPPLEMENTARY", "Supplementary / Special Exam"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="past_papers")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE)
    exam_type = models.CharField(max_length=20, choices=ExamType.choices, default=ExamType.MAIN)
    academic_year = models.CharField(max_length=20, default="2024/2025")
    title = models.CharField(max_length=200)
    file_attachment = models.FileField(upload_to="past_papers/", null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.course.code} - {self.title} ({self.academic_year})"


class AttachmentPlacement(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted for Approval"
        APPROVED = "APPROVED", "Approved"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        REJECTED = "REJECTED", "Rejected"

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="attachments")
    term = models.ForeignKey(AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True)
    company_name = models.CharField(max_length=150)
    company_branch_location = models.CharField(max_length=150, default="Headquarters")
    company_address = models.TextField(blank=True)
    company_supervisor_name = models.CharField(max_length=100)
    company_supervisor_email = models.EmailField(blank=True)
    company_supervisor_phone = models.CharField(max_length=30)
    department_or_unit = models.CharField(max_length=100, default="IT / Operations")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    academic_supervisor = models.ForeignKey("accounts.FacultyProfile", on_delete=models.SET_NULL, null=True, blank=True, related_name="supervised_attachments")
    intro_letter_reference = models.CharField(max_length=60, unique=True, null=True, blank=True)
    offer_letter = models.FileField(upload_to="attachments/offers/", null=True, blank=True)
    final_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    final_grade = models.CharField(max_length=5, blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Attachment: {self.student.roll_no} at {self.company_name} ({self.status})"

    @property
    def duration_weeks(self):
        if self.start_date and self.end_date:
            days = (self.end_date - self.start_date).days
            return max(1, round(days / 7))
        return 0


class AttachmentLogbookEntry(models.Model):
    attachment = models.ForeignKey(AttachmentPlacement, on_delete=models.CASCADE, related_name="logbook_entries")
    week_number = models.PositiveIntegerField()
    date_from = models.DateField()
    date_to = models.DateField()
    activities_summary = models.TextField()
    skills_acquired = models.TextField()
    challenges_encountered = models.TextField(blank=True)
    company_supervisor_signed = models.BooleanField(default=False)
    faculty_supervisor_reviewed = models.BooleanField(default=False)
    faculty_feedback = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["week_number"]
        unique_together = ("attachment", "week_number")

    def __str__(self):
        return f"Week {self.week_number} Logbook ({self.attachment.student.roll_no})"


class AttachmentAssessment(models.Model):
    attachment = models.OneToOneField(AttachmentPlacement, on_delete=models.CASCADE, related_name="assessment")
    assessor = models.ForeignKey("accounts.FacultyProfile", on_delete=models.CASCADE, related_name="attachment_assessments")
    assessment_date = models.DateField(default=timezone.now)
    organization_suitability_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("8.00")) # max 10
    student_attendance_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("13.00"))      # max 15
    technical_skills_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("30.00"))        # max 35
    logbook_maintenance_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("17.00"))     # max 20
    oral_presentation_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("17.00"))       # max 20
    total_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("85.00"))                   # max 100
    grade = models.CharField(max_length=5, default="A")
    assessor_comments = models.TextField(blank=True)
    industry_supervisor_comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        self.total_score = (
            (self.organization_suitability_score or Decimal(0)) +
            (self.student_attendance_score or Decimal(0)) +
            (self.technical_skills_score or Decimal(0)) +
            (self.logbook_maintenance_score or Decimal(0)) +
            (self.oral_presentation_score or Decimal(0))
        )
        if self.total_score >= Decimal("70.00"):
            self.grade = "A"
        elif self.total_score >= Decimal("60.00"):
            self.grade = "B"
        elif self.total_score >= Decimal("50.00"):
            self.grade = "C"
        elif self.total_score >= Decimal("40.00"):
            self.grade = "D"
        else:
            self.grade = "F"

    def __str__(self):
        return f"Assessment: {self.attachment.student.roll_no} - Score: {self.total_score} ({self.grade})"


# ==============================================================================
# COURSE & LECTURER EVALUATION (QA SURVEY)
# ==============================================================================

class EvaluationWindow(models.Model):
    """Admin-controlled window that opens/closes the evaluation period per term."""
    term = models.OneToOneField(AcademicTerm, on_delete=models.CASCADE,
                                related_name="evaluation_window")
    is_open = models.BooleanField(default=False)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-term__start_date"]

    def __str__(self):
        state = "Open" if self.is_open else "Closed"
        return f"Evaluation Window — {self.term.name} [{state}]"


class CourseEvaluation(models.Model):
    """
    Anonymous student evaluation of a course/lecturer for a given term.
    Anonymity is preserved: no direct FK to the student is stored after submission.
    Instead we track a hashed token so the student can only submit once per enrollment.
    """
    RATING = [(i, str(i)) for i in range(1, 6)]   # 1-5 Likert scale

    # Context (non-identifying but needed for analytics)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="evaluations")
    term = models.ForeignKey(AcademicTerm, on_delete=models.CASCADE, related_name="evaluations")
    # Hashed token: SHA-256( student_id || course_id || term_id ) — used ONLY to prevent duplicate submissions
    submission_token = models.CharField(max_length=64, unique=True, db_index=True)

    # Dimension ratings (1-5)
    teaching_quality     = models.PositiveSmallIntegerField(choices=RATING)
    course_content       = models.PositiveSmallIntegerField(choices=RATING)
    assessment_fairness  = models.PositiveSmallIntegerField(choices=RATING)
    resources_adequacy   = models.PositiveSmallIntegerField(choices=RATING)
    overall_satisfaction = models.PositiveSmallIntegerField(choices=RATING)

    # Open-ended (completely anonymous)
    strengths  = models.TextField(blank=True, default="",
                                  help_text="What did you find most valuable about this course?")
    suggestions = models.TextField(blank=True, default="",
                                   help_text="How could this course be improved?")

    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]

    @property
    def average_score(self):
        dims = [self.teaching_quality, self.course_content,
                self.assessment_fairness, self.resources_adequacy, self.overall_satisfaction]
        return round(sum(dims) / len(dims), 2)

    def __str__(self):
        return f"Evaluation — {self.course.code} / {self.term.name} (avg {self.average_score})"


# ==============================================================================
# 20. GRANULAR ROLES, PERMISSIONS & USER-LEVEL ACCESS OVERRIDES
# ==============================================================================

class SystemPermission(models.Model):
    """
    Granular permission definition across all university operational modules.
    """
    code = models.CharField(max_length=60, unique=True, db_index=True,
                            help_text="Machine identifier e.g. 'exams.approve_senate'")
    name = models.CharField(max_length=120)
    module = models.CharField(max_length=60, db_index=True,
                              help_text="System subsystem e.g. 'Examinations', 'Academics', 'Finance'")
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["module", "name"]

    def __str__(self):
        return f"[{self.module}] {self.name} ({self.code})"


class StaffRole(models.Model):
    """
    Custom and pre-defined administrative & staff roles with assigned permission sets.
    """
    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=60, unique=True)
    description = models.TextField(blank=True, default="")
    color = models.CharField(max_length=20, default="#6C5CE7",
                             help_text="Hex code for role badge e.g. '#00b894'")
    is_system_role = models.BooleanField(default=False,
                                         help_text="Core immutable system role")
    permissions = models.ManyToManyField(SystemPermission, related_name="roles", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    @property
    def permissions_count(self):
        return self.permissions.count()

    def __str__(self):
        return self.name


class StaffRoleAssignment(models.Model):
    """
    Assigns a Staff member / User to one or more StaffRoles with optional Department scope.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="staff_role_assignments")
    role = models.ForeignKey(StaffRole, on_delete=models.CASCADE, related_name="assignments")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="staff_role_assignments",
                                   help_text="Optional departmental scope (e.g. HOD of SCIT)")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-assigned_at"]
        unique_together = [("user", "role", "department")]

    def __str__(self):
        dept_str = f" @ {self.department.code}" if self.department else ""
        return f"{self.user.get_full_name() or self.user.username} -> {self.role.name}{dept_str}"


class UserPermissionOverride(models.Model):
    """
    Individual user-level permission override (Explicit Grant or Explicit Deny)
    taking priority over base role permissions.
    """
    class OverrideType(models.TextChoices):
        GRANT = "GRANT", "Explicit Grant (Allow)"
        DENY = "DENY", "Explicit Deny (Block/Revoke)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="permission_overrides")
    permission = models.ForeignKey(SystemPermission, on_delete=models.CASCADE,
                                   related_name="user_overrides")
    override_type = models.CharField(max_length=10, choices=OverrideType.choices,
                                     default=OverrideType.GRANT)
    reason = models.TextField(blank=True, default="",
                              help_text="Audit rationale for special access override")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user", "permission__module", "permission__name"]
        unique_together = [("user", "permission")]

    def __str__(self):
        return f"{self.user.username}: {self.permission.code} [{self.override_type}]"


class DocumentReleaseControl(models.Model):
    """
    Registry control gates for academic document access, downloads, and lifecycle management.
    Controls scheduled release windows, financial clearance gates, and administrative locking.
    """
    class DocumentType(models.TextChoices):
        TRANSCRIPT_OFFICIAL = "transcript_official", "Official Academic Transcript"
        TRANSCRIPT_PROVISIONAL = "transcript_provisional", "Provisional Transcript"
        EXAM_CARD = "exam_card", "Examination Card"
        RESULTS_STATEMENT = "results_statement", "Statement of Results"
        PROGRESS_REPORT = "progress_report", "Progressive Academic Report"
        ADMISSION_LETTER = "admission_letter", "Official Admission Letter"
        APPLICATION_DOCS = "application_docs", "Application & Admission Documents"

    term = models.ForeignKey(
        "AcademicTerm",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_controls",
        help_text="Academic term this control applies to (leave empty for universal policy)."
    )
    document_type = models.CharField(
        max_length=32,
        choices=DocumentType.choices,
        help_text="The category of document governed by this control."
    )
    is_open = models.BooleanField(
        default=True,
        help_text="Master toggle to immediately permit or block student access/downloads."
    )
    open_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when downloads automatically become available to students."
    )
    lock_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when downloads automatically lock / expire."
    )
    require_financial_clearance = models.BooleanField(
        default=False,
        help_text="Require student to have no outstanding fee balance beyond the allowed threshold."
    )
    max_allowed_fee_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Maximum outstanding balance (KES) allowed before document is locked."
    )
    require_senate_approval = models.BooleanField(
        default=False,
        help_text="Require results to be Senate-approved before opening this document."
    )
    notes = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Custom message displayed to students when access is locked."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_type", "-term__start_date"]
        unique_together = [("term", "document_type")]

    def __str__(self):
        term_str = f" ({self.term.name})" if self.term else " (Universal)"
        status = "OPEN" if self.is_open else "LOCKED"
        return f"{self.get_document_type_display()}{term_str} [{status}]"


class ApplicationAttachment(models.Model):
    """
    Documents and supporting certificates submitted during application (KCSE Slip, ID, Photos).
    Maintains permanent applicant -> application -> submitted documents -> admission -> student lifecycle.
    """
    class DocType(models.TextChoices):
        KCSE_CERTIFICATE = "KCSE_CERTIFICATE", "KCSE Certificate / Result Slip"
        NATIONAL_ID = "NATIONAL_ID", "National ID / Birth Certificate / Passport"
        PASSPORT_PHOTO = "PASSPORT_PHOTO", "Passport Size Photograph"
        LEAVING_CERTIFICATE = "LEAVING_CERTIFICATE", "School Leaving Certificate"
        TRANSCRIPT = "TRANSCRIPT", "Previous Academic Transcript"
        SPONSOR_LETTER = "SPONSOR_LETTER", "Sponsorship / Financial Guarantee"
        MEDICAL_REPORT = "MEDICAL_REPORT", "Medical Examination Report"
        OTHER = "OTHER", "Other Supporting Document"

    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Verification"
        VERIFIED = "VERIFIED", "Verified & Approved"
        REJECTED = "REJECTED", "Rejected / Incomplete"
        FLAGGED = "FLAGGED", "Flagged for Investigation"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="attachments")
    document_type = models.CharField(max_length=40, choices=DocType.choices, default=DocType.OTHER)
    name = models.CharField(max_length=200, help_text="Display title e.g. 'KCSE Result Slip'")
    file = models.FileField(upload_to="applications/attachments/")
    file_name = models.CharField(max_length=255, blank=True, default="")
    file_size = models.PositiveIntegerField(default=0, help_text="Size in bytes")
    mime_type = models.CharField(max_length=100, blank=True, default="application/pdf")
    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING, db_index=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_attachments")
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(blank=True, default="")
    is_visible_to_student = models.BooleanField(default=True, help_text="Permit student to view/download this file in portal")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.name} ({self.get_document_type_display()}) - {self.application.application_number}"

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            import os
            self.file_name = os.path.basename(self.file.name)
        if self.file and not self.file_size:
            try:
                self.file_size = self.file.size
            except Exception:
                pass
        super().save(*args, **kwargs)


class AdmissionDocumentTemplate(models.Model):
    """
    Configurable document templates with dynamic placeholders for admission letters, offers, and forms.
    """
    class DocumentType(models.TextChoices):
        ADMISSION_LETTER = "ADMISSION_LETTER", "Official Admission Letter"
        PROVISIONAL_OFFER = "PROVISIONAL_OFFER", "Provisional Letter of Offer"
        ACCEPTANCE_FORM = "ACCEPTANCE_FORM", "Acceptance of Offer Form"
        CALLING_LETTER = "CALLING_LETTER", "Reporting & Calling Letter"

    name = models.CharField(max_length=160, help_text="Template name e.g. 'Standard Undergraduate Admission Letter'")
    document_type = models.CharField(max_length=40, choices=DocumentType.choices, default=DocumentType.ADMISSION_LETTER)
    academic_year = models.ForeignKey("AcademicYear", on_delete=models.SET_NULL, null=True, blank=True, help_text="Applicable academic year (leave blank for universal default)")
    program = models.ForeignKey(Program, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific programme (leave blank for all programmes)")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default template for this document type")
    version = models.PositiveIntegerField(default=1)

    # Document content & structure with dynamic placeholder tags
    header_title = models.CharField(max_length=255, default="OFFICE OF THE REGISTRAR (ACADEMIC AFFAIRS)")
    salutation_template = models.CharField(max_length=255, default="Dear {{student_name}},")
    subject_template = models.CharField(max_length=255, default="ADMISSION TO THE {{programme_name}} ({{programme_code}})")
    body_template = models.TextField(
        default="I am pleased to inform you that you have been offered admission to the {{programme_name}} in the {{faculty_name}} for the {{academic_year}} Academic Year commencing in {{semester}}.\n\nYou are required to report to the university on {{reporting_date}} for orientation, registration and fee payment verification."
    )
    terms_and_conditions = models.TextField(
        blank=True,
        default="1. This offer of admission is subject to verification of your original academic and identification certificates.\n2. All university fees must be paid in full or in approved installments prior to course registration.\n3. University rules, regulations and academic policies apply at all times."
    )
    fee_schedule_instructions = models.TextField(
        blank=True,
        default="Tuition and statutory fees must be deposited to the University Bank Account (Absa Bank, Acc No. 0451234567, Westlands Branch) or via M-Pesa Paybill 522522 with your Application/Student Number as reference."
    )
    signatory_name = models.CharField(max_length=120, default="Dr. Margaret Omolo, PhD")
    signatory_title = models.CharField(max_length=120, default="Registrar, Academic & Student Affairs")
    signatory_signature = models.ImageField(upload_to="admissions/signatures/", null=True, blank=True)
    official_seal = models.ImageField(upload_to="admissions/seals/", null=True, blank=True)
    verification_base_url = models.CharField(max_length=255, default="https://ums.ac.ke/verify-admission/")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-updated_at"]

    def __str__(self):
        return f"{self.name} (v{self.version}) [{self.get_document_type_display()}]"


class IssuedAdmissionDocument(models.Model):
    """
    Versioned record of officially issued admission letters for an Application/Student.
    Preserves exact historical rendered snapshots, metadata, and PDF storage.
    """
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        CURRENT = "CURRENT", "Current Active Version"
        SUPERSEDED = "SUPERSEDED", "Superseded by Newer Version"
        REVOKED = "REVOKED", "Revoked / Voided"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="issued_documents")
    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_documents")
    template = models.ForeignKey(AdmissionDocumentTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="issued_documents")
    document_type = models.CharField(max_length=40, choices=AdmissionDocumentTemplate.DocumentType.choices, default=AdmissionDocumentTemplate.DocumentType.ADMISSION_LETTER)
    document_reference = models.CharField(max_length=80, unique=True, db_index=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CURRENT, db_index=True)
    issue_date = models.DateField(default=timezone.now)
    reporting_date = models.DateField(null=True, blank=True)
    academic_year = models.ForeignKey("AcademicYear", on_delete=models.SET_NULL, null=True, blank=True)
    semester = models.ForeignKey("AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True)
    rendered_context = models.JSONField(default=dict, blank=True, help_text="Snapshot of all evaluation tokens at time of issue")
    rendered_content = models.TextField(blank=True, default="", help_text="Rendered text content")
    pdf_file = models.FileField(upload_to="admissions/issued_letters/", null=True, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="generated_admission_documents")
    generated_at = models.DateTimeField(default=timezone.now)
    # Signatory Snapshot Information (Immutable preservation of historical signature state)
    signatory = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="signed_admission_documents")
    signatory_name = models.CharField(max_length=150, blank=True, default="")
    signatory_title = models.CharField(max_length=150, blank=True, default="")
    signatory_office = models.CharField(max_length=150, blank=True, default="")
    signature_version = models.PositiveIntegerField(null=True, blank=True)
    signature_snapshot = models.FileField(upload_to="signatures/document_snapshots/", null=True, blank=True, help_text="Immutable snapshot of signature image at issue time")
    co_signatory = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="co_signed_admission_documents")
    co_signatory_name = models.CharField(max_length=150, blank=True, default="")
    co_signatory_title = models.CharField(max_length=150, blank=True, default="")
    co_signature_snapshot = models.FileField(upload_to="signatures/document_snapshots/", null=True, blank=True)
    change_reason = models.CharField(max_length=255, blank=True, default="", help_text="Reason for generation or regeneration")
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="revoked_admission_documents")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True, default="")
    is_current_version = models.BooleanField(default=True, db_index=True)
    is_visible_to_student = models.BooleanField(default=True)

    class Meta:
        ordering = ["-version", "-generated_at"]

    def __str__(self):
        return f"{self.document_reference} (v{self.version}) - {self.application.full_name} [{self.status}]"

    def get_rendered_value(self, key, default=""):
        if isinstance(self.rendered_context, dict):
            return self.rendered_context.get(key, default)
        return default

    @property
    def is_revoked(self):
        return self.status == self.Status.REVOKED

    @property
    def is_valid(self):
        return self.status in [self.Status.CURRENT, self.Status.ISSUED]


class DocumentDeliveryLog(models.Model):
    """
    Audit log of document transmissions (Email, SMS, Portal Notification) to applicants and students.
    """
    class Method(models.TextChoices):
        EMAIL = "EMAIL", "Email Transmission"
        SMS = "SMS", "SMS Notification"
        PORTAL_NOTICE = "PORTAL_NOTICE", "Student Portal Notification"

    class Status(models.TextChoices):
        SENT = "SENT", "Sent Successfully"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Failed"

    document = models.ForeignKey(IssuedAdmissionDocument, on_delete=models.CASCADE, related_name="delivery_logs")
    delivery_method = models.CharField(max_length=20, choices=Method.choices, default=Method.EMAIL)
    recipient = models.CharField(max_length=160, help_text="Email address, phone number, or student ID")
    subject = models.CharField(max_length=255, blank=True, default="")
    message_body = models.TextField()
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_document_deliveries")
    sent_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    failure_reason = models.TextField(blank=True, default="")
    ip_address = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.delivery_method} to {self.recipient} [{self.status}] - {self.document.document_reference}"


class ApplicationCustomField(models.Model):
    """
    Dynamic administrator-defined fields for admissions applications (e.g. 'Campus', 'Accomodation Preference').
    Values are automatically available in Admission Document templates.
    """
    class FieldType(models.TextChoices):
        TEXT = "TEXT", "Short Text"
        TEXTAREA = "TEXTAREA", "Long Text"
        SELECT = "SELECT", "Dropdown Select"
        CHECKBOX = "CHECKBOX", "Yes / No Checkbox"
        NUMBER = "NUMBER", "Number"

    name = models.SlugField(max_length=60, unique=True, help_text="Identifier used in templates e.g. 'campus'")
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    choices_list = models.TextField(blank=True, default="", help_text="Comma-separated choices for dropdowns e.g. 'Main Campus, Nairobi CBD, Mombasa'")
    is_required = models.BooleanField(default=False)
    default_value = models.CharField(max_length=255, blank=True, default="")
    help_text = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "label"]

    def __str__(self):
        return f"{self.label} ({{{{{self.name}}}}})"

    def get_choices(self):
        if not self.choices_list:
            return []
        return [c.strip() for c in self.choices_list.split(",") if c.strip()]


class ApplicationCustomFieldValue(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="custom_values")
    field = models.ForeignKey(ApplicationCustomField, on_delete=models.CASCADE, related_name="application_values")
    value = models.TextField(blank=True, default="")

    class Meta:
        unique_together = [("application", "field")]

    def __str__(self):
        return f"{self.field.name}: {self.value}"


class DocumentSignatureConfig(models.Model):
    """
    Central configurable document-to-signature policy mapping.
    Controls signatory roles, explicit authorized users, placement, number of signatures,
    and whether a signature is required for document finalization.
    """
    class DocumentType(models.TextChoices):
        ADMISSION_LETTER = "ADMISSION_LETTER", "Admission Letter"
        OFFER_LETTER = "OFFER_LETTER", "Offer Letter"
        ACADEMIC_TRANSCRIPT = "ACADEMIC_TRANSCRIPT", "Academic Transcript"
        PROVISIONAL_TRANSCRIPT = "PROVISIONAL_TRANSCRIPT", "Provisional Transcript"
        EXAM_RESULT_SLIP = "EXAM_RESULT_SLIP", "Examination Result Slip"
        DEGREE_CERTIFICATE = "DEGREE_CERTIFICATE", "Degree Certificate"
        FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT", "Official Financial Statement"
        DEFERMENT_DECISION = "DEFERMENT_DECISION", "Deferment Decision Letter"
        CLEARANCE_CERTIFICATE = "CLEARANCE_CERTIFICATE", "Clearance Certificate"
        OTHER = "OTHER", "Other Official Document"

    class SignaturePosition(models.TextChoices):
        BOTTOM_RIGHT = "BOTTOM_RIGHT", "Bottom Right"
        BOTTOM_LEFT = "BOTTOM_LEFT", "Bottom Left"
        BOTTOM_CENTER = "BOTTOM_CENTER", "Bottom Center"
        DUAL_BOTTOM = "DUAL_BOTTOM", "Dual Signatures (Left & Right)"

    document_type = models.CharField(max_length=50, choices=DocumentType.choices, unique=True)
    title = models.CharField(max_length=120, help_text="e.g. Official Admission Letter Signatory Policy")
    is_signature_required = models.BooleanField(default=True)
    required_roles = models.CharField(
        max_length=255,
        default="ADMIN,REGISTRAR,STAFF",
        help_text="Comma-separated user roles authorized to sign (e.g. REGISTRAR,STAFF,ADMIN)"
    )
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="authorized_signature_documents",
        help_text="Explicitly authorized individuals (leave empty to allow anyone with required role)"
    )
    number_of_signatures = models.PositiveSmallIntegerField(default=1)
    primary_label = models.CharField(max_length=100, default="Registrar (Academic Affairs)")
    primary_position = models.CharField(max_length=30, choices=SignaturePosition.choices, default=SignaturePosition.BOTTOM_RIGHT)
    secondary_label = models.CharField(max_length=100, blank=True, default="", help_text="For dual signature documents")
    secondary_position = models.CharField(max_length=30, choices=SignaturePosition.choices, default=SignaturePosition.BOTTOM_LEFT)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_document_type_display()} Signature Config"

    def get_authorized_roles_list(self):
        return [r.strip().upper() for r in (self.required_roles or "").split(",") if r.strip()]

    def is_user_authorized(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or getattr(user, 'role', '') == 'ADMIN':
            return True
        if self.authorized_users.exists():
            return self.authorized_users.filter(pk=user.pk).exists()
        allowed_roles = self.get_authorized_roles_list()
        user_role = str(getattr(user, 'role', '')).upper()
        return user_role in allowed_roles


from .module_models import SystemModule, SystemSubmodule, SystemFeature, ModuleDependency  # noqa: E402,F401
from .control_models import SystemRestriction, MessageTemplate, MessageDelivery, ControlNotification, ControlHeartbeat  # noqa: E402,F401
from .backup_models import (  # noqa: E402,F401
    BackupStorage,
    BackupRetentionPolicy,
    BackupSchedule,
    BackupJob,
    BackupArtifact,
    BackupVerification,
    BackupRestoreJob,
    BackupLog,
    BackupSetting,
)
from .golive_models import (  # noqa: E402,F401
    GoLiveCategory,
    ReadinessStatus,
    GoLiveReadiness,
    IssueSeverity,
    IssueStatus,
    GoLiveIssue,
)
from .identity_models import (  # noqa: E402,F401
    AccountStatus,
    UserType,
    UserGroup,
    UserGroupMembership,
    UserAccount,
    PasswordHistoryEntry,
    PasswordResetToken,
    LoginRecord,
    InstitutionalEmail,
    EmailDeliveryRecord,
    UserImportBatch,
)
