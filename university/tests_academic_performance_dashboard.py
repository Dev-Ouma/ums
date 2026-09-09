from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.models import (
    AcademicTerm, Course, Department, Exam, Program, Result
)
from university import services

User = get_user_model()


class AcademicPerformanceDashboardTest(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_user(
            username="admin_perf",
            email="admin_perf@example.com",
            password="Password123!",
            role=Role.ADMIN,
        )

        # Non-admin user (student)
        self.student_user = User.objects.create_user(
            username="stud_perf",
            email="stud_perf@example.com",
            password="Password123!",
            role=Role.STUDENT,
        )

        # Department & Program
        self.dept_cs = Department.objects.create(name="Computer Science", code="CS", color="#6C5CE7")
        self.dept_biz = Department.objects.create(name="Business", code="BUS", color="#00b894")

        self.prog_cs = Program.objects.create(
            name="BSc Computer Science", code="BSCS", department=self.dept_cs
        )
        self.prog_biz = Program.objects.create(
            name="BBA Business Admin", code="BBA", department=self.dept_biz
        )

        # Terms
        self.term_1 = AcademicTerm.objects.create(
            name="Term 1 2026", start_date="2026-01-10", end_date="2026-04-30", is_current=False
        )
        self.term_2 = AcademicTerm.objects.create(
            name="Term 2 2026", start_date="2026-05-10", end_date="2026-08-30", is_current=True
        )

        # Courses
        self.course_1 = Course.objects.create(
            code="CS101", title="Intro to CS", department=self.dept_cs,
            program=self.prog_cs, semester_no=1, credits=3
        )
        self.course_2 = Course.objects.create(
            code="BUS101", title="Principles of Management", department=self.dept_biz,
            program=self.prog_biz, semester_no=1, credits=3
        )

        # Students
        self.student_1 = StudentProfile.objects.create(
            user=self.student_user, roll_no="CS001", program=self.prog_cs, current_semester=1
        )
        self.user_2 = User.objects.create_user(
            username="stud_perf2", email="stud2@example.com", password="Password123!", role=Role.STUDENT
        )
        self.student_2 = StudentProfile.objects.create(
            user=self.user_2, roll_no="BUS001", program=self.prog_biz, current_semester=1
        )

        # Exams (FINAL & PUBLISHED)
        self.exam_1 = Exam.objects.create(
            name="CS101 Final", course=self.course_1, term=self.term_1,
            kind=Exam.Kind.FINAL, status=Exam.Status.PUBLISHED,
            max_marks=100, pass_mark=40
        )
        self.exam_2 = Exam.objects.create(
            name="BUS101 Final", course=self.course_2, term=self.term_2,
            kind=Exam.Kind.FINAL, status=Exam.Status.PUBLISHED,
            max_marks=100, pass_mark=40
        )

        # Results:
        # student 1 gets 85 (Grade A, 4.0 GP) in CS101
        self.res_1 = Result.objects.create(
            exam=self.exam_1, student=self.student_1,
            marks_obtained=Decimal("85.0"), attendance="PRESENT"
        )
        # student 2 gets 35 (Grade F, 0.0 GP) in BUS101 -> at risk!
        self.res_2 = Result.objects.create(
            exam=self.exam_2, student=self.student_2,
            marks_obtained=Decimal("35.0"), attendance="PRESENT"
        )

    def test_academic_performance_data_aggregation(self):
        data = services.academic_performance_data()
        self.assertEqual(data["total_results"], 2)
        self.assertEqual(data["total_students"], 2)
        self.assertEqual(data["pass_count"], 1)
        self.assertEqual(data["fail_count"], 1)
        self.assertEqual(data["pass_rate"], 50.0)
        self.assertEqual(data["fail_rate"], 50.0)
        self.assertEqual(data["at_risk_count"], 1)
        self.assertEqual(len(data["at_risk_details"]), 1)
        self.assertEqual(data["at_risk_details"][0]["roll_no"], "BUS001")
        self.assertIn("A", data["grade_labels"])
        self.assertIn("F", data["grade_labels"])

    def test_academic_performance_data_filtered_by_department(self):
        data = services.academic_performance_data(department_id=self.dept_cs.pk)
        self.assertEqual(data["total_results"], 1)
        self.assertEqual(data["pass_rate"], 100.0)
        self.assertEqual(data["fail_rate"], 0.0)
        self.assertEqual(data["at_risk_count"], 0)

    def test_academic_performance_data_filtered_by_term(self):
        data = services.academic_performance_data(term_id=self.term_1.pk)
        self.assertEqual(data["total_results"], 1)
        self.assertEqual(data["avg_gpa"], 4.0)

    def test_api_academic_performance_endpoint_requires_auth(self):
        url = reverse("university:api_academic_performance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_api_academic_performance_endpoint_forbidden_for_student(self):
        self.client.login(username="stud_perf", password="Password123!")
        url = reverse("university:api_academic_performance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_api_academic_performance_endpoint_admin_success(self):
        self.client.login(username="admin_perf", password="Password123!")
        url = reverse("university:api_academic_performance")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["total_results"], 2)
        self.assertEqual(json_data["at_risk_count"], 1)

    def test_admin_dashboard_renders_academic_performance(self):
        self.client.login(username="admin_perf", password="Password123!")
        url = reverse("university:dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Performance Overview")
        self.assertContains(response, "academicTrendChart")
        self.assertContains(response, "gradeDistChart")
        self.assertContains(response, "deptPerfChart")
        self.assertContains(response, "initial-acad-perf-data")
        self.assertContains(response, "Students at Risk")
        self.assertContains(response, "Dean's Honor Roll")
        self.assertContains(response, "href=\"/manage/students/\"")
        self.assertContains(response, "href=\"/manage/faculty/\"")
        self.assertContains(response, "href=\"/manage/fees/\"")
        self.assertContains(response, "View All")
        self.assertContains(response, "Pending Approvals Hub")
        self.assertContains(response, "Admit Student")
        self.assertContains(response, "Admissions Review")
