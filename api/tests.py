"""Backend tests for FHIR transforms, API endpoints, and upsert idempotency."""

from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.fhir.transforms import transform_patient, transform_observation
from api.models import Patient, Observation


# --- Sample FHIR resources (from the HAPI sandbox structure) ---

SAMPLE_FHIR_PATIENT = {
    "resourceType": "Patient",
    "id": "sindhu-syn-000004",
    "identifier": [
        {
            "system": "https://sindhu-ecrf.local/synthetic-patient-id",
            "value": "SYN-000004",
        }
    ],
    "active": True,
    "name": [
        {
            "text": "Synthetic Patient SYN-000004",
            "family": "SYN-000004",
            "given": ["Synthetic"],
        }
    ],
    "gender": "male",
    "birthDate": "1952-01-01",
}

SAMPLE_FHIR_OBSERVATION = {
    "resourceType": "Observation",
    "id": "sindhu-syn-000004-enc-00-alt",
    "status": "final",
    "category": [
        {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}
    ],
    "code": {
        "coding": [
            {"system": "http://loinc.org", "code": "1742-6", "display": "Alanine Aminotransferase"}
        ],
        "text": "Alanine Aminotransferase",
    },
    "subject": {"reference": "Patient/sindhu-syn-000004"},
    "effectiveDateTime": "2024-01-01",
    "valueQuantity": {"value": 3.0, "unit": "U/L", "system": "http://unitsofmeasure.org", "code": "U/L"},
}

SAMPLE_FHIR_OBSERVATION_NO_VALUE = {
    "resourceType": "Observation",
    "id": "obs-no-value",
    "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "12345", "display": "Some Panel"}]},
    "subject": {"reference": "Patient/sindhu-syn-000004"},
}


class TransformPatientTest(TestCase):
    """Test the FHIR Patient → internal model transform."""

    def test_full_patient_transform(self):
        result = transform_patient(SAMPLE_FHIR_PATIENT)
        self.assertEqual(result["fhir_id"], "sindhu-syn-000004")
        self.assertEqual(result["identifier_system"], "https://sindhu-ecrf.local/synthetic-patient-id")
        self.assertEqual(result["identifier_value"], "SYN-000004")
        self.assertTrue(result["active"])
        self.assertEqual(result["family_name"], "SYN-000004")
        self.assertEqual(result["given_name"], "Synthetic")
        self.assertEqual(result["full_name"], "Synthetic Patient SYN-000004")
        self.assertEqual(result["gender"], "male")
        self.assertEqual(result["birth_date"], date(1952, 1, 1))

    def test_patient_missing_fields(self):
        minimal = {"resourceType": "Patient", "id": "minimal-001"}
        result = transform_patient(minimal)
        self.assertEqual(result["fhir_id"], "minimal-001")
        self.assertIsNone(result["family_name"])
        self.assertIsNone(result["gender"])
        self.assertIsNone(result["birth_date"])
        self.assertTrue(result["active"])  # defaults to True

    def test_patient_constructed_name(self):
        """If text is missing, full_name should be constructed from given + family."""
        patient = {
            "id": "p1",
            "name": [{"family": "Smith", "given": ["John"]}],
        }
        result = transform_patient(patient)
        self.assertEqual(result["full_name"], "John Smith")


class TransformObservationTest(TestCase):
    """Test the FHIR Observation → internal model transform."""

    def test_full_observation_transform(self):
        result = transform_observation(SAMPLE_FHIR_OBSERVATION)
        self.assertEqual(result["fhir_id"], "sindhu-syn-000004-enc-00-alt")
        self.assertEqual(result["patient_id"], "sindhu-syn-000004")  # "Patient/" stripped
        self.assertEqual(result["status"], "final")
        self.assertEqual(result["category"], "laboratory")
        self.assertEqual(result["code_system"], "http://loinc.org")
        self.assertEqual(result["code"], "1742-6")
        self.assertEqual(result["code_display"], "Alanine Aminotransferase")
        self.assertEqual(result["code_text"], "Alanine Aminotransferase")
        self.assertEqual(result["effective_date"], datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result["value"], 3.0)
        self.assertEqual(result["value_unit"], "U/L")

    def test_observation_without_value_quantity(self):
        """Observations without valueQuantity should have nullable value fields."""
        result = transform_observation(SAMPLE_FHIR_OBSERVATION_NO_VALUE)
        self.assertEqual(result["fhir_id"], "obs-no-value")
        self.assertIsNone(result["value"])
        self.assertIsNone(result["value_unit"])

    def test_observation_subject_reference_parsing(self):
        """Verify Patient/ prefix is stripped from subject.reference."""
        obs = {"id": "o1", "subject": {"reference": "Patient/abc-123"}}
        result = transform_observation(obs)
        self.assertEqual(result["patient_id"], "abc-123")


class UpsertIdempotencyTest(TestCase):
    """Test that re-running upserts doesn't create duplicates."""

    def test_patient_upsert_is_idempotent(self):
        data = transform_patient(SAMPLE_FHIR_PATIENT)
        Patient.objects.update_or_create(fhir_id=data["fhir_id"], defaults={
            "identifier_system": data["identifier_system"],
            "identifier_value": data["identifier_value"],
            "active": data["active"],
            "family_name": data["family_name"],
            "given_name": data["given_name"],
            "full_name": data["full_name"],
            "gender": data["gender"],
            "birth_date": data["birth_date"],
        })
        self.assertEqual(Patient.objects.count(), 1)

        # Re-run the same upsert — should update, not create
        Patient.objects.update_or_create(fhir_id=data["fhir_id"], defaults={
            "identifier_system": data["identifier_system"],
            "identifier_value": data["identifier_value"],
            "active": data["active"],
            "family_name": data["family_name"],
            "given_name": data["given_name"],
            "full_name": "Updated Name",
            "gender": data["gender"],
            "birth_date": data["birth_date"],
        })
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(Patient.objects.get().full_name, "Updated Name")

    def test_observation_upsert_is_idempotent(self):
        patient = Patient.objects.create(fhir_id="sindhu-syn-000004", full_name="Test")
        data = transform_observation(SAMPLE_FHIR_OBSERVATION)
        Observation.objects.update_or_create(fhir_id=data["fhir_id"], defaults={
            "patient_id": data["patient_id"],
            "status": data["status"],
            "category": data["category"],
            "code_system": data["code_system"],
            "code": data["code"],
            "code_display": data["code_display"],
            "code_text": data["code_text"],
            "effective_date": data["effective_date"],
            "value": data["value"],
            "value_unit": data["value_unit"],
            "value_system": data["value_system"],
            "value_code": data["value_code"],
        })
        self.assertEqual(Observation.objects.count(), 1)

        # Re-run — should update, not create
        Observation.objects.update_or_create(fhir_id=data["fhir_id"], defaults={
            "patient_id": data["patient_id"],
            "status": data["status"],
            "category": data["category"],
            "code_system": data["code_system"],
            "code": data["code"],
            "code_display": "Updated Display",
            "code_text": data["code_text"],
            "effective_date": data["effective_date"],
            "value": data["value"],
            "value_unit": data["value_unit"],
            "value_system": data["value_system"],
            "value_code": data["value_code"],
        })
        self.assertEqual(Observation.objects.count(), 1)
        self.assertEqual(Observation.objects.get().code_display, "Updated Display")


class ApiEndpointTest(TestCase):
    """Test the REST API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.patient = Patient.objects.create(
            fhir_id="test-patient-001",
            full_name="Test Patient",
            given_name="Test",
            family_name="Patient",
            gender="male",
            birth_date=date(1990, 5, 15),
        )
        self.observation = Observation.objects.create(
            fhir_id="test-obs-001",
            patient=self.patient,
            status="final",
            code_display="Heart rate",
            code="8867-4",
            value=Decimal("72"),
            value_unit="bpm",
        )

    def test_patient_list(self):
        url = reverse("patient-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["fhir_id"], "test-patient-001")
        self.assertEqual(response.data["results"][0]["observation_count"], 1)

    def test_patient_detail_with_observations(self):
        url = reverse("patient-detail", kwargs={"pk": "test-patient-001"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["fhir_id"], "test-patient-001")
        self.assertEqual(len(response.data["observations"]), 1)
        self.assertEqual(response.data["observations"][0]["code_display"], "Heart rate")

    def test_patient_detail_not_found(self):
        url = reverse("patient-detail", kwargs={"pk": "nonexistent"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_observation_list_filter_by_patient(self):
        url = reverse("observation-list")
        response = self.client.get(url, {"patient": "test-patient-001"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_migration_status_endpoint(self):
        url = reverse("migration-status")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["patients"], 1)
        self.assertEqual(response.data["observations"], 1)
