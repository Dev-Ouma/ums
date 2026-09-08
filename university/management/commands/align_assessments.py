"""Bring existing exam data into the CAT 30% + final 70% structure.

The examination workflow needs rooms to schedule into, and the course total
only resolves when a course/term carries exactly one CAT worth 30% and one
final worth 70%. Legacy rows predate both. This command is idempotent: run it
as many times as you like.
"""
from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from university.models import Exam, ExamAudit, ExamRoom, Result

ROOMS = [
    ("Main Hall", 240, "Administration Block, Ground Floor"),
    ("Science Auditorium", 160, "Science Block, First Floor"),
    ("Engineering Hall B", 120, "Engineering Block B"),
    ("Lecture Theatre 3", 80, "Arts Block, Second Floor"),
    ("Seminar Room 12", 40, "Library Building"),
]


class Command(BaseCommand):
    help = "Create examination rooms and reshape each course/term into CAT 30% + final 70%."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without writing anything.")

    @transaction.atomic
    def handle(self, *args, **options):
        dry = options["dry_run"]
        made_rooms = []
        for name, capacity, location in ROOMS:
            room, created = ExamRoom.objects.get_or_create(
                name=name, defaults={"capacity": capacity, "location": location})
            if created:
                made_rooms.append(room)
        rooms = list(ExamRoom.objects.filter(active=True).order_by("pk"))
        self.stdout.write(f"Rooms: {len(rooms)} active ({len(made_rooms)} created)")

        groups = {}
        for exam in Exam.objects.select_related("course").order_by("date", "pk"):
            groups.setdefault((exam.course_id, exam.term_id), []).append(exam)

        reshaped = skipped = seats = 0
        for index, (key, exams) in enumerate(sorted(groups.items(), key=lambda kv: kv[0])):
            if len(exams) != 2:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"  skip {exams[0].course.code}: {len(exams)} sitting(s), expected 2"))
                continue
            cat, final = exams  # ordered by date: the earlier sitting is the CAT
            room = rooms[index % len(rooms)] if rooms else None
            for exam, kind, name, weight, start in [
                    (cat, Exam.Kind.CAT, "Continuous Assessment Test", 30, time(9, 0)),
                    (final, Exam.Kind.FINAL, "Final Examination", 70, time(14, 0))]:
                changes = []
                if exam.kind != kind or exam.weight != weight:
                    changes.append(f"{exam.kind}@{exam.weight:.0f}% → {kind}@{weight}%")
                exam.kind, exam.weight = kind, weight
                if exam.name in ("Mid Term", "Final Term"):
                    exam.name = name
                if not exam.start_time:
                    exam.start_time, exam.end_time = start, time(start.hour + 2, 0)
                if not exam.room_id and room:
                    exam.room = room
                if not exam.invigilator_id and exam.course.faculty_id:
                    exam.invigilator = exam.course.faculty
                if exam.results.exists() and exam.status == Exam.Status.DRAFT:
                    exam.status = Exam.Status.PUBLISHED
                    exam.published_at = timezone.now()
                if not dry:
                    exam.revision += 1
                    exam.save()
                    # Legacy results carry no attendance or seating.
                    pending = exam.results.filter(attendance="PENDING")
                    if pending.exists():
                        for seat, result in enumerate(
                                exam.results.order_by("student__roll_no"), 1):
                            result.seat_number = result.seat_number or seat
                            if result.attendance == "PENDING":
                                result.attendance = "ABSENT" if result.marks_obtained is None else "PRESENT"
                            result.save(update_fields=["seat_number", "attendance"])
                            seats += 1
                    if changes:
                        ExamAudit.objects.create(
                            exam=exam, action="Reweighted",
                            detail="Aligned to the CAT 30% / final 70% structure: " + "; ".join(changes))
                reshaped += 1

        self.stdout.write(f"Sittings aligned: {reshaped}; groups skipped: {skipped}; results seated: {seats}")
        if dry:
            self.stdout.write(self.style.WARNING("Dry run — rolling back."))
            transaction.set_rollback(True)
        else:
            self.stdout.write(self.style.SUCCESS("Done."))
