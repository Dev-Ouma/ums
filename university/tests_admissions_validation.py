from datetime import date, timedelta
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
    COUNTRIES_LIST,
    KENYAN_COUNTIES,
    get_available_intakes_data,
    get_default_active_intake,
    get_or_create_applicant_draft,
    normalize_and_validate_email,
    normalize_and_validate_phone,
    validate_name_field,
    validate_date_of_birth,
)

User = get_user_model()


class AdmissionsValidationAndIntakesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = date.today()

        self.academic_year = AcademicYear.objects.create(
            name="2026/2027",
            code="AY-2026-2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
            is_current=True,
        )
        self.school = School.objects.create(name="School of Science", code="SS")
        self.department = Department.objects.create(name="Computer Science", code="CS", school=self.school)
        self.program = Program.objects.create(
            name="BSc in Software Engineering",
            code="BSE",
            department=self.department,
            duration_years=4,
            status=Program.Status.ACTIVE,
        )

        # Active Intake (dates cover today or current cycle)
        self.active_intake = Intake.objects.create(
            name="September 2026 Intake",
            academic_year=self.academic_year,
            start_date=self.today - timedelta(days=10),
            end_date=self.today + timedelta(days=60),
            is_active=True,
        )

        # Upcoming Intake
        self.upcoming_intake = Intake.objects.create(
            name="January 2027 Intake",
            academic_year=self.academic_year,
            start_date=self.today + timedelta(days=100),
            end_date=self.today + timedelta(days=160),
            is_active=True,
        )

        # Inactive / Closed Intake
        self.closed_intake = Intake.objects.create(
            name="May 2026 Closed Intake",
            academic_year=self.academic_year,
            start_date=self.today - timedelta(days=120),
            end_date=self.today - timedelta(days=30),
            is_active=False,
        )

        self.user = User.objects.create_user(
            username="test_applicant",
            email="test.applicant@example.com",
            first_name="Kelvin",
            last_name="Ochieng",
            phone="0712345678",
            password="SecurePassword123!",
            role=Role.APPLICANT,
        )

        self.save_draft_url = reverse("university:admissions_api_save_draft")
        self.get_draft_url = reverse("university:admissions_api_get_draft")
        self.intakes_api_url = reverse("university:admissions_api_intakes_available")
        self.locations_api_url = reverse("university:admissions_api_locations")
        self.submit_url = reverse("university:admissions_api_submit")

    # 1. Intake Engine Tests
    def test_default_active_intake_resolution(self):
        """The backend must determine the most active intake based on date ranges and active status."""
        default_itk = get_default_active_intake()
        self.assertIsNotNone(default_itk)
        self.assertEqual(default_itk.id, self.active_intake.id)

    def test_available_intakes_api_badges_and_default(self):
        """GET /api/admissions/intakes/available returns badge statuses and flags the default."""
        resp = self.client.get(self.intakes_api_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("has_active_intake"))
        self.assertEqual(data.get("default_intake_id"), self.active_intake.id)

        intakes = data.get("intakes", [])
        self.assertGreaterEqual(len(intakes), 2)

        active_entry = next((i for i in intakes if i["id"] == self.active_intake.id), None)
        self.assertIsNotNone(active_entry)
        self.assertEqual(active_entry["status_label"], "ACTIVE")
        self.assertTrue(active_entry["is_default"])

        upcoming_entry = next((i for i in intakes if i["id"] == self.upcoming_intake.id), None)
        self.assertIsNotNone(upcoming_entry)
        self.assertEqual(upcoming_entry["status_label"], "Upcoming")
        self.assertFalse(upcoming_entry["is_default"])

    def test_saving_inactive_intake_is_rejected(self):
        """Draft save with an inactive intake must be rejected with 422."""
        self.client.force_login(self.user)
        payload = {
            "step": 1,
            "version": 1,
            "data": {
                "intake_id": self.closed_intake.id,
            }
        }
        resp = self.client.post(self.save_draft_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 422)
        res_data = resp.json()
        self.assertIn("intake_id", res_data.get("field_errors", {}))

    # 2. Location Engine Tests
    def test_locations_api_endpoint(self):
        """GET /api/admissions/locations/ returns default country Kenya and default county Nairobi."""
        resp = self.client.get(self.locations_api_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("default_country"), "Kenya")
        self.assertEqual(data.get("default_county"), "Nairobi")
        self.assertIn("Nairobi", data.get("counties", []))
        self.assertIn("Mombasa", data.get("counties", []))
        self.assertEqual(len(data.get("counties", [])), 47)
        self.assertIn("Kenya", data.get("countries", []))
        self.assertIn("Uganda", data.get("countries", []))

    def test_country_county_validation_kenya(self):
        """When Country is Kenya, County must be one of the 47 Kenyan counties."""
        self.client.force_login(self.user)
        # Invalid county for Kenya
        bad_payload = {
            "version": 1,
            "data": {
                "country": "Kenya",
                "county": "NonExistentCountyXYZ",
            }
        }
        resp = self.client.post(self.save_draft_url, data=json.dumps(bad_payload), content_type="application/json")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("county", resp.json().get("field_errors", {}))

        # Valid county for Kenya
        good_payload = {
            "version": 1,
            "data": {
                "country": "Kenya",
                "county": "Kisumu",
            }
        }
        resp2 = self.client.post(self.save_draft_url, data=json.dumps(good_payload), content_type="application/json")
        self.assertEqual(resp2.status_code, 200)

    def test_country_county_international(self):
        """When Country is not Kenya, non-Kenyan region/state is accepted."""
        self.client.force_login(self.user)
        payload = {
            "version": 1,
            "data": {
                "country": "Uganda",
                "county": "Kampala Central",
            }
        }
        resp = self.client.post(self.save_draft_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        draft = Application.objects.get(applicant_user=self.user, status=Application.Status.DRAFT)
        self.assertEqual(draft.country, "Uganda")
        self.assertEqual(draft.county, "Kampala Central")

    # 3. Email Validation & Normalization
    def test_email_validation_and_normalization(self):
        """Test RFC email validation rejects malformed emails and normalizes valid ones."""
        # Helper unit test
        valid, norm, err = normalize_and_validate_email("  Kelvin.Ochieng@Gmail.COM ")
        self.assertTrue(valid)
        self.assertEqual(norm, "kelvin.ochieng@gmail.com")

        for bad in ["john", "john@", "john@gmail", "john@.com", "john..doe@gmail.com", "john@gmail..com"]:
            v, n, e = normalize_and_validate_email(bad)
            self.assertFalse(v, f"Should reject {bad}")
            self.assertIsNone(n)

        # API integration test
        self.client.force_login(self.user)
        for invalid_email in ["john", "john@", "john@gmail", "john..doe@gmail.com"]:
            resp = self.client.post(
                self.save_draft_url,
                data=json.dumps({"version": 1, "data": {"email": invalid_email}}),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 422, f"Failed for {invalid_email}")
            self.assertIn("email", resp.json().get("field_errors", {}))

    # 4. Mobile Phone Validation & E.164 Normalization
    def test_phone_validation_and_e164_normalization(self):
        """Test phone numbers normalize to +254XXXXXXXXX and reject garbage text."""
        # Helper unit test
        for raw, expected in [
            ("0712345678", "+254712345678"),
            ("0112345678", "+254112345678"),
            ("+254 712 345 678", "+254712345678"),
            ("254712345678", "+254712345678"),
            ("+1 415 555 2671", "+14155552671"),
        ]:
            v, norm, e = normalize_and_validate_phone(raw)
            self.assertTrue(v, f"Failed for {raw}")
            self.assertEqual(norm, expected)

        # Invalid phone checks
        for garbage in ["07hbhbh09784", "abc123456", "07123abc45", "07!!!!!!!!!", "hello"]:
            v, norm, e = normalize_and_validate_phone(garbage)
            self.assertFalse(v, f"Should reject {garbage}")
            self.assertIsNone(norm)

        # API integration test
        self.client.force_login(self.user)
        resp_bad = self.client.post(
            self.save_draft_url,
            data=json.dumps({"version": 1, "data": {"phone": "07hbhbh09784"}}),
            content_type="application/json",
        )
        self.assertEqual(resp_bad.status_code, 422)
        self.assertIn("phone", resp_bad.json().get("field_errors", {}))

        # Valid phone auto-normalized in draft
        resp_good = self.client.post(
            self.save_draft_url,
            data=json.dumps({"version": 1, "data": {"phone": "0722334455"}}),
            content_type="application/json",
        )
        self.assertEqual(resp_good.status_code, 200)
        draft = Application.objects.get(applicant_user=self.user, status=Application.Status.DRAFT)
        self.assertEqual(draft.phone, "+254722334455")

    # 5. Name Validation
    def test_name_validation(self):
        """Name fields must accept letters, hyphens, and apostrophes; reject numbers and symbols."""
        v1, n1, _ = validate_name_field("kelvin")
        self.assertTrue(v1)
        self.assertEqual(n1, "Kelvin")

        v2, n2, _ = validate_name_field("mary-jane")
        self.assertTrue(v2)
        self.assertEqual(n2, "Mary-Jane")

        v3, n3, _ = validate_name_field("o'connor")
        self.assertTrue(v3)
        self.assertEqual(n3, "O'Connor")

        for bad in ["Kelvin123", "K", "!@#$%"]:
            v, n, _ = validate_name_field(bad)
            self.assertFalse(v, f"Should reject {bad}")

    # 6. Date of Birth & Minimum Age Constraint
    def test_dob_validation(self):
        """Applicant must be >= 16 years old and not in the future."""
        today = date.today()
        # Future date
        v_fut, _, _ = validate_date_of_birth(today + timedelta(days=1))
        self.assertFalse(v_fut)
        # 10 years old (too young)
        v_young, _, _ = validate_date_of_birth(date(today.year - 10, today.month, today.day))
        self.assertFalse(v_young)
        # 18 years old (valid)
        v_ok, parsed, _ = validate_date_of_birth(date(today.year - 18, today.month, today.day))
        self.assertTrue(v_ok)
        self.assertEqual(parsed, date(today.year - 18, today.month, today.day))

    # 7. Safe Hydration & Refresh Persistence
    def test_draft_hydration_preserves_all_fields(self):
        """GET draft must return normalized values, intakes data, and location data."""
        self.client.force_login(self.user)
        payload = {
            "version": 1,
            "data": {
                "first_name": "Kelvin",
                "middle_name": "Kiprono",
                "last_name": "Ochieng",
                "email": "kelvin.ochieng@example.com",
                "phone": "0712345678",
                "country": "Kenya",
                "county": "Nairobi",
                "nationality": "Kenyan",
                "intake_id": self.active_intake.id,
                "program": str(self.program.id),
            }
        }
        save_resp = self.client.post(self.save_draft_url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(save_resp.status_code, 200)

        # Reload / Hydrate
        get_resp = self.client.get(self.get_draft_url)
        self.assertEqual(get_resp.status_code, 200)
        fields = get_resp.json()["draft"]["fields"]
        self.assertEqual(fields["first_name"], "Kelvin")
        self.assertEqual(fields["middle_name"], "Kiprono")
        self.assertEqual(fields["last_name"], "Ochieng")
        self.assertEqual(fields["phone"], "+254712345678")
        self.assertEqual(fields["country"], "Kenya")
        self.assertEqual(fields["county"], "Nairobi")
        self.assertEqual(fields["intake_id"], self.active_intake.id)
