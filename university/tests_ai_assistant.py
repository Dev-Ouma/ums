import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import Role, StudentProfile, FacultyProfile
from university import ai
from university.models import (
    AcademicTerm, AcademicYear, Course, Department, Program, School, FeeInvoice
)

User = get_user_model()


class AIAssistantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="School of Computing", code="SCIT")
        cls.dept = Department.objects.create(name="Computer Science", code="CS", school=cls.school)
        cls.program = Program.objects.create(name="BSc Computer Science", code="BSCS", department=cls.dept)
        cls.course = Course.objects.create(
            code="CS101",
            title="Introduction to Programming",
            credits=3,
            department=cls.dept
        )
        cls.academic_year = AcademicYear.objects.create(
            name="2025/2026",
            start_date="2025-09-01",
            end_date="2026-08-31",
            is_current=True
        )
        cls.academic_term = AcademicTerm.objects.create(
            academic_year=cls.academic_year,
            name="Semester 1",
            term_type=AcademicTerm.TermType.SEMESTER,
            start_date="2025-09-01",
            end_date="2025-12-20",
            is_current=True
        )

        # Student user
        cls.student_user = User.objects.create_user(
            username="test_student",
            email="student@university.test",
            password="Password123!",
            role=Role.STUDENT,
            first_name="Jane",
            last_name="Doe"
        )
        cls.student_profile = StudentProfile.objects.create(
            user=cls.student_user,
            program=cls.program,
            roll_no="SCIT/001/2025"
        )

        # Faculty user
        cls.faculty_user = User.objects.create_user(
            username="test_faculty",
            email="faculty@university.test",
            password="Password123!",
            role=Role.FACULTY,
            first_name="Alan",
            last_name="Turing"
        )
        cls.faculty_profile = FacultyProfile.objects.create(
            user=cls.faculty_user,
            department=cls.dept
        )

        # Admin user
        cls.admin_user = User.objects.create_superuser(
            username="test_admin",
            email="admin@university.test",
            password="Password123!",
            role=Role.ADMIN
        )

    def setUp(self):
        self.client = Client()

    def test_predict_performance_bounds(self):
        """Test performance predictor under normal and edge bounds."""
        res = ai.predict_performance(80.0, 70.0, 90.0)
        self.assertIn("projected", res)
        self.assertGreaterEqual(res["projected"], 0.0)
        self.assertLessEqual(res["projected"], 100.0)

        # Extreme values
        res_high = ai.predict_performance(150.0, 200.0, 500.0)
        self.assertEqual(res_high["projected"], 100.0)

        res_neg = ai.predict_performance(-50.0, -10.0, -99.0)
        self.assertEqual(res_neg["projected"], 0.0)

        # None values
        res_none = ai.predict_performance(None, None, None)
        self.assertEqual(res_none["projected"], 0.0)

    def test_safe_helpers_with_unusual_users(self):
        """Ensure _get_user_name handles None, unauthenticated, or blank display names without crashing."""
        self.assertEqual(ai._get_user_name(None), "there")

        # Blank user
        blank_user = User(username="", first_name="", last_name="")
        self.assertEqual(ai._get_user_name(blank_user), "there")

        # Authenticated student
        self.assertEqual(ai._get_user_name(self.student_user), "Jane")

    def test_assistant_reply_empty_and_greetings(self):
        """Test empty queries and standard greeting intents."""
        res_empty = ai.assistant_reply("", self.student_user)
        self.assertIn("reply", res_empty)
        self.assertIn("Jane", res_empty["reply"])

        res_hi = ai.assistant_reply("Hello there!", self.student_user)
        self.assertIn("reply", res_hi)
        self.assertIn("Jane", res_hi["reply"])
        self.assertTrue(len(res_hi["suggestions"]) > 0)

    def test_fees_intent_student_and_staff(self):
        """Test fee query returns personalized balance for student and summary for staff."""
        # Student with zero fees
        res_student = ai.assistant_reply("How much fees do I owe?", self.student_user)
        self.assertIn("reply", res_student)
        self.assertIn("fees are fully settled", res_student["reply"])
        self.assertIn("/me/fees/", res_student["reply"])

        # Create fee invoice for student
        FeeInvoice.objects.create(
            student=self.student_profile,
            title="Tuition Fee",
            amount=Decimal("45000.00"),
            due_date="2025-11-01"
        )
        res_student_due = ai.assistant_reply("Check my fee balance", self.student_user)
        self.assertIn("KES 45,000", res_student_due["reply"])
        self.assertIn("/me/fees/pay/", res_student_due["reply"])

        # Admin fee query
        res_admin = ai.assistant_reply("What are the total fees collected?", self.admin_user)
        self.assertIn("reply", res_admin)
        self.assertIn("Financial Summary", res_admin["reply"])

    def test_course_registration_intent(self):
        """Test course registration intent returns action links."""
        res = ai.assistant_reply("How do I register for units?", self.student_user)
        self.assertIn("/academics/register/", res["reply"])
        self.assertIn("Course & Semester Registration", res["reply"])

    def test_exam_card_and_exams_intent(self):
        """Test exam card questions return exam card generation links."""
        res = ai.assistant_reply("Where can I download my exam card?", self.student_user)
        self.assertIn("/academics/exam-card/", res["reply"])

    def test_graduation_and_clearance_intent(self):
        """Test graduation clearance inquiries."""
        res = ai.assistant_reply("How do I apply for graduation clearance?", self.student_user)
        self.assertIn("/academics/graduation/", res["reply"])
        self.assertIn("Finance", res["reply"])
        self.assertIn("Library", res["reply"])

    def test_hostels_and_accommodation_intent(self):
        """Test hostel booking questions."""
        res = ai.assistant_reply("Can I book a hostel room?", self.student_user)
        self.assertIn("/campus/hostels/", res["reply"])

    def test_library_and_past_papers_intent(self):
        """Test library and past papers questions."""
        res = ai.assistant_reply("Where can I find library books and past exam papers?", self.student_user)
        self.assertIn("/campus/library/", res["reply"])

    def test_admissions_intent(self):
        """Test admissions inquiry."""
        res = ai.assistant_reply("How can a student apply for admission?", self.student_user)
        self.assertIn("/admissions/apply/", res["reply"])

    def test_specific_course_code_lookup(self):
        """Test that typing a course code returns the specific course title and credits."""
        res = ai.assistant_reply("Tell me about CS101", self.student_user)
        self.assertIn("CS101", res["reply"])
        self.assertIn("Introduction to Programming", res["reply"])
        self.assertIn("3", res["reply"])

    def test_academic_calendar_intent(self):
        """Test academic calendar query retrieves current term and year."""
        res = ai.assistant_reply("When does the semester start and end?", self.student_user)
        self.assertIn("2025/2026", res["reply"])
        self.assertIn("Semester 1", res["reply"])

    def test_fallback_with_zero_errors(self):
        """Test random unrecognized queries return safe fallback guide with action links."""
        res = ai.assistant_reply("gibberishxyzzy 12349098 random non existing string", self.student_user)
        self.assertIn("reply", res)
        self.assertIn("Finances", res["reply"])
        self.assertIn("Academics", res["reply"])
        self.assertTrue(len(res["suggestions"]) > 0)

    # --------------------------------------------------------------------------
    # View Level Tests (HTTP)
    # --------------------------------------------------------------------------
    def test_view_ai_assistant_page(self):
        """GET /ai/assistant/ renders successfully for logged in user."""
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("university:ai_assistant"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UMS AI Assistant")
        self.assertContains(response, "Check my fees")

    def test_view_ai_reply_valid_post(self):
        """POST /ai/reply/ with JSON returns 200 JSON with assistant reply."""
        self.client.force_login(self.student_user)
        response = self.client.post(
            reverse("university:ai_reply"),
            data=json.dumps({"message": "How do I register for courses?"}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("icon", data)
        self.assertIn("suggestions", data)
        self.assertIn("/academics/register/", data["reply"])

    def test_view_ai_reply_malformed_json_never_500(self):
        """POST /ai/reply/ with invalid payload never throws 500."""
        self.client.force_login(self.student_user)
        response = self.client.post(
            reverse("university:ai_reply"),
            data="Not valid json {{{",
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)

    def test_view_ai_insights_student_and_staff(self):
        """Test /ai/insights/ renders for student and staff."""
        self.client.force_login(self.student_user)
        res_student = self.client.get(reverse("university:ai_insights"))
        self.assertEqual(res_student.status_code, 200)

        self.client.force_login(self.faculty_user)
        res_faculty = self.client.get(reverse("university:ai_insights"))
        self.assertEqual(res_faculty.status_code, 200)
