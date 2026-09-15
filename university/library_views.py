from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from functools import wraps

from university.library_services import (
    get_user_library_status, issue_book, return_book,
    search_books, seed_default_books
)
from university.models import AcademicTerm, Book, BookLoan, Course, PastExamPaper
from university.permissions_services import has_user_permission

User = get_user_model()


def _permission_required(permission_code):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("accounts:login")
            if not has_user_permission(request.user, permission_code):
                messages.error(request, "You do not have permission to perform this library operation.")
                return redirect("university:dashboard")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def _admin_required(view_func):
    """Backward-compatible alias for library operations visibility."""
    return _permission_required("library.view")(view_func)


# ==============================================================================
# STUDENT LIBRARY & PAST PAPERS PORTAL
# ==============================================================================

@login_required
def student_library_portal(request):
    """Student library book search, active borrowings, and digital past papers."""
    if not Book.objects.exists():
        seed_default_books()

    active_tab = request.GET.get("tab", "catalog").strip()
    if active_tab not in {"catalog", "my_loans", "past_papers"}:
        active_tab = "catalog"
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("category", "").strip()

    books_qs = search_books(q, cat)
    paginator = Paginator(books_qs, 12)
    books_page = paginator.get_page(request.GET.get("page"))

    # User's active loans & fines
    user_status = get_user_library_status(request.user)

    # Past Exam Papers
    past_papers = PastExamPaper.objects.select_related("course", "term").all()
    pp_course = request.GET.get("pp_course", "").strip()
    if pp_course:
        past_papers = past_papers.filter(
            Q(course__code__icontains=pp_course) |
            Q(course__title__icontains=pp_course) |
            Q(title__icontains=pp_course)
        )

    categories = Book.objects.values_list("category", flat=True).distinct()

    context = {
        "active_tab": active_tab,
        "books_page": books_page,
        "user_status": user_status,
        "past_papers": past_papers[:25],
        "categories": sorted(list(filter(None, categories))),
        "q": q,
        "selected_category": cat,
        "pp_course": pp_course,
    }
    return render(request, "library/student_portal.html", context)


# ==============================================================================
# ADMIN LIBRARY DESK
# ==============================================================================

@login_required
@_admin_required
def admin_library_dashboard(request):
    """Librarian desk: circulation tracking, issue/return, and catalog management."""
    if not Book.objects.exists():
        seed_default_books()

    active_tab = request.GET.get("tab", "circulation").strip()
    if active_tab not in {"circulation", "catalog", "past_papers"}:
        active_tab = "circulation"
    
    # Metrics
    total_titles = Book.objects.count()
    active_loans = BookLoan.objects.filter(status=BookLoan.Status.ACTIVE).count()
    overdue_loans = BookLoan.objects.filter(status=BookLoan.Status.OVERDUE).count()

    # Loans list
    loans = BookLoan.objects.select_related("book", "borrower", "issued_by").all().order_by("-issue_date")
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if q:
        loans = loans.filter(
            Q(book__title__icontains=q) |
            Q(book__isbn__icontains=q) |
            Q(borrower__username__icontains=q)
        )
    if status_filter:
        loans = loans.filter(status=status_filter)

    paginator = Paginator(loans, 20)
    loans_page = paginator.get_page(request.GET.get("page"))

    books = Book.objects.all().order_by("title")
    past_papers = PastExamPaper.objects.select_related("course", "term").all().order_by("-uploaded_at")

    context = {
        "active_tab": active_tab,
        "total_titles": total_titles,
        "active_loans": active_loans,
        "overdue_loans": overdue_loans,
        "loans_page": loans_page,
        "books": books,
        "past_papers": past_papers,
        "courses": Course.objects.all(),
        "terms": AcademicTerm.objects.all(),
        "q": q,
        "selected_status": status_filter,
        "statuses": BookLoan.Status.choices,
    }
    return render(request, "library/admin_dashboard.html", context)


@login_required
@_permission_required("library.circulate")
@require_POST
def admin_library_issue(request):
    """Check out a book to a user."""
    book_id = request.POST.get("book_id")
    borrower_username = request.POST.get("borrower_username", "").strip()
    try:
        days = int(request.POST.get("days", 14))
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= 60:
        messages.error(request, "Loan period must be between 1 and 60 days.")
        return redirect("/manage/library/?tab=circulation")

    get_object_or_404(Book, pk=book_id)

    borrower = User.objects.filter(Q(username=borrower_username) | Q(email=borrower_username)).first()
    if not borrower:
        messages.error(request, f"User '{borrower_username}' was not found in the university directory.")
        return redirect("/manage/library/?tab=circulation")

    loan, msg = issue_book(book_id, borrower, days=days, staff_user=request.user, request=request)
    if loan:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("/manage/library/?tab=circulation")


@login_required
@_permission_required("library.circulate")
@require_POST
def admin_library_return(request, pk):
    """Process return of a loaned book."""
    get_object_or_404(BookLoan, pk=pk)
    success, msg = return_book(pk, staff_user=request.user, request=request)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("/manage/library/?tab=circulation")


@login_required
@_permission_required("library.manage_catalog")
@require_POST
def admin_library_book_create(request):
    """Add a new volume to the catalog."""
    if request.method == "POST":
        title = request.POST.get("title")
        author = request.POST.get("author")
        isbn = request.POST.get("isbn", "")
        category = request.POST.get("category", "General")
        call_no = request.POST.get("call_number", "")
        shelf = request.POST.get("shelf_location", "General Stacks")
        try:
            copies = int(request.POST.get("copies", 1))
        except (TypeError, ValueError):
            copies = 0
        if not title or not title.strip() or not author or not author.strip() or not 1 <= copies <= 10000:
            messages.error(request, "Title, author, and a copy count between 1 and 10,000 are required.")
            return redirect("/manage/library/?tab=catalog")

        Book.objects.create(
            title=title, author=author, isbn=isbn, category=category,
            call_number=call_no, shelf_location=shelf, total_copies=copies,
            available_copies=copies
        )
        messages.success(request, f"Added book '{title}' to catalog.")
    return redirect("/manage/library/?tab=catalog")


@login_required
@_permission_required("library.manage_catalog")
@require_POST
def admin_past_paper_create(request):
    """Upload or register a past examination paper."""
    if request.method == "POST":
        course_id = request.POST.get("course_id")
        term_id = request.POST.get("term_id")
        title = request.POST.get("title")
        etype = request.POST.get("exam_type", "MAIN")
        ay = request.POST.get("academic_year", "2024/2025")
        file_obj = request.FILES.get("file_attachment")

        course = get_object_or_404(Course, pk=course_id)
        term = get_object_or_404(AcademicTerm, pk=term_id)

        PastExamPaper.objects.create(
            course=course, term=term, title=title, exam_type=etype,
            academic_year=ay, file_attachment=file_obj, uploaded_by=request.user
        )
        messages.success(request, f"Registered past paper for '{course.code}'.")
    return redirect("/manage/library/?tab=past_papers")
