"""Backend tests for the FHIR client, transforms, migration command, and API."""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.fhir.client import FhirApiError, FhirClient, OBSERVATION_SUBJECT_BATCH
from api.fhir.transforms import transform_patient, transform_observation
from api.models import MigrationRun, Observation, Patient


def setUpModule():
    """Several tests deliberately trigger retry/skip warnings; keep the run readable."""
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


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


def bundle(*resources, next_url=None):
    """Build a minimal FHIR searchset Bundle, optionally with a 'next' link."""
    links = [{"relation": "self", "url": "http://fhir.test/self"}]
    if next_url:
        links.append({"relation": "next", "url": next_url})
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": links,
        "entry": [{"resource": resource} for resource in resources],
    }


class FakeResponse:
    """Stands in for a requests.Response in client tests."""

    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data if json_data is not None else {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


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
            "name": [{"family": "Smith", "given": ["John", "Robert"]}],
        }
        result = transform_patient(patient)
        self.assertEqual(result["full_name"], "John Robert Smith")
        self.assertEqual(result["given_name"], "John")

    def test_patient_nested_lists_are_traversed(self):
        """Regression: an earlier _safe_get bailed out on list indexes, nulling every name."""
        result = transform_patient({"id": "p1", "name": [{"family": "Smith"}]})
        self.assertEqual(result["family_name"], "Smith")

    def test_explicit_null_active_does_not_reach_the_column(self):
        """FHIR omits `active` to mean "no assertion"; null must not hit a NOT NULL column."""
        self.assertIs(transform_patient({"id": "p1", "active": None})["active"], True)
        self.assertIs(transform_patient({"id": "p1", "active": False})["active"], False)


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
        self.assertEqual(result["value"], Decimal("3.0"))
        self.assertEqual(result["value_unit"], "U/L")

    def test_observation_without_value_quantity(self):
        """Observations without valueQuantity should have nullable value fields."""
        result = transform_observation(SAMPLE_FHIR_OBSERVATION_NO_VALUE)
        self.assertEqual(result["fhir_id"], "obs-no-value")
        self.assertIsNone(result["value"])
        self.assertIsNone(result["value_unit"])

    def test_subject_reference_forms(self):
        """Relative, absolute and versioned references all resolve to the bare id."""
        cases = {
            "Patient/abc-123": "abc-123",
            "http://hapi.fhir.org/baseR4/Patient/abc-123": "abc-123",
            "Patient/abc-123/_history/2": "abc-123",
        }
        for reference, expected in cases.items():
            with self.subTest(reference=reference):
                result = transform_observation({"id": "o1", "subject": {"reference": reference}})
                self.assertEqual(result["patient_id"], expected)

    def test_unresolvable_subject_references_yield_none(self):
        """Contained, missing and non-Patient subjects have no FK we can use."""
        for subject in [{"reference": "#contained"}, {"reference": "Group/g1"}, {}, None]:
            with self.subTest(subject=subject):
                observation = {"id": "o1"}
                if subject is not None:
                    observation["subject"] = subject
                self.assertIsNone(transform_observation(observation)["patient_id"])

    def test_effective_date_falls_back_to_period_and_instant(self):
        expected = datetime(2024, 3, 4, 9, 30, tzinfo=timezone.utc)
        period = {"id": "o1", "effectivePeriod": {"start": "2024-03-04T09:30:00+00:00"}}
        instant = {"id": "o2", "effectiveInstant": "2024-03-04T09:30:00+00:00"}
        self.assertEqual(transform_observation(period)["effective_date"], expected)
        self.assertEqual(transform_observation(instant)["effective_date"], expected)

    def test_decimal_conversion_avoids_float_representation_error(self):
        observation = {"id": "o1", "valueQuantity": {"value": 0.1}}
        self.assertEqual(transform_observation(observation)["value"], Decimal("0.1"))

    def test_unstorable_values_become_null_instead_of_raising(self):
        for raw in [1e30, "not-a-number", float("nan")]:
            with self.subTest(raw=raw):
                observation = {"id": "o1", "valueQuantity": {"value": raw}}
                self.assertIsNone(transform_observation(observation)["value"])


class FhirClientRetryTest(TestCase):
    """Test retry/backoff behaviour against a mocked HTTP session."""

    def setUp(self):
        self.client_under_test = FhirClient(base_url="http://fhir.test", max_retries=3)
        self.addCleanup(self.client_under_test.close)
        self.session = Mock()
        self.client_under_test.session = self.session

    def test_retries_transient_error_then_succeeds(self):
        self.session.get.side_effect = [
            FakeResponse(503),
            FakeResponse(200, {"resourceType": "Bundle"}),
        ]
        with patch("api.fhir.client.time.sleep") as sleep:
            result = self.client_under_test._fetch_with_retry("http://fhir.test/Patient")
        self.assertEqual(result, {"resourceType": "Bundle"})
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_honours_retry_after_header(self):
        self.session.get.side_effect = [
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, {"resourceType": "Bundle"}),
        ]
        with patch("api.fhir.client.time.sleep") as sleep:
            self.client_under_test._fetch_with_retry("http://fhir.test/Patient")
        sleep.assert_called_once_with(7.0)

    def test_retries_timeouts(self):
        self.session.get.side_effect = [
            requests.exceptions.Timeout(),
            FakeResponse(200, {"resourceType": "Bundle"}),
        ]
        with patch("api.fhir.client.time.sleep"):
            result = self.client_under_test._fetch_with_retry("http://fhir.test/Patient")
        self.assertEqual(result, {"resourceType": "Bundle"})

    def test_does_not_retry_client_errors(self):
        """A 404 will never succeed — failing fast keeps the retry budget for real blips."""
        self.session.get.return_value = FakeResponse(404)
        with patch("api.fhir.client.time.sleep") as sleep:
            with self.assertRaises(FhirApiError) as ctx:
                self.client_under_test._fetch_with_retry("http://fhir.test/Patient")
        self.assertEqual(self.session.get.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        self.assertIn("404", str(ctx.exception))

    def test_gives_up_after_max_retries_and_reports_the_reason(self):
        self.session.get.return_value = FakeResponse(503)
        with patch("api.fhir.client.time.sleep") as sleep:
            with self.assertRaises(FhirApiError) as ctx:
                self.client_under_test._fetch_with_retry("http://fhir.test/Patient")
        self.assertEqual(self.session.get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)  # no sleep after the final attempt
        self.assertIn("503", str(ctx.exception))


class FhirClientPaginationTest(TestCase):
    """Test bundle pagination and the observation fetch strategy."""

    def setUp(self):
        self.client_under_test = FhirClient(
            base_url="http://fhir.test", page_size=2, observation_page_size=2,
            delay_between_pages=0,
        )
        self.addCleanup(self.client_under_test.close)
        self.session = Mock()
        self.client_under_test.session = self.session

    def test_iter_patient_pages_follows_next_link(self):
        self.session.get.side_effect = [
            FakeResponse(200, bundle(
                {"resourceType": "Patient", "id": "p1"},
                {"resourceType": "OperationOutcome", "issue": []},
                next_url="http://fhir.test/page2",
            )),
            FakeResponse(200, bundle({"resourceType": "Patient", "id": "p2"})),
        ]
        pages = list(self.client_under_test.iter_patient_pages())
        self.assertEqual([[p["id"] for p in page] for page in pages], [["p1"], ["p2"]])
        self.assertEqual(self.session.get.call_args_list[1].args[0], "http://fhir.test/page2")

    def test_patient_search_does_not_use_revinclude(self):
        """
        Regression guard. HAPI silently truncates _revinclude at 1000 resources per
        page, which dropped ~90% of observations at a page size of 100.
        """
        self.session.get.return_value = FakeResponse(200, bundle())
        list(self.client_under_test.iter_patient_pages())
        self.assertNotIn("revinclude", self.session.get.call_args.args[0])

    def test_observations_are_fetched_in_batches_and_paginated(self):
        patient_ids = [f"p{n}" for n in range(OBSERVATION_SUBJECT_BATCH + 1)]
        self.session.get.side_effect = [
            # First batch spans two pages, second batch one.
            FakeResponse(200, bundle(
                {"resourceType": "Observation", "id": "o1"}, next_url="http://fhir.test/obs2",
            )),
            FakeResponse(200, bundle({"resourceType": "Observation", "id": "o2"})),
            FakeResponse(200, bundle({"resourceType": "Observation", "id": "o3"})),
        ]
        pages = list(self.client_under_test.iter_observation_pages(patient_ids))
        self.assertEqual([o["id"] for page in pages for o in page], ["o1", "o2", "o3"])

        requested = [call.args[0] for call in self.session.get.call_args_list]
        self.assertEqual(len(requested), 3)
        self.assertIn("subject=", requested[0])
        # The 51st id must not be dropped — it belongs to the second batch.
        self.assertIn(f"p{OBSERVATION_SUBJECT_BATCH}", requested[2])

    def test_get_resource_count_returns_none_when_total_is_absent(self):
        self.session.get.return_value = FakeResponse(200, {"resourceType": "Bundle"})
        self.assertIsNone(self.client_under_test.get_resource_count("Patient"))


class FakeFhirClient:
    """Stands in for FhirClient in migration command tests."""

    def __init__(
        self,
        patient_pages,
        observation_pages=None,
        counts=None,
        fail_after_pages=None,
        fail_on_counts=False,
    ):
        self.patient_pages = patient_pages
        self.observation_pages = observation_pages or []
        self.counts = counts or {}
        self.fail_after_pages = fail_after_pages
        self.fail_on_counts = fail_on_counts
        self.observation_requests = []
        self.start_offsets = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True

    def get_resource_count(self, resource_type, search_params=None):
        if self.fail_on_counts:
            raise FhirApiError("server unreachable")
        return self.counts.get(resource_type)

    def iter_patient_pages(self, start_offset=0):
        self.start_offsets.append(start_offset)
        # Mirrors the server: _offset skips resources already consumed.
        consumed = 0
        for index, page in enumerate(self.patient_pages):
            if consumed < start_offset:
                consumed += len(page)
                continue
            if self.fail_after_pages is not None and index == self.fail_after_pages:
                raise FhirApiError("simulated upstream failure")
            yield page

    def iter_observation_pages(self, patient_ids):
        self.observation_requests.append(set(patient_ids))
        yield from self.observation_pages


class MigrateFhirCommandTest(TestCase):
    """Test the migration command end to end with a stubbed FHIR client."""

    def run_command(self, fake_client, **options):
        out, err = StringIO(), StringIO()
        with patch(
            "api.management.commands.migrate_fhir.FhirClient", return_value=fake_client
        ):
            call_command("migrate_fhir", stdout=out, stderr=err, **options)
        return out.getvalue()

    def test_migrates_patients_and_their_observations(self):
        fake = FakeFhirClient(
            patient_pages=[[SAMPLE_FHIR_PATIENT]],
            observation_pages=[[SAMPLE_FHIR_OBSERVATION]],
            counts={"Patient": 1, "Observation": 1},
        )
        output = self.run_command(fake)

        patient = Patient.objects.get()
        self.assertEqual(patient.fhir_id, "sindhu-syn-000004")
        self.assertEqual(patient.full_name, "Synthetic Patient SYN-000004")
        observation = Observation.objects.get()
        self.assertEqual(observation.patient_id, patient.fhir_id)
        self.assertEqual(observation.value, Decimal("3.0"))
        self.assertIn("matches server total", output)
        self.assertTrue(fake.closed)  # HTTP session released

    def test_observations_are_only_requested_for_patients_that_were_written(self):
        fake = FakeFhirClient(patient_pages=[[SAMPLE_FHIR_PATIENT, {"resourceType": "Patient"}]])
        self.run_command(fake)
        self.assertEqual(fake.observation_requests, [{"sindhu-syn-000004"}])

    def test_rerunning_updates_instead_of_duplicating(self):
        renamed = {**SAMPLE_FHIR_PATIENT, "name": [{"text": "Updated Name"}]}
        self.run_command(FakeFhirClient(
            patient_pages=[[SAMPLE_FHIR_PATIENT]], observation_pages=[[SAMPLE_FHIR_OBSERVATION]],
        ))
        self.run_command(FakeFhirClient(
            patient_pages=[[renamed]], observation_pages=[[SAMPLE_FHIR_OBSERVATION]],
        ))

        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 1)
        self.assertEqual(Patient.objects.get().full_name, "Updated Name")

    def test_duplicate_ids_within_one_page_do_not_break_the_upsert(self):
        self.run_command(FakeFhirClient(patient_pages=[[SAMPLE_FHIR_PATIENT, SAMPLE_FHIR_PATIENT]]))
        self.assertEqual(Patient.objects.count(), 1)

    def test_skips_observations_with_an_unresolvable_subject(self):
        orphan = {**SAMPLE_FHIR_OBSERVATION, "id": "orphan", "subject": {"reference": "Patient/nope"}}
        output = self.run_command(FakeFhirClient(
            patient_pages=[[SAMPLE_FHIR_PATIENT]],
            observation_pages=[[SAMPLE_FHIR_OBSERVATION, orphan]],
        ))
        self.assertEqual(Observation.objects.count(), 1)
        self.assertIn("Observations skipped:  1", output)

    def test_reports_rows_the_source_no_longer_has(self):
        """More rows locally than upstream means deletions at the source, not a shortfall."""
        Patient.objects.create(fhir_id="deleted-upstream")
        output = self.run_command(FakeFhirClient(
            patient_pages=[[SAMPLE_FHIR_PATIENT]], counts={"Patient": 1},
        ))
        self.assertIn("may have been deleted at the source", output)

    def test_flags_a_field_that_is_null_on_every_row(self):
        """The canary for a broken mapping — a whole column of nulls is a bug, not sparse data."""
        nameless = {"resourceType": "Patient", "id": "p1"}
        output = self.run_command(FakeFhirClient(patient_pages=[[nameless]]))
        self.assertIn("every migrated row is missing patient names", output)

    def test_upstream_failure_aborts_with_a_nonzero_exit(self):
        fake = FakeFhirClient(patient_pages=[[SAMPLE_FHIR_PATIENT], []], fail_after_pages=1)
        with self.assertRaises(CommandError) as ctx:
            self.run_command(fake)
        self.assertIn("simulated upstream failure", str(ctx.exception))
        # The page that did succeed is still committed — re-running resumes safely.
        self.assertEqual(Patient.objects.count(), 1)

    def test_migrating_nothing_is_a_failure(self):
        with self.assertRaises(CommandError):
            self.run_command(FakeFhirClient(patient_pages=[[]]))

    def test_partial_run_skips_the_count_comparison(self):
        fake = FakeFhirClient(
            patient_pages=[[SAMPLE_FHIR_PATIENT]], counts={"Patient": 5000},
        )
        output = self.run_command(fake, max_pages=1)
        self.assertIn("count comparison skipped", output)


class MigrationCheckpointTest(TestCase):
    """Test that an interrupted run resumes instead of restarting."""

    def patients(self, *ids):
        return [{**SAMPLE_FHIR_PATIENT, "id": fhir_id} for fhir_id in ids]

    def run_command(self, fake_client, **options):
        out = StringIO()
        with patch(
            "api.management.commands.migrate_fhir.FhirClient", return_value=fake_client
        ):
            call_command("migrate_fhir", stdout=out, stderr=StringIO(), **options)
        return out.getvalue()

    def test_completed_run_is_recorded(self):
        self.run_command(FakeFhirClient(patient_pages=[self.patients("p1", "p2")]))
        run = MigrationRun.objects.get()
        self.assertEqual(run.status, MigrationRun.Status.COMPLETE)
        self.assertEqual(run.patients_offset, 2)
        self.assertEqual(run.patients_written, 2)
        self.assertIsNone(MigrationRun.resumable())

    def test_checkpoint_advances_per_page_and_survives_a_failure(self):
        fake = FakeFhirClient(
            patient_pages=[self.patients("p1", "p2"), self.patients("p3")],
            fail_after_pages=1,
        )
        with self.assertRaises(CommandError):
            self.run_command(fake)

        run = MigrationRun.objects.get()
        self.assertEqual(run.status, MigrationRun.Status.FAILED)
        self.assertIn("simulated upstream failure", run.error)
        # First page committed and checkpointed; the failing page did not advance it.
        self.assertEqual(run.patients_offset, 2)
        self.assertEqual(Patient.objects.count(), 2)

    def test_next_run_resumes_from_the_checkpoint(self):
        failing = FakeFhirClient(
            patient_pages=[self.patients("p1", "p2"), self.patients("p3")],
            fail_after_pages=1,
        )
        with self.assertRaises(CommandError):
            self.run_command(failing)

        resuming = FakeFhirClient(
            patient_pages=[self.patients("p1", "p2"), self.patients("p3")],
        )
        output = self.run_command(resuming)

        # Resumed at the right offset and did not refetch the first page.
        self.assertEqual(resuming.start_offsets, [2])
        self.assertEqual(resuming.observation_requests, [{"p3"}])
        self.assertIn("Resuming the failed run", output)

        # Same run row, now complete, with cumulative counters.
        run = MigrationRun.objects.get()
        self.assertEqual(run.status, MigrationRun.Status.COMPLETE)
        self.assertEqual(run.patients_offset, 3)
        self.assertEqual(run.patients_written, 3)
        self.assertEqual(Patient.objects.count(), 3)

    def test_max_pages_leaves_a_resumable_checkpoint(self):
        pages = [self.patients("p1"), self.patients("p2")]
        self.run_command(FakeFhirClient(patient_pages=pages), max_pages=1)
        self.assertEqual(MigrationRun.objects.get().status, MigrationRun.Status.RUNNING)

        resuming = FakeFhirClient(patient_pages=pages)
        self.run_command(resuming)
        self.assertEqual(resuming.start_offsets, [1])
        self.assertEqual(MigrationRun.objects.get().status, MigrationRun.Status.COMPLETE)
        self.assertEqual(Patient.objects.count(), 2)

    def test_unreachable_server_leaves_no_phantom_run(self):
        """
        A startup failure must not record a run at all.

        It previously created the row before reading the server's counts, so an
        unreachable server left a `running` row at offset 0 that nothing would
        mark failed — the UI then reported an incomplete migration over a
        complete dataset, with no error to explain it.
        """
        with self.assertRaises(CommandError):
            self.run_command(FakeFhirClient(patient_pages=[], fail_on_counts=True))
        self.assertFalse(MigrationRun.objects.exists())

    def test_unreachable_server_does_not_disturb_an_earlier_run(self):
        self.run_command(FakeFhirClient(patient_pages=[self.patients("p1")]))
        with self.assertRaises(CommandError):
            self.run_command(FakeFhirClient(patient_pages=[], fail_on_counts=True))

        run = MigrationRun.objects.get()
        self.assertEqual(run.status, MigrationRun.Status.COMPLETE)
        self.assertIsNone(MigrationRun.resumable())

    def test_restart_ignores_the_checkpoint(self):
        pages = [self.patients("p1"), self.patients("p2")]
        self.run_command(FakeFhirClient(patient_pages=pages), max_pages=1)

        restarted = FakeFhirClient(patient_pages=pages)
        output = self.run_command(restarted, restart=True)

        self.assertEqual(restarted.start_offsets, [0])
        self.assertIn("--restart", output)
        self.assertEqual(MigrationRun.objects.count(), 2)

    def test_a_finished_run_is_not_resumed(self):
        """A completed migration starts a fresh run rather than reusing the old cursor."""
        self.run_command(FakeFhirClient(patient_pages=[self.patients("p1")]))
        second = FakeFhirClient(patient_pages=[self.patients("p1")])
        self.run_command(second)

        self.assertEqual(second.start_offsets, [0])
        self.assertEqual(MigrationRun.objects.count(), 2)
        self.assertEqual(Patient.objects.count(), 1)  # still idempotent


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

    def test_observation_value_serialises_as_a_number(self):
        """Clients should not have to strip padding from "72.000000"."""
        url = reverse("patient-detail", kwargs={"pk": "test-patient-001"})
        response = self.client.get(url)
        self.assertEqual(response.data["observations"][0]["value"], Decimal("72"))
        self.assertIn(b'"value":72', response.render().content)

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
        self.assertIsNone(response.data["last_run"])  # nothing has run yet

    def test_migration_status_reports_the_latest_run(self):
        MigrationRun.objects.create(
            status=MigrationRun.Status.FAILED, patients_offset=300, error="boom"
        )
        response = self.client.get(reverse("migration-status"))
        self.assertEqual(response.data["last_run"]["status"], "failed")
        self.assertEqual(response.data["last_run"]["patients_offset"], 300)
