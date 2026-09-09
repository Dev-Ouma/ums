"""Student-facing and admin-facing views for the Student Requests module
(Deferment, Withdrawal, Sick Leave)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import StudentProfile
from .academics_views import _get_student
from .examination_services import is_admin
from .forms import StudentRequestForm
from .models import StudentRequest
from .student_requests_services import decide_request, mark_under_review, resume_studies, submit_request


@login_required
def student_requests(request):
    """Student's own request history + submission form."""
    sp = _get_student(request)
    pending = StudentRequest.objects.filter(student=sp, status=StudentRequest.Status.PENDING).exists()

    if request.method == "POST":
        if not sp.is_active_student:
            messages.error(request, f"You cannot submit a new request while your status is "
                                    f"'{sp.get_status_display()}'.")
            return redirect("university:student_requests")
        form = StudentRequestForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                submit_request(
                    sp, form.cleaned_data["request_type"], form.cleaned_data["reason"],
                    start_date=form.cleaned_data.get("start_date"), end_date=form.cleaned_data.get("end_date"),
                    supporting_document=form.cleaned_data.get("supporting_document"), http_request=request,
                )
                messages.success(request, "Your request has been submitted and is awaiting review.")
                return redirect("university:student_requests")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
    else:
        form = StudentRequestForm()

    history = StudentRequest.objects.filter(student=sp).order_by("-submitted_at")
    return render(request, "academics/student_requests.html", {
        "student": sp, "form": form, "history": history, "pending": pending,
        "can_resume": sp.status in (StudentProfile.Status.DEFERRED, StudentProfile.Status.ON_LEAVE),
    })


@login_required
@require_POST
def student_resume_studies(request):
    sp = _get_student(request)
    try:
        resume_studies(sp, http_request=request)
        messages.success(request, "Welcome back! Your account is now active. "
                                  "You may proceed to Semester Registration.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("university:student_requests")


@login_required
def admin_student_requests(request):
    if not is_admin(request.user):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "").strip()
    status_filter = request.GET.get("status", "").strip()

    qs = StudentRequest.objects.select_related("student__user", "reviewed_by")
    if query:
        qs = qs.filter(Q(student__roll_no__icontains=query) | Q(student__user__first_name__icontains=query) |
                       Q(student__user__last_name__icontains=query))
    if type_filter:
        qs = qs.filter(request_type=type_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    counts = {
        "all": StudentRequest.objects.count(),
        "pending": StudentRequest.objects.filter(status=StudentRequest.Status.PENDING).count(),
        "under_review": StudentRequest.objects.filter(status=StudentRequest.Status.UNDER_REVIEW).count(),
        "approved": StudentRequest.objects.filter(status=StudentRequest.Status.APPROVED).count(),
        "rejected": StudentRequest.objects.filter(status=StudentRequest.Status.REJECTED).count(),
    }
    return render(request, "academics/admin_student_requests_list.html", {
        "page": page, "q": query, "type_filter": type_filter, "status_filter": status_filter,
        "counts": counts, "types": StudentRequest.Type.choices, "statuses": StudentRequest.Status.choices,
    })


@login_required
def admin_student_request_detail(request, pk):
    if not is_admin(request.user):
        raise PermissionDenied
    req = get_object_or_404(StudentRequest.objects.select_related("student__user", "reviewed_by"), pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "review":
                mark_under_review(request.user, req, http_request=request)
                messages.success(request, "Request marked as Under Review.")
            elif action in ("approve", "reject"):
                decision = StudentRequest.Status.APPROVED if action == "approve" else StudentRequest.Status.REJECTED
                comments = request.POST.get("comments", "").strip()
                decide_request(request.user, req, decision, comments=comments, http_request=request)
                messages.success(request, f"Request {decision.lower()}.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("university:admin_student_request_detail", pk=req.pk)

    history = StudentRequest.objects.filter(student=req.student).exclude(pk=req.pk).order_by("-submitted_at")
    return render(request, "academics/admin_student_request_detail.html", {"req": req, "history": history})
