from django.contrib import admin

from .models import (
    Assignment, Attendance, ClassSchedule, Course, Department, Enrollment, Event,
    Exam, FeeInvoice, Notice, Payment, Program, Result, Submission, AcademicTerm,
    DocumentReleaseControl, School, AcademicYear,
)

for model in (Course, Enrollment, Attendance,
              Assignment, Submission, FeeInvoice, Payment, Event, Notice, ClassSchedule):
    admin.site.register(model)


class SemesterInline(admin.TabularInline):
    model = AcademicTerm
    extra = 1
    fields = ("name", "term_type", "semester_number", "start_date", "end_date", "registration_start_date", "registration_end_date", "status", "is_current")


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "start_date", "end_date", "status", "is_current", "semesters_count")
    list_filter = ("status", "is_current")
    search_fields = ("name", "code", "reference_no")
    inlines = [SemesterInline]

    def semesters_count(self, obj):
        return obj.semesters.count()
    semesters_count.short_description = "Semesters"


@admin.register(AcademicTerm)
class AcademicTermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "term_type", "semester_number", "start_date", "end_date", "status", "is_current")
    list_filter = ("status", "is_current", "term_type", "academic_year")
    search_fields = ("name", "academic_year__name")


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "dean_name", "department_count")
    search_fields = ("code", "name", "dean_name")

    def department_count(self, obj):
        return obj.departments.count()
    department_count.short_description = "Departments"


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "school", "program_count", "course_count")
    list_filter = ("school",)
    search_fields = ("code", "name")

    def program_count(self, obj):
        return obj.programs.count()
    program_count.short_description = "Programmes"

    def course_count(self, obj):
        return obj.courses.count()
    course_count.short_description = "Courses"


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "level", "program_type", "study_mode", "status", "student_count", "course_count")
    list_filter = ("status", "level", "program_type", "study_mode", "department__school", "department")
    search_fields = ("code", "name", "award_title", "department__name")

    def student_count(self, obj):
        return obj.students.count()
    student_count.short_description = "Students"

    def course_count(self, obj):
        return obj.courses.count()
    course_count.short_description = "Courses"


# Result changes must pass through the examination workflow, including for superusers.
class ReadOnlyExamAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Exam, ReadOnlyExamAdmin)
admin.site.register(Result, ReadOnlyExamAdmin)


@admin.register(DocumentReleaseControl)
class DocumentReleaseControlAdmin(admin.ModelAdmin):
    list_display = (
        "document_type", "term", "is_open", "open_date", "lock_date",
        "require_financial_clearance", "max_allowed_fee_balance", "require_senate_approval"
    )
    list_filter = ("document_type", "is_open", "require_financial_clearance", "term")
    search_fields = ("document_type", "notes")

