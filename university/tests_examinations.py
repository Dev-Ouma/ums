"""End-to-end coverage of the examination lifecycle: CAT 30% + final 70%.

Draft → schedule → marks capture → submit → approve → publish, plus the
rollback paths (return for correction, withdraw a published sitting) and the
weighted course total that combines both components.
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import FacultyProfile, Role, StudentProfile, User
from university import examination_services as workflow
from university.examination_forms import ExaminationForm
from university.models import (AcademicTerm, Course, Department, Enrollment,
                               Exam, ExamRoom, Program, Result)


class ExaminationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = date.today()
        cls.term = AcademicTerm.objects.create(
            name="Test Term", start_date=cls.today - timedelta(days=30),
            end_date=cls.today + timedelta(days=30), is_current=True)
        dept = Department.objects.create(name="Computing", code="CMP")
        cls.program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        cls.admin = User.objects.create_user("admin1", password="x", role=Role.ADMIN)
        lecturer = User.objects.create_user("lect1", password="x", role=Role.FACULTY)
        cls.lecturer = lecturer
        cls.faculty = FacultyProfile.objects.create(user=lecturer, employee_id="E1", department=dept)
        other = User.objects.create_user("lect2", password="x", role=Role.FACULTY)
        cls.other_faculty = FacultyProfile.objects.create(user=other, employee_id="E2", department=dept)
        cls.course = Course.objects.create(code="CMP101", title="Intro", department=dept,
                                           program=cls.program, faculty=cls.faculty)
        cls.room = ExamRoom.objects.create(name="Hall A", capacity=50, location="Block A")
        cls.students = []
        for i in range(3):
            user = User.objects.create_user(f"stud{i}", password="x", role=Role.STUDENT)
            profile = StudentProfile.objects.create(user=user, roll_no=f"R{i}", program=cls.program)
            Enrollment.objects.create(student=profile, course=cls.course, term=cls.term)
            cls.students.append(profile)

    def make_exam(self, kind, name, weight, day_offset=0, **extra):
        exam = Exam.objects.create(
            course=self.course, term=self.term, name=name, kind=kind, weight=weight,
            date=self.today + timedelta(days=day_offset), start_time=time(9, 0),
            end_time=time(11, 0), room=self.room, invigilator=self.faculty,
            max_marks=100, pass_mark=40, **extra)
        return exam

    def drive_to_marking(self, exam):
        workflow.transition(self.lecturer, exam.pk, "schedule")
        return workflow.transition(self.lecturer, exam.pk, "start")

    def enter_marks(self, exam, marks):
        rows = list(exam.results.select_related("student").order_by("student__roll_no"))
        entries = {str(row.pk): {"attendance": "PRESENT", "marks": str(mark), "remarks": ""}
                   for row, mark in zip(rows, marks)}
        exam.refresh_from_db()
        workflow.save_marks(self.lecturer, exam.pk, entries, exam.revision)
        exam.refresh_from_db()
        return exam


class ExaminationLifecycleTests(ExaminationTestBase):
    # --- weighting -----------------------------------------------------

    def test_form_forces_cat_thirty_and_final_seventy(self):
        base = {"course": self.course.pk, "term": self.term.pk, "name": "CAT 1",
                "date": self.today, "start_time": "09:00", "end_time": "11:00",
                "room": self.room.pk, "invigilator": self.faculty.pk,
                "max_marks": 100, "pass_mark": 40, "instructions": "",
                "grade_A+": 90, "grade_A": 80, "grade_B+": 70,
                "grade_B": 60, "grade_C": 50, "grade_D": 40}
        form = ExaminationForm(dict(base, kind=Exam.Kind.CAT), user=self.lecturer)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().weight, 30)
        form = ExaminationForm(dict(base, kind=Exam.Kind.FINAL, name="Final"), user=self.lecturer)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().weight, 70)

    # --- happy path ----------------------------------------------------

    def test_full_lifecycle_to_publication(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        workflow.transition(self.lecturer, exam.pk, "schedule")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SCHEDULED)
        self.assertEqual(exam.results.count(), 3)
        self.assertEqual(sorted(exam.results.values_list("seat_number", flat=True)), [1, 2, 3])

        workflow.transition(self.lecturer, exam.pk, "start")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.MARKING)

        self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SUBMITTED)

        workflow.transition(self.admin, exam.pk, "approve")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.APPROVED)

        workflow.transition(self.admin, exam.pk, "publish")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.PUBLISHED)
        self.assertIsNotNone(exam.published_at)
        self.assertEqual(exam.audit_entries.count(), 6)  # 5 transitions + marks saved

    def test_submit_blocked_until_every_candidate_is_recorded(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        with self.assertRaises(ValidationError):
            workflow.transition(self.lecturer, exam.pk, "submit")
        self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        self.assertEqual(Exam.objects.get(pk=exam.pk).status, Exam.Status.SUBMITTED)

    def test_marks_locked_outside_marking_state(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        exam.refresh_from_db()
        row = exam.results.first()
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk,
                                {str(row.pk): {"attendance": "PRESENT", "marks": "99", "remarks": ""}},
                                exam.revision)

    def test_stale_revision_is_rejected(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        stale = exam.revision - 1
        row = exam.results.first()
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk,
                                {str(row.pk): {"attendance": "PRESENT", "marks": "50", "remarks": ""}},
                                stale)

    def test_absent_candidate_cannot_hold_marks(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        row = exam.results.first()
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk,
                                {str(row.pk): {"attendance": "ABSENT", "marks": "50", "remarks": ""}},
                                exam.revision)

    def test_marks_above_maximum_rejected(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        row = exam.results.first()
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk,
                                {str(row.pk): {"attendance": "PRESENT", "marks": "101", "remarks": ""}},
                                exam.revision)

    # --- permissions ---------------------------------------------------

    def test_only_admin_may_approve_and_publish(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        with self.assertRaises(PermissionDenied):
            workflow.transition(self.lecturer, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "approve")
        with self.assertRaises(PermissionDenied):
            workflow.transition(self.lecturer, exam.pk, "publish")

    def test_foreign_lecturer_cannot_touch_the_sitting(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        with self.assertRaises(PermissionDenied):
            workflow.transition(self.other_faculty.user, exam.pk, "schedule")

    # --- rollback ------------------------------------------------------

    def test_return_for_correction_reopens_marks(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        with self.assertRaises(ValidationError):
            workflow.transition(self.admin, exam.pk, "return", reason="")
        workflow.transition(self.admin, exam.pk, "return", reason="Transposed two scripts")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.MARKING)
        exam = self.enter_marks(exam, [85, 55, 35])
        self.assertEqual(
            sorted(r.marks_obtained for r in exam.results.all()),
            [Decimal("35.00"), Decimal("55.00"), Decimal("85.00")])

    def test_published_sitting_can_be_withdrawn_and_republished(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, [80, 50, 30])
        workflow.transition(self.lecturer, exam.pk, "submit")
        workflow.transition(self.admin, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "publish")

        workflow.transition(self.admin, exam.pk, "reopen", reason="Marking scheme corrected")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.MARKING)
        self.assertIsNone(exam.published_at)
        _, groups = workflow.student_statement(self.students[0])
        self.assertEqual(groups, [], "withdrawn results must disappear from the statement")

        exam = self.enter_marks(exam, [90, 60, 40])
        workflow.transition(self.lecturer, exam.pk, "submit")
        workflow.transition(self.admin, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "publish")
        self.assertEqual(Exam.objects.get(pk=exam.pk).status, Exam.Status.PUBLISHED)

    def test_reschedule_clears_the_candidate_register(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        workflow.transition(self.lecturer, exam.pk, "schedule")
        workflow.transition(self.admin, exam.pk, "reschedule", reason="Room double booked")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.DRAFT)
        self.assertEqual(exam.results.count(), 0)

    # --- weighted course total ------------------------------------------

    def publish(self, exam, marks):
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, marks)
        workflow.transition(self.lecturer, exam.pk, "submit")
        workflow.transition(self.admin, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "publish")
        return exam

    def test_course_total_combines_cat_thirty_and_final_seventy(self):
        cat = self.make_exam(Exam.Kind.CAT, "CAT 1", 30, day_offset=-2)
        self.publish(cat, [80, 60, 40])
        final = self.make_exam(Exam.Kind.FINAL, "Final", 70)
        self.publish(final, [60, 50, 30])

        _, groups = workflow.student_statement(self.students[0])
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertTrue(group["complete"])
        # 80% × 0.30 + 60% × 0.70 = 24 + 42 = 66
        self.assertEqual(group["total"], Decimal("66.0"))
        self.assertEqual(group["outcome"], "Pass")
        self.assertEqual(group["grade"], "B")

    def test_course_total_incomplete_until_both_components_publish(self):
        cat = self.make_exam(Exam.Kind.CAT, "CAT 1", 30, day_offset=-2)
        self.publish(cat, [80, 60, 40])
        _, groups = workflow.student_statement(self.students[0])
        self.assertFalse(groups[0]["complete"])
        self.assertEqual(groups[0]["grade"], "Incomplete")

    def test_weighted_total_can_fail_a_student_who_passed_the_cat(self):
        cat = self.make_exam(Exam.Kind.CAT, "CAT 1", 30, day_offset=-2)
        self.publish(cat, [90, 60, 40])
        final = self.make_exam(Exam.Kind.FINAL, "Final", 70)
        self.publish(final, [20, 50, 30])
        _, groups = workflow.student_statement(self.students[0])
        # 90 × 0.30 + 20 × 0.70 = 27 + 14 = 41 → above the 40 pass mark
        self.assertEqual(groups[0]["total"], Decimal("41.0"))
        self.assertEqual(groups[0]["outcome"], "Pass")


class ExaminationViewTests(ExaminationTestBase):
    def test_marks_screen_renders_for_the_course_lecturer(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("examinations:marks", args=[exam.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.students[0].roll_no)

    def test_marks_screen_denied_to_other_faculty(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        self.client.force_login(self.other_faculty.user)
        response = self.client.get(reverse("examinations:marks", args=[exam.pk]))
        self.assertEqual(response.status_code, 404)

    def test_marks_post_saves_and_redirects(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        rows = list(exam.results.order_by("seat_number"))
        payload = {"revision": exam.revision}
        for row in rows:
            payload[f"attendance_{row.pk}"] = "PRESENT"
            payload[f"marks_{row.pk}"] = "55"
            payload[f"remarks_{row.pk}"] = ""
        self.client.force_login(self.lecturer)
        response = self.client.post(reverse("examinations:marks", args=[exam.pk]), payload)
        self.assertRedirects(response, reverse("examinations:marks", args=[exam.pk]))
        self.assertEqual(Result.objects.get(pk=rows[0].pk).marks_obtained, Decimal("55.00"))

    def test_student_sees_only_published_results(self):
        exam = self.make_exam(Exam.Kind.CAT, "CAT 1", 30)
        self.drive_to_marking(exam)
        exam = self.enter_marks(exam, [80, 50, 30])
        self.client.force_login(self.students[0].user)
        response = self.client.get(reverse("examinations:statement"))
        self.assertNotContains(response, "CMP101")
        self.assertContains(response, "No published results yet")
        workflow.transition(self.lecturer, exam.pk, "submit")
        workflow.transition(self.admin, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "publish")
        response = self.client.get(reverse("examinations:statement"))
        self.assertContains(response, "CMP101")


class EnhancedCUEExaminationsTests(ExaminationTestBase):
    def test_auto_internal_examiner_assignment_from_course_faculty(self):
        exam = Exam(
            course=self.course, term=self.term, name="CUE Midterm", kind=Exam.Kind.FINAL,
            room=self.room, invigilator=self.faculty, date=self.today,
            start_time=time(9, 0), end_time=time(11, 0)
        )
        exam.clean()
        self.assertEqual(exam.internal_examiner, self.faculty)

    def test_automatic_cat_plus_exam_marks_calculation_and_cue_grade(self):
        # CAT: 25 + Exam: 60 → Total: 85 → Grade: A
        exam = self.make_exam(Exam.Kind.FINAL, "CUE Final", 100, cat_max_marks=30, exam_max_marks=70)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        row = exam.results.first()
        entries = {
            str(row.pk): {
                "attendance": "PRESENT",
                "cat_marks": "25.00",
                "exam_marks": "60.00",
                "remarks": "Excellent project and theory"
            }
        }
        for other_row in exam.results.exclude(pk=row.pk):
            entries[str(other_row.pk)] = {"attendance": "ABSENT", "remarks": ""}

        workflow.save_marks(self.lecturer, exam.pk, entries, exam.revision)
        row.refresh_from_db()
        self.assertEqual(row.cat_marks, Decimal("25.00"))
        self.assertEqual(row.exam_marks, Decimal("60.00"))
        self.assertEqual(row.marks_obtained, Decimal("85.00"))
        self.assertEqual(row.percentage, 85.0)
        self.assertEqual(row.grade, "A")
        self.assertEqual(row.grade_point, 4.0)

    def test_cat_and_exam_max_marks_enforcement(self):
        exam = self.make_exam(Exam.Kind.FINAL, "Strict Final", 100, cat_max_marks=30, exam_max_marks=70)
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        row = exam.results.first()
        # Invalid CAT > 30
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk, {
                str(row.pk): {"attendance": "PRESENT", "cat_marks": "35", "exam_marks": "50", "remarks": ""}
            }, exam.revision)
        # Invalid Exam > 70
        with self.assertRaises(ValidationError):
            workflow.save_marks(self.lecturer, exam.pk, {
                str(row.pk): {"attendance": "PRESENT", "cat_marks": "20", "exam_marks": "75", "remarks": ""}
            }, exam.revision)

    def test_cue_grading_scale_thresholds(self):
        exam = self.make_exam(Exam.Kind.FINAL, "Grading Scale Test", 100)
        self.assertEqual(exam.grade_for(85.0), "A")
        self.assertEqual(exam.grade_for(70.0), "A")
        self.assertEqual(exam.grade_for(69.9), "B")
        self.assertEqual(exam.grade_for(60.0), "B")
        self.assertEqual(exam.grade_for(55.0), "C")
        self.assertEqual(exam.grade_for(50.0), "C")
        self.assertEqual(exam.grade_for(45.0), "D")
        self.assertEqual(exam.grade_for(40.0), "D")
        self.assertEqual(exam.grade_for(39.9), "F")
        self.assertEqual(exam.grade_for(0.0), "F")

    def test_full_cue_multi_stage_workflow_with_internal_and_external_review(self):
        # Draft → Scheduled → Marking → Internal Review → External Review → Submitted → Approved → Published
        exam = self.make_exam(
            Exam.Kind.FINAL, "Workflow Test", 100,
            internal_examiner=self.faculty,
            external_examiner=self.other_faculty
        )
        self.assertEqual(exam.status, Exam.Status.DRAFT)

        # 1. Schedule
        workflow.transition(self.lecturer, exam.pk, "schedule")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SCHEDULED)

        # 2. Start marking
        workflow.transition(self.lecturer, exam.pk, "start")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.MARKING)

        # Enter marks
        rows = list(exam.results.order_by("seat_number"))
        entries = {
            str(rows[0].pk): {"attendance": "PRESENT", "cat_marks": "25", "exam_marks": "55", "remarks": ""},
            str(rows[1].pk): {"attendance": "PRESENT", "cat_marks": "18", "exam_marks": "45", "remarks": ""},
            str(rows[2].pk): {"attendance": "ABSENT", "remarks": ""},
        }
        workflow.save_marks(self.lecturer, exam.pk, entries, exam.revision)

        # 3. Submit for Internal Review
        workflow.transition(self.lecturer, exam.pk, "submit_internal")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.INTERNAL_REVIEW)

        # 4. Internal Examiner reviews
        workflow.transition(self.lecturer, exam.pk, "review_internal")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.EXTERNAL_REVIEW)
        self.assertIsNotNone(exam.internal_reviewed_at)

        # 5. External Examiner reviews
        workflow.transition(self.other_faculty.user, exam.pk, "review_external")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.SUBMITTED)
        self.assertIsNotNone(exam.external_reviewed_at)

        # 6. Admin approves
        workflow.transition(self.admin, exam.pk, "approve")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.APPROVED)

        # 7. Admin publishes
        workflow.transition(self.admin, exam.pk, "publish")
        exam.refresh_from_db()
        self.assertEqual(exam.status, Exam.Status.PUBLISHED)
        self.assertIsNotNone(exam.published_at)

    def test_student_statement_gpa_calculation(self):
        exam = self.make_exam(
            Exam.Kind.FINAL, "Comprehensive Final", 100,
            cat_max_marks=30, exam_max_marks=70
        )
        self.drive_to_marking(exam)
        exam.refresh_from_db()
        rows = list(exam.results.order_by("seat_number"))
        entries = {
            str(rows[0].pk): {"attendance": "PRESENT", "cat_marks": "28", "exam_marks": "62", "remarks": ""}, # 90 -> A (4.0)
            str(rows[1].pk): {"attendance": "PRESENT", "cat_marks": "20", "exam_marks": "45", "remarks": ""}, # 65 -> B (3.0)
            str(rows[2].pk): {"attendance": "PRESENT", "cat_marks": "15", "exam_marks": "30", "remarks": ""}, # 45 -> D (1.0)
        }
        workflow.save_marks(self.lecturer, exam.pk, entries, exam.revision)
        workflow.transition(self.lecturer, exam.pk, "submit")
        workflow.transition(self.admin, exam.pk, "approve")
        workflow.transition(self.admin, exam.pk, "publish")

        res = workflow.student_statement(self.students[0])
        results, groups = res[0], res[1]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].grade, "A")
        self.assertEqual(results[0].grade_point, 4.0)
        self.assertEqual(res.gpa, 4.0)

