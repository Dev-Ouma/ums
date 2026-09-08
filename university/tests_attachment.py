from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import FacultyProfile, Role, StudentProfile
from university.attachment_services import (
    apply_for_attachment, approve_attachment_application,
    assign_academic_supervisor, generate_attachment_intro_letter_pdf,
    generate_attachment_logbook_pdf, record_logbook_entry,
    reject_attachment_application, review_logbook_entry,
    submit_attachment_assessment
)
from university.models import (
    AcademicTerm, AttachmentAssessment, AttachmentLogbookEntry,
    AttachmentPlacement, Department, Program
)

User = get_user_model()


class IndustrialAttachmentTestCase(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin_coord", email="admin@uni.edu", password="password123",
            role=Role.ADMIN, is_staff=True
        )
        self.faculty_user = User.objects.create_user(
            username="prof_supervisor", email="prof@uni.edu", password="password123",
            role=Role.FACULTY, first_name="Grace", last_name="Kamau"
        )
        self.student_user = User.objects.create_user(
            username="student_attache", email="student@uni.edu", password="password123",
            role=Role.STUDENT, first_name="Brian", last_name="Wanyonyi"
        )

        self.dept = Department.objects.create(name="Computing & Informatics", code="CI")
        self.prog = Program.objects.create(name="BSc Computer Science", code="BCS", department=self.dept, duration_years=4)

        self.faculty_profile = FacultyProfile.objects.create(
            user=self.faculty_user,
            employee_id="FAC-0042",
            department=self.dept,
            designation="Senior Lecturer",
            specialization="Systems & Networks"
        )
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user,
            roll_no="BCS/0099/2023",
            program=self.prog,
            current_semester=6
        )
        self.term = AcademicTerm.objects.create(
            name="2025/2026 Sem 2",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 5, 20),
            is_current=True
        )

        self.start_date = date(2026, 5, 25)
        self.end_date = date(2026, 8, 15) # ~12 weeks

        self.client = Client()

    def test_apply_for_attachment_validation(self):
        """Test duration constraints (minimum 8 weeks)."""
        # Invalid: end before start
        with self.assertRaises(ValidationError):
            apply_for_attachment(
                student_profile=self.student_profile,
                company_name="Safaricom PLC",
                company_branch_location="HQ",
                company_address="PO Box 123",
                company_supervisor_name="John Doe",
                company_supervisor_email="john@safaricom.co.ke",
                company_supervisor_phone="+254711000000",
                department_or_unit="DevOps",
                start_date=self.end_date,
                end_date=self.start_date,
            )

        # Invalid: under 8 weeks (e.g. 2 weeks)
        with self.assertRaises(ValidationError):
            apply_for_attachment(
                student_profile=self.student_profile,
                company_name="Safaricom PLC",
                company_branch_location="HQ",
                company_address="PO Box 123",
                company_supervisor_name="John Doe",
                company_supervisor_email="john@safaricom.co.ke",
                company_supervisor_phone="+254711000000",
                department_or_unit="DevOps",
                start_date=self.start_date,
                end_date=self.start_date + timedelta(days=14),
            )

    def test_apply_for_attachment_success(self):
        """Test successful attachment placement registration."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Safaricom PLC",
            company_branch_location="Nairobi HQ",
            company_address="P.O. Box 48100-00100 Nairobi",
            company_supervisor_name="Eng. Sarah Mwangi",
            company_supervisor_email="smwangi@safaricom.co.ke",
            company_supervisor_phone="+254712345678",
            department_or_unit="Cloud & Cybersecurity",
            start_date=self.start_date,
            end_date=self.end_date,
            term=self.term
        )
        self.assertIsNotNone(placement)
        self.assertEqual(placement.status, AttachmentPlacement.Status.SUBMITTED)
        self.assertIn("NIU/ATT/", placement.intro_letter_reference)
        self.assertEqual(placement.duration_weeks, 12)

    def test_admin_approval_and_rejection(self):
        """Test admin state transitions for attachment applications."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Kenya Airways",
            company_branch_location="Embakasi Pride Centre",
            company_address="PO Box 19002",
            company_supervisor_name="Capt. Mark",
            company_supervisor_email="mark@kq.com",
            company_supervisor_phone="+254722000000",
            department_or_unit="Flight Operations Systems",
            start_date=self.start_date,
            end_date=self.end_date
        )

        # Admin Approval
        approved = approve_attachment_application(placement.id, admin_user=self.admin_user)
        self.assertEqual(approved.status, AttachmentPlacement.Status.APPROVED)

        # Admin Rejection
        rejected = reject_attachment_application(placement.id, admin_user=self.admin_user, reason="Invalid company insurance letter")
        self.assertEqual(rejected.status, AttachmentPlacement.Status.REJECTED)
        self.assertEqual(rejected.remarks, "Invalid company insurance letter")

    def test_assign_academic_supervisor(self):
        """Test assigning faculty supervisor moves approved placement to IN_PROGRESS."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Equity Bank Group",
            company_branch_location="Upper Hill",
            company_address="PO Box 75104",
            company_supervisor_name="David M.",
            company_supervisor_email="david@equity.co.ke",
            company_supervisor_phone="+254700000000",
            department_or_unit="Fintech Lab",
            start_date=self.start_date,
            end_date=self.end_date
        )
        approve_attachment_application(placement.id, admin_user=self.admin_user)
        
        assigned = assign_academic_supervisor(
            placement.id,
            faculty_profile=self.faculty_profile,
            admin_user=self.admin_user
        )
        self.assertEqual(assigned.academic_supervisor, self.faculty_profile)
        self.assertEqual(assigned.status, AttachmentPlacement.Status.IN_PROGRESS)

    def test_record_logbook_entry_and_review(self):
        """Test student weekly logbook recording and supervisor review."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="KCB Bank",
            company_branch_location="Kencom House",
            company_address="PO Box 48400",
            company_supervisor_name="Lucy K.",
            company_supervisor_email="lucy@kcb.co.ke",
            company_supervisor_phone="+254733000000",
            department_or_unit="Core Banking",
            start_date=self.start_date,
            end_date=self.end_date
        )

        # Record Week 1 entry
        entry = record_logbook_entry(
            placement=placement,
            week_number=1,
            date_from=self.start_date,
            date_to=self.start_date + timedelta(days=5),
            activities_summary="Oriented with T24 Core Banking servers and LDAP active directory.",
            skills_acquired="Linux shell scripting, banking database schemas",
            challenges_encountered="Permissions setup took 2 days."
        )
        self.assertEqual(entry.week_number, 1)
        self.assertFalse(entry.faculty_supervisor_reviewed)

        # Faculty review
        reviewed = review_logbook_entry(entry.id, self.faculty_user, feedback="Excellent technical start. Keep security principles in mind.")
        self.assertTrue(reviewed.faculty_supervisor_reviewed)
        self.assertIn("Excellent technical start", reviewed.faculty_feedback)

    def test_attachment_assessment_rubric(self):
        """Test 5-part rubric assessment and grade computation."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Google Kenya",
            company_branch_location="Westlands",
            company_address="PO Box 66100",
            company_supervisor_name="Alex G.",
            company_supervisor_email="alex@google.com",
            company_supervisor_phone="+254799000000",
            department_or_unit="Cloud Engineering",
            start_date=self.start_date,
            end_date=self.end_date
        )
        assign_academic_supervisor(placement.id, self.faculty_profile, self.admin_user)

        # Assessment: Org(9/10) + Att(14/15) + Tech(32/35) + Log(18/20) + Pres(18/20) = 91/100 -> Grade A
        assessment = submit_attachment_assessment(
            placement_id=placement.id,
            assessor_faculty=self.faculty_profile,
            org_suitability=9.0,
            attendance_score=14.0,
            technical_score=32.0,
            logbook_score=18.0,
            presentation_score=18.0,
            assessor_comments="Outstanding candidate with deep cloud architecture abilities.",
            industry_comments="High work ethic, recommended for graduate hire."
        )
        self.assertEqual(assessment.total_score, Decimal("91.00"))
        self.assertEqual(assessment.grade, "A")

        placement.refresh_from_db()
        self.assertEqual(placement.status, AttachmentPlacement.Status.COMPLETED)
        self.assertEqual(placement.final_score, Decimal("91.00"))
        self.assertEqual(placement.final_grade, "A")

    def test_intro_letter_pdf_generation(self):
        """Test official ReportLab Introductory Letter PDF generation."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Microsoft ADC",
            company_branch_location="Dunhill Towers, Nairobi",
            company_address="PO Box 100",
            company_supervisor_name="Dr. Njoroge",
            company_supervisor_email="njoroge@microsoft.com",
            company_supervisor_phone="+254711222333",
            department_or_unit="AI Research",
            start_date=self.start_date,
            end_date=self.end_date
        )
        pdf_bytes = generate_attachment_intro_letter_pdf(placement)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_compiled_logbook_pdf_generation(self):
        """Test compiled Logbook & Assessment Report PDF generation."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="Huawei Kenya",
            company_branch_location="Lavington HQ",
            company_address="PO Box 200",
            company_supervisor_name="Eng. Chen",
            company_supervisor_email="chen@huawei.com",
            company_supervisor_phone="+254744555666",
            department_or_unit="5G Telecommunications",
            start_date=self.start_date,
            end_date=self.end_date
        )
        record_logbook_entry(
            placement=placement,
            week_number=1,
            date_from=self.start_date,
            date_to=self.start_date + timedelta(days=5),
            activities_summary="Configured fiber optic routers and base stations.",
            skills_acquired="Optical networking"
        )
        pdf_bytes = generate_attachment_logbook_pdf(placement)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_student_views_access(self):
        """Test student portal rendering and submission endpoints."""
        self.client.force_login(self.student_user)

        # GET Student Portal
        res = self.client.get(reverse("university:student_attachment_portal"))
        self.assertEqual(res.status_code, 200)

        # POST Placement Application
        post_data = {
            "company_name": "Oracle Kenya",
            "company_branch_location": "Kilimani",
            "company_address": "PO Box 300",
            "company_supervisor_name": "James Otieno",
            "company_supervisor_email": "james@oracle.com",
            "company_supervisor_phone": "+254722334455",
            "department_or_unit": "Cloud Systems",
            "start_date": self.start_date.strftime("%Y-%m-%d"),
            "end_date": self.end_date.strftime("%Y-%m-%d"),
        }
        res_post = self.client.post(reverse("university:student_attachment_apply"), post_data)
        self.assertEqual(res_post.status_code, 302)

        placement = AttachmentPlacement.objects.filter(student=self.student_profile).first()
        self.assertIsNotNone(placement)

        # POST Logbook entry
        log_data = {
            "week_number": "1",
            "date_from": self.start_date.strftime("%Y-%m-%d"),
            "date_to": (self.start_date + timedelta(days=5)).strftime("%Y-%m-%d"),
            "activities_summary": "Database backup scripts setup.",
            "skills_acquired": "PL/SQL and Bash scripting",
            "challenges_encountered": "None",
        }
        res_log = self.client.post(
            reverse("university:student_attachment_logbook_submit", kwargs={"pk": placement.id}),
            log_data
        )
        self.assertEqual(res_log.status_code, 302)
        self.assertEqual(placement.logbook_entries.count(), 1)

    def test_faculty_and_admin_views_access(self):
        """Test faculty supervisor dashboard and admin coordination desk."""
        placement = apply_for_attachment(
            student_profile=self.student_profile,
            company_name="IBM East Africa",
            company_branch_location="Nairobi",
            company_address="PO Box 400",
            company_supervisor_name="Dr. Omondi",
            company_supervisor_email="omondi@ibm.com",
            company_supervisor_phone="+254711999888",
            department_or_unit="Watson AI Lab",
            start_date=self.start_date,
            end_date=self.end_date
        )

        # Admin View
        self.client.force_login(self.admin_user)
        res_admin = self.client.get(reverse("university:admin_attachment_dashboard"))
        self.assertEqual(res_admin.status_code, 200)

        # Admin Approve
        res_approve = self.client.post(
            reverse("university:admin_attachment_action", kwargs={"pk": placement.id}),
            {"action": "approve"}
        )
        self.assertEqual(res_approve.status_code, 302)

        # Admin Assign Supervisor
        res_assign = self.client.post(
            reverse("university:admin_attachment_action", kwargs={"pk": placement.id}),
            {"action": "assign_supervisor", "faculty_id": self.faculty_profile.id}
        )
        self.assertEqual(res_assign.status_code, 302)

        # Faculty View
        self.client.force_login(self.faculty_user)
        res_faculty = self.client.get(reverse("university:faculty_attachment_dashboard"))
        self.assertEqual(res_faculty.status_code, 200)

        # Faculty Grade Assessment
        grade_data = {
            "org_suitability": "9.0",
            "attendance_score": "14.0",
            "technical_score": "33.0",
            "logbook_score": "18.0",
            "presentation_score": "19.0",
            "assessor_comments": "Exemplary practicum performance.",
            "industry_comments": "Hardworking and reliable.",
        }
        res_grade = self.client.post(
            reverse("university:faculty_attachment_grade", kwargs={"pk": placement.id}),
            grade_data
        )
        self.assertEqual(res_grade.status_code, 302)

        placement.refresh_from_db()
        self.assertEqual(placement.status, AttachmentPlacement.Status.COMPLETED)
        self.assertEqual(placement.final_grade, "A")
