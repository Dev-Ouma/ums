from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import StudentProfile
from university.admissions_services import matriculate_applicant
from university.models import AcademicTerm, AcademicYear, Application, Department, Intake, Program, SemesterRegistration
from university.student_requests_services import resume_studies


class MatriculationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        dept = Department.objects.create(name="Computing", code="CMP")
        cls.program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        cls.academic_year = AcademicYear.objects.create(
            name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31))
        cls.intake = Intake.objects.create(
            name="Test Intake", academic_year=cls.academic_year,
            start_date=timezone.now().date(), end_date=timezone.now().date() + timedelta(days=90),
            is_active=True)

    def make_application(self, email="app@example.com"):
        return Application.objects.create(
            application_number=f"APP-{email}", intake=self.intake, program=self.program,
            first_name="Jane", last_name="Doe", email=email, phone="0700000000",
            date_of_birth=date(2004, 1, 1), gender="FEMALE", national_id=email,
            status=Application.Status.ACCEPTED,
        )


class MatriculationActiveTermTests(MatriculationTestBase):
    def setUp(self):
        self.term = AcademicTerm.objects.create(
            name="Fall 2026", start_date=timezone.now().date() - timedelta(days=10),
            end_date=timezone.now().date() + timedelta(days=80), is_current=True)

    def test_matriculation_assigns_active_term_and_registration(self):
        app = self.make_application()
        student, user, password = matriculate_applicant(app)

        self.assertEqual(student.status, StudentProfile.Status.ACTIVE)
        self.assertEqual(student.current_semester, 1)
        self.assertEqual(student.program, self.program)

        reg = SemesterRegistration.objects.get(student=student, term=self.term)
        self.assertEqual(reg.status, SemesterRegistration.DRAFT)
        self.assertEqual(reg.semester_no, 1)
        self.assertEqual(reg.academic_year, f"{self.term.start_date.year}/{self.term.end_date.year}")

    def test_fee_invoice_linked_to_active_term(self):
        app = self.make_application()
        student, _, _ = matriculate_applicant(app)
        invoice = student.invoices.first()
        self.assertEqual(invoice.term, self.term)

    def test_matriculation_idempotent_for_existing_student(self):
        app = self.make_application()
        student1, _, _ = matriculate_applicant(app)
        student2, _, password2 = matriculate_applicant(app)
        self.assertEqual(student1.pk, student2.pk)
        self.assertIsNone(password2)
        self.assertEqual(SemesterRegistration.objects.filter(student=student1).count(), 1)


class MatriculationNoActiveTermTests(MatriculationTestBase):
    def test_matriculation_without_any_term_does_not_crash(self):
        app = self.make_application()
        student, user, password = matriculate_applicant(app)
        self.assertEqual(student.status, StudentProfile.Status.ACTIVE)
        self.assertFalse(SemesterRegistration.objects.filter(student=student).exists())
        invoice = student.invoices.first()
        self.assertIsNone(invoice.term)


class ResumeStudiesTermAssignmentTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="Computing", code="CMP")
        self.program = Program.objects.create(name="BSc CS", code="BCS", department=dept)
        from accounts.models import Role, User
        user = User.objects.create_user("stud1", password="x", role=Role.STUDENT)
        self.student = StudentProfile.objects.create(
            user=user, roll_no="R1", program=self.program, status=StudentProfile.Status.DEFERRED)
        # Term active back when the student deferred (no longer current).
        AcademicTerm.objects.create(
            name="Spring 2026", start_date=timezone.now().date() - timedelta(days=200),
            end_date=timezone.now().date() - timedelta(days=110), is_current=False)
        # A new term is now active.
        self.new_term = AcademicTerm.objects.create(
            name="Fall 2026", start_date=timezone.now().date() - timedelta(days=10),
            end_date=timezone.now().date() + timedelta(days=80), is_current=True)

    def test_resume_assigns_currently_active_term_not_old_one(self):
        resume_studies(self.student)
        reg = SemesterRegistration.objects.get(student=self.student)
        self.assertEqual(reg.term, self.new_term)

    def test_resume_does_not_duplicate_existing_registration(self):
        SemesterRegistration.objects.create(
            student=self.student, term=self.new_term, semester_no=1, academic_year="2026/2027")
        resume_studies(self.student)
        self.assertEqual(SemesterRegistration.objects.filter(student=self.student, term=self.new_term).count(), 1)
