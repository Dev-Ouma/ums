from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from university.audit_services import log_activity
from university.models import AuditLog, Book, BookLoan, Course, PastExamPaper


def seed_default_books():
    """Seed foundational textbooks and academic volumes."""
    if Book.objects.exists():
        return 0

    catalog = [
        {"title": "Introduction to Algorithms (CLRS)", "author": "Thomas H. Cormen, Charles E. Leiserson", "isbn": "9780262033848", "category": "Computer Science", "call_number": "QA76.6 .C662", "total": 8, "shelf": "Stack 2, Shelf A"},
        {"title": "Compilers: Principles, Techniques, and Tools", "author": "Alfred V. Aho, Monica S. Lam, Jeffrey Ullman", "isbn": "9780321486813", "category": "Computer Science", "call_number": "QA76.76 .C65", "total": 5, "shelf": "Stack 2, Shelf B"},
        {"title": "Operating System Concepts", "author": "Abraham Silberschatz, Peter B. Galvin", "isbn": "9781119800361", "category": "Computer Science", "call_number": "QA76.76 .O63", "total": 6, "shelf": "Stack 2, Shelf C"},
        {"title": "Database System Concepts", "author": "Avi Silberschatz, Henry F. Korth", "isbn": "9780078022159", "category": "Information Technology", "call_number": "QA76.9 .D3", "total": 7, "shelf": "Stack 3, Shelf A"},
        {"title": "Computer Networking: A Top-Down Approach", "author": "James F. Kurose, Keith W. Ross", "isbn": "9780133594140", "category": "Networking", "call_number": "TK5105.5 .K87", "total": 6, "shelf": "Stack 3, Shelf D"},
        {"title": "Artificial Intelligence: A Modern Approach", "author": "Stuart Russell, Peter Norvig", "isbn": "9780136042594", "category": "Artificial Intelligence", "call_number": "Q335 .R87", "total": 5, "shelf": "Stack 4, Shelf A"},
        {"title": "Principles of Corporate Finance", "author": "Richard A. Brealey, Stewart C. Myers", "isbn": "9781260013900", "category": "Business & Economics", "call_number": "HG4026 .B67", "total": 4, "shelf": "Stack 5, Shelf B"},
    ]

    created_count = 0
    for item in catalog:
        Book.objects.create(
            title=item["title"],
            author=item["author"],
            isbn=item["isbn"],
            category=item["category"],
            call_number=item["call_number"],
            total_copies=item["total"],
            available_copies=item["total"],
            shelf_location=item["shelf"],
            year_published=2022
        )
        created_count += 1

    return created_count


def search_books(query="", category=""):
    """Search library catalog by title, author, ISBN, or call number."""
    qs = Book.objects.all().order_by("title")
    if query:
        qs = qs.filter(
            Q(title__icontains=query) |
            Q(author__icontains=query) |
            Q(isbn__icontains=query) |
            Q(call_number__icontains=query)
        )
    if category:
        qs = qs.filter(category=category)
    return qs


@transaction.atomic
def issue_book(book_id, borrower_user, days=14, staff_user=None, request=None):
    """Issue a library book to a student or faculty member."""
    book = Book.objects.select_for_update().get(pk=book_id)

    if book.available_copies <= 0:
        return None, f"No copies available for '{book.title}'."

    # Max active loans check (e.g. 4 for students, 8 for staff)
    active_loans_count = BookLoan.objects.filter(borrower=borrower_user, status=BookLoan.Status.ACTIVE).count()
    max_allowed = 8 if getattr(borrower_user, "role", "") == "FACULTY" else 4
    if active_loans_count >= max_allowed:
        return None, f"Borrowing limit reached ({active_loans_count}/{max_allowed} active loans)."

    # Overdue loans check
    overdue_count = BookLoan.objects.filter(borrower=borrower_user, status=BookLoan.Status.OVERDUE).count()
    if overdue_count > 0:
        return None, "Borrower has overdue books that must be returned first."

    book.available_copies = F("available_copies") - 1
    book.save(update_fields=["available_copies"])
    book.refresh_from_db()

    due = timezone.now().date() + timedelta(days=days)
    loan = BookLoan.objects.create(
        book=book,
        borrower=borrower_user,
        issued_by=staff_user,
        issue_date=timezone.now().date(),
        due_date=due,
        status=BookLoan.Status.ACTIVE
    )

    log_activity(
        request=request,
        user=staff_user or borrower_user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.NOTICES,
        entity="BookLoan",
        entity_id=loan.id,
        description=f"Issued book '{book.title}' to {borrower_user.username} (Due: {due})"
    )

    return loan, f"Successfully issued '{book.title}'. Due on {due.strftime('%b %d, %Y')}."


@transaction.atomic
def return_book(loan_id, staff_user=None, request=None):
    """Process return of borrowed book and compute overdue fine if applicable."""
    loan = BookLoan.objects.select_for_update().get(pk=loan_id)
    if loan.status == BookLoan.Status.RETURNED:
        return False, "Book is already marked as returned."

    today = timezone.now().date()
    fine = Decimal("0.00")
    if today > loan.due_date:
        overdue_days = (today - loan.due_date).days
        fine = Decimal(str(overdue_days * 50))  # KES 50 per day overdue fine

    loan.return_date = today
    loan.status = BookLoan.Status.RETURNED
    loan.fine_accrued = fine
    loan.save(update_fields=["return_date", "status", "fine_accrued"])

    # Increment available copies
    book = loan.book
    book.available_copies = F("available_copies") + 1
    book.save(update_fields=["available_copies"])
    book.refresh_from_db()

    log_activity(
        request=request,
        user=staff_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.NOTICES,
        entity="BookLoan",
        entity_id=loan.id,
        description=f"Book returned: '{book.title}' by {loan.borrower.username} (Fine: KES {fine:,.2f})"
    )

    msg = f"Returned '{book.title}'."
    if fine > Decimal("0.00"):
        msg += f" Overdue fine assessed: KES {fine:,.2f}."
    return True, msg


def get_user_library_status(user):
    """Return user's active loans, overdue items, and total unpaid fines."""
    today = timezone.now().date()
    loans = BookLoan.objects.filter(borrower=user)
    active = loans.filter(status=BookLoan.Status.ACTIVE)
    
    # Auto-flag overdue loans
    for l in active:
        if today > l.due_date and l.status != BookLoan.Status.OVERDUE:
            l.status = BookLoan.Status.OVERDUE
            overdue_days = (today - l.due_date).days
            l.fine_accrued = Decimal(str(overdue_days * 50))
            l.save(update_fields=["status", "fine_accrued"])

    overdue = loans.filter(status=BookLoan.Status.OVERDUE)
    total_fines = sum(l.fine_accrued for l in loans.filter(fine_paid=False))

    return {
        "active_loans": active.select_related("book"),
        "overdue_loans": overdue.select_related("book"),
        "total_fines": total_fines,
        "is_cleared": overdue.count() == 0 and total_fines <= Decimal("0.00"),
    }
