"""
Management command to seed or update demo role user accounts in UMS.

Creates accounts for all institutional roles:
- superadmin
- admin
- vc (Vice Chancellor)
- dvcaa (Deputy Vice Chancellor Academic Affairs)
- registrar (Academic Registrar)
- ictdirector (Director of ICT)
- dean (Dean of Faculty / School)
- hod (Head of Department)
- examofficer (Examinations Officer)
- lecturer / instructor / teacher
- student
- finance (Finance & Accounts Officer)
- admissions (Admissions Officer)
- auditor (Compliance Auditor)

All accounts are created with password: demo1234
"""
import os
from datetime import date
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import FacultyProfile, Role, StudentProfile
from university.models import Department, Program, StaffRole, StaffRoleAssignment
from university.permissions_services import seed_default_permissions_and_roles

User = get_user_model()
DEFAULT_PASSWORD = os.environ.get("DEMO_ACCOUNTS_PASSWORD", "demo1234")
EMAIL_DOMAIN = "example.com"


class Command(BaseCommand):
    help = "Seed or reset demo user accounts for all institutional roles with password demo1234."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Default password for demo accounts (default: demo1234)."
        )

    def handle(self, *args, **options):
        password = options["password"]
        self.stdout.write("Initializing system permissions and staff roles...")
        seed_default_permissions_and_roles()

        dept = Department.objects.first()
        if not dept:
            dept = Department.objects.create(name="Computer Science", code="CSE", color="#6C5CE7")
        program = Program.objects.first()
        if not program:
            program = Program.objects.create(name="B.Tech Computer Science", code="BT-CSE", department=dept, level="UG", duration_years=4)

        demo_accounts = [
            {
                "username": "superadmin",
                "first": "Super",
                "last": "Administrator",
                "role": Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "staff_role": "identity_admin",
                "designation": "Super Administrator",
                "description": "Full system administrator with unrestricted access across all modules",
            },
            {
                "username": "admin",
                "first": "System",
                "last": "Administrator",
                "role": Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "staff_role": "identity_admin",
                "designation": "System Administrator",
                "description": "System administrator managing university configuration and operations",
            },
            {
                "username": "vc",
                "first": "Prof. Victor",
                "last": "Chege",
                "role": Role.ADMIN,
                "is_staff": True,
                "is_superuser": False,
                "staff_role": "vc",
                "designation": "Vice Chancellor",
                "description": "Chief Executive & Academic Head of the University",
            },
            {
                "username": "dvcaa",
                "first": "Prof. David",
                "last": "Kariuki",
                "role": Role.FACULTY,
                "is_staff": True,
                "is_superuser": False,
                "staff_role": "dvcaa",
                "designation": "Deputy Vice Chancellor (Academic Affairs)",
                "description": "Executive oversight of academic faculties, Senate, and curricula",
            },
            {
                "username": "registrar",
                "first": "Dr. Rachel",
                "last": "Wanjiku",
                "role": Role.FACULTY,
                "is_staff": True,
                "is_superuser": False,
                "staff_role": "academic_registrar",
                "designation": "Academic Registrar",
                "description": "Academic registrations, graduations, and official student records",
            },
            {
                "username": "ictdirector",
                "first": "Ian",
                "last": "Mwangi",
                "role": Role.ADMIN,
                "is_staff": True,
                "is_superuser": False,
                "staff_role": "identity_admin",
                "designation": "Director of ICT & Digital Services",
                "description": "Head of university ICT infrastructure, user identity, and cybersecurity",
            },
            {
                "username": "dean",
                "first": "Prof. Daniel",
                "last": "Otieno",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "dean",
                "designation": "Dean, School of Computing",
                "description": "Dean approving exam marks, publishing results, and overseeing school faculty",
            },
            {
                "username": "hod",
                "first": "Dr. Hassan",
                "last": "Omar",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "hod",
                "department": dept,
                "designation": "Head of Department, Computer Science",
                "description": "HoD managing departmental teaching, mark reviews, and approvals",
            },
            {
                "username": "examofficer",
                "first": "Esther",
                "last": "Njoroge",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "exam_officer",
                "designation": "Examinations Officer",
                "description": "Central examination sessions coordinator and transcript officer",
            },
            {
                "username": "lecturer",
                "first": "Dr. Leonard",
                "last": "Mutua",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "lecturer",
                "department": dept,
                "designation": "Senior Lecturer",
                "description": "Course lecturer & instructor capturing marks and assessments",
            },
            {
                "username": "prof.rao",
                "first": "Aarav",
                "last": "Rao",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "lecturer",
                "department": dept,
                "designation": "Professor of Computing",
                "description": "Senior faculty & lecturer for Computer Science courses",
            },
            {
                "username": "student",
                "first": "Samuel",
                "last": "Kamau",
                "role": Role.STUDENT,
                "is_staff": False,
                "is_superuser": False,
                "roll_no": "DEMO-STU-001",
                "description": "Undergraduate student viewing portal, units, fees, and marks",
            },
            {
                "username": "stu.aarav",
                "first": "Aarav",
                "last": "Sharma",
                "role": Role.STUDENT,
                "is_staff": False,
                "is_superuser": False,
                "roll_no": "UMS20260001",
                "description": "Enrolled student account with historical grades and fee statements",
            },
            {
                "username": "finance",
                "first": "Faith",
                "last": "Nduta",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "finance_officer",
                "designation": "Chief Finance Officer",
                "description": "Finance officer managing fee invoices, payments, and clearances",
            },
            {
                "username": "admissions",
                "first": "Alice",
                "last": "Chebet",
                "role": Role.FACULTY,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "admissions_officer",
                "designation": "Admissions Officer",
                "description": "Admissions officer reviewing applications and registering new cohorts",
            },
            {
                "username": "auditor",
                "first": "Arthur",
                "last": "Maina",
                "role": Role.ADMIN,
                "is_staff": False,
                "is_superuser": False,
                "staff_role": "auditor",
                "designation": "System & Compliance Auditor",
                "description": "Internal compliance auditor inspecting audit logs and trail events",
            },
        ]

        created_count = 0
        updated_count = 0

        self.stdout.write(f"\nSeeding sample users with password '{password}':\n")
        header = f"{'Username':<14} | {'Role':<10} | {'Display Name':<24} | {'Staff Role / Title':<32}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for item in demo_accounts:
            uname = item["username"]
            user, created = User.objects.get_or_create(
                username=uname,
                defaults={
                    "first_name": item["first"],
                    "last_name": item["last"],
                    "email": f"{uname}@{EMAIL_DOMAIN}",
                    "role": item["role"],
                    "phone": "0000",
                    "is_staff": item.get("is_staff", False),
                    "is_superuser": item.get("is_superuser", False),
                }
            )
            user.set_password(password)
            user.role = item["role"]
            user.first_name = item["first"]
            user.last_name = item["last"]
            user.email = f"{uname}@{EMAIL_DOMAIN}"
            user.is_staff = item.get("is_staff", False)
            user.is_superuser = item.get("is_superuser", False)
            user.save()

            if created:
                created_count += 1
            else:
                updated_count += 1

            # Setup FacultyProfile if faculty or admin staff
            if item["role"] in (Role.FACULTY, Role.ADMIN) and not item.get("roll_no"):
                fp, _ = FacultyProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "employee_id": f"EMP-{uname.upper()}",
                        "department": item.get("department", dept),
                        "designation": item.get("designation", "Staff Member"),
                        "joining_date": date(2020, 1, 15),
                    }
                )
                if item.get("designation"):
                    fp.designation = item["designation"]
                if item.get("department"):
                    fp.department = item["department"]
                fp.save()

            # Setup StudentProfile if student
            if item["role"] == Role.STUDENT:
                sp, _ = StudentProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "roll_no": item.get("roll_no", f"ROLL-{uname.upper()}"),
                        "program": program,
                        "current_semester": 1,
                        "gender": "M",
                        "date_of_birth": date(2003, 5, 20),
                        "admission_date": date(2023, 9, 1),
                    }
                )
                if item.get("roll_no"):
                    sp.roll_no = item["roll_no"]
                sp.program = program
                sp.save()

            # Assign StaffRole if configured
            staff_role_code = item.get("staff_role")
            if staff_role_code:
                srole = StaffRole.objects.filter(code=staff_role_code).first()
                if srole:
                    sassign, _ = StaffRoleAssignment.objects.get_or_create(
                        user=user,
                        role=srole,
                        defaults={
                            "department": item.get("department", None),
                            "is_active": True,
                        }
                    )
                    sassign.is_active = True
                    sassign.department = item.get("department", None)
                    sassign.save()

            from university.identity_services import ensure_account
            from university.identity_models import AccountStatus, UserAccount
            try:
                acc = ensure_account(user)
                if acc:
                    acc.status = AccountStatus.ACTIVE
                    acc.failed_login_attempts = 0
                    acc.locked_until = None
                    acc.must_change_password = False
                    acc.save()
            except Exception:
                pass

            title_display = item.get("designation", item.get("staff_role", item["role"]))
            self.stdout.write(
                f"{uname:<14} | {user.role:<10} | {user.display_name:<24} | {title_display:<32}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Seeded {len(demo_accounts)} demo accounts ({created_count} created, {updated_count} updated)."
            f"\nAll passwords set to: {password}\n"
        ))
