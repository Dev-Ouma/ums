from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import StudentProfile
from university.models import Department, Program
from university.progressive_report_io import build_progressive_report_context

User = get_user_model()


class StudentProgressiveReportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.dept = Department.objects.create(name="Computer Science", code="CS")
        self.program = Program.objects.create(name="BSc Computer Science", code="BSCS", department=self.dept)

        # Staff (admin) user
        self.admin_user = User.objects.create_user(
            username="adminuser",
            email="admin@university.edu",
            password="Password123!",
            is_staff=True,
            role="ADMIN"
        )

        # Student user & profile
        self.student_user = User.objects.create_user(
            username="student1",
            email="student1@university.edu",
            password="Password123!",
            role="STUDENT"
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="CS/001/2024",
            program=self.program,
            current_semester=3
        )

    def test_progressive_report_context_builder(self):
        ctx = build_progressive_report_context(self.student)
        self.assertEqual(ctx["student"], self.student)
        self.assertIn("cgpa", ctx)
        self.assertIn("semesters", ctx)
        self.assertIn("earned_credits", ctx)

    def test_admin_progressive_reports_directory_view(self):
        self.client.login(username="adminuser", password="Password123!")
        url = reverse("university:admin_progressive_reports")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Progressive Reports")
        self.assertContains(response, "CS/001/2024")

    def test_progressive_report_detail_view(self):
        self.client.login(username="adminuser", password="Password123!")
        url = reverse("university:progressive_report_detail", kwargs={"student_id": self.student.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Progressive Report")
        self.assertContains(response, "CS/001/2024")

    def test_progressive_report_export_pdf(self):
        self.client.login(username="adminuser", password="Password123!")
        url = reverse("university:progressive_report_export", kwargs={"student_id": self.student.pk, "fmt": "pdf"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertTrue(len(response.content) > 0)

    def test_progressive_report_export_excel(self):
        self.client.login(username="adminuser", password="Password123!")
        url = reverse("university:progressive_report_export", kwargs={"student_id": self.student.pk, "fmt": "excel"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertTrue(len(response.content) > 0)

    def test_progressive_report_export_csv(self):
        self.client.login(username="adminuser", password="Password123!")
        url = reverse("university:progressive_report_export", kwargs={"student_id": self.student.pk, "fmt": "csv"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertTrue(len(response.content) > 0)

    def test_student_progressive_report_view(self):
        self.client.login(username="student1", password="Password123!")
        url = reverse("university:student_progressive_report")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CS/001/2024")

    def test_student_cannot_view_other_student_report(self):
        """Student should not be able to view another student's report."""
        other_user = User.objects.create_user(
            username="student2",
            email="student2@university.edu",
            password="Password123!",
            role="STUDENT"
        )
        other_student = StudentProfile.objects.create(
            user=other_user,
            roll_no="CS/002/2024",
            program=self.program,
            current_semester=2
        )
        self.client.login(username="student1", password="Password123!")
        url = reverse("university:progressive_report_detail", kwargs={"student_id": other_student.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
