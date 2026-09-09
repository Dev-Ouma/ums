import datetime
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.models import (
    AcademicYear, AcademicTerm, Department, Program, Course,
    SemesterRegistration, Enrollment, Application, SystemSetting, AuditLog
)
from university.academic_calendar_services import (
    get_current_academic_year, get_current_semester, get_active_academic_context,
    set_current_academic_year, set_current_semester,
    publish_academic_year, unpublish_academic_year,
    close_academic_year, reopen_academic_year,
    publish_semester, unpublish_semester,
    close_semester, reopen_semester,
    is_registration_open_for_semester
)
from university.student_numbering_services import (
    generate_student_registration_number, preview_student_registration_number
)
from university.admissions_services import matriculate_applicant
from university.recycle_bin_services import move_to_recycle_bin, restore_from_recycle_bin

User = get_user_model()


class AcademicCalendarTests(TestCase):
    def setUp(self):
        # Admin user
        self.admin_user = User.objects.create_user(
            username="admin_calendar",
            email="admin_cal@university.edu",
            first_name="Admin",
            last_name="Calendar",
            role=Role.ADMIN,
            is_staff=True,
            is_superuser=True,
        )

        # Department & Program
        self.dept = Department.objects.create(name="Computer Science & Informatics", code="CS")
        self.program = Program.objects.create(
            name="Bachelor of Science in Computer Science",
            code="BS-CS",
            department=self.dept,
            level="UG",
            duration_value=4,
        )

        # Base dates
        today = timezone.now().date()
        self.cur_year = today.year

        # Clean slate for academic years in test DB
        AcademicTerm.objects.all().delete()
        AcademicYear.objects.all().delete()

        # Create Academic Year 1 (2026/2027)
        self.ay_2026 = AcademicYear.objects.create(
            name=f"{self.cur_year}/{self.cur_year + 1}",
            start_date=datetime.date(self.cur_year, 7, 1),
            end_date=datetime.date(self.cur_year + 1, 6, 30),
            status=AcademicYear.Status.PUBLISHED,
            is_current=True,
        )

        # Create Semesters under 2026/2027
        self.sem1 = AcademicTerm.objects.create(
            academic_year=self.ay_2026,
            name=f"Semester 1 {self.cur_year}",
            semester_number=1,
            start_date=datetime.date(self.cur_year, 7, 1),
            end_date=datetime.date(self.cur_year, 12, 20),
            registration_start_date=datetime.date(self.cur_year, 7, 1),
            registration_end_date=datetime.date(self.cur_year, 12, 31),
            exam_start_date=datetime.date(self.cur_year, 12, 1),
            exam_end_date=datetime.date(self.cur_year, 12, 20),
            status=AcademicYear.Status.CURRENT,
            is_current=True,
        )

        self.sem2 = AcademicTerm.objects.create(
            academic_year=self.ay_2026,
            name=f"Semester 2 {self.cur_year + 1}",
            semester_number=2,
            start_date=datetime.date(self.cur_year + 1, 1, 10),
            end_date=datetime.date(self.cur_year + 1, 5, 30),
            registration_start_date=datetime.date(self.cur_year + 1, 1, 10),
            registration_end_date=datetime.date(self.cur_year + 1, 2, 15),
            status=AcademicYear.Status.PUBLISHED,
            is_current=False,
        )

    def test_academic_year_lifecycle_and_single_current_enforcement(self):
        """Test creating, publishing, setting current, and closing academic years."""
        # Create second academic year 2027/2028
        ay_2027 = AcademicYear.objects.create(
            name=f"{self.cur_year + 1}/{self.cur_year + 2}",
            start_date=datetime.date(self.cur_year + 1, 7, 1),
            end_date=datetime.date(self.cur_year + 2, 6, 30),
            status=AcademicYear.Status.DRAFT,
            is_current=False,
        )

        # Initial state: ay_2026 is current
        self.assertTrue(self.ay_2026.is_current)
        self.assertEqual(get_current_academic_year().pk, self.ay_2026.pk)

        # Publish 2027
        publish_academic_year(ay_2027.pk, user=self.admin_user)
        ay_2027.refresh_from_db()
        self.assertEqual(ay_2027.status, AcademicYear.Status.PUBLISHED)

        # Set 2027 as current
        set_current_academic_year(ay_2027.pk, user=self.admin_user)
        ay_2027.refresh_from_db()
        self.ay_2026.refresh_from_db()

        self.assertTrue(ay_2027.is_current)
        self.assertEqual(ay_2027.status, AcademicYear.Status.CURRENT)
        # 2026 must atomically lose is_current
        self.assertFalse(self.ay_2026.is_current)
        self.assertEqual(get_current_academic_year().pk, ay_2027.pk)

        # Close 2026
        close_academic_year(self.ay_2026.pk, user=self.admin_user)
        self.ay_2026.refresh_from_db()
        self.assertEqual(self.ay_2026.status, AcademicYear.Status.CLOSED)

        # Reopen 2026
        reopen_academic_year(self.ay_2026.pk, user=self.admin_user)
        self.ay_2026.refresh_from_db()
        self.assertEqual(self.ay_2026.status, AcademicYear.Status.PUBLISHED)

    def test_academic_year_date_validation(self):
        """Test date validation: start_date must be strictly before end_date."""
        invalid_ay = AcademicYear(
            name="Invalid Year",
            start_date=datetime.date(2028, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )
        with self.assertRaises(ValidationError):
            invalid_ay.clean()

    def test_semester_lifecycle_and_boundary_validations(self):
        """Test semester dates must lie within academic year and single current enforcement."""
        # 1. Single current semester enforcement
        self.assertTrue(self.sem1.is_current)
        self.assertFalse(self.sem2.is_current)

        set_current_semester(self.sem2.pk, user=self.admin_user)
        self.sem1.refresh_from_db()
        self.sem2.refresh_from_db()

        self.assertTrue(self.sem2.is_current)
        self.assertFalse(self.sem1.is_current)
        self.assertEqual(get_current_semester().pk, self.sem2.pk)

        # 2. Semester dates outside parent year raise ValidationError
        out_of_bounds_sem = AcademicTerm(
            academic_year=self.ay_2026,
            name="Out of Bounds Semester",
            start_date=datetime.date(self.cur_year - 2, 1, 1),
            end_date=datetime.date(self.cur_year - 1, 5, 1),
        )
        with self.assertRaises(ValidationError):
            out_of_bounds_sem.clean()

        # 3. Registration start > registration end raises ValidationError
        invalid_reg_sem = AcademicTerm(
            academic_year=self.ay_2026,
            name="Invalid Reg Dates",
            start_date=datetime.date(self.cur_year, 8, 1),
            end_date=datetime.date(self.cur_year, 11, 1),
            registration_start_date=datetime.date(self.cur_year, 9, 1),
            registration_end_date=datetime.date(self.cur_year, 8, 15),
        )
        with self.assertRaises(ValidationError):
            invalid_reg_sem.clean()

    def test_central_academic_calendar_service(self):
        """Verify centralized academic calendar context returns consistent operational data."""
        ctx = get_active_academic_context()
        self.assertEqual(ctx["academic_year"].pk, self.ay_2026.pk)
        self.assertEqual(ctx["academic_year_name"], self.ay_2026.name)
        self.assertIsNotNone(ctx["semester"])
        self.assertTrue(ctx["is_registration_open"])

        # Test registration open helper
        self.assertTrue(is_registration_open_for_semester(self.sem1))

        # Close sem1 and verify registration is closed
        close_semester(self.sem1.pk, user=self.admin_user)
        self.sem1.refresh_from_db()
        self.assertFalse(is_registration_open_for_semester(self.sem1))

    def test_configurable_student_registration_number_generation(self):
        """Test admin-configurable student roll number generator with format PROG/SEQ/YEAR_END."""
        # 1. Default pattern: {PROG}/{SEQ:03d}/{YEAR_END}
        num1 = generate_student_registration_number(program=self.program, academic_year=self.ay_2026)
        end_year = self.ay_2026.end_date.year
        self.assertEqual(num1, f"BS-CS/001/{end_year}")

        # 2. Create student with this number and verify next sequence increments to 002
        u1 = User.objects.create_user(username="test_std_1", email="s1@uni.edu", role=Role.STUDENT)
        StudentProfile.objects.create(user=u1, roll_no=num1, program=self.program)

        num2 = generate_student_registration_number(program=self.program, academic_year=self.ay_2026)
        self.assertEqual(num2, f"BS-CS/002/{end_year}")

        # 3. Test explicit format requested: Programme Code e.g. ST01, Unique number 001, Academic year end 2026 -> ST01/001/2026
        st01_prog = Program.objects.create(name="Software Tech", code="ST01", department=self.dept)
        ay_2025_2026 = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 7, 1),
            end_date=datetime.date(2026, 6, 30),
            status=AcademicYear.Status.CLOSED,
        )
        sample_3digit = preview_student_registration_number(
            pattern="{PROG}/{SEQ:03d}/{YEAR_END}",
            program=st01_prog,
            academic_year=ay_2025_2026
        )
        self.assertEqual(sample_3digit, "ST01/001/2026")

        sample_4digit = preview_student_registration_number(
            pattern="{PROG}/{SEQ:04d}/{YEAR_END}",
            program=st01_prog,
            academic_year=ay_2025_2026
        )
        self.assertEqual(sample_4digit, "ST01/0001/2026")

    def test_matriculation_assigns_current_calendar_and_generated_number(self):
        """Test matriculating an accepted applicant automatically assigns current academic year and semester."""
        app = Application.objects.create(
            application_number="APP-CAL-001",
            program=self.program,
            first_name="Jane",
            last_name="Doe",
            email="janedoe@example.com",
            phone="+254700111222",
            date_of_birth=datetime.date(2003, 5, 15),
            status=Application.Status.ACCEPTED,
        )

        student, user, _ = matriculate_applicant(app, created_by=self.admin_user)

        self.assertIsNotNone(student)
        self.assertTrue(len(student.roll_no) > 0)

        # Check SemesterRegistration was automatically created for current semester & year
        reg = SemesterRegistration.objects.filter(student=student).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.term.pk, self.sem1.pk)
        self.assertEqual(reg.academic_year, self.ay_2026.name)

    def test_student_unit_registration_gated_by_semester_calendar(self):
        """Test that unit registration is blocked if current semester is closed."""
        # Create student
        u = User.objects.create_user(username="student_reg_test", email="srt@uni.edu", role=Role.STUDENT)
        sp = StudentProfile.objects.create(user=u, roll_no="REG-001", program=self.program, current_semester=1)

        client = Client()
        client.force_login(u)

        reg_url = reverse("university:student_register_units")
        res = client.get(reg_url)
        self.assertEqual(res.status_code, 200)

        # Now close sem1 and sem2
        close_semester(self.sem1.pk, user=self.admin_user)
        close_semester(self.sem2.pk, user=self.admin_user)
        self.sem1.refresh_from_db()
        self.sem2.refresh_from_db()

        res_closed = client.get(reg_url)
        self.assertEqual(res_closed.status_code, 200)
        # Should be blocked
        self.assertTrue(res_closed.context.get("blocked", False))

    def test_academic_calendar_admin_views_and_actions(self):
        """Test admin views for academic years, semester creation, and actions."""
        client = Client()
        client.force_login(self.admin_user)

        # 1. Directory view
        res = client.get(reverse("university:admin_academic_years"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.ay_2026.name)

        # 2. Detail view
        detail_url = reverse("university:academic_year_detail", args=[self.ay_2026.pk])
        res_detail = client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, self.sem1.name)

        # 3. Create Semester via view
        new_sem_url = reverse("university:semester_create", args=[self.ay_2026.pk])
        res_post_sem = client.post(new_sem_url, {
            "name": f"Summer Session {self.cur_year + 1}",
            "term_type": "SUMMER",
            "semester_number": 3,
            "start_date": datetime.date(self.cur_year + 1, 6, 1),
            "end_date": datetime.date(self.cur_year + 1, 6, 28),
            "status": "PUBLISHED",
        })
        self.assertEqual(res_post_sem.status_code, 302)
        self.assertTrue(AcademicTerm.objects.filter(name__icontains="Summer Session").exists())

        # 4. AJAX numbering preview endpoint
        preview_url = reverse("university:api_numbering_preview")
        res_preview = client.get(f"{preview_url}?pattern={{PROG}}/{{SEQ:03d}}/{{YEAR_END}}")
        self.assertEqual(res_preview.status_code, 200)
        data = res_preview.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["preview"], f"BS-CS/001/{self.ay_2026.end_date.year}")

    def test_audit_log_and_recycle_bin_integration(self):
        """Test that academic year transitions are audited and soft-deletes move to Recycle Bin."""
        # Set current academic year and verify audit
        set_current_academic_year(self.ay_2026.pk, user=self.admin_user)
        audit = AuditLog.objects.filter(
            action=AuditLog.Action.SET_CURRENT,
            module=AuditLog.Module.CALENDAR,
            entity="AcademicYear",
            entity_id=str(self.ay_2026.id),
        ).first()
        self.assertIsNotNone(audit)

        # Create temporary non-current academic year to test Recycle Bin
        temp_ay = AcademicYear.objects.create(
            name="Temporary Year",
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 12, 31),
            status=AcademicYear.Status.DRAFT,
            is_current=False,
        )

        item = move_to_recycle_bin(temp_ay, user=self.admin_user)
        self.assertFalse(AcademicYear.objects.filter(name="Temporary Year").exists())

        # Restore from recycle bin
        restored = restore_from_recycle_bin(item.pk, user=self.admin_user)
        self.assertIsNotNone(restored)
        self.assertTrue(AcademicYear.objects.filter(name="Temporary Year").exists())
