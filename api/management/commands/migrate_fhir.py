"""Django management command to migrate FHIR patients + observations."""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from api.fhir.client import FhirClient, FhirApiError
from api.fhir.transforms import transform_patient, transform_observation
from api.models import Patient, Observation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Migrate patient records and observations from a FHIR R4 server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="Number of patients per FHIR API page (default: 100)",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=None,
            help="Stop after N pages (useful for testing; default: all pages)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.1,
            help="Delay between API page requests in seconds (default: 0.1)",
        )

    def handle(self, *args, **options):
        page_size = options["page_size"]
        max_pages = options["max_pages"]
        delay = options["delay"]

        client = FhirClient(page_size=page_size, delay_between_pages=delay)

        # Pre-migration: get expected counts for validation
        self.stdout.write("Fetching expected counts from FHIR server...")
        try:
            expected_patients = client.get_resource_count("Patient")
            expected_observations = client.get_resource_count("Observation")
        except FhirApiError as e:
            self.stderr.write(self.style.ERROR(f"Failed to get counts: {e}"))
            return

        self.stdout.write(
            f"Expected: {expected_patients} patients, {expected_observations} observations"
        )

        total_patients = 0
        total_observations = 0
        failed_resources = 0
        page_num = 0

        self.stdout.write("Starting migration...\n")

        try:
            for patients, observations in client.fetch_patients_with_observations():
                page_num += 1

                with transaction.atomic():
                    for fhir_patient in patients:
                        try:
                            data = transform_patient(fhir_patient)
                            if not data["fhir_id"]:
                                failed_resources += 1
                                logger.warning("Patient missing fhir_id, skipping")
                                continue
                            Patient.objects.update_or_create(
                                fhir_id=data["fhir_id"],
                                defaults={
                                    "identifier_system": data["identifier_system"],
                                    "identifier_value": data["identifier_value"],
                                    "active": data["active"],
                                    "family_name": data["family_name"],
                                    "given_name": data["given_name"],
                                    "full_name": data["full_name"],
                                    "gender": data["gender"],
                                    "birth_date": data["birth_date"],
                                },
                            )
                            total_patients += 1
                        except Exception as e:
                            failed_resources += 1
                            logger.error(
                                "Failed to upsert patient %s: %s",
                                fhir_patient.get("id", "unknown"), e,
                            )

                    for fhir_obs in observations:
                        try:
                            data = transform_observation(fhir_obs)
                            if not data["fhir_id"] or not data["patient_id"]:
                                failed_resources += 1
                                logger.warning(
                                    "Observation %s missing fhir_id or patient_id, skipping",
                                    fhir_obs.get("id", "unknown"),
                                )
                                continue

                            # Skip if patient doesn't exist (deleted on source)
                            if not Patient.objects.filter(
                                fhir_id=data["patient_id"]
                            ).exists():
                                failed_resources += 1
                                logger.warning(
                                    "Observation %s references missing patient %s, skipping",
                                    data["fhir_id"], data["patient_id"],
                                )
                                continue

                            Observation.objects.update_or_create(
                                fhir_id=data["fhir_id"],
                                defaults={
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
                                },
                            )
                            total_observations += 1
                        except Exception as e:
                            failed_resources += 1
                            logger.error(
                                "Failed to upsert observation %s: %s",
                                fhir_obs.get("id", "unknown"), e,
                            )

                self.stdout.write(
                    f"  Page {page_num}: "
                    f"{len(patients)} patients, {len(observations)} observations | "
                    f"Cumulative: {total_patients} patients, {total_observations} observations"
                )

                if max_pages and page_num >= max_pages:
                    self.stdout.write(
                        self.style.WARNING(f"\nStopped after {max_pages} pages (--max-pages)")
                    )
                    break

        except FhirApiError as e:
            self.stderr.write(self.style.ERROR(f"\nMigration aborted: {e}"))
            self.stderr.write(
                f"Partial results: {total_patients} patients, {total_observations} observations"
            )
            return

        # Post-migration summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("Migration complete!"))
        self.stdout.write(f"  Patients migrated:    {total_patients}")
        self.stdout.write(f"  Observations migrated: {total_observations}")
        self.stdout.write(f"  Failed resources:     {failed_resources}")
        if not (max_pages and page_num >= max_pages):
            self.stdout.write(f"  Expected patients:    {expected_patients}")
            self.stdout.write(f"  Expected observations: {expected_observations}")
            if total_patients != expected_patients:
                self.stdout.write(
                    self.style.WARNING(
                        f"  WARNING: patient count mismatch "
                        f"({total_patients} vs {expected_patients})"
                    )
                )
        self.stdout.write("=" * 60)
