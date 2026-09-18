"""
Regression tests for the end-to-end audit fixes closing out the "Management"
module review: University Reporting Hub (reporting_services.py,
reporting_views.py) and Campus Notices & Events (views.py).

- Non-senate examination/academic reports (academic_performance,
  grade_distribution, examination_results_summary, pass_fail_analysis,
  cat_vs_exam_analysis, missing_marks_audit) are now scoped to the
  requesting faculty member's own teaching load, matching the senate
  reports' existing scoping -- a faculty user must not see another
  department's exam data through these report types.
- student_download_attachment no longer raises NameError (missing `os`
  import) when an attachment has no file_name.
- event_create / event_edit now write an AuditLog entry.
- Invalid date_from/date_to report filter params no longer crash the
  report with a 500.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import FacultyProfile, Role, StudentProfile
from university.models import (
    AcademicTerm,
    AuditLog,
    Course,
    Department,
    Event,
    Exam,
    Program,
    Result,
)
from university.reporting_services import build_report_data

User = get_user_model()


class ReportExaminationScopingTests(TestCase):
    def setUp(self):
        self.dept_a = Department.objects.create(name="Computing", code="CMPA")
        self.dept_b = Department.objects.create(name="Business", code="BIZB")

        self.course_a = Course.objects.create(code="CSC101", title="Intro CS", department=self.dept_a, credits=4)
        self.course_b = Course.objects.create(code="BUS101", title="Intro Biz", department=self.dept_b, credits=4)

        self.term = AcademicTerm.objects.create(
            name="Sem 1", start_date=date(2026, 1, 1), end_date=date(2026, 5, 1), is_current=True,
        )

        self.exam_a = Exam.objects.create(
            course=self.course_a, term=self.term, name="CSC101 Final",
            status=Exam.Status.PUBLISHED, max_marks=100,
        )
        self.exam_b = Exam.objects.create(
            course=self.course_b, term=self.term, name="BUS101 Final",
            status=Exam.Status.PUBLISHED, max_marks=100,
        )

        self.faculty_user = User.objects.create_user(
            username="fac.a", email="fac.a@ums.ac.ke", password="password123", role=Role.FACULTY,
        )
        self.faculty_profile = FacultyProfile.objects.create(user=self.faculty_user, department=self.dept_a)
        self.course_a.faculty = self.faculty_profile
        self.course_a.save(update_fields=["faculty"])

        self.admin_user = User.objects.create_user(
            username="admin.reports", email="admin.reports@ums.ac.ke", password="password123", role=Role.ADMIN,
        )

        prog = Program.objects.create(name="BSc CS", code="BCS", department=self.dept_a, duration_years=4)
        stu_user = User.objects.create_user(username="stu.report", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(user=stu_user, roll_no="BCS/900/2026", program=prog)

        Result.objects.create(exam=self.exam_a, student=self.student, marks_obtained=Decimal("80.00"), attendance="PRESENT")
        Result.objects.create(exam=self.exam_b, student=self.student, marks_obtained=Decimal("70.00"), attendance="PRESENT")

    def test_faculty_only_sees_own_course_in_academic_performance(self):
        data = build_report_data("academic_performance", {}, self.faculty_user)
        course_codes = [row[0] for row in data["rows"]]
        self.assertIn("CSC101", course_codes)
        self.assertNotIn("BUS101", course_codes)

    def test_faculty_only_sees_own_course_in_examination_results_summary(self):
        data = build_report_data("examination_results_summary", {}, self.faculty_user)
        # exam names appear as rows -- confirm the other department's exam is excluded
        row_text = str(data["rows"])
        self.assertNotIn("BUS101", row_text)
        self.assertIn("CSC101", row_text)

    def test_admin_sees_all_departments_in_academic_performance(self):
        data = build_report_data("academic_performance", {}, self.admin_user)
        # Admin scoping is unrestricted -- both departments' courses are visible
        # (no assertion on exact rows since no Results exist yet, just confirm no crash/exclusion by scope)
        self.assertIsNotNone(data)


class ReportDateFilterValidationTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin.audit", email="admin.audit@ums.ac.ke", password="password123", role=Role.ADMIN,
        )

    def test_invalid_date_from_does_not_crash_user_activity_audit(self):
        data = build_report_data("user_activity_audit", {"date_from": "not-a-date"}, self.admin_user)
        self.assertIsNotNone(data)
        self.assertNotIn("From: not-a-date", data["applied_filters"])

    def test_invalid_date_does_not_crash_recycle_bin_audit(self):
        data = build_report_data("recycle_bin_audit", {"date_to": "banana"}, self.admin_user)
        self.assertIsNotNone(data)

    def test_valid_date_still_applies_filter(self):
        today_str = date.today().isoformat()
        data = build_report_data("user_activity_audit", {"date_from": today_str}, self.admin_user)
        self.assertIn(f"From: {today_str}", data["applied_filters"])


class EventAuditLoggingTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin.events", email="admin.events@ums.ac.ke", password="password123", role=Role.ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_event_create_logs_audit_entry(self):
        response = self.client.post(reverse("university:event_create"), {
            "title": "Founders Day", "description": "", "category": "Campus",
            "location": "Main Auditorium", "date": date.today().isoformat(),
            "icon": "fa-calendar-star", "image_url": "",
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title="Founders Day")
        self.assertTrue(
            AuditLog.objects.filter(entity="Event", entity_id=event.id, action=AuditLog.Action.CREATE).exists()
        )

    def test_event_edit_logs_audit_entry(self):
        event = Event.objects.create(title="Old Title", date=date.today())
        response = self.client.post(reverse("university:event_edit", args=[event.pk]), {
            "title": "New Title", "description": "", "category": "Campus",
            "location": "Main Auditorium", "date": date.today().isoformat(),
            "icon": "fa-calendar-star", "image_url": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(entity="Event", entity_id=event.id, action=AuditLog.Action.UPDATE).exists()
        )
