from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role, StudentProfile
from university.hostel_services import (
    allocate_hostel_room, apply_hostel_room, checkin_hostel_student,
    checkout_hostel_student, get_available_rooms, seed_default_hostels
)
from university.models import (
    AcademicTerm, HostelAllocation, HostelBlock, HostelRoom
)


def _admin_required(view_func):
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        user_role = getattr(request.user, "role", "")
        if not (request.user.is_staff or request.user.is_superuser or user_role in (Role.ADMIN, "ADMIN")):
            messages.error(request, "Access restricted. Administrator privileges required.")
            return redirect("university:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ==============================================================================
# STUDENT HOSTEL PORTAL
# ==============================================================================

@login_required
def student_hostel_portal(request):
    """Student campus accommodation booking and active allocation hub."""
    try:
        sp = request.user.student_profile
    except Exception:
        messages.error(request, "Only enrolled students can apply for campus accommodation.")
        return redirect("university:dashboard")

    # Seed hostels if none exist
    if not HostelBlock.objects.exists():
        seed_default_hostels()

    current_term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()

    # Active allocation for current term
    active_alloc = HostelAllocation.objects.filter(
        student=sp,
        term=current_term
    ).select_related("room__block", "term").first()

    roommates = []
    if active_alloc and active_alloc.status in [HostelAllocation.Status.ALLOCATED, HostelAllocation.Status.CHECKED_IN]:
        roommates = HostelAllocation.objects.filter(
            room=active_alloc.room,
            term=current_term,
            status__in=[HostelAllocation.Status.ALLOCATED, HostelAllocation.Status.CHECKED_IN]
        ).exclude(student=sp).select_related("student__user")

    # Available rooms
    available_rooms = get_available_rooms()

    context = {
        "student": sp,
        "current_term": current_term,
        "active_alloc": active_alloc,
        "roommates": roommates,
        "available_rooms": available_rooms,
        "blocks": HostelBlock.objects.all().prefetch_related("rooms"),
    }
    return render(request, "hostels/student_portal.html", context)


@login_required
@require_POST
def student_hostel_apply(request):
    """Student applies for a specific room."""
    try:
        sp = request.user.student_profile
    except Exception:
        return redirect("university:dashboard")

    room_id = request.POST.get("room_id")
    notes = request.POST.get("notes", "").strip()
    room = get_object_or_404(HostelRoom, pk=room_id)
    term = AcademicTerm.objects.filter(is_current=True).first() or AcademicTerm.objects.first()

    alloc, msg = apply_hostel_room(sp, room, term, notes=notes, request=request)
    if alloc:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect("university:student_hostel_portal")


# ==============================================================================
# ADMIN HOSTEL MANAGEMENT
# ==============================================================================

@login_required
@_admin_required
def admin_hostels_dashboard(request):
    """Admin accommodation and residence management desk."""
    if not HostelBlock.objects.exists():
        seed_default_hostels()

    blocks = HostelBlock.objects.all().prefetch_related("rooms")
    allocations = HostelAllocation.objects.select_related(
        "student__user", "student__program", "room__block", "term"
    ).all().order_by("-applied_at")

    # Metrics
    total_rooms = HostelRoom.objects.count()
    total_capacity = HostelRoom.objects.aggregate(Sum("capacity"))["capacity__sum"] or 0
    total_occupied = HostelRoom.objects.aggregate(Sum("occupied_beds"))["occupied_beds__sum"] or 0
    free_beds = max(0, total_capacity - total_occupied)

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    block_filter = request.GET.get("block", "").strip()

    if q:
        allocations = allocations.filter(
            Q(student__roll_no__icontains=q) |
            Q(student__user__first_name__icontains=q) |
            Q(student__user__last_name__icontains=q) |
            Q(room__room_number__icontains=q)
        )
    if status_filter:
        allocations = allocations.filter(status=status_filter)
    if block_filter:
        allocations = allocations.filter(room__block_id=block_filter)

    paginator = Paginator(allocations, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "blocks": blocks,
        "page_obj": page_obj,
        "total_rooms": total_rooms,
        "total_capacity": total_capacity,
        "total_occupied": total_occupied,
        "free_beds": free_beds,
        "q": q,
        "selected_status": status_filter,
        "selected_block": block_filter,
        "statuses": HostelAllocation.Status.choices,
    }
    return render(request, "hostels/admin_dashboard.html", context)


@login_required
@_admin_required
@require_POST
def admin_hostel_allocate(request, pk):
    """Approve hostel room allocation."""
    success, msg = allocate_hostel_room(pk, admin_user=request.user, request=request)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("university:admin_hostels_dashboard")


@login_required
@_admin_required
@require_POST
def admin_hostel_checkin(request, pk):
    """Mark student arrived and record room key."""
    key = request.POST.get("key_number", "").strip()
    success, msg = checkin_hostel_student(pk, key_number=key, admin_user=request.user, request=request)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("university:admin_hostels_dashboard")


@login_required
@_admin_required
@require_POST
def admin_hostel_checkout(request, pk):
    """Release student bed space."""
    success, msg = checkout_hostel_student(pk, admin_user=request.user, request=request)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("university:admin_hostels_dashboard")
