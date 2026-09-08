from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile
from university.graduation_services import (
    approve_senate_graduation, audit_graduation_eligibility,
    classify_degree_honours, generate_clearance_certificate_pdf,
    generate_degree_certificate_pdf, initiate_student_clearance,
    process_department_clearance
)
from university.hostel_services import (
    allocate_hostel_room, apply_hostel_room, checkin_hostel_student,
    checkout_hostel_student, get_available_rooms, seed_default_hostels
)
from university.library_services import (
    get_user_library_status, issue_book, return_book,
    search_books, seed_default_books
)
from university.models import (
    AcademicTerm, Book, BookLoan, Course, Department, DepartmentClearance,
    Exam, FeeInvoice, GraduationApplication, GraduationCeremony, HostelAllocation,
    HostelBlock, HostelRoom, PastExamPaper, Program, Result
)

User = get_user_model()


class GraduationHostelLibraryTestCase(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin_staff", email="admin@uni.edu", password="password123",
            role=Role.ADMIN, is_staff=True
        )
        self.student_user = User.objects.create_user(
            username="student_grad", email="student@uni.edu", password="password123",
            role=Role.STUDENT, first_name="Kelvin", last_name="Ochieng"
        )
        self.dept = Department.objects.create(name="Computing & Informatics", code="CI")
        self.prog = Program.objects.create(name="BSc Computer Science", code="BCS", department=self.dept, duration_years=4)
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user, roll_no="BCS/0014/2022",
            program=self.prog, current_semester=8
        )
        self.term = AcademicTerm.objects.create(
            name="2025/2026 Sem 2",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 5, 20),
            is_current=True
        )

        self.course1 = Course.objects.create(code="CSC401", title="Compiler Construction", department=self.dept, credits=4)
        self.course2 = Course.objects.create(code="CSC402", title="Machine Learning", department=self.dept, credits=4)

        # Create published results with Distinction
        self.exam1 = Exam.objects.create(
            course=self.course1, term=self.term, name="CSC401 Final Exam",
            status=Exam.Status.PUBLISHED, max_marks=100
        )
        self.exam2 = Exam.objects.create(
            course=self.course2, term=self.term, name="CSC402 Final Exam",
            status=Exam.Status.PUBLISHED, max_marks=100
        )
        Result.objects.create(exam=self.exam1, student=self.student_profile, marks_obtained=82, attendance="PRESENT")
        Result.objects.create(exam=self.exam2, student=self.student_profile, marks_obtained=78, attendance="PRESENT")

        self.ceremony = GraduationCeremony.objects.create(
            academic_year="2025/2026",
            title="15th Congregation for the Conferment of Degrees",
            ceremony_date=date(2026, 12, 10),
            venue="Main University Pavilion"
        )

        self.client = Client()

    # ==============================================================================
    # 1. GRADUATION & MULTI-DEPARTMENT CLEARANCE TESTS
    # ==============================================================================
    def test_classify_degree_honours(self):
        """Test CUE-aligned CGPA honors classifications."""
        self.assertEqual(classify_degree_honours(Decimal("3.85")), GraduationApplication.Classification.FIRST_CLASS)
        self.assertEqual(classify_degree_honours(Decimal("3.40")), GraduationApplication.Classification.SECOND_UPPER)
        self.assertEqual(classify_degree_honours(Decimal("2.65")), GraduationApplication.Classification.SECOND_LOWER)
        self.assertEqual(classify_degree_honours(Decimal("1.50")), GraduationApplication.Classification.PASS)

    def test_audit_graduation_eligibility(self):
        """Test academic and financial graduation eligibility audit."""
        audit = audit_graduation_eligibility(self.student_profile)
        self.assertGreater(audit["cgpa"], Decimal("3.00"))
        self.assertEqual(audit["credits_earned"], 8)
        self.assertEqual(len(audit["failed_courses"]), 0)

    def test_initiate_clearance_and_departmental_workflow(self):
        """Test initiating clearance populates 5 stations and auto-clears verified departments."""
        app, audit = initiate_student_clearance(self.student_profile, ceremony=self.ceremony)
        self.assertIsNotNone(app)
        self.assertEqual(app.clearances.count(), 5)
        self.assertIn("NIU/DEG/", app.certificate_serial)

        # Verify finance was auto-cleared due to zero fee balance
        fin_dc = app.clearances.filter(department=DepartmentClearance.DepartmentType.FINANCE).first()
        self.assertEqual(fin_dc.status, DepartmentClearance.ClearanceStatus.CLEARED)

        # Clear remaining 4 stations manually
        for dept in [DepartmentClearance.DepartmentType.LIBRARY, DepartmentClearance.DepartmentType.ACADEMIC_DEAN,
                     DepartmentClearance.DepartmentType.STUDENT_AFFAIRS, DepartmentClearance.DepartmentType.REGISTRAR]:
            dc = app.clearances.filter(department=dept).first()
            process_department_clearance(dc.id, DepartmentClearance.ClearanceStatus.CLEARED, user=self.admin_user, remarks="Requirements verified.")

        app.refresh_from_db()
        self.assertEqual(app.status, GraduationApplication.Status.CLEARED)

        # Senate conferment approval
        app = approve_senate_graduation(app.id, user=self.admin_user)
        self.assertEqual(app.status, GraduationApplication.Status.SENATE_APPROVED)
        self.assertIsNotNone(app.senate_approved_at)

    def test_generate_degree_and_clearance_certificates(self):
        """Test PDF certificate generation for degree and university clearance."""
        app, _ = initiate_student_clearance(self.student_profile, ceremony=self.ceremony)
        
        # Degree Certificate PDF
        degree_pdf = generate_degree_certificate_pdf(app)
        self.assertIsInstance(degree_pdf, bytes)
        self.assertTrue(degree_pdf.startswith(b"%PDF"))

        # Clearance Certificate PDF
        clearance_pdf = generate_clearance_certificate_pdf(app)
        self.assertIsInstance(clearance_pdf, bytes)
        self.assertTrue(clearance_pdf.startswith(b"%PDF"))

    def test_graduation_views_access(self):
        """Test student portal and admin dashboard HTTP endpoints."""
        # Student Portal
        self.client.login(username="student_grad", password="password123")
        res_student = self.client.get(reverse("university:student_graduation"))
        self.assertEqual(res_student.status_code, 200)

        # Apply via POST
        res_apply = self.client.post(reverse("university:student_apply_clearance"), {"ceremony_id": self.ceremony.id})
        self.assertEqual(res_apply.status_code, 302)

        # Clearance Certificate PDF download
        res_cert = self.client.get(reverse("university:student_clearance_certificate_pdf"))
        self.assertEqual(res_cert.status_code, 200)
        self.assertEqual(res_cert["Content-Type"], "application/pdf")

        # Admin Dashboard
        self.client.logout()
        self.client.login(username="admin_staff", password="password123")
        res_admin = self.client.get(reverse("university:admin_graduation_dashboard"))
        self.assertEqual(res_admin.status_code, 200)

        # Admin Clearance Queue
        res_queue = self.client.get(reverse("university:admin_clearance_queue", kwargs={"department": "finance"}))
        self.assertEqual(res_queue.status_code, 200)

    # ==============================================================================
    # 2. CAMPUS ACCOMMODATION & HOSTEL TESTS
    # ==============================================================================
    def test_hostel_lifecycle(self):
        """Test hostel seeding, availability check, room application, allocation, and checkin/out."""
        seed_count = seed_default_hostels()
        self.assertGreater(seed_count, 0)

        avail_rooms = get_available_rooms()
        self.assertGreater(len(avail_rooms), 0)
        target_room = avail_rooms[0]
        initial_occupied = target_room.occupied_beds

        # Student applies
        alloc, msg = apply_hostel_room(self.student_profile, target_room, self.term)
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc.status, HostelAllocation.Status.APPLIED)

        # Admin allocates
        success, msg = allocate_hostel_room(alloc.id, admin_user=self.admin_user)
        self.assertTrue(success)
        alloc.refresh_from_db()
        target_room.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.ALLOCATED)
        self.assertEqual(target_room.occupied_beds, initial_occupied + 1)

        # Verify automated billing invoice was created
        inv = FeeInvoice.objects.filter(student=self.student_profile, title__icontains="Hostel Fee").first()
        self.assertIsNotNone(inv)
        self.assertEqual(inv.amount, target_room.fee_per_semester)

        # Check-In
        success, msg = checkin_hostel_student(alloc.id, key_number="KEY-101-A", admin_user=self.admin_user)
        self.assertTrue(success)
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.CHECKED_IN)
        self.assertEqual(alloc.room_key_number, "KEY-101-A")

        # Check-Out releases bed
        success, msg = checkout_hostel_student(alloc.id, admin_user=self.admin_user)
        self.assertTrue(success)
        alloc.refresh_from_db()
        target_room.refresh_from_db()
        self.assertEqual(alloc.status, HostelAllocation.Status.CHECKED_OUT)
        self.assertEqual(target_room.occupied_beds, initial_occupied)

    def test_hostel_views(self):
        """Test student hostel portal and admin management views."""
        seed_default_hostels()
        self.client.login(username="student_grad", password="password123")
        res = self.client.get(reverse("university:student_hostel_portal"))
        self.assertEqual(res.status_code, 200)

        self.client.logout()
        self.client.login(username="admin_staff", password="password123")
        res_admin = self.client.get(reverse("university:admin_hostels_dashboard"))
        self.assertEqual(res_admin.status_code, 200)

    # ==============================================================================
    # 3. LIBRARY & PAST EXAM PAPERS TESTS
    # ==============================================================================
    def test_library_checkout_return_and_fines(self):
        """Test book catalog seeding, issuing books, copy decrement, and returns."""
        seed_count = seed_default_books()
        self.assertGreater(seed_count, 0)

        # Search book
        books = search_books("Algorithms")
        self.assertGreater(books.count(), 0)
        book = books.first()
        initial_avail = book.available_copies

        # Issue book
        loan, msg = issue_book(book.id, self.student_user, days=14, staff_user=self.admin_user)
        self.assertIsNotNone(loan)
        book.refresh_from_db()
        self.assertEqual(book.available_copies, initial_avail - 1)
        self.assertEqual(loan.status, BookLoan.Status.ACTIVE)

        # User status
        status = get_user_library_status(self.student_user)
        self.assertEqual(status["active_loans"].count(), 1)
        self.assertTrue(status["is_cleared"])

        # Return book
        success, msg = return_book(loan.id, staff_user=self.admin_user)
        self.assertTrue(success)
        book.refresh_from_db()
        loan.refresh_from_db()
        self.assertEqual(book.available_copies, initial_avail)
        self.assertEqual(loan.status, BookLoan.Status.RETURNED)

    def test_past_exam_papers(self):
        """Test creating and retrieving digital past examination papers."""
        pp = PastExamPaper.objects.create(
            course=self.course1,
            term=self.term,
            exam_type=PastExamPaper.ExamType.MAIN,
            academic_year="2025/2026",
            title="CSC 401 Main University Exam"
        )
        self.assertIsNotNone(pp)
        self.assertEqual(self.course1.past_papers.count(), 1)

    def test_library_views(self):
        """Test student library portal and admin library desk."""
        seed_default_books()
        self.client.login(username="student_grad", password="password123")
        res = self.client.get(reverse("university:student_library_portal"))
        self.assertEqual(res.status_code, 200)

        self.client.logout()
        self.client.login(username="admin_staff", password="password123")
        res_admin = self.client.get(reverse("university:admin_library_dashboard"))
        self.assertEqual(res_admin.status_code, 200)
