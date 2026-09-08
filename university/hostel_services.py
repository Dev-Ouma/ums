from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import StudentProfile
from university.audit_services import log_activity
from university.models import (
    AcademicTerm, AuditLog, FeeInvoice, FeeStructure,
    HostelAllocation, HostelBlock, HostelRoom
)


def seed_default_hostels():
    """Populate sample hostel blocks and rooms if database is empty."""
    if HostelBlock.objects.exists():
        return 0

    blocks_data = [
        {"name": "Hall 1 — Mount Kenya", "code": "H1-MTK", "campus": "Main Campus", "gender": HostelBlock.Gender.MALE, "warden": "Dr. Joseph Omondi", "phone": "+254 711 000 101"},
        {"name": "Hall 2 — Kilimanjaro", "code": "H2-KILI", "campus": "Main Campus", "gender": HostelBlock.Gender.FEMALE, "warden": "Dr. Grace Muthoni", "phone": "+254 711 000 102"},
        {"name": "Hall 3 — Aberdare Towers", "code": "H3-ABER", "campus": "Main Campus", "gender": HostelBlock.Gender.MIXED, "warden": "Prof. David Mutua", "phone": "+254 711 000 103"},
    ]

    created_rooms = 0
    for b_info in blocks_data:
        b = HostelBlock.objects.create(
            name=b_info["name"],
            code=b_info["code"],
            campus=b_info["campus"],
            gender=b_info["gender"],
            warden_name=b_info["warden"],
            warden_phone=b_info["phone"],
            description=f"Premier residential hall at {b_info['campus']}."
        )

        # Create 6 rooms per block across 2 floors
        for fl in [1, 2]:
            for r_num in [1, 2, 3]:
                r_code = f"{fl}0{r_num}"
                rtype = HostelRoom.RoomType.DOUBLE if r_num != 1 else HostelRoom.RoomType.SINGLE
                cap = 2 if rtype == HostelRoom.RoomType.DOUBLE else 1
                fee = Decimal("9500.00") if cap == 1 else Decimal("6500.00")
                HostelRoom.objects.create(
                    block=b,
                    room_number=r_code,
                    floor=fl,
                    room_type=rtype,
                    capacity=cap,
                    occupied_beds=0,
                    fee_per_semester=fee,
                    is_active=True
                )
                created_rooms += 1

    return created_rooms


def get_available_rooms(gender=None, campus=None):
    """Retrieve rooms that have at least one unoccupied bed."""
    qs = HostelRoom.objects.filter(is_active=True).select_related("block")
    if gender:
        qs = qs.filter(block__gender__in=[gender, HostelBlock.Gender.MIXED])
    if campus:
        qs = qs.filter(block__campus=campus)
    # Only rooms where capacity > occupied_beds
    return [r for r in qs if r.available_beds > 0]


@transaction.atomic
def apply_hostel_room(student, room, term, notes="", request=None):
    """Student submits an application for a specific hostel room."""
    # Check if student already has an active allocation this semester
    existing = HostelAllocation.objects.filter(
        student=student,
        term=term,
        status__in=[HostelAllocation.Status.APPLIED, HostelAllocation.Status.ALLOCATED, HostelAllocation.Status.CHECKED_IN]
    ).first()
    if existing:
        return None, f"Student already has an active room allocation or pending application ({existing.room})."

    if room.available_beds <= 0:
        return None, f"Room {room} is fully occupied."

    alloc = HostelAllocation.objects.create(
        student=student,
        room=room,
        term=term,
        status=HostelAllocation.Status.APPLIED,
        notes=notes
    )

    log_activity(
        request=request,
        user=student.user,
        action=AuditLog.Action.CREATE,
        module=AuditLog.Module.NOTICES,
        entity="HostelAllocation",
        entity_id=alloc.id,
        description=f"Hostel application submitted by {student.roll_no} for {room}",
        new_state={"room": str(room), "status": alloc.status}
    )

    return alloc, "Room reservation application submitted successfully."


@transaction.atomic
def allocate_hostel_room(allocation_id, admin_user=None, request=None):
    """Warden or housing admin approves room assignment and auto-bills semester hostel fee."""
    alloc = HostelAllocation.objects.select_for_update().get(pk=allocation_id)
    room = alloc.room

    if room.available_beds <= 0 and alloc.status != HostelAllocation.Status.ALLOCATED:
        return False, "Cannot allocate: Room has reached maximum bed capacity."

    if alloc.status != HostelAllocation.Status.ALLOCATED:
        room.occupied_beds = F("occupied_beds") + 1
        room.save(update_fields=["occupied_beds"])
        room.refresh_from_db()

    alloc.status = HostelAllocation.Status.ALLOCATED
    alloc.allocated_at = timezone.now()
    alloc.save(update_fields=["status", "allocated_at"])

    # Auto-generate or update hostel billing invoice
    FeeInvoice.objects.get_or_create(
        student=alloc.student,
        term=alloc.term,
        title=f"Hostel Fee: {room.block.code} - Room {room.room_number}",
        defaults={
            "amount": room.fee_per_semester,
            "amount_paid": Decimal("0.00"),
            "issued_on": timezone.now().date(),
            "due_date": timezone.now().date() + timezone.timedelta(days=30),
        }
    )

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.NOTICES,
        entity="HostelAllocation",
        entity_id=alloc.id,
        description=f"Approved hostel room allocation {room} for {alloc.student.roll_no}",
        new_state={"status": alloc.status, "room": str(room)}
    )

    return True, f"Room {room.room_number} successfully allocated to {alloc.student.user.display_name}."


@transaction.atomic
def checkin_hostel_student(allocation_id, key_number="", admin_user=None, request=None):
    """Record student physical arrival and room key handover."""
    alloc = HostelAllocation.objects.select_for_update().get(pk=allocation_id)
    alloc.status = HostelAllocation.Status.CHECKED_IN
    alloc.check_in_date = timezone.now().date()
    alloc.room_key_number = key_number or f"KEY-{alloc.room.room_number}"
    alloc.save(update_fields=["status", "check_in_date", "room_key_number"])

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.NOTICES,
        entity="HostelAllocation",
        entity_id=alloc.id,
        description=f"Checked-in {alloc.student.roll_no} to {alloc.room} with key {alloc.room_key_number}",
    )
    return True, f"Checked-in {alloc.student.roll_no}."


@transaction.atomic
def checkout_hostel_student(allocation_id, admin_user=None, request=None):
    """Release student bed space upon departure or semester conclusion."""
    alloc = HostelAllocation.objects.select_for_update().get(pk=allocation_id)
    room = alloc.room

    if alloc.status in [HostelAllocation.Status.ALLOCATED, HostelAllocation.Status.CHECKED_IN]:
        if room.occupied_beds > 0:
            room.occupied_beds = F("occupied_beds") - 1
            room.save(update_fields=["occupied_beds"])
            room.refresh_from_db()

    alloc.status = HostelAllocation.Status.CHECKED_OUT
    alloc.check_out_date = timezone.now().date()
    alloc.save(update_fields=["status", "check_out_date"])

    log_activity(
        request=request,
        user=admin_user,
        action=AuditLog.Action.UPDATE,
        module=AuditLog.Module.NOTICES,
        entity="HostelAllocation",
        entity_id=alloc.id,
        description=f"Checked-out {alloc.student.roll_no} from {alloc.room}. Bed space released.",
    )
    return True, f"Checked-out {alloc.student.roll_no} and released bed space."
