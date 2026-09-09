from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from university import student_requests_services as services
from university.models import AcademicTerm, Department, Program, StudentRequest


class StudentRequestTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = date.today()
        cls.term = AcademicTerm.objects.create(
            name="Test Term", start_date=cls.today - timedelta(days=30),
            end_date=cls.today + timedelta(days=30), is_current=True)
        dept = Department.objects.create(name="Computing", code="CMP")
        cls.program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        cls.admin = User.objects.create_user("admin1", password="x", role=Role.ADMIN)
        cls.student_user = User.objects.create_user("stud1", password="x", role=Role.STUDENT)
        cls.student = StudentProfile.objects.create(user=cls.student_user, roll_no="R1", program=cls.program)
        cls.other_user = User.objects.create_user("stud2", password="x", role=Role.STUDENT)
        cls.other_student = StudentProfile.objects.create(user=cls.other_user, roll_no="R2", program=cls.program)


class StudentRequestServiceTests(StudentRequestTestBase):
    def test_submit_request_success(self):
        req = services.submit_request(self.student, StudentRequest.Type.SICK_LEAVE, "Flu",
                                      start_date=self.today, end_date=self.today + timedelta(days=7))
        self.assertEqual(req.status, StudentRequest.Status.PENDING)
        self.assertEqual(req.student, self.student)

    def test_cannot_submit_second_pending_request(self):
        services.submit_request(self.student, StudentRequest.Type.SICK_LEAVE, "Flu",
                                start_date=self.today, end_date=self.today + timedelta(days=7))
        with self.assertRaises(ValidationError):
            services.submit_request(self.student, StudentRequest.Type.DEFERMENT, "Other reason")

    def test_cannot_submit_while_inactive(self):
        self.student.status = StudentProfile.Status.WITHDRAWN
        self.student.save()
        with self.assertRaises(ValidationError):
            services.submit_request(self.student, StudentRequest.Type.DEFERMENT, "Reason")

    def test_approve_deferment_updates_student_status(self):
        req = services.submit_request(self.student, StudentRequest.Type.DEFERMENT, "Financial hardship",
                                      start_date=self.today, end_date=self.today + timedelta(days=90))
        services.decide_request(self.admin, req, StudentRequest.Status.APPROVED, comments="Approved")
        self.student.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.DEFERRED)
        self.assertEqual(req.status, StudentRequest.Status.APPROVED)
        self.assertEqual(req.reviewed_by, self.admin)
        self.assertIsNotNone(req.decided_at)

    def test_approve_withdrawal_updates_student_status(self):
        req = services.submit_request(self.student, StudentRequest.Type.WITHDRAWAL, "Leaving university")
        services.decide_request(self.admin, req, StudentRequest.Status.APPROVED)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.WITHDRAWN)

    def test_approve_sick_leave_updates_student_status(self):
        req = services.submit_request(self.student, StudentRequest.Type.SICK_LEAVE, "Surgery recovery",
                                      start_date=self.today, end_date=self.today + timedelta(days=14))
        services.decide_request(self.admin, req, StudentRequest.Status.APPROVED)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ON_LEAVE)

    def test_reject_does_not_change_student_status(self):
        req = services.submit_request(self.student, StudentRequest.Type.DEFERMENT, "Reason",
                                      start_date=self.today, end_date=self.today + timedelta(days=30))
        services.decide_request(self.admin, req, StudentRequest.Status.REJECTED, comments="Insufficient grounds")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ACTIVE)
        self.assertEqual(req.status, StudentRequest.Status.REJECTED)

    def test_cannot_decide_already_decided_request(self):
        req = services.submit_request(self.student, StudentRequest.Type.WITHDRAWAL, "Reason")
        services.decide_request(self.admin, req, StudentRequest.Status.APPROVED)
        with self.assertRaises(ValidationError):
            services.decide_request(self.admin, req, StudentRequest.Status.REJECTED)

    def test_mark_under_review(self):
        req = services.submit_request(self.student, StudentRequest.Type.SICK_LEAVE, "Reason",
                                      start_date=self.today, end_date=self.today + timedelta(days=5))
        services.mark_under_review(self.admin, req)
        req.refresh_from_db()
        self.assertEqual(req.status, StudentRequest.Status.UNDER_REVIEW)

    def test_resume_studies_from_deferred(self):
        self.student.status = StudentProfile.Status.DEFERRED
        self.student.save()
        services.resume_studies(self.student)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ACTIVE)

    def test_resume_studies_rejects_active_student(self):
        with self.assertRaises(ValidationError):
            services.resume_studies(self.student)

    def test_sync_leave_expiry_auto_reverts_after_end_date(self):
        req = StudentRequest.objects.create(
            student=self.student, request_type=StudentRequest.Type.SICK_LEAVE, reason="Reason",
            start_date=self.today - timedelta(days=20), end_date=self.today - timedelta(days=1),
            status=StudentRequest.Status.APPROVED)
        self.student.status = StudentProfile.Status.ON_LEAVE
        self.student.save()
        services.sync_leave_expiry(self.student)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ACTIVE)

    def test_sync_leave_expiry_keeps_active_leave(self):
        StudentRequest.objects.create(
            student=self.student, request_type=StudentRequest.Type.SICK_LEAVE, reason="Reason",
            start_date=self.today, end_date=self.today + timedelta(days=10),
            status=StudentRequest.Status.APPROVED)
        self.student.status = StudentProfile.Status.ON_LEAVE
        self.student.save()
        services.sync_leave_expiry(self.student)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ON_LEAVE)


class StudentRequestViewTests(StudentRequestTestBase):
    def test_student_can_submit_request(self):
        self.client.force_login(self.student_user)
        resp = self.client.post(reverse("university:student_requests"), {
            "request_type": StudentRequest.Type.SICK_LEAVE, "reason": "Flu",
            "start_date": self.today.isoformat(), "end_date": (self.today + timedelta(days=5)).isoformat(),
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(StudentRequest.objects.filter(student=self.student).exists())

    def test_student_sees_only_own_history(self):
        StudentRequest.objects.create(student=self.other_student, request_type=StudentRequest.Type.WITHDRAWAL,
                                      reason="Other student's reason")
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("university:student_requests"))
        self.assertNotContains(resp, "Other student's reason")

    def test_anonymous_blocked(self):
        resp = self.client.get(reverse("university:student_requests"))
        self.assertNotEqual(resp.status_code, 200)

    def test_admin_can_list_requests(self):
        StudentRequest.objects.create(student=self.student, request_type=StudentRequest.Type.DEFERMENT,
                                      reason="Reason", start_date=self.today, end_date=self.today + timedelta(days=30))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("university:admin_student_requests"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "R1")

    def test_non_admin_blocked_from_admin_list(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("university:admin_student_requests"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_approve_request(self):
        req = StudentRequest.objects.create(student=self.student, request_type=StudentRequest.Type.WITHDRAWAL,
                                            reason="Leaving")
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("university:admin_student_request_detail", args=[req.pk]),
                                {"action": "approve", "comments": "Confirmed"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(req.status, StudentRequest.Status.APPROVED)
        self.assertEqual(self.student.status, StudentProfile.Status.WITHDRAWN)

    def test_deferred_student_blocked_from_unit_registration(self):
        self.student.status = StudentProfile.Status.DEFERRED
        self.student.save()
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("university:student_register_units"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "unavailable")

    def test_resume_studies_view(self):
        self.student.status = StudentProfile.Status.DEFERRED
        self.student.save()
        self.client.force_login(self.student_user)
        resp = self.client.post(reverse("university:student_resume_studies"), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, StudentProfile.Status.ACTIVE)
