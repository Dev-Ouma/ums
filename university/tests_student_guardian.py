from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from university.models import Department, Program


class StudentGuardianAdminTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Guardian Studies", code="GDS")
        program = Program.objects.create(name="BSc Guardian Studies", code="BGS", department=department)
        self.admin = User.objects.create_user(
            username="guardian.admin", password="AdminPass123!", role=Role.ADMIN,
            email="admin@guardian.test")
        self.student = User.objects.create_user(
            username="guardian.student", password="StudentPass123!", role=Role.STUDENT,
            first_name="Test", last_name="Student", email="student@guardian.test")
        self.profile = StudentProfile.objects.create(
            user=self.student, roll_no="GDS-001", program=program)
        self.client.force_login(self.admin)

    def test_admin_can_update_parent_guardian_details(self):
        response = self.client.post(
            reverse("university:student_edit", args=[self.profile.pk]),
            {
                "first_name": "Test", "last_name": "Student",
                "email": "student@guardian.test", "phone": "+254700000000",
                "username": self.student.username, "password": "", "roll_no": "GDS-001",
                "program": self.profile.program.pk, "current_semester": 1, "gender": "O",
                "address": "Student address", "guardian_name": "Jane Guardian",
                "guardian_relationship": "Parent", "guardian_phone": "+254711111111",
                "guardian_email": "jane@guardian.test", "guardian_address": "Nairobi",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.guardian_name, "Jane Guardian")
        self.assertEqual(self.profile.guardian_relationship, "Parent")
        self.assertEqual(self.profile.guardian_phone, "+254711111111")
        self.assertEqual(self.profile.guardian_email, "jane@guardian.test")
        self.assertEqual(self.profile.guardian_address, "Nairobi")

