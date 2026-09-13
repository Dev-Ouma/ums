from datetime import timedelta
import json
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, Client
from django.utils import timezone

from university.models import (
    SystemRestriction, Notice, AuditLog
)
from accounts.models import StudentProfile, FacultyProfile, Role
from university.models import Department, Program, AcademicYear, AcademicTerm
from university.control_services import (
    start_maintenance_mode, current_status, tick
)

User = get_user_model()


class MaintenanceLifecycleTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Computer Science", code="CS", color="#2563eb")
        self.prog = Program.objects.create(name="BSc Computer Science", code="BCS", department=self.dept)
        today = timezone.now().date()
        self.year = AcademicYear.objects.create(
            name="2026/2027", start_date=today, end_date=today + timedelta(days=365), is_current=True
        )
        self.term = AcademicTerm.objects.create(
            name="Semester 1", academic_year=self.year, start_date=today, end_date=today + timedelta(days=120), is_current=True
        )

        self.admin = User.objects.create_superuser(
            username="admin_test", email="admin_test@example.com", password="demo1234"
        )
        self.student_user = User.objects.create_user(
            username="student_test", email="student_test@example.com", password="demo1234", role=Role.STUDENT
        )
        StudentProfile.objects.create(user=self.student_user, program=self.prog, roll_no="CS/2026/001")

        self.client = Client(SERVER_NAME='127.0.0.1')

    def test_start_2_minute_maintenance(self):
        """Verify server authoritative 2-minute maintenance activation, duration, audit log, and banner notice."""
        now_before = timezone.now()
        r = start_maintenance_mode(duration_minutes=2, user=self.admin)
        now_after = timezone.now()

        self.assertEqual(r.status, SystemRestriction.Status.ACTIVE)
        self.assertEqual(r.kind, SystemRestriction.Kind.MAINTENANCE)
        self.assertTrue(r.starts_at >= now_before - timedelta(seconds=2))
        self.assertTrue(r.ends_at >= r.starts_at + timedelta(minutes=2) - timedelta(seconds=1))

        # Check Audit Log
        audit_entry = AuditLog.objects.filter(entity="SystemRestriction", entity_id=r.pk, action="MAINTENANCE_START").first()
        self.assertIsNotNone(audit_entry)
        self.assertIn("System Maintenance Started", audit_entry.description)
        self.assertIn("Duration: 2 minutes", audit_entry.description)

        # Check Notice created for banner & in-app
        notice = Notice.objects.filter(restriction=r).first()
        self.assertIsNotNone(notice)
        self.assertEqual(notice.status, 'PUBLISHED')
        self.assertIn('BANNER', notice.locations)
        self.assertIn('IN_APP', notice.locations)

        # Check current_status() returns active maintenance payload
        status = current_status()
        self.assertEqual(status['status'], 'Maintenance')
        self.assertTrue(status['maintenance'])
        self.assertIsNotNone(status['active_maintenance'])
        self.assertEqual(status['active_maintenance']['id'], r.pk)
        self.assertEqual(status['active_maintenance']['status'], "Maintenance in progress")

    def test_normal_operations_blocked_with_503_and_maintenance_screen(self):
        """Verify regular users receive HTTP 503 and polished maintenance page while session is kept."""
        r = start_maintenance_mode(duration_minutes=2, user=self.admin)

        self.client.force_login(self.student_user)
        res = self.client.get('/dashboard/')
        self.assertEqual(res.status_code, 503)
        self.assertTemplateUsed(res, 'control/unavailable.html')
        self.assertContains(res, "System Under Maintenance", status_code=503)
        self.assertContains(res, "Maintenance in progress", status_code=503)
        self.assertContains(res, "ESTIMATED TIME REMAINING", status_code=503)

        # Verify session is preserved (user is not logged out)
        self.assertTrue('_auth_user_id' in self.client.session)

    def test_api_requests_receive_structured_503(self):
        """Verify API and JSON requests receive structured 503 with remaining time and timestamps."""
        start_maintenance_mode(duration_minutes=2, user=self.admin)

        self.client.force_login(self.student_user)
        res = self.client.get('/dashboard/', HTTP_ACCEPT='application/json')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.headers.get('Content-Type'), 'application/json')

        data = res.json()
        self.assertEqual(data['status'], 'UNAVAILABLE')
        self.assertEqual(data['system_status'], 'Maintenance in progress')
        self.assertTrue(data['maintenance'])
        self.assertIn('remaining_seconds', data)
        self.assertIn('expected_end', data)
        self.assertIn('server_time', data)

    def test_payment_callbacks_remain_operational(self):
        """Verify critical payment webhooks (M-Pesa, card, bank) bypass maintenance mode completely."""
        start_maintenance_mode(duration_minutes=2, user=self.admin)

        # Test mpesa callback URL is exempt from maintenance 503
        payload = json.dumps({"Body": {"stkCallback": {"ResultCode": 1032, "ResultDesc": "Cancelled"}}})
        res = self.client.post(
            '/api/payments/callback/mpesa/',
            data=payload,
            content_type='application/json'
        )
        # Should NOT return 503 (will return 200 or business logic response)
        self.assertNotEqual(res.status_code, 503)

    def test_authorized_admin_bypass_and_audit(self):
        """Verify authorized admin with bypass permission can access system and an audit log is recorded."""
        start_maintenance_mode(duration_minutes=2, user=self.admin)

        self.client.force_login(self.admin)
        res = self.client.get('/system-control/')
        self.assertEqual(res.status_code, 200)

        # Verify bypass audit log exists
        bypass_log = AuditLog.objects.filter(action="MAINTENANCE_BYPASS", user=self.admin).first()
        self.assertIsNotNone(bypass_log)
        self.assertIn("Authorized maintenance bypass access", bypass_log.description)

    def test_server_authoritative_auto_resume(self):
        """Verify that when the 2-minute period expires, system automatically resumes to Operational."""
        r = start_maintenance_mode(duration_minutes=2, user=self.admin)

        # Simulate expiration by setting starts_at and ends_at in the past
        r.starts_at = timezone.now() - timedelta(minutes=3)
        r.ends_at = timezone.now() - timedelta(minutes=1)
        r.save(update_fields=['starts_at', 'ends_at'])

        # Ordinary student makes request to /dashboard/
        self.client.force_login(self.student_user)
        res = self.client.get('/dashboard/')

        # Middleware triggers tick() automatically and serves normal response 200
        self.assertEqual(res.status_code, 200)

        # Verify restriction is COMPLETED
        r.refresh_from_db()
        self.assertEqual(r.status, SystemRestriction.Status.COMPLETED)

        # Verify system status is Operational
        status = current_status()
        self.assertEqual(status['status'], 'Operational')
        self.assertFalse(status['maintenance'])

        # Verify Auto Complete Audit Log was created
        auto_log = AuditLog.objects.filter(entity="SystemRestriction", entity_id=r.pk, action="MAINTENANCE_AUTO_COMPLETE").first()
        self.assertIsNotNone(auto_log)
        self.assertIn("System Maintenance Automatically Completed", auto_log.description)
        self.assertIn("Status: Operational", auto_log.description)

    def test_management_command_start_maintenance(self):
        """Verify CLI management command starts 2-minute maintenance with server clock."""
        call_command('start_maintenance', duration=2)
        active_r = SystemRestriction.objects.filter(status=SystemRestriction.Status.ACTIVE, kind=SystemRestriction.Kind.MAINTENANCE).first()
        self.assertIsNotNone(active_r)
        self.assertEqual(round((active_r.ends_at - active_r.starts_at).total_seconds() / 60), 2)
