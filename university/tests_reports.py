import io
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Role, StudentProfile, FacultyProfile
from university.models import (
    Department,
    Program,
    AcademicTerm,
    Course,
    SemesterRegistration,
    Exam,
    Result,
    AuditLog,
)
from university.reporting_services import (
    REPORT_REGISTRY,
    REPORT_CATEGORIES,
    build_report_data,
    generate_report_pdf,
    generate_report_excel,
    generate_report_csv,
)


class UniversityReportsTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username="admin_reports",
            email="admin_reports@niu.ac.ke",
            password="adminpassword123",
            first_name="Admin",
            last_name="Officer"
        )

        # Faculty user
        self.faculty_user = User.objects.create_user(
            username="prof_john",
            email="john@niu.ac.ke",
            password="facultypassword123",
            first_name="John",
            last_name="Doe",
            role=Role.FACULTY
        )

        # Student user
        self.student_user = User.objects.create_user(
            username="stud_alice",
            email="alice@niu.ac.ke",
            password="studentpassword123",
            first_name="Alice",
            last_name="Smith",
            role=Role.STUDENT
        )

        # Department & Program
        self.dept = Department.objects.create(name="School of Computing", code="SCIT")
        self.faculty_profile = FacultyProfile.objects.create(
            user=self.faculty_user,
            employee_id="FAC-0099",
            department=self.dept,
            designation="Senior Lecturer"
        )

        self.prog = Program.objects.create(name="BSc Computer Science", code="BCS", department=self.dept)
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="BCS-2026-0001",
            program=self.prog,
            current_semester=2,
            gender="F",
            admission_date=date(2026, 1, 10)
        )

        # Academic Term
        self.term = AcademicTerm.objects.create(
            name="2025/2026 Term 1",
            start_date=date(2026, 1, 5),
            end_date=date(2026, 5, 20)
        )

        # Course
        self.course = Course.objects.create(
            code="CSC 301",
            title="Compiler Construction",
            department=self.dept,
            program=self.prog,
            faculty=self.faculty_profile,
            credits=3
        )

        # Semester Registration
        self.sem_reg = SemesterRegistration.objects.create(
            student=self.student_profile,
            term=self.term,
            semester_no=2,
            status="APPROVED"
        )

        # Exam & Result
        self.exam = Exam.objects.create(
            name="CSC 301 Main Examination",
            course=self.course,
            term=self.term,
            date=date(2026, 4, 15),
            max_marks=100,
            pass_mark=40,
            cat_max_marks=30,
            exam_max_marks=70,
            status="HELD"
        )

        self.result = Result.objects.create(
            exam=self.exam,
            student=self.student_profile,
            attendance="PRESENT",
            cat_marks=Decimal("24.00"),
            exam_marks=Decimal("56.00"),
            marks_obtained=Decimal("80.00")
        )

    # --------------------------------------------------------------------------
    # 1. Access Control Tests
    # --------------------------------------------------------------------------

    def test_admin_reports_dashboard_access(self):
        """Admin can access reports dashboard; unauthenticated is redirected."""
        # Unauthenticated
        res = self.client.get(reverse("university:admin_reports_dashboard"))
        self.assertEqual(res.status_code, 302)

        # Student cannot access admin reports
        self.client.force_login(self.student_user)
        res = self.client.get(reverse("university:admin_reports_dashboard"))
        self.assertEqual(res.status_code, 302)

        # Admin access
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("university:admin_reports_dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "reports/admin_reports_dashboard.html")
        self.assertIn("categorized_reports", res.context)
        self.assertEqual(res.context["total_reports_count"], len(REPORT_REGISTRY))

    # --------------------------------------------------------------------------
    # 2. Query Builder & Service Layer Tests
    # --------------------------------------------------------------------------

    def test_student_register_report(self):
        """Verify student register query results and KPIs."""
        data = build_report_data("student_register", {}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(data["title"], "Student Register & Cohort Directory")
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0][1], "BCS-2026-0001")
        self.assertEqual(data["rows"][0][2], "Alice Smith")

        # Test department filter
        data_filtered = build_report_data("student_register", {"department": self.dept.id}, user=self.admin_user)
        self.assertEqual(len(data_filtered["rows"]), 1)

    def test_student_demographics_report(self):
        """Verify student demographics aggregation."""
        data = build_report_data("student_demographics", {}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertTrue(len(data["rows"]) >= 1)
        # Check female column has 1
        found = False
        for r in data["rows"]:
            if r[0] == "School of Computing":
                self.assertEqual(r[2], "1")  # Female count
                found = True
        self.assertTrue(found)

    def test_senate_consolidated_sheet(self):
        """Verify Senate marksheet computing student totals, means, and recommendation."""
        data = build_report_data("senate_consolidated_sheet", {"term": self.term.id}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        # Columns: ["#", "Roll No", "Student Name", "Programme", "Units Taken", "Passed", "Failed", "Mean (%)", "Senate Recommendation"]
        self.assertEqual(row[1], "BCS-2026-0001")
        self.assertEqual(row[4], "1")  # Units taken
        self.assertEqual(row[5], "1")  # Passed
        self.assertEqual(row[6], "0")  # Failed
        self.assertIn("PASS", row[8])  # Senate Recommendation

    def test_academic_performance_report(self):
        """Verify mean score and pass rate calculations."""
        data = build_report_data("academic_performance", {"term": self.term.id}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row[0], "CSC 301")
        self.assertEqual(row[4], "80.0")  # Mean mark
        self.assertEqual(row[7], "100.0%")  # Pass rate

    def test_grade_distribution_report(self):
        """Verify Grade A bucket placement for 80% mark."""
        data = build_report_data("grade_distribution", {"course": self.course.id}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["rows"]), 5)
        # Grade A row should have count 1
        grade_a_row = data["rows"][0]
        self.assertEqual(grade_a_row[0], "Grade A")
        self.assertEqual(grade_a_row[2], "1")

    def test_cat_vs_exam_analysis(self):
        """Verify CAT (24/30) and Exam (56/70) variance analysis."""
        data = build_report_data("cat_vs_exam_analysis", {"course": self.course.id}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row[0], "BCS-2026-0001")
        self.assertEqual(row[3], "24.00")
        self.assertEqual(row[4], "56.00")
        self.assertEqual(row[5], "80.00")

    def test_faculty_workload_report(self):
        """Verify lecturer contact hour audit."""
        data = build_report_data("faculty_workload", {"department": self.dept.id}, user=self.admin_user)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row[0], "FAC-0099")
        self.assertEqual(row[4], "1")  # Assigned courses
        self.assertEqual(row[6], "3 hrs")  # Contact hours

    # --------------------------------------------------------------------------
    # 3. Export Engines Tests (PDF, Excel, CSV)
    # --------------------------------------------------------------------------

    def test_generate_report_pdf(self):
        """Verify ReportLab PDF generation starts with standard PDF header."""
        data = build_report_data("student_register", {}, user=self.admin_user)
        pdf_bytes = generate_report_pdf(data)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertTrue(len(pdf_bytes) > 1000)

    def test_generate_report_excel(self):
        """Verify OpenPyXL Excel spreadsheet generation."""
        data = build_report_data("senate_consolidated_sheet", {}, user=self.admin_user)
        excel_bytes = generate_report_excel(data)
        self.assertTrue(excel_bytes.startswith(b"PK"))  # Zip header for .xlsx
        self.assertTrue(len(excel_bytes) > 1000)

    def test_generate_report_csv(self):
        """Verify CSV generation with UTF-8 BOM."""
        data = build_report_data("grade_distribution", {}, user=self.admin_user)
        csv_bytes = generate_report_csv(data)
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        self.assertIn(b"Grade A", csv_bytes)

    # --------------------------------------------------------------------------
    # 4. View & Export Endpoints Tests
    # --------------------------------------------------------------------------

    def test_report_web_view(self):
        """Verify /manage/reports/<report_key>/ renders properly with filters."""
        self.client.force_login(self.admin_user)
        url = reverse("university:report_view", args=["student_register"])
        res = self.client.get(url, {"department": self.dept.id})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "reports/report_view.html")
        self.assertIn("report_data", res.context)
        self.assertEqual(res.context["total_rows_count"], 1)

    def test_export_report_endpoint(self):
        """Verify /manage/reports/<report_key>/export/<fmt>/ triggers binary download and logs audit."""
        self.client.force_login(self.admin_user)

        # PDF export
        pdf_url = reverse("university:export_report", args=["senate_consolidated_sheet", "pdf"])
        res = self.client.get(pdf_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF-"))

        # Excel export
        excel_url = reverse("university:export_report", args=["academic_performance", "excel"])
        res = self.client.get(excel_url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("spreadsheetml", res["Content-Type"])

        # CSV export
        csv_url = reverse("university:export_report", args=["student_demographics", "csv"])
        res = self.client.get(csv_url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res["Content-Type"])

        # Audit log verified
        export_logs = AuditLog.objects.filter(action=AuditLog.Action.EXPORT)
        self.assertTrue(export_logs.exists())

    def test_faculty_reports_view(self):
        """Verify /teach/reports/ displays assigned courses for faculty."""
        self.client.force_login(self.faculty_user)
        url = reverse("university:faculty_reports")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "reports/faculty_reports.html")
        self.assertEqual(res.context["total_courses"], 1)

    def test_student_reports_view(self):
        """Verify /me/reports/ displays academic records hub for student."""
        self.client.force_login(self.student_user)
        url = reverse("university:student_reports")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "reports/student_reports.html")
        self.assertEqual(res.context["passed_count"], 1)

    def test_admin_reports_dashboard_advanced_filters(self):
        """Verify search and multi-criteria filters on admin reports dashboard."""
        self.client.force_login(self.admin_user)
        url = reverse("university:admin_reports_dashboard")

        # 1. Test search query
        res_search = self.client.get(url, {"q": "senate"})
        self.assertEqual(res_search.status_code, 200)
        filtered = res_search.context["filtered_reports"]
        self.assertTrue(any("senate" in r["title"].lower() or "senate" in r["audience"].lower() for r in filtered))

        # 2. Test category filter
        res_cat = self.client.get(url, {"category": "student"})
        self.assertEqual(res_cat.status_code, 200)
        filtered_cat = res_cat.context["filtered_reports"]
        self.assertTrue(all(r["category"] == "student" for r in filtered_cat))
        self.assertGreater(len(filtered_cat), 0)

        # 3. Test audience filter
        res_aud = self.client.get(url, {"audience": "Registrar"})
        self.assertEqual(res_aud.status_code, 200)
        filtered_aud = res_aud.context["filtered_reports"]
        self.assertTrue(all("registrar" in r["audience"].lower() for r in filtered_aud))

        # 4. Test sorting
        res_sort = self.client.get(url, {"sort": "name_asc"})
        self.assertEqual(res_sort.status_code, 200)
        titles = [r["title"] for r in res_sort.context["filtered_reports"]]
        self.assertEqual(titles, sorted(titles))

    def test_report_view_server_side_pagination(self):
        """Verify server-side pagination across reports with page sizing."""
        self.client.force_login(self.admin_user)
        url = reverse("university:report_view", args=["student_register"])

        # Page size 15
        res_p15 = self.client.get(url, {"per_page": 15, "page": 1})
        self.assertEqual(res_p15.status_code, 200)
        self.assertEqual(res_p15.context["per_page"], 15)
        self.assertIn("page_obj", res_p15.context)
        self.assertIn("total_rows_count", res_p15.context)

        # Page size 50
        res_p50 = self.client.get(url, {"per_page": 50, "page": 1})
        self.assertEqual(res_p50.status_code, 200)
        self.assertEqual(res_p50.context["per_page"], 50)


