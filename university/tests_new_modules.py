from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile
from university.admissions_services import (
    generate_admission_letter_pdf,
    generate_application_number,
    matriculate_applicant,
)
from university.examination_operations import (
    generate_exam_card_pdf,
    generate_nominal_roll_pdf,
)
from university.financial_services import (
    check_financial_clearance,
    generate_fee_receipt_pdf,
    generate_student_statement_pdf,
    get_or_create_semester_invoice,
)
from university.models import (
    AcademicTerm, AcademicYear, Application, Course, Department, Enrollment, Exam,
    FeeInvoice, FeeStructure, Intake, Payment, Program,
    SupplementaryExamRegistration,
)

User = get_user_model()


class AdmissionsAndFinancialTests(TestCase):
    def setUp(self):
        # Admin user
        self.admin = User.objects.create_superuser(
            username="admin_test", email="admin@ums.ac.ke", password="password123", role=Role.ADMIN
        )

        # Department & Program
        self.dept = Department.objects.create(name="School of Computing", code="SOC")
        self.prog = Program.objects.create(department=self.dept, name="BSc Computer Science", code="BCS")

        # Academic Year & Intake
        self.ay = AcademicYear.objects.create(
            name="2026/2027",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            is_current=True,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Regular Intake",
            academic_year=self.ay,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=60),
            is_active=True,
        )

        # Academic Term
        self.term = AcademicTerm.objects.create(
            name="2026/2027 Semester 1",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=120),
            is_current=True,
        )

        # Fee Structure for Program
        self.fee_structure = FeeStructure.objects.create(
            program=self.prog,
            term=self.term,
            year_of_study=1,
            semester=1,
            tuition_fee=Decimal("45000.00"),
            registration_fee=Decimal("1500.00"),
            examination_fee=Decimal("3000.00"),
            library_fee=Decimal("1000.00"),
            activity_fee=Decimal("1000.00"),
            medical_fee=Decimal("1500.00"),
            ict_fee=Decimal("2000.00"),
            student_union_fee=Decimal("500.00"),
        )

    def test_fee_structure_total(self):
        self.assertEqual(self.fee_structure.total_fee, Decimal("55500.00"))

    def test_public_application_submission(self):
        response = self.client.post(reverse("university:admissions_apply"), {
            "program": self.prog.id,
            "first_name": "Kelvin",
            "last_name": "Wanyonyi",
            "email": "kelvin@example.com",
            "phone": "+254712345678",
            "date_of_birth": "2005-04-12",
            "gender": "MALE",
            "national_id": "38472910",
            "address": "P.O. Box 100, Nairobi",
            "secondary_school": "Alliance High School",
            "kcse_index_number": "12345678001",
            "kcse_mean_grade": "A-",
            "kcse_year": "2025",
        })
        self.assertEqual(response.status_code, 302)

        app = Application.objects.filter(email="kelvin@example.com").first()
        self.assertIsNotNone(app)
        self.assertTrue(app.application_number.startswith("APP-"))
        self.assertEqual(app.status, Application.Status.SUBMITTED)

    def test_admission_letter_generation(self):
        app = Application.objects.create(
            application_number="APP-2026-TEST",
            intake=self.intake,
            program=self.prog,
            first_name="Grace",
            last_name="Mutua",
            email="grace@example.com",
            phone="+254722000000",
            date_of_birth=date(2005, 5, 20),
            gender="FEMALE",
            national_id="39281044",
            status=Application.Status.ACCEPTED,
        )
        pdf_bytes = generate_admission_letter_pdf(app)
        self.assertTrue(len(pdf_bytes) > 500)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_matriculation_process(self):
        app = Application.objects.create(
            application_number="APP-2026-MATRIC",
            intake=self.intake,
            program=self.prog,
            first_name="Brian",
            last_name="Kimani",
            email="brian@example.com",
            phone="+254733000000",
            date_of_birth=date(2005, 8, 15),
            gender="MALE",
            national_id="38192019",
            status=Application.Status.ACCEPTED,
        )
        student_profile, user, pw = matriculate_applicant(app, created_by=self.admin)
        self.assertIsNotNone(student_profile)
        self.assertEqual(student_profile.user.email, "brian@example.com")
        self.assertEqual(student_profile.user.role, Role.STUDENT)
        self.assertEqual(app.status, Application.Status.ENROLLED)

        # Check automated invoice creation
        inv = FeeInvoice.objects.filter(student=student_profile).first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.amount, Decimal("55500.00"))

    def test_financial_clearance_and_receipt(self):
        # Create student user
        user = User.objects.create_user(
            username="test.student", email="teststu@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        student = StudentProfile.objects.create(
            user=user, roll_no="BCS/0099/2026", program=self.prog, current_semester=1
        )

        inv, _ = get_or_create_semester_invoice(student, term=self.term)
        self.assertEqual(inv.amount, Decimal("55500.00"))

        # Initially uncleared
        clearance = check_financial_clearance(student, term=self.term)
        self.assertFalse(clearance["is_cleared"])
        self.assertEqual(clearance["balance"], Decimal("55500.00"))

        # Pay invoice
        inv.amount_paid = Decimal("55500.00")
        inv.save()
        pmt = Payment.objects.create(invoice=inv, amount=Decimal("55500.00"), method="M-Pesa", reference="QA12345678")

        # Now cleared
        clearance_after = check_financial_clearance(student, term=self.term)
        self.assertTrue(clearance_after["is_cleared"])
        self.assertEqual(clearance_after["balance"], Decimal("0.00"))

        # Receipt PDF
        receipt_pdf = generate_fee_receipt_pdf(pmt)
        self.assertTrue(receipt_pdf.startswith(b"%PDF"))

        # Statement PDF
        statement_pdf = generate_student_statement_pdf(student)
        self.assertTrue(statement_pdf.startswith(b"%PDF"))

    def test_exam_card_generation(self):
        user = User.objects.create_user(
            username="exam.student", email="examstu@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        student = StudentProfile.objects.create(
            user=user, roll_no="BCS/0100/2026", program=self.prog, current_semester=1
        )

        course = Course.objects.create(
            department=self.dept, code="CSC101", title="Intro to Programming", credits=3
        )
        Enrollment.objects.create(student=student, course=course, status=Enrollment.ACTIVE)

        # 1. Uncleared balance generates Financial Hold Notice PDF
        FeeInvoice.objects.create(
            student=student, term=self.term, title="Term Fee", amount=Decimal("50000"), amount_paid=Decimal("0"),
            due_date=date.today() + timedelta(days=10)
        )
        hold_card_pdf = generate_exam_card_pdf(student, term=self.term)
        self.assertTrue(hold_card_pdf.startswith(b"%PDF"))

        # 2. Cleared balance generates Full Exam Pass PDF
        FeeInvoice.objects.filter(student=student).update(amount_paid=Decimal("50000"))
        cleared_card_pdf = generate_exam_card_pdf(student, term=self.term)
        self.assertTrue(cleared_card_pdf.startswith(b"%PDF"))

    def test_nominal_roll_generation(self):
        course = Course.objects.create(
            department=self.dept, code="CSC102", title="Discrete Mathematics", credits=3
        )
        exam = Exam.objects.create(
            course=course, term=self.term, date=date.today() + timedelta(days=20),
            kind=Exam.Kind.FINAL, exam_max_marks=70, cat_max_marks=30
        )
        nominal_pdf = generate_nominal_roll_pdf(exam)
        self.assertTrue(nominal_pdf.startswith(b"%PDF"))

    def test_supplementary_exam_registration(self):
        user = User.objects.create_user(
            username="supp.student", email="suppstu@ums.ac.ke", password="password123", role=Role.STUDENT
        )
        student = StudentProfile.objects.create(
            user=user, roll_no="BCS/0101/2026", program=self.prog, current_semester=1
        )
        course = Course.objects.create(
            department=self.dept, code="CSC103", title="Computer Architecture", credits=3
        )

        self.client.force_login(user)
        response = self.client.post(reverse("university:student_supplementary_apply", args=[course.id]), {
            "exam_type": "SUPPLEMENTARY",
            "reason": "Retake after failing end of term paper",
        })
        self.assertEqual(response.status_code, 302)

        reg = SupplementaryExamRegistration.objects.filter(student=student, course=course).first()
        self.assertIsNotNone(reg)
        self.assertEqual(reg.status, SupplementaryExamRegistration.Status.PENDING)

        # Admin approves supplementary registration
        self.client.force_login(self.admin)
        appr_response = self.client.post(reverse("university:admin_supplementary_decision", args=[reg.pk]), {
            "action": "approve"
        })
        self.assertEqual(appr_response.status_code, 302)

        reg.refresh_from_db()
        self.assertEqual(reg.status, SupplementaryExamRegistration.Status.APPROVED)
        self.assertIsNotNone(reg.fee_invoice)
        self.assertEqual(reg.fee_invoice.amount, Decimal("1000.00"))
