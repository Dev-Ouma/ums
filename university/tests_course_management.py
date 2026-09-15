"""
Course detail access-scoping tests.

course_detail() previously had no permission or ownership check at all
(@login_required only) — any authenticated user, including an unrelated
student, could view any course's full enrolled-student roster plus a
university-wide directory of every unenrolled student. Fixed to require
admin, the course's own assigned faculty, or an enrolled student — and
enrolled students never see the staff-only roster/enroll-student surface.
"""
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Role, FacultyProfile, StudentProfile
from university.models import Department, Program, Course, Enrollment


class CourseDetailAccessTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="course.admin", email="course.admin@ums.ac.ke", password="password123",
            role=Role.ADMIN, is_staff=True, is_superuser=True
        )
        self.dept = Department.objects.create(name="School of Computing", code="SOC")
        self.program = Program.objects.create(name="BSc Computer Science", code="BCS-CD", department=self.dept, level="UG")

        self.owning_faculty_user = User.objects.create_user(
            username="course.owner", email="course.owner@ums.ac.ke", password="password123", role=Role.FACULTY
        )
        self.owning_faculty = FacultyProfile.objects.create(user=self.owning_faculty_user, employee_id="CD-F1", department=self.dept)

        self.other_faculty_user = User.objects.create_user(
            username="course.other.faculty", email="course.other.faculty@ums.ac.ke", password="password123", role=Role.FACULTY
        )
        FacultyProfile.objects.create(user=self.other_faculty_user, employee_id="CD-F2", department=self.dept)

        self.course = Course.objects.create(
            code="CSC-CD-101", title="Detail Access Course", department=self.dept,
            program=self.program, faculty=self.owning_faculty,
        )

        self.enrolled_student_user = User.objects.create_user(
            username="course.enrolled", email="course.enrolled@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        self.enrolled_student = StudentProfile.objects.create(
            user=self.enrolled_student_user, roll_no="CD/001/2026", program=self.program
        )
        Enrollment.objects.create(student=self.enrolled_student, course=self.course)

        self.other_student_user = User.objects.create_user(
            username="course.unrelated", email="course.unrelated@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        StudentProfile.objects.create(user=self.other_student_user, roll_no="CD/002/2026", program=self.program)

    def _url(self):
        return reverse("university:course_detail", args=[self.course.pk])

    def test_admin_sees_full_roster_and_directory(self):
        client = Client()
        client.force_login(self.admin_user)
        res = client.get(self._url())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["is_staff_viewer"])
        self.assertIn(self.enrolled_student.pk, [e.student_id for e in res.context["roster"]])

    def test_owning_faculty_sees_full_roster(self):
        client = Client()
        client.force_login(self.owning_faculty_user)
        res = client.get(self._url())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["is_staff_viewer"])

    def test_enrolled_student_can_view_but_not_see_full_roster(self):
        client = Client()
        client.force_login(self.enrolled_student_user)
        res = client.get(self._url())
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["is_staff_viewer"])
        self.assertEqual(res.context["roster"].count(), 0)
        self.assertEqual(res.context["available_students"].count(), 0)

    def test_unrelated_student_is_denied(self):
        client = Client()
        client.force_login(self.other_student_user)
        res = client.get(self._url())
        self.assertEqual(res.status_code, 403)

    def test_unrelated_faculty_is_denied(self):
        client = Client()
        client.force_login(self.other_faculty_user)
        res = client.get(self._url())
        self.assertEqual(res.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        client = Client()
        res = client.get(self._url())
        self.assertEqual(res.status_code, 302)
