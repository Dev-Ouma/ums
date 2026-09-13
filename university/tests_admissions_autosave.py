from datetime import date
from decimal import Decimal
import io
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role
from university.models import (
    AcademicYear,
    Application,
    ApplicationAttachment,
    Department,
    Intake,
    Program,
    School,
)
from university.admissions_draft_services import (
    calculate_completion_percentage,
    get_or_create_applicant_draft,
    serialize_draft_state,
    update_applicant_draft,
    validate_and_submit_application,
)

User = get_user_model()


class AdmissionsAutoSaveTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.academic_year = AcademicYear.objects.create(
            name="2025/2026",
            code="AY-2025-2026",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 8, 31),
            is_current=True,
        )
        self.school = School.objects.create(name="School of Computing", code="SC")
        self.department = Department.objects.create(name="Computer Science", code="CS", school=self.school)
        self.program = Program.objects.create(
            name="Bachelor of Science in Software Engineering",
            code="BSE",
            department=self.department,
            duration_years=4,
            status=Program.Status.ACTIVE,
        )
        self.intake = Intake.objects.create(
            name="September 2026 Main Intake",
            academic_year=self.academic_year,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 15),
            is_active=True,
        )

        self.applicant_user = User.objects.create_user(
            username="applicant_test",
            email="applicant.test@example.com",
            first_name="Kelvin",
            last_name="Ochieng",
            phone="0712345678",
            password="TestPassword123!",
            role=Role.APPLICANT,
        )

        self.other_applicant = User.objects.create_user(
            username="other_applicant",
            email="other.applicant@example.com",
            first_name="Jane",
            last_name="Wanjiku",
            phone="0722334455",
            password="TestPassword123!",
            role=Role.APPLICANT,
        )

        self.apply_url = reverse("university:admissions_apply")
        self.get_draft_url = reverse("university:admissions_api_get_draft")
        self.save_draft_url = reverse("university:admissions_api_save_draft")
        self.submit_url = reverse("university:admissions_api_submit")
        self.upload_doc_url = reverse("university:admissions_upload_document")
        self.remove_doc_url = reverse("university:admissions_remove_document")

    def test_get_or_create_draft_idempotency_for_authenticated_applicant(self):
        """Authenticated applicant should get a single, idempotent active draft Application."""
        self.client.force_login(self.applicant_user)
        resp1 = self.client.get(self.get_draft_url)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertTrue(data1.get("success"))
        draft_num1 = data1["draft"]["application_number"]

        # Call again
        resp2 = self.client.get(self.get_draft_url)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        draft_num2 = data2["draft"]["application_number"]

        self.assertEqual(draft_num1, draft_num2)
        self.assertEqual(
            Application.objects.filter(applicant_user=self.applicant_user, status=Application.Status.DRAFT).count(),
            1,
        )

    def test_anonymous_session_draft_creation_and_recovery(self):
        """Anonymous user gets a session-bound draft and recovers it on subsequent requests."""
        resp1 = self.client.get(self.get_draft_url)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        app_num1 = data1["draft"]["application_number"]

        # Next request in same session
        resp2 = self.client.get(self.get_draft_url)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        app_num2 = data2["draft"]["application_number"]

        self.assertEqual(app_num1, app_num2)

    def test_patch_draft_partial_field_updates_without_strict_validation(self):
        """Saving partial draft fields should succeed without requiring all mandatory submission fields."""
        self.client.force_login(self.applicant_user)
        
        # Partial update 1: Just guardian details
        payload1 = {
            "step": 3,
            "version": 1,
            "data": {
                "guardian_name": "Mary Jane Wabs",
                "guardian_relationship": "Parent",
                "guardian_phone": "+254712999888",
            }
        }
        resp1 = self.client.post(
            self.save_draft_url,
            data=json.dumps(payload1),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200)
        res_data1 = resp1.json()
        self.assertTrue(res_data1.get("success"))
        self.assertEqual(res_data1.get("version"), 2)
        self.assertEqual(res_data1.get("step"), 3)

        # Verify in DB
        draft = Application.objects.get(applicant_user=self.applicant_user, status=Application.Status.DRAFT)
        self.assertEqual(draft.guardian_name, "Mary Jane Wabs")
        self.assertEqual(draft.guardian_phone, "+254712999888")
        self.assertEqual(draft.draft_step, 3)
        self.assertEqual(draft.draft_version, 2)

        # Partial update 2: Programme selection
        payload2 = {
            "step": 1,
            "version": 2,
            "data": {
                "program": str(self.program.id),
            }
        }
        resp2 = self.client.post(
            self.save_draft_url,
            data=json.dumps(payload2),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.program, self.program)
        self.assertEqual(draft.draft_version, 3)

    def test_optimistic_locking_concurrency_conflict(self):
        """Out-of-order or stale client writes must trigger a 409 conflict and return current server state."""
        self.client.force_login(self.applicant_user)
        draft = get_or_create_applicant_draft(self.client.request().wsgi_request)
        draft.applicant_user = self.applicant_user
        draft.draft_version = 5
        draft.save()

        # Stale request with version=2 (client fell behind)
        stale_payload = {
            "version": 2,
            "data": {
                "first_name": "StaleName",
            }
        }
        resp = self.client.post(
            self.save_draft_url,
            data=json.dumps(stale_payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        res_json = resp.json()
        self.assertTrue(res_json.get("conflict"))
        self.assertEqual(res_json.get("server_version"), 5)

    def test_tenant_isolation_unauthorized_access(self):
        """User A must not be able to mutate or inspect User B's draft."""
        # Create draft for applicant A
        self.client.force_login(self.applicant_user)
        resp_a = self.client.get(self.get_draft_url)
        app_num_a = resp_a.json()["draft"]["application_number"]
        self.client.logout()

        # Login as applicant B
        self.client.force_login(self.other_applicant)
        resp_b = self.client.get(self.get_draft_url)
        app_num_b = resp_b.json()["draft"]["application_number"]

        # Ensure different drafts
        self.assertNotEqual(app_num_a, app_num_b)

        # Mutating draft for applicant B affects applicant B's draft only
        self.client.post(
            self.save_draft_url,
            data=json.dumps({"data": {"guardian_name": "Applicant B Guardian"}}),
            content_type="application/json",
        )
        draft_a = Application.objects.get(application_number=app_num_a)
        draft_b = Application.objects.get(application_number=app_num_b)
        self.assertEqual(draft_b.guardian_name, "Applicant B Guardian")
        self.assertNotEqual(draft_a.guardian_name, "Applicant B Guardian")

    def test_document_upload_persists_to_draft_and_survives_refresh(self):
        """Document uploads must attach directly to draft Application and survive page reloads."""
        self.client.force_login(self.applicant_user)

        pdf_file = SimpleUploadedFile("kcse_results.pdf", b"%PDF-1.4 dummy valid pdf content", content_type="application/pdf")
        upload_resp = self.client.post(
            self.upload_doc_url,
            {
                "document_type": "kcse_document",
                "file": pdf_file,
            }
        )
        self.assertEqual(upload_resp.status_code, 200)
        upload_data = upload_resp.json()
        self.assertTrue(upload_data.get("success"))

        # Verify DB ApplicationAttachment is linked to applicant's draft
        draft = Application.objects.get(applicant_user=self.applicant_user, status=Application.Status.DRAFT)
        self.assertTrue(draft.attachments.filter(document_type=ApplicationAttachment.DocType.KCSE_CERTIFICATE).exists())

        # Hydrate via GET draft API
        get_resp = self.client.get(self.get_draft_url)
        self.assertEqual(get_resp.status_code, 200)
        docs = get_resp.json()["draft"]["documents"]
        self.assertIn("kcse_document", docs)
        self.assertTrue(docs["kcse_document"]["uploaded"])

    def test_final_submission_strict_validation(self):
        """Final submission must reject incomplete applications and approve complete ones."""
        self.client.force_login(self.applicant_user)

        # 1. Incomplete submission (missing required fields and docs)
        sub_resp1 = self.client.post(
            self.submit_url,
            data=json.dumps({"data": {"first_name": "Kelvin"}}),
            content_type="application/json",
        )
        self.assertEqual(sub_resp1.status_code, 400)
        res1 = sub_resp1.json()
        self.assertFalse(res1.get("success", False))
        self.assertTrue(len(res1.get("errors", [])) > 0)

        # 2. Complete all required fields
        draft = get_or_create_applicant_draft(self.client.request().wsgi_request)
        draft.applicant_user = self.applicant_user
        draft.program = self.program
        draft.first_name = "Kelvin"
        draft.last_name = "Ochieng"
        draft.email = "kelvin.ochieng@example.com"
        draft.phone = "+254712345678"
        draft.date_of_birth = date(2003, 5, 12)
        draft.gender = "MALE"
        draft.national_id = "34567890"
        draft.guardian_name = "Mary Jane Wabs"
        draft.guardian_relationship = "Parent"
        draft.guardian_phone = "+254712999888"
        draft.secondary_school = "Alliance High School"
        draft.kcse_index_number = "12345678001"
        draft.kcse_mean_grade = "A-"
        draft.kcse_year = 2025
        draft.save()

        # Add 3 required attachments
        for dt, title in [
            (ApplicationAttachment.DocType.KCSE_CERTIFICATE, "KCSE Slip"),
            (ApplicationAttachment.DocType.NATIONAL_ID, "National ID"),
            (ApplicationAttachment.DocType.PASSPORT_PHOTO, "Passport Photo"),
        ]:
            ApplicationAttachment.objects.create(
                application=draft,
                document_type=dt,
                name=title,
                file=SimpleUploadedFile(f"{dt}.pdf", b"%PDF-1.4 dummy", content_type="application/pdf"),
                file_name=f"{dt}.pdf",
                file_size=1024,
                mime_type="application/pdf",
            )

        # 3. Final submit complete draft
        sub_resp2 = self.client.post(self.submit_url, data={}, content_type="application/json")
        self.assertEqual(sub_resp2.status_code, 200)
        res2 = sub_resp2.json()
        self.assertTrue(res2.get("success"))
        self.assertEqual(res2.get("status"), Application.Status.READY_FOR_PAYMENT)
        self.assertIn("pay-fee", res2.get("redirect_url"))

        # Verify DB status transitioned
        draft.refresh_from_db()
        self.assertEqual(draft.status, Application.Status.READY_FOR_PAYMENT)
