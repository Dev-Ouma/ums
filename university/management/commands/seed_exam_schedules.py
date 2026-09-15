from datetime import date
from django.core.management.base import BaseCommand
from django.utils import timezone
from university.models import (
    Program, Course, Cohort, AcademicYear, AcademicTerm,
    ExamRoom, ExamSchedule, ExamScheduleItem, Exam, Department
)

class Command(BaseCommand):
    help = "Seed examination schedules and courses matching reference workflows"

    def handle(self, *args, **options):
        # 1. Ensure Department
        dept, _ = Department.objects.get_or_create(
            code="AGRI",
            defaults={"name": "Agricultural Sciences and Technology"}
        )
        cs_dept, _ = Department.objects.get_or_create(
            code="CS",
            defaults={"name": "Computer Science and Informatics"}
        )

        # 2. Ensure Cohort SEPT-2026
        cohort, _ = Cohort.objects.get_or_create(
            name="SEPT-2026",
            defaults={
                "start_date": date(2026, 9, 1),
                "end_date": date(2030, 8, 31),
                "description": "September 2026 Intake Cohort"
            }
        )

        # 3. Ensure Academic Year 2026-2027 / 2026/2027
        ay = AcademicYear.objects.filter(code="AY-2026-2027").first()
        if not ay:
            ay, _ = AcademicYear.objects.get_or_create(
                name="2026/2027",
                defaults={
                    "code": "AY-2026-2027",
                    "start_date": date(2026, 8, 1),
                    "end_date": date(2027, 7, 31),
                    "is_current": True,
                    "status": AcademicYear.Status.CURRENT
                }
            )

        # 4. Ensure Term
        term, _ = AcademicTerm.objects.get_or_create(
            academic_year=ay,
            name="Semester 1",
            defaults={
                "term_type": AcademicTerm.TermType.SEMESTER,
                "semester_number": 1,
                "start_date": date(2026, 8, 15),
                "end_date": date(2026, 12, 15),
                "exam_start_date": date(2026, 10, 1),
                "exam_end_date": date(2026, 10, 20),
                "status": AcademicYear.Status.CURRENT,
                "is_current": True
            }
        )

        # 5. Ensure Exam Rooms / Centers
        online_room, _ = ExamRoom.objects.get_or_create(
            name="ONLINE",
            defaults={"capacity": 1000, "location": "Virtual Campus", "active": True}
        )
        hall_a, _ = ExamRoom.objects.get_or_create(
            name="Main Hall",
            defaults={"capacity": 250, "location": "Academic Complex", "active": True}
        )

        # 6. Ensure Programs
        programs_data = [
            ("AG01", "Bachelor of Agritechnology and Food Systems", dept),
            ("ST01", "Bachelor of Data Science", cs_dept),
            ("ST02", "Bachelor of Science in Cybersecurity and Digital Forensics", cs_dept),
            ("ST03", "Bachelor of Science in Mathematics and Computing", cs_dept),
            ("ST04", "Bachelor of Science in Computer Science", cs_dept),
            ("ST05", "Bachelor of Science in Interactive Media Technologies", cs_dept),
            ("ST06", "Bachelor of Science in Nursing (Upgrading)", dept),
        ]
        created_programs = {}
        for code, name, d in programs_data:
            p, _ = Program.objects.get_or_create(
                code=code,
                defaults={"name": name, "department": d, "duration_years": 4}
            )
            created_programs[code] = p

        # 7. Ensure Courses for AG01 (Year 1, Semester 1)
        ag01_courses_data = [
            ("ATF 101", "Agriculture and Emerging Farming Systems", 1, "Elective", False, "Online", date(2026, 8, 10), "Mid-day"),
            ("ATF 103", "Sustainable Energy in Agriculture", 1, "Elective", False, "Online", date(2026, 8, 11), "Morning"),
            ("ATF 105", "Principles of Soil Sciences", 1, "Core", False, "Online", date(2026, 8, 12), "Morning"),
            ("ATF 109", "Principles of Crop Production", 1, "Elective", False, "Online", date(2026, 8, 13), "Morning"),
            ("CIT 107", "ICT and Digital Tools in Agriculture", 1, "Elective", False, "Online", date(2026, 8, 14), "Mid-day"),
        ]
        ag01_prog = created_programs["AG01"]
        for ccode, ctitle, sem_no, is_core, is_prac, mode, edate, sess in ag01_courses_data:
            Course.objects.get_or_create(
                code=ccode,
                defaults={
                    "title": ctitle,
                    "program": ag01_prog,
                    "department": dept,
                    "credits": 3,
                    "semester_no": sem_no,
                    "status": Course.STATUS_ACTIVE
                }
            )

        # Ensure Courses for ST04 (Year 1, Semester 1)
        st04_prog = created_programs["ST04"]
        st04_courses_data = [
            ("CSC 101", "Fundamentals of Computing", 1, "Core", False, "Physical", date(2026, 8, 10), "Morning"),
            ("CSC 103", "Structured Programming", 1, "Core", True, "Physical", date(2026, 8, 12), "Morning"),
            ("MAT 105", "Discrete Mathematics", 1, "Core", False, "Physical", date(2026, 8, 14), "Mid-day"),
        ]
        for ccode, ctitle, sem_no, is_core, is_prac, mode, edate, sess in st04_courses_data:
            Course.objects.get_or_create(
                code=ccode,
                defaults={
                    "title": ctitle,
                    "program": st04_prog,
                    "department": cs_dept,
                    "credits": 3,
                    "semester_no": sem_no,
                    "status": Course.STATUS_ACTIVE
                }
            )

        # 8. Create Sample Exam Schedules matching reference image
        schedules_to_create = [
            ("AG01", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("AG01", 1, 2, "AUGUST-2026-EXAM", "Active"),
            ("ST01", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST02", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST03", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST04", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST04", 1, 2, "AUGUST-2026-EXAM", "Active"),
            ("ST05", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST06", 1, 1, "AUGUST-2026-EXAM", "Active"),
            ("ST06", 1, 2, "AUGUST-2026-EXAM", "Active"),
        ]

        for pcode, yr, sem, exam_name, status in schedules_to_create:
            prog = created_programs[pcode]
            sched, created = ExamSchedule.objects.get_or_create(
                program=prog,
                cohort=cohort,
                academic_year=ay,
                study_year=yr,
                semester=sem,
                defaults={
                    "name": exam_name,
                    "term": term,
                    "exam_type": "Regular",
                    "status": status,
                }
            )
            # Add items for AG01 Sem 1
            if pcode == "AG01" and sem == 1:
                for ccode, ctitle, sem_no, is_core, is_prac, mode, edate, sess in ag01_courses_data:
                    c = Course.objects.get(code=ccode)
                    item, _ = ExamScheduleItem.objects.get_or_create(
                        schedule=sched,
                        course=c,
                        defaults={
                            "is_core": is_core,
                            "is_practical": is_prac,
                            "mode_of_exam": mode,
                            "room": online_room,
                            "center_name": "ONLINE",
                            "exam_date": edate,
                            "exam_session": sess,
                        }
                    )
                    # Sync to Exam model
                    exam, _ = Exam.objects.get_or_create(
                        course=c,
                        term=term,
                        name=f"{exam_name} - {ccode}",
                        defaults={
                            "date": edate,
                            "room": online_room,
                            "status": Exam.Status.SCHEDULED,
                            "kind": Exam.Kind.FINAL,
                            "weight": 70,
                        }
                    )
                    if not item.exam:
                        item.exam = exam
                        item.save(update_fields=["exam"])

            # Add items for ST04 Sem 1
            if pcode == "ST04" and sem == 1:
                for ccode, ctitle, sem_no, is_core, is_prac, mode, edate, sess in st04_courses_data:
                    c = Course.objects.get(code=ccode)
                    item, _ = ExamScheduleItem.objects.get_or_create(
                        schedule=sched,
                        course=c,
                        defaults={
                            "is_core": is_core,
                            "is_practical": is_prac,
                            "mode_of_exam": mode,
                            "room": hall_a,
                            "center_name": "Main Hall",
                            "exam_date": edate,
                            "exam_session": sess,
                        }
                    )
                    exam, _ = Exam.objects.get_or_create(
                        course=c,
                        term=term,
                        name=f"{exam_name} - {ccode}",
                        defaults={
                            "date": edate,
                            "room": hall_a,
                            "status": Exam.Status.SCHEDULED,
                            "kind": Exam.Kind.FINAL,
                            "weight": 70,
                        }
                    )
                    if not item.exam:
                        item.exam = exam
                        item.save(update_fields=["exam"])

        self.stdout.write(self.style.SUCCESS("Successfully seeded sample examination schedules!"))
