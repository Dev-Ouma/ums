from django.contrib import admin

from .models import (
    Assignment, Attendance, ClassSchedule, Course, Department, Enrollment, Event,
    Exam, FeeInvoice, Notice, Payment, Program, Result, Submission, AcademicTerm,
)

for model in (Department, Program, AcademicTerm, Course, Enrollment, Attendance,
              Assignment, Submission, FeeInvoice, Payment, Event, Notice, ClassSchedule):
    admin.site.register(model)


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
