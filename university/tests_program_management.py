import io
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User, StudentProfile, Role
from university.models import School, Department, Program, Course, AuditLog, RecycleBinItem
from university.program_io import (
    export_program_csv, export_program_excel, export_program_pdf,
    generate_program_import_template, parse_and_validate_program_import_file,
    commit_program_import,
)
from university.recycle_bin_services import restore_from_recycle_bin


class ProgramManagementTests(TestCase):
    def setUp(self):
        # 1. Setup Admin & Student Users
        self.admin_user = User.objects.create_user(
            username="prog.admin", email="admin@ums.ac.ke", password="password123",
            role=Role.ADMIN, is_staff=True, is_superuser=True
        )
        self.student_user = User.objects.create_user(
            username="prog.student", email="student@ums.ac.ke", password="password123",
            role=Role.STUDENT
        )

        # 2. Setup Academic Hierarchy
        self.school = School.objects.create(
            name="School of Computing & Engineering", code="SCE", dean_name="Prof. Alan Turing"
        )
        self.dept = Department.objects.create(
            name="Computer Science & Engineering", code="CSE", school=self.school
        )

        # 3. Setup Programmes (University Degree + TVET Diploma)
        self.prog_degree = Program.objects.create(
            code="BSc-CS",
            name="Bachelor of Science in Computer Science",
            department=self.dept,
            level="UG",
            program_type=Program.ProgramType.DEGREE,
            award_title="Bachelor of Science in Computer Science",
            study_mode=Program.StudyMode.FULL_TIME,
            duration_value=4,
            duration_unit=Program.DurationUnit.YEARS,
            min_credits=120,
            status=Program.Status.ACTIVE,
        )

        self.prog_tvet = Program.objects.create(
            code="DIP-IT",
            name="Diploma in Information Technology",
            department=self.dept,
            level="DIP",
            program_type=Program.ProgramType.DIPLOMA,
            award_title="Diploma in Information Technology",
            study_mode=Program.StudyMode.FULL_TIME,
            duration_value=2,
            duration_unit=Program.DurationUnit.YEARS,
            min_credits=64,
            status=Program.Status.ACTIVE,
        )

        # 4. Attach Course and Student to Programme
        self.course = Course.objects.create(
            code="CS101", title="Intro to Computing", department=self.dept,
            program=self.prog_degree, credits=4, semester_no=1, status="Active"
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user, roll_no="CS/2026/001", program=self.prog_degree, current_semester=1
        )

    def test_program_hierarchy_and_properties(self):
        """Test School -> Department -> Program relationship and custom properties."""
        self.assertEqual(self.prog_degree.school, self.school)
        self.assertEqual(self.prog_degree.faculty_name, "School of Computing & Engineering")
        self.assertEqual(self.prog_degree.enrolled_students_count, 1)
        self.assertEqual(self.prog_degree.active_courses_count, 1)
        self.assertEqual(self.prog_degree.duration_years, 4)
        self.assertEqual(self.prog_degree.total_semesters, 8)

        # TVET 2-year duration check
        self.assertEqual(self.prog_tvet.duration_years, 2)
        self.assertEqual(self.prog_tvet.total_semesters, 4)

    def test_admin_programs_list_and_permissions(self):
        """Test access controls, search, and filtering on the admin programmes directory."""
        client = Client()

        # Non-logged in redirects to login
        res_anon = client.get(reverse("university:admin_programs"))
        self.assertEqual(res_anon.status_code, 302)

        # Students are forbidden
        client.force_login(self.student_user)
        res_student = client.get(reverse("university:admin_programs"))
        self.assertEqual(res_student.status_code, 403)

        # Admin access
        client.force_login(self.admin_user)
        res_admin = client.get(reverse("university:admin_programs"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "BSc-CS")
        self.assertContains(res_admin, "DIP-IT")

        # Test Search
        res_search = client.get(reverse("university:admin_programs") + "?q=Diploma")
        self.assertContains(res_search, "DIP-IT")
        self.assertNotContains(res_search, "BSc-CS")

        # Test Filter by Level
        res_level = client.get(reverse("university:admin_programs") + "?level=UG")
        self.assertContains(res_level, "BSc-CS")
        self.assertNotContains(res_level, "DIP-IT")

        # Test alias route /manage/programmes/
        res_alias = client.get(reverse("university:admin_programmes"))
        self.assertEqual(res_alias.status_code, 200)

    def test_program_detail_view(self):
        """Test detailed programme page showing courses, students, and governance."""
        client = Client()
        client.force_login(self.admin_user)

        url = reverse("university:program_detail", args=[self.prog_degree.pk])
        res = client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "CS101")
        self.assertContains(res, "CS/2026/001")
        self.assertContains(res, "School of Computing &amp; Engineering")

    def test_program_create_and_edit(self):
        """Test program creation and update forms with audit log tracking."""
        client = Client()
        client.force_login(self.admin_user)

        # Create
        create_url = reverse("university:program_create")
        post_data = {
            "code": "CERT-CYBER",
            "name": "Certificate in Cybersecurity Fundamentals",
            "award_title": "Certificate in Cybersecurity",
            "department": self.dept.pk,
            "program_type": Program.ProgramType.CERTIFICATE,
            "level": "CERT",
            "study_mode": Program.StudyMode.FULL_TIME,
            "duration_value": 1,
            "duration_unit": Program.DurationUnit.YEARS,
            "min_credits": 30,
            "total_seats": 40,
            "status": Program.Status.ACTIVE,
            "description": "Foundational vocational cybersecurity course.",
        }
        res_create = client.post(create_url, post_data)
        self.assertEqual(res_create.status_code, 302)

        new_prog = Program.objects.get(code="CERT-CYBER")
        self.assertEqual(new_prog.name, "Certificate in Cybersecurity Fundamentals")

        # AuditLog verified
        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CREATE,
            module=AuditLog.Module.PROGRAMMES,
            new_state__code="CERT-CYBER"
        ).first()
        self.assertIsNotNone(audit)

        # Edit
        edit_url = reverse("university:program_edit", args=[new_prog.pk])
        post_data["name"] = "Advanced Certificate in Cybersecurity"
        res_edit = client.post(edit_url, post_data)
        self.assertEqual(res_edit.status_code, 302)

        new_prog.refresh_from_db()
        self.assertEqual(new_prog.name, "Advanced Certificate in Cybersecurity")

    def test_program_delete_moves_to_recycle_bin_and_restores(self):
        """Test safe deletion to Recycle Bin and subsequent restoration."""
        client = Client()
        client.force_login(self.admin_user)

        prog_to_delete = Program.objects.create(
            code="PHD-CS",
            name="Doctor of Philosophy in Computer Science",
            department=self.dept,
            level="PHD",
            program_type=Program.ProgramType.DOCTORATE,
            duration_value=3,
            status=Program.Status.ACTIVE,
        )

        del_url = reverse("university:program_delete", args=[prog_to_delete.pk])
        res = client.post(del_url)
        self.assertEqual(res.status_code, 302)

        # Program removed from active DB
        self.assertFalse(Program.objects.filter(code="PHD-CS").exists())

        # Recycle bin item created
        recycle_item = RecycleBinItem.objects.filter(
            module=RecycleBinItem.Module.PROGRAMMES,
            object_id=str(prog_to_delete.pk)
        ).first()
        self.assertIsNotNone(recycle_item)
        self.assertFalse(recycle_item.is_restored)

        # Restore from recycle bin
        restored = restore_from_recycle_bin(recycle_item.pk, user=self.admin_user)
        self.assertTrue(restored)
        self.assertTrue(Program.objects.filter(code="PHD-CS").exists())

    def test_program_exports(self):
        """Test CSV, Excel, and PDF exports for programmes."""
        qs = Program.objects.all()

        csv_bytes = export_program_csv(qs)
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        self.assertIn(b"BSc-CS", csv_bytes)

        excel_bytes = export_program_excel(qs)
        self.assertGreater(len(excel_bytes), 2000)

        pdf_bytes = export_program_pdf(qs)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_program_import_and_commit(self):
        """Test spreadsheet import validation, error handling, and commit."""
        # Generate valid CSV
        csv_content = (
            "code,name,department_code,level,program_type,award_title,study_mode,duration_value,duration_unit,min_credits,total_seats,status,description\n"
            "MSc-AI,Master of Science in Artificial Intelligence,CSE,MS,Masters,Master of Science in AI,Full-Time,2,Years,60,30,Active,AI master degree.\n"
            "BSc-CS,Duplicate Code Row,CSE,UG,Degree,Duplicate,Full-Time,4,Years,120,50,Active,Duplicate test.\n"
            "BAD-DEPT,Bad Department Row,INVALID_DEPT,UG,Degree,Bad Dept,Full-Time,4,Years,120,50,Active,Bad dept test.\n"
        )
        file = SimpleUploadedFile("programs.csv", csv_content.encode("utf-8"), content_type="text/csv")

        parsed = parse_and_validate_program_import_file(file)
        self.assertEqual(parsed["total_rows"], 3)
        self.assertEqual(parsed["valid_count"], 1)
        self.assertEqual(parsed["invalid_count"], 2)

        # Valid row is MSc-AI
        self.assertEqual(parsed["valid_rows"][0]["code"], "MSC-AI")

        # Invalid rows detected
        self.assertIn("already exists", parsed["invalid_rows"][0]["errors"][0])
        self.assertIn("does not exist", parsed["invalid_rows"][1]["errors"][0])

        # Commit valid rows
        imported = commit_program_import(parsed["valid_rows"], user=self.admin_user)
        self.assertEqual(imported, 1)
        self.assertTrue(Program.objects.filter(code="MSC-AI").exists())

    def test_api_academic_hierarchy(self):
        """Test JSON API for dependent dropdown cascading (School -> Department -> Program)."""
        client = Client()
        client.force_login(self.admin_user)

        url = reverse("university:api_academic_hierarchy")

        # Complete hierarchy
        res = client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("schools", data)
        self.assertIn("departments", data)
        self.assertIn("programs", data)

        # Cascading: filter departments by school
        res_school = client.get(f"{url}?school_id={self.school.pk}")
        data_school = res_school.json()
        self.assertIn("departments", data_school)
        self.assertEqual(data_school["departments"][0]["code"], "CSE")

        # Cascading: filter programs by department
        res_dept = client.get(f"{url}?department_id={self.dept.pk}")
        data_dept = res_dept.json()
        self.assertIn("programs", data_dept)
        self.assertTrue(any(p["code"] == "BSc-CS" for p in data_dept["programs"]))
