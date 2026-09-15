"""Comprehensive test suite for the Examination Marks Send-Back, Rollback, Unpublishing, and Versioning workflow."""
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university import examination_services as workflow
from university.models import (
    AcademicTerm, Course, Department, Enrollment, Exam, ExamRoom,
    MarksVersion, MarksWorkflowEvent, Program, Result, School, StaffRoleAssignment, StaffRole
)
from university.permissions_services import seed_default_permissions_and_roles


class MarksWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_default_permissions_and_roles()
        cls.today = date.today()
        cls.term = AcademicTerm.objects.create(
            name="Term 1 2026",
            start_date=cls.today - timedelta(days=30),
            end_date=cls.today + timedelta(days=30),
            is_current=True
        )
        cls.school = School.objects.create(name="School of Computing", code="SOC")
        cls.dept = Department.objects.create(name="Computer Science", code="CS", school=cls.school)
        cls.program = Program.objects.create(name="BSc CS", code="BCS", department=cls.dept)

        # Users and roles
        cls.admin = User.objects.create_user("admin_wf", password="x", role=Role.ADMIN)

        cls.lecturer_user = User.objects.create_user("lect_wf", password="x", role=Role.FACULTY)
        cls.lecturer = FacultyProfile.objects.create(user=cls.lecturer_user, employee_id="L1", department=cls.dept)

        cls.hod_user = User.objects.create_user("hod_wf", password="x", role=Role.FACULTY)
        cls.hod_faculty = FacultyProfile.objects.create(user=cls.hod_user, employee_id="H1", department=cls.dept, designation="Head of Department")
        cls.hod_role, _ = StaffRole.objects.get_or_create(code="hod", defaults={"name": "Head of Department"})
        StaffRoleAssignment.objects.create(user=cls.hod_user, role=cls.hod_role, department=cls.dept, is_active=True)

        cls.dean_user = User.objects.create_user("dean_wf", password="x", role=Role.FACULTY)
        cls.dean_faculty = FacultyProfile.objects.create(user=cls.dean_user, employee_id="D1", department=cls.dept, designation="Dean of School")
        cls.dean_role, _ = StaffRole.objects.get_or_create(code="dean", defaults={"name": "Dean"})
        StaffRoleAssignment.objects.create(user=cls.dean_user, role=cls.dean_role, school=cls.school, is_active=True)

        cls.course = Course.objects.create(
            code="CS101", title="Intro to CS", department=cls.dept,
            program=cls.program, faculty=cls.lecturer
        )
        cls.room = ExamRoom.objects.create(name="Lab 1", capacity=40, location="Block B")

        cls.students = []
        for i in range(3):
            u = User.objects.create_user(f"st_wf_{i}", password="x", role=Role.STUDENT)
            sp = StudentProfile.objects.create(user=u, roll_no=f"ROLL{i}", program=cls.program)
            Enrollment.objects.create(student=sp, course=cls.course, term=cls.term)
            cls.students.append(sp)

    def make_and_mark_exam(self, user=None):
        """Helper to create an exam and fill marks ready for submission."""
        user = user or self.lecturer_user
        exam = Exam.objects.create(
            course=self.course, term=self.term, name="End Term Exam",
            kind=Exam.Kind.FINAL, weight=100, date=self.today,
            start_time=time(9, 0), end_time=time(12, 0),
            room=self.room, invigilator=self.course.faculty,
            max_marks=100, pass_mark=40
        )
        workflow.transition(self.admin, exam.pk, "schedule")
        workflow.transition(self.admin, exam.pk, "start")
        rows = list(exam.results.order_by("student__roll_no"))
        entries = {
            str(rows[0].pk): {"attendance": "PRESENT", "cat_marks": "25", "exam_marks": "60", "marks": "85", "remarks": "Good"},
            str(rows[1].pk): {"attendance": "PRESENT", "cat_marks": "20", "exam_marks": "35", "marks": "55", "remarks": ""},
            str(rows[2].pk): {"attendance": "ABSENT", "remarks": "Sick"},
        }
        exam.refresh_from_db()
        workflow.save_marks(user, exam.pk, entries, exam.revision)
        exam.refresh_from_db()
        return exam

    def test_forward_approval_and_publish_workflow(self):
        """Draft -> Scheduled -> Marking -> Submitted -> HoD Approved -> Published."""
        exam = self.make_and_mark_exam()

        # 1. Lecturer submits to HoD
        workflow.transition(self.lecturer_user, exam.pk, "submit")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SUBMITTED)
        self.assertEqual(exam.submitted_by, self.lecturer_user)
        self.assertIsNotNone(exam.submitted_at)
        self.assertEqual(exam.current_version, 1)

        # Verify version snapshot v1 was created
        v1 = exam.marks_versions.filter(version_number=1).first()
        self.assertIsNotNone(v1)
        self.assertEqual(len(v1.snapshot), 3)

        # 2. HoD approves
        workflow.transition(self.hod_user, exam.pk, "hod_approve")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.HOD_APPROVED)
        self.assertEqual(exam.hod_approved_by, self.hod_user)
        self.assertIsNotNone(exam.hod_approved_at)

        # 3. Dean publishes
        workflow.transition(self.dean_user, exam.pk, "publish")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.PUBLISHED)
        self.assertEqual(exam.dean_published_by, self.dean_user)
        self.assertIsNotNone(exam.published_at)

        # Verify workflow events recorded
        actions_recorded = list(exam.workflow_events.values_list("action", flat=True))
        self.assertIn("SUBMIT", actions_recorded)
        self.assertIn("HOD_APPROVE", actions_recorded)
        self.assertIn("PUBLISH", actions_recorded)

    def test_role_specific_http_workflow_from_capture_to_student_release(self):
        """Every role sees and completes only its operational stage through the UI routes."""
        exam = self.make_and_mark_exam()

        self.client.force_login(self.lecturer_user)
        marks_page = self.client.get(f"/manage/academics/examinations/marks/?exam_id={exam.pk}")
        self.assertEqual(marks_page.status_code, 200)
        self.assertContains(marks_page, "Submit Marks as Final")
        submit = self.client.post(f"/manage/academics/examinations/{exam.pk}/action/", {
            "action": "submit", "revision": exam.revision,
        })
        self.assertEqual(submit.status_code, 302)
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SUBMITTED)

        self.client.force_login(self.hod_user)
        approval_queue = self.client.get('/manage/academics/examinations/workflow/approval/')
        self.assertEqual(approval_queue.status_code, 200)
        self.assertContains(approval_queue, exam.course.code)
        approval_page = self.client.get(f"/manage/academics/examinations/{exam.pk}/")
        self.assertContains(approval_page, "Approve Marks (HoD)")
        self.client.post(f"/manage/academics/examinations/{exam.pk}/action/", {
            "action": "hod_approve", "revision": exam.revision,
        })
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.HOD_APPROVED)

        self.client.force_login(self.dean_user)
        publication_queue = self.client.get('/manage/academics/examinations/workflow/publication/')
        self.assertEqual(publication_queue.status_code, 200)
        self.assertContains(publication_queue, exam.course.code)
        publication_page = self.client.get(f"/manage/academics/examinations/{exam.pk}/")
        self.assertContains(publication_page, "Publish Official Results (Dean)")
        self.client.post(f"/manage/academics/examinations/{exam.pk}/action/", {
            "action": "publish", "revision": exam.revision,
        })
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.PUBLISHED)

        self.client.force_login(self.students[0].user)
        statement = self.client.get('/manage/academics/examinations/results/')
        self.assertEqual(statement.status_code, 200)
        self.assertContains(statement, exam.course.code)

    def test_hod_send_back_requires_reason(self):
        """HoD cannot send back marks without providing a reason."""
        exam = self.make_and_mark_exam()
        workflow.transition(self.lecturer_user, exam.pk, "submit")

        with self.assertRaises(ValidationError):
            workflow.transition(self.hod_user, exam.pk, "hod_send_back", reason="")

        workflow.transition(
            self.hod_user, exam.pk, "hod_send_back",
            reason="Marks mismatch on student ROLL0",
            reason_category="Incorrect Mark Entry"
        )
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.RETURNED_TO_INSTRUCTOR)

        event = exam.workflow_events.filter(action="HOD_SEND_BACK").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.reason_category, "Incorrect Mark Entry")
        self.assertEqual(event.actor, self.hod_user)

    def test_resubmission_increments_version_and_preserves_history(self):
        """When returned marks are corrected and resubmitted, a new version is created."""
        exam = self.make_and_mark_exam()
        workflow.transition(self.lecturer_user, exam.pk, "submit")
        self.assertEqual(exam.current_version, 1)

        # Return to instructor
        workflow.transition(self.hod_user, exam.pk, "hod_send_back", reason="Missing ROLL1 script")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.RETURNED_TO_INSTRUCTOR)

        # Lecturer can edit marks now
        row = exam.results.get(student=self.students[1])
        entries = {
            str(r.pk): {
                "attendance": r.attendance,
                "cat_marks": str(r.cat_marks or ''),
                "exam_marks": "50" if r.pk == row.pk else str(r.exam_marks or ''),
                "marks": "70" if r.pk == row.pk else str(r.marks_obtained or ''),
                "remarks": "Updated" if r.pk == row.pk else r.remarks,
            }
            for r in exam.results.all()
        }
        workflow.save_marks(self.lecturer_user, exam.pk, entries, exam.revision)

        # Resubmit
        workflow.transition(self.lecturer_user, exam.pk, "resubmit")
        exam.refresh_from_db()
        self.assertEqual(exam.current_version, 2)
        self.assertEqual(exam.status, Exam.Status.SUBMITTED)

        # Both versions exist
        self.assertTrue(exam.marks_versions.filter(version_number=1).exists())
        self.assertTrue(exam.marks_versions.filter(version_number=2).exists())

    def test_separation_of_duties_enforced(self):
        """An HoD who is also the lecturer cannot approve their own submission."""
        # Set HoD as course lecturer
        self.course.faculty = self.hod_faculty
        self.course.save()

        exam = self.make_and_mark_exam(user=self.hod_user)
        workflow.transition(self.hod_user, exam.pk, "submit")

        # HoD tries to approve own submission -> Blocked!
        with self.assertRaises(PermissionDenied):
            workflow.transition(self.hod_user, exam.pk, "hod_approve")

        # Admin override works
        workflow.transition(self.admin, exam.pk, "hod_approve")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.HOD_APPROVED)

        # Restore lecturer for subsequent tests
        self.course.faculty = self.lecturer
        self.course.save()

    def test_reopen_approved_marks(self):
        """HoD or Dean can reopen approved marks before publication."""
        exam = self.make_and_mark_exam()
        workflow.transition(self.lecturer_user, exam.pk, "submit")
        workflow.transition(self.hod_user, exam.pk, "hod_approve")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.HOD_APPROVED)

        # Without reason -> blocked
        with self.assertRaises(ValidationError):
            workflow.transition(self.hod_user, exam.pk, "reopen_approved", reason="")

        workflow.transition(self.hod_user, exam.pk, "reopen_approved", reason="Discovered calculation error in moderation")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.RETURNED_TO_INSTRUCTOR)
        self.assertIsNone(exam.hod_approved_by)

    def test_dean_send_back_to_hod_and_instructor(self):
        """Dean can return marks to HoD or directly to instructor."""
        exam = self.make_and_mark_exam()
        workflow.transition(self.lecturer_user, exam.pk, "submit")
        workflow.transition(self.hod_user, exam.pk, "hod_approve")

        # Dean returns to HoD
        workflow.transition(self.dean_user, exam.pk, "dean_send_back_hod", reason="Board requests moderation review")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.RETURNED_TO_HOD)

        # Dean returns directly to instructor
        workflow.transition(self.dean_user, exam.pk, "dean_send_back_instructor", reason="Major missing records")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.RETURNED_TO_INSTRUCTOR)

    def test_unpublish_preserves_marks_and_hides_from_student_statement(self):
        """Unpublishing removes published results from student statements while preserving all data."""
        exam = self.make_and_mark_exam()
        workflow.transition(self.lecturer_user, exam.pk, "submit")
        workflow.transition(self.hod_user, exam.pk, "hod_approve")
        workflow.transition(self.dean_user, exam.pk, "publish")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.PUBLISHED)

        # Student statement shows results
        statement = workflow.student_statement(self.students[0])
        self.assertEqual(len(statement.results), 1)

        # Unpublish requires reason
        with self.assertRaises(ValidationError):
            workflow.transition(self.dean_user, exam.pk, "unpublish", reason="")

        # Dean unpublishes
        workflow.transition(
            self.dean_user, exam.pk, "unpublish",
            reason="Senate Resolution 2026/04 - re-evaluation ordered",
            reason_category="Senate Resolution",
            target_stage=Exam.Status.UNPUBLISHED
        )
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.UNPUBLISHED)
        self.assertIsNone(exam.published_at)

        # Marks and results are STILL PRESERVED!
        self.assertEqual(exam.results.count(), 3)
        self.assertEqual(exam.results.filter(marks_obtained__isnull=False).count(), 2)

        # Student statement NO LONGER displays the unpublished exam
        statement = workflow.student_statement(self.students[0])
        self.assertEqual(len(statement.results), 0)

        # Audit event created
        event = exam.workflow_events.filter(action="UNPUBLISH").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.reason_category, "Senate Resolution")
        self.assertEqual(event.actor, self.dean_user)
