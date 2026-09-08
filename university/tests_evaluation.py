"""
Tests for Course & Lecturer Evaluation (QA Survey) Module
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role, StudentProfile, FacultyProfile
from university.models import (
    AcademicTerm, Course, Department, Enrollment, EvaluationWindow, CourseEvaluation,
    Program,
)
from university.evaluation_services import (
    make_submission_token,
    get_evaluation_window,
    is_window_open,
    get_student_pending_evaluations,
    get_student_submitted_evaluations,
    submit_evaluation,
    get_course_analytics,
    get_admin_overview,
    get_faculty_course_analytics,
    generate_evaluation_excel,
    generate_evaluation_pdf,
)

User = get_user_model()


def _create_term(name="Semester 1 2025", current=True):
    return AcademicTerm.objects.create(
        name=name,
        start_date="2025-01-01",
        end_date="2025-06-30",
        is_current=current,
    )


def _create_department():
    return Department.objects.get_or_create(name="Computer Science", code="CS")[0]


def _create_program(dept):
    return Program.objects.get_or_create(
        name="BSc Computer Science", code="BSCS", department=dept
    )[0]


def _create_student_user(username="teststudent_eval", roll="ST-EVAL-001"):
    u = User.objects.create_user(
        username=username,
        password="testpass123",
        role=Role.STUDENT,
    )
    dept = _create_department()
    prog = _create_program(dept)
    sp = StudentProfile.objects.create(
        user=u,
        roll_no=roll,
        program=prog,
    )
    return u, sp


def _create_faculty_user():
    u = User.objects.create_user(
        username="testfaculty_eval",
        password="testpass123",
        role=Role.FACULTY,
    )
    dept = _create_department()
    fp = FacultyProfile.objects.create(
        user=u,
        employee_id="FAC-EVAL-001",
        department=dept,
    )
    return u, fp


def _create_admin_user():
    return User.objects.create_superuser(
        username="testadmin_eval",
        password="testpass123",
    )


def _create_course(faculty=None):
    dept = _create_department()
    return Course.objects.create(
        code="CS-EVAL-101",
        title="Test Course for Evaluation",
        department=dept,
        faculty=faculty,
        credits=3,
    )


def _create_enrollment(student, course, term):
    return Enrollment.objects.get_or_create(
        student=student, course=course, term=term
    )[0]


# ==============================================================================
# SERVICE LAYER TESTS
# ==============================================================================

class TokenTest(TestCase):
    def test_token_is_64_hex_chars(self):
        token = make_submission_token(1, 2, 3)
        self.assertEqual(len(token), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in token))

    def test_token_deterministic(self):
        t1 = make_submission_token(10, 20, 30)
        t2 = make_submission_token(10, 20, 30)
        self.assertEqual(t1, t2)

    def test_different_inputs_different_tokens(self):
        self.assertNotEqual(
            make_submission_token(1, 2, 3),
            make_submission_token(1, 2, 4),
        )


class EvaluationWindowTest(TestCase):
    def test_window_created_if_not_exists(self):
        term = _create_term()
        window = get_evaluation_window(term)
        self.assertIsNotNone(window)
        self.assertFalse(window.is_open)

    def test_window_closed_by_default(self):
        term = _create_term()
        self.assertFalse(is_window_open(term))

    def test_window_open(self):
        term = _create_term()
        w = get_evaluation_window(term)
        w.is_open = True
        w.save()
        self.assertTrue(is_window_open(term))

    def test_window_closed_before_open_time(self):
        term = _create_term()
        future = timezone.now() + timezone.timedelta(hours=1)
        w = get_evaluation_window(term)
        w.is_open = True
        w.opens_at = future
        w.save()
        self.assertFalse(is_window_open(term))

    def test_window_closed_after_close_time(self):
        term = _create_term()
        past = timezone.now() - timezone.timedelta(hours=1)
        w = get_evaluation_window(term)
        w.is_open = True
        w.closes_at = past
        w.save()
        self.assertFalse(is_window_open(term))


class SubmissionServiceTest(TestCase):
    def setUp(self):
        self.term = _create_term()
        self.user, self.sp = _create_student_user()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        _create_enrollment(self.sp, self.course, self.term)

    def _valid_data(self):
        return {
            "teaching_quality": 4,
            "course_content": 5,
            "assessment_fairness": 3,
            "resources_adequacy": 4,
            "overall_satisfaction": 4,
            "strengths": "Great examples",
            "suggestions": "More exercises",
        }

    def test_successful_submission(self):
        ev, err = submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        self.assertIsNone(err)
        self.assertIsNotNone(ev)
        self.assertIsInstance(ev, CourseEvaluation)

    def test_average_score_correct(self):
        ev, _ = submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        # (4+5+3+4+4) / 5 = 4.0
        self.assertEqual(float(ev.average_score), 4.0)

    def test_duplicate_submission_rejected(self):
        submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        ev2, err = submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        self.assertIsNone(ev2)
        self.assertIn("already submitted", err)

    def test_unenrolled_student_rejected(self):
        u2, sp2 = _create_student_user(
            username="teststudent_eval_2", roll="ST-EVAL-002"
        )
        ev, err = submit_evaluation(sp2, self.course, self.term, self._valid_data())
        self.assertIsNone(ev)
        self.assertIn("not enrolled", err)

    def test_no_student_identity_in_db(self):
        submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        ev = CourseEvaluation.objects.filter(course=self.course, term=self.term).first()
        # Verify no student FK
        self.assertFalse(hasattr(ev, 'student_id') and ev._meta.get_field('course'))
        field_names = [f.name for f in ev._meta.get_fields()]
        self.assertNotIn("student", field_names)

    def test_pending_evaluations_list(self):
        pending = get_student_pending_evaluations(self.sp, term=self.term)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["course"], self.course)

    def test_submitted_moves_to_submitted_list(self):
        submit_evaluation(self.sp, self.course, self.term, self._valid_data())
        pending = get_student_pending_evaluations(self.sp, term=self.term)
        submitted = get_student_submitted_evaluations(self.sp, term=self.term)
        self.assertEqual(len(pending), 0)
        self.assertEqual(len(submitted), 1)


class AnalyticsServiceTest(TestCase):
    def setUp(self):
        self.term = _create_term()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        # Create 3 evaluations
        for i, data in enumerate([
            {"teaching_quality": 5, "course_content": 5, "assessment_fairness": 5,
             "resources_adequacy": 5, "overall_satisfaction": 5},
            {"teaching_quality": 3, "course_content": 3, "assessment_fairness": 3,
             "resources_adequacy": 3, "overall_satisfaction": 3},
            {"teaching_quality": 4, "course_content": 4, "assessment_fairness": 4,
             "resources_adequacy": 4, "overall_satisfaction": 4},
        ]):
            CourseEvaluation.objects.create(
                course=self.course,
                term=self.term,
                submission_token=f"test-token-analytics-{i}",
                **data,
            )

    def test_total_correct(self):
        data = get_course_analytics(self.course, term=self.term)
        self.assertEqual(data["total"], 3)

    def test_average_correct(self):
        data = get_course_analytics(self.course, term=self.term)
        # avg of (5+3+4) / 3 per dimension = 4.0 per dim, composite = 4.0
        self.assertEqual(data["average"], 4.0)

    def test_distribution_correct(self):
        data = get_course_analytics(self.course, term=self.term)
        dist = data["distribution"]
        self.assertEqual(dist[3], 1)
        self.assertEqual(dist[4], 1)
        self.assertEqual(dist[5], 1)

    def test_admin_overview_includes_course(self):
        # Use a distinct term to avoid picking up other test evaluations
        distinct_term = AcademicTerm.objects.create(
            name="Distinct Term Eval Test", start_date="2024-01-01", end_date="2024-06-30"
        )
        CourseEvaluation.objects.create(
            course=self.course, term=distinct_term,
            submission_token="distinct-overview-token",
            teaching_quality=4, course_content=4,
            assessment_fairness=4, resources_adequacy=4,
            overall_satisfaction=4,
        )
        overview = get_admin_overview(term=distinct_term)
        self.assertEqual(len(overview), 1)
        self.assertEqual(overview[0]["course"], self.course)

    def test_faculty_analytics(self):
        results = get_faculty_course_analytics(self.fp, term=self.term)
        self.assertEqual(len(results), 1)

    def test_empty_term_returns_zero(self):
        other_term = AcademicTerm.objects.create(
            name="Semester 2 2025", start_date="2025-07-01", end_date="2025-12-31"
        )
        data = get_course_analytics(self.course, term=other_term)
        self.assertEqual(data["total"], 0)


class ReportGenerationTest(TestCase):
    def setUp(self):
        self.term = _create_term()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        CourseEvaluation.objects.create(
            course=self.course, term=self.term,
            submission_token="report-test-token",
            teaching_quality=4, course_content=4,
            assessment_fairness=4, resources_adequacy=4,
            overall_satisfaction=4,
        )

    def test_excel_returns_bytes(self):
        buf = generate_evaluation_excel(term=self.term)
        data = buf.read()
        self.assertGreater(len(data), 0)
        # XLSX magic number: PK zip header
        self.assertTrue(data[:2] == b"PK")

    def test_pdf_returns_bytes(self):
        buf = generate_evaluation_pdf(term=self.term)
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data[:4] == b"%PDF")


# ==============================================================================
# VIEW LAYER TESTS
# ==============================================================================

class StudentEvaluationViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.term = _create_term()
        self.user, self.sp = _create_student_user()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        _create_enrollment(self.sp, self.course, self.term)
        # Open window
        w = get_evaluation_window(self.term)
        w.is_open = True
        w.save()
        self.client.force_login(self.user)

    def test_portal_accessible(self):
        resp = self.client.get(reverse("university:student_evaluation_portal"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Course Evaluations")

    def test_portal_shows_pending(self):
        resp = self.client.get(reverse("university:student_evaluation_portal"))
        self.assertContains(resp, self.course.code)

    def test_submit_form_get(self):
        resp = self.client.get(
            reverse("university:student_evaluation_submit", args=[self.course.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_submit_form_post_valid(self):
        resp = self.client.post(
            reverse("university:student_evaluation_submit", args=[self.course.pk]),
            {
                "teaching_quality": "5",
                "course_content": "4",
                "assessment_fairness": "3",
                "resources_adequacy": "4",
                "overall_satisfaction": "5",
                "strengths": "Very good",
                "suggestions": "More examples",
            }
        )
        self.assertRedirects(resp, reverse("university:student_evaluation_portal"))
        self.assertEqual(CourseEvaluation.objects.count(), 1)

    def test_submit_duplicate_redirects(self):
        # First submission
        self.client.post(
            reverse("university:student_evaluation_submit", args=[self.course.pk]),
            {"teaching_quality": "5", "course_content": "4", "assessment_fairness": "3",
             "resources_adequacy": "4", "overall_satisfaction": "5"}
        )
        # Second attempt: should redirect to portal with info message
        resp = self.client.get(
            reverse("university:student_evaluation_submit", args=[self.course.pk])
        )
        self.assertRedirects(resp, reverse("university:student_evaluation_portal"))

    def test_closed_window_blocks_submission(self):
        w = get_evaluation_window(self.term)
        w.is_open = False
        w.save()
        resp = self.client.get(
            reverse("university:student_evaluation_submit", args=[self.course.pk])
        )
        self.assertRedirects(resp, reverse("university:student_evaluation_portal"))


class FacultyEvaluationViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.term = _create_term()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        CourseEvaluation.objects.create(
            course=self.course, term=self.term,
            submission_token="fac-view-token",
            teaching_quality=4, course_content=4,
            assessment_fairness=4, resources_adequacy=4,
            overall_satisfaction=4,
        )
        self.client.force_login(self.user_fac)

    def test_faculty_dashboard_accessible(self):
        resp = self.client.get(reverse("university:faculty_evaluation_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_faculty_sees_course_analytics(self):
        resp = self.client.get(
            reverse("university:faculty_evaluation_dashboard"),
            {"term": self.term.pk}
        )
        self.assertContains(resp, self.course.code)


class AdminEvaluationViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.term = _create_term()
        self.admin = _create_admin_user()
        self.user_fac, self.fp = _create_faculty_user()
        self.course = _create_course(faculty=self.fp)
        CourseEvaluation.objects.create(
            course=self.course, term=self.term,
            submission_token="admin-view-token",
            teaching_quality=4, course_content=4,
            assessment_fairness=4, resources_adequacy=4,
            overall_satisfaction=4,
        )
        self.client.force_login(self.admin)

    def test_admin_dashboard_accessible(self):
        resp = self.client.get(reverse("university:admin_evaluation_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_admin_course_detail_accessible(self):
        resp = self.client.get(
            reverse("university:admin_evaluation_course_detail", args=[self.course.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_window_toggle_opens(self):
        resp = self.client.post(reverse("university:admin_evaluation_window_toggle"))
        self.assertRedirects(resp, reverse("university:admin_evaluation_dashboard"))
        w = EvaluationWindow.objects.get(term=self.term)
        self.assertTrue(w.is_open)

    def test_window_toggle_closes(self):
        # Open first
        w = get_evaluation_window(self.term)
        w.is_open = True
        w.save()
        self.client.post(reverse("university:admin_evaluation_window_toggle"))
        w.refresh_from_db()
        self.assertFalse(w.is_open)

    def test_excel_export(self):
        resp = self.client.get(
            reverse("university:admin_evaluation_export", args=["xlsx"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    def test_pdf_export(self):
        resp = self.client.get(
            reverse("university:admin_evaluation_export", args=["pdf"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_invalid_format_raises_404(self):
        resp = self.client.get(
            reverse("university:admin_evaluation_export", args=["xml"])
        )
        self.assertEqual(resp.status_code, 404)

    def test_non_admin_blocked(self):
        # Log in as faculty instead
        self.client.logout()
        self.client.force_login(self.user_fac)
        resp = self.client.get(reverse("university:admin_evaluation_dashboard"))
        self.assertEqual(resp.status_code, 403)
