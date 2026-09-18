"""
Regression tests for the end-to-end Campus Services (Hostels & Library)
module audit fixes:
- Missing audit logging on book/past-paper catalog creation
- Past-paper upload no longer defaults to a stale hardcoded "2024/2025"
  academic year when left blank
- Shared OVERDUE_FINE_PER_DAY_KES constant used consistently
- Overdue auto-flagging in get_user_library_status is now row-locked
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from university.library_services import (
    OVERDUE_FINE_PER_DAY_KES,
    get_user_library_status,
    issue_book,
)
from university.models import (
    AcademicTerm,
    AcademicYear,
    AuditLog,
    Book,
    BookLoan,
    Course,
    Department,
    PastExamPaper,
)

User = get_user_model()


class LibraryCatalogAuditLoggingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="lib.admin", email="lib.admin@ums.ac.ke", password="password123", role=Role.ADMIN,
        )
        self.dept = Department.objects.create(name="Computing", code="CMP")
        self.course = Course.objects.create(code="CSC101", title="Intro", department=self.dept, credits=4)
        self.ay = AcademicYear.objects.create(name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31), is_current=True)
        self.term = AcademicTerm.objects.create(
            name="Sem 1", academic_year=self.ay, start_date=date(2026, 9, 1), end_date=date(2026, 12, 20), is_current=True,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_book_create_logs_audit_entry(self):
        response = self.client.post(reverse("university:admin_library_book_create"), {
            "title": "New Book", "author": "Jane Doe", "copies": "3",
        })
        self.assertEqual(response.status_code, 302)
        book = Book.objects.get(title="New Book")
        self.assertTrue(
            AuditLog.objects.filter(entity="Book", entity_id=book.id, action=AuditLog.Action.CREATE).exists()
        )

    def test_past_paper_create_requires_academic_year(self):
        response = self.client.post(reverse("university:admin_past_paper_create"), {
            "course_id": self.course.pk, "term_id": self.term.pk, "title": "CSC101 2026 Paper",
            "exam_type": "MAIN", "academic_year": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PastExamPaper.objects.filter(title="CSC101 2026 Paper").exists())

    def test_past_paper_create_logs_audit_entry_with_real_year(self):
        response = self.client.post(reverse("university:admin_past_paper_create"), {
            "course_id": self.course.pk, "term_id": self.term.pk, "title": "CSC101 2026 Paper",
            "exam_type": "MAIN", "academic_year": "2026/2027",
        })
        self.assertEqual(response.status_code, 302)
        paper = PastExamPaper.objects.get(title="CSC101 2026 Paper")
        self.assertEqual(paper.academic_year, "2026/2027")
        self.assertTrue(
            AuditLog.objects.filter(entity="PastExamPaper", entity_id=paper.id, action=AuditLog.Action.CREATE).exists()
        )


class LibraryOverdueFineConsistencyTests(TestCase):
    def setUp(self):
        self.borrower = User.objects.create_user(username="borrower1", password="x", role=Role.STUDENT)
        self.book = Book.objects.create(title="Overdue Test Book", author="Author", total_copies=1, available_copies=1)

    def test_get_user_library_status_uses_shared_fine_rate(self):
        loan = BookLoan.objects.create(
            book=self.book, borrower=self.borrower,
            issue_date=date.today() - timedelta(days=20),
            due_date=date.today() - timedelta(days=5),
            status=BookLoan.Status.ACTIVE,
        )
        status = get_user_library_status(self.borrower)
        loan.refresh_from_db()
        self.assertEqual(loan.status, BookLoan.Status.OVERDUE)
        self.assertEqual(loan.fine_accrued, OVERDUE_FINE_PER_DAY_KES * 5)
        self.assertEqual(status["total_fines"], OVERDUE_FINE_PER_DAY_KES * 5)

    def test_get_user_library_status_idempotent_on_repeated_calls(self):
        BookLoan.objects.create(
            book=self.book, borrower=self.borrower,
            issue_date=date.today() - timedelta(days=20),
            due_date=date.today() - timedelta(days=5),
            status=BookLoan.Status.ACTIVE,
        )
        first = get_user_library_status(self.borrower)
        second = get_user_library_status(self.borrower)
        self.assertEqual(first["total_fines"], second["total_fines"])
