"""Django management command to migrate FHIR patients + observations."""

import logging
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.fhir.client import FhirClient, FhirApiError
from api.fhir.transforms import transform_patient, transform_observation
from api.models import MigrationRun, Observation, Patient

logger = logging.getLogger(__name__)

PATIENT_UPDATE_FIELDS = [
    "identifier_system",
    "identifier_value",
    "active",
    "family_name",
    "given_name",
    "full_name",
    "gender",
    "birth_date",
    "migrated_at",
]

OBSERVATION_UPDATE_FIELDS = [
    # Field name, not the "patient_id" attname — bulk_create resolves these
    # through Meta.get_field().
    "patient",
    "status",
    "category",
    "code_system",
    "code",
    "code_display",
    "code_text",
    "effective_date",
    "value",
    "value_unit",
    "value_system",
    "value_code",
    "migrated_at",
]


class Command(BaseCommand):
    help = "Migrate patient records and observations from a FHIR R4 server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--page-size",
            type=int,
            default=None,
            help="Patients per FHIR API page (default: settings.FHIR_PAGE_SIZE)",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=None,
            help="Stop after N patient pages (useful for testing; default: all pages)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.1,
            help="Delay between API page requests in seconds (default: 0.1)",
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help="Ignore any unfinished run and start again from the first page",
        )

    def handle(self, *args, **options):
        max_pages = options["max_pages"]

        client = FhirClient(
            page_size=options["page_size"],
            delay_between_pages=options["delay"],
        )

        with client:
            run = self._start_run(client, restart=options["restart"])
            # Seeded from the run so the totals stay cumulative across a resume.
            stats = Counter({
                "patients_written": run.patients_written,
                "patients_skipped": run.patients_skipped,
                "observations_written": run.observations_written,
                "observations_skipped": run.observations_skipped,
            })
            self.stdout.write("Starting migration...\n")
            pages_read = self._migrate(client, run, stats, max_pages)

        complete = max_pages is None or pages_read < max_pages
        if complete:
            self._finish_run(run, MigrationRun.Status.COMPLETE)

        self._report(run, stats, complete)

        if not run.patients_written:
            raise CommandError("Migration wrote no patients — treating this as a failure.")

    # --- checkpointing -----------------------------------------------------

    def _start_run(self, client, restart):
        """
        Resume the last unfinished run, or open a new one.

        Expected counts are read *before* any run row is touched. Doing it the
        other way round meant an unreachable server left behind a `running` row
        at offset 0 that nothing would ever mark failed — so the UI reported an
        incomplete migration over a complete dataset, with no error to explain it.
        """
        expected = self._fetch_expected_counts(client)
        unfinished = MigrationRun.resumable()

        if unfinished and not restart:
            run = unfinished
            self.stdout.write(
                self.style.WARNING(
                    f"Resuming the {run.status} run started "
                    f"{run.started_at:%Y-%m-%d %H:%M:%S} at patient offset "
                    f"{run.patients_offset}"
                )
            )
            run.status = MigrationRun.Status.RUNNING
            run.error = ""
            run.save(update_fields=["status", "error", "updated_at"])
        else:
            if unfinished:
                self.stdout.write(
                    self.style.WARNING(
                        f"--restart: abandoning the unfinished run at offset "
                        f"{unfinished.patients_offset} and starting over"
                    )
                )
            run = MigrationRun.objects.create()

        run.expected_patients = expected["patients"]
        run.expected_observations = expected["observations"]
        run.save(update_fields=["expected_patients", "expected_observations", "updated_at"])
        return run

    def _checkpoint(self, run, patients_read, stats):
        """
        Record progress after a page and everything belonging to it is committed.

        Written after the page's observations, never before: a crash in between
        replays the page on the next run, and replaying is harmless because every
        write is an upsert. Checkpointing first would skip it instead.
        """
        run.patients_offset += patients_read
        run.patients_written = stats["patients_written"]
        run.patients_skipped = stats["patients_skipped"]
        run.observations_written = stats["observations_written"]
        run.observations_skipped = stats["observations_skipped"]
        run.save()

    def _finish_run(self, run, status, error=""):
        run.status = status
        run.error = error
        run.save(update_fields=["status", "error", "updated_at"])

    # --- fetch + persist ---------------------------------------------------

    def _fetch_expected_counts(self, client):
        """Read the server's own totals up front, for post-migration validation."""
        self.stdout.write("Fetching expected counts from FHIR server...")
        try:
            expected = {
                "patients": client.get_resource_count("Patient"),
                # Only observations that have a subject can belong to a patient in
                # our model. On the sandbox ~4,000 of 42,000 have none at all, so
                # comparing against the unfiltered total would report a phantom gap.
                "observations": client.get_resource_count(
                    "Observation", {"subject:missing": "false"}
                ),
            }
        except FhirApiError as e:
            raise CommandError(f"Failed to read expected counts: {e}")

        self.stdout.write(
            f"Server reports: {self._format_count(expected['patients'])} patients, "
            f"{self._format_count(expected['observations'])} observations with a subject"
        )
        return expected

    def _migrate(self, client, run, stats, max_pages):
        """Walk patient pages, persisting each page and its observations. Returns pages read."""
        pages_read = 0
        try:
            for fhir_patients in client.iter_patient_pages(start_offset=run.patients_offset):
                pages_read += 1
                patient_ids = self._upsert_patients(fhir_patients, stats)

                # Patients are committed before their observations are fetched so the
                # FK targets exist, and so no DB transaction stays open across HTTP calls.
                for fhir_observations in client.iter_observation_pages(patient_ids):
                    self._upsert_observations(fhir_observations, patient_ids, stats)

                self._checkpoint(run, len(fhir_patients), stats)

                self.stdout.write(
                    f"  Page {pages_read} (offset {run.patients_offset}): "
                    f"{len(fhir_patients)} patients read | "
                    f"cumulative {stats['patients_written']} patients, "
                    f"{stats['observations_written']} observations written"
                )
                # Python block-buffers stdout when it isn't a tty, so progress from
                # a long run piped to a log only appears at exit — and is lost
                # entirely if the process is killed. Flush each page instead.
                self.stdout.flush()

                if max_pages and pages_read >= max_pages:
                    self.stdout.write(
                        self.style.WARNING(f"\nStopped after {max_pages} pages (--max-pages)")
                    )
                    break
        except FhirApiError as e:
            # The checkpoint survives, so the next run picks up at this page
            # rather than page one. Exit non-zero so a scheduler notices.
            self._finish_run(run, MigrationRun.Status.FAILED, str(e))
            raise CommandError(
                f"Migration aborted: {e}\n"
                f"Checkpointed at patient offset {run.patients_offset} — "
                f"re-run `manage.py migrate_fhir` to resume from there."
            )
        except Exception as e:
            # Anything unexpected still has to leave a truthful status behind.
            self._finish_run(run, MigrationRun.Status.FAILED, f"{type(e).__name__}: {e}")
            raise
        return pages_read

    def _upsert_patients(self, fhir_patients, stats):
        """Upsert a page of patients in one query. Returns the ids successfully written."""
        rows = {}
        for fhir_patient in fhir_patients:
            data = transform_patient(fhir_patient)
            if not data["fhir_id"]:
                stats["patients_skipped"] += 1
                logger.warning("Patient resource has no id; skipping")
                continue
            # Later duplicates of the same id win, and de-duplicating here keeps
            # the ON CONFLICT clause from touching a row twice in one statement.
            rows[data["fhir_id"]] = Patient(**data)

        if rows:
            with transaction.atomic():
                Patient.objects.bulk_create(
                    list(rows.values()),
                    update_conflicts=True,
                    unique_fields=["fhir_id"],
                    update_fields=PATIENT_UPDATE_FIELDS,
                )
        stats["patients_written"] += len(rows)
        return set(rows)

    def _upsert_observations(self, fhir_observations, known_patient_ids, stats):
        """Upsert a page of observations in one query, skipping unresolvable subjects."""
        rows = {}
        for fhir_observation in fhir_observations:
            data = transform_observation(fhir_observation)
            if not data["fhir_id"]:
                stats["observations_skipped"] += 1
                logger.warning("Observation resource has no id; skipping")
                continue
            # Checked against the ids just written rather than with a per-row
            # existence query, which would be an N+1 over the whole migration.
            if data["patient_id"] not in known_patient_ids:
                stats["observations_skipped"] += 1
                logger.warning(
                    "Observation %s has an unresolvable subject (%s); skipping",
                    data["fhir_id"], data["patient_id"] or "none",
                )
                continue
            rows[data["fhir_id"]] = Observation(**data)

        if rows:
            with transaction.atomic():
                Observation.objects.bulk_create(
                    list(rows.values()),
                    update_conflicts=True,
                    unique_fields=["fhir_id"],
                    update_fields=OBSERVATION_UPDATE_FIELDS,
                )
        stats["observations_written"] += len(rows)

    # --- reporting + validation -------------------------------------------

    def _report(self, run, stats, complete):
        """Print the summary and the validation checks from Plan.md section 3."""
        # Validation reads the destination rather than the in-process counters:
        # the counters describe what we believe we did, the database is what
        # actually happened, and a resumed run may have replayed a page.
        stored_patients = Patient.objects.count()
        stored_observations = Observation.objects.count()
        patients_unnamed = Patient.objects.filter(full_name__isnull=True).count()
        observations_uncoded = Observation.objects.filter(
            code__isnull=True, code_text__isnull=True
        ).count()
        observations_valueless = Observation.objects.filter(value__isnull=True).count()

        self.stdout.write("\n" + "=" * 64)
        self.stdout.write(self.style.SUCCESS("Migration finished"))
        self.stdout.write(f"  Status:                {run.status}")
        self.stdout.write(f"  Patient offset:        {run.patients_offset}")
        self.stdout.write(f"  Patients written:      {stats['patients_written']}")
        self.stdout.write(f"  Observations written:  {stats['observations_written']}")
        self.stdout.write(f"  Patients skipped:      {stats['patients_skipped']}")
        self.stdout.write(f"  Observations skipped:  {stats['observations_skipped']}")

        self.stdout.write("\nData quality (all rows in the database)")
        self.stdout.write(f"  Patients:                     {stored_patients}")
        self.stdout.write(f"  Patients with no name:        {patients_unnamed}")
        self.stdout.write(f"  Observations:                 {stored_observations}")
        self.stdout.write(f"  Observations with no code:    {observations_uncoded}")
        self.stdout.write(
            f"  Observations with no value:   {observations_valueless} "
            f"(expected for panel observations that use component arrays)"
        )

        self.stdout.write("\nValidation")
        # A field that is null for every single row almost always means a broken
        # mapping rather than sparse source data, so call it out explicitly.
        self._check_not_universally_null("patient names", patients_unnamed, stored_patients)
        self._check_not_universally_null(
            "observation codes", observations_uncoded, stored_observations
        )

        if complete:
            self._check_expected_count("Patient", stored_patients, run.expected_patients)
            self._check_expected_count(
                "Observation", stored_observations, run.expected_observations,
                note="the remainder reference a subject that is not a migrated patient",
            )
        else:
            self.stdout.write(
                f"  ~ count comparison skipped (partial run; resume from offset "
                f"{run.patients_offset} by re-running the command)"
            )

        # Plan.md's orphaned-observation check is enforced by the schema: patient
        # is a non-null FK, so an orphan cannot be committed in the first place.
        self.stdout.write("  ✓ referential integrity guaranteed by the patient FK constraint")
        self.stdout.write("=" * 64)

    def _check_not_universally_null(self, label, missing, total):
        if total and missing == total:
            self.stdout.write(
                self.style.ERROR(
                    f"  ✗ every migrated row is missing {label} — check the transform mapping"
                )
            )
        else:
            self.stdout.write(f"  ✓ {label} present on {total - missing}/{total} rows")

    def _check_expected_count(self, resource_type, written, expected, note=None):
        if expected is None:
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ {resource_type}: server did not report a total; cannot verify count"
                )
            )
        elif written == expected:
            self.stdout.write(f"  ✓ {resource_type}: {written} stored matches server total")
        elif written > expected:
            # We hold rows the source no longer reports. Deletions at the source
            # are the usual cause, and nothing here removes them — the migration
            # has no tombstone handling.
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ {resource_type}: stored {written}, more than the {expected} the "
                    f"server reports ({written - expected} may have been deleted at the "
                    f"source since they were migrated)"
                )
            )
        else:
            # A warning rather than a failure: the sandbox is shared and mutates
            # while we read it, so an exact match is not guaranteed.
            detail = f" — {note}" if note else ""
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ {resource_type}: stored {written} of {expected} reported by the "
                    f"server ({expected - written} unaccounted for{detail})"
                )
            )

    @staticmethod
    def _format_count(count):
        return "an unreported number of" if count is None else str(count)
