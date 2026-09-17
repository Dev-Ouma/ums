from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from university.api.models import ApiKey
from university.models import (
    AcademicTerm, Course, Department, Exam, FeeInvoice, Program, Result,
)


class ApiKeyModelTests(TestCase):
    def test_generate_returns_a_raw_key_that_is_never_stored(self):
        user = User.objects.create_user(username="apikey.user", email="apikey.user@example.com",
                                        password="pass12345", role=Role.STUDENT)
        api_key, raw_key = ApiKey.generate(user, "Test key")
        self.assertTrue(raw_key.startswith("ums_"))
        self.assertNotEqual(api_key.key_hash, raw_key)
        self.assertEqual(api_key.key_hash, ApiKey.hash_key(raw_key))

    def test_revoke_deactivates_the_key(self):
        user = User.objects.create_user(username="apikey.user2", email="apikey.user2@example.com",
                                        password="pass12345", role=Role.STUDENT)
        api_key, _ = ApiKey.generate(user, "Test key")
        api_key.revoke()
        self.assertFalse(api_key.is_active)
        self.assertIsNotNone(api_key.revoked_at)


class ApiKeyAuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="apiauth.user", email="apiauth.user@example.com",
            password="pass12345", role=Role.STUDENT)
        self.api_key, self.raw_key = ApiKey.generate(self.user, "Test key")

    def test_valid_key_authenticates_the_request(self):
        client = Client()
        res = client.get(reverse("api_v1:me"), HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["username"], "apiauth.user")

    def test_missing_key_is_rejected(self):
        client = Client()
        res = client.get(reverse("api_v1:me"))
        self.assertEqual(res.status_code, 401)

    def test_invalid_key_is_rejected(self):
        client = Client()
        res = client.get(reverse("api_v1:me"), HTTP_AUTHORIZATION="Api-Key not-a-real-key")
        self.assertEqual(res.status_code, 401)

    def test_revoked_key_is_rejected(self):
        self.api_key.revoke()
        client = Client()
        res = client.get(reverse("api_v1:me"), HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        self.assertEqual(res.status_code, 401)

    def test_using_a_key_updates_last_used_at(self):
        self.assertIsNone(self.api_key.last_used_at)
        client = Client()
        client.get(reverse("api_v1:me"), HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        self.api_key.refresh_from_db()
        self.assertIsNotNone(self.api_key.last_used_at)

    def test_session_authentication_also_works(self):
        client = Client()
        client.force_login(self.user)
        res = client.get(reverse("api_v1:me"))
        self.assertEqual(res.status_code, 200)


class MyFeeBalanceViewTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="API Dept", code="APID")
        self.program = Program.objects.create(name="BSc API", code="BSC-API", department=self.dept)
        self.student_user = User.objects.create_user(
            username="fee.api.student", email="fee.api.student@example.com",
            password="pass12345", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(
            user=self.student_user, roll_no="STU-API-1", program=self.program, current_semester=1)
        self.api_key, self.raw_key = ApiKey.generate(self.student_user, "Test key")

    def test_returns_the_students_own_balance(self):
        FeeInvoice.objects.create(student=self.student, amount=Decimal("50000.00"),
                                  amount_paid=Decimal("20000.00"), due_date="2026-12-31")
        client = Client()
        res = client.get(reverse("api_v1:my-fee-balance"), HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(Decimal(body["total_billed"]), Decimal("50000.00"))
        self.assertEqual(Decimal(body["balance"]), Decimal("30000.00"))

    def test_non_student_account_gets_404(self):
        staff_user = User.objects.create_user(
            username="staff.api.user", email="staff.api.user@example.com",
            password="pass12345", role=Role.FACULTY)
        _, raw_key = ApiKey.generate(staff_user, "Test key")
        client = Client()
        res = client.get(reverse("api_v1:my-fee-balance"), HTTP_AUTHORIZATION=f"Api-Key {raw_key}")
        self.assertEqual(res.status_code, 404)


class MyExamResultsViewTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="API Exam Dept", code="APIED")
        self.program = Program.objects.create(name="BSc APIE", code="BSC-APIE", department=self.dept)
        self.course = Course.objects.create(code="APIE101", title="API Exams", department=self.dept, program=self.program)
        self.term = AcademicTerm.objects.create(
            name="API Term", start_date="2026-01-01", end_date="2026-04-30")
        self.student_user = User.objects.create_user(
            username="exam.api.student", email="exam.api.student@example.com",
            password="pass12345", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(
            user=self.student_user, roll_no="STU-API-2", program=self.program, current_semester=1)
        self.api_key, self.raw_key = ApiKey.generate(self.student_user, "Test key")

    def test_only_published_exam_results_are_returned(self):
        published_exam = Exam.objects.create(
            course=self.course, term=self.term, name="Published Final",
            status=Exam.Status.PUBLISHED, max_marks=100)
        Result.objects.create(exam=published_exam, student=self.student,
                              marks_obtained=Decimal("75.00"))

        unpublished_exam = Exam.objects.create(
            course=self.course, term=self.term, name="Unpublished Final",
            status=Exam.Status.MARKING, max_marks=100)
        Result.objects.create(exam=unpublished_exam, student=self.student,
                              marks_obtained=Decimal("60.00"))

        client = Client()
        res = client.get(reverse("api_v1:my-exam-results"), HTTP_AUTHORIZATION=f"Api-Key {self.raw_key}")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        exam_names = [r["exam_name"] for r in body]
        self.assertIn("Published Final", exam_names)
        self.assertNotIn("Unpublished Final", exam_names)


class ApiKeySelfServiceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="selfservice.user", email="selfservice.user@example.com",
            password="pass12345", role=Role.STUDENT)

    def test_user_can_create_and_list_their_own_key(self):
        client = Client()
        client.force_login(self.user)
        res = client.post(reverse("university:api_key_create"), {"name": "My App"})
        self.assertEqual(res.status_code, 302)
        self.assertTrue(ApiKey.objects.filter(user=self.user, name="My App").exists())

        res_list = client.get(reverse("university:api_key_list"))
        self.assertContains(res_list, "My App")

    def test_user_cannot_revoke_another_users_key(self):
        other_user = User.objects.create_user(
            username="other.selfservice", email="other.selfservice@example.com",
            password="pass12345", role=Role.STUDENT)
        api_key, _ = ApiKey.generate(other_user, "Not yours")

        client = Client()
        client.force_login(self.user)
        res = client.post(reverse("university:api_key_revoke", args=[api_key.pk]))
        self.assertEqual(res.status_code, 404)
        api_key.refresh_from_db()
        self.assertTrue(api_key.is_active)
