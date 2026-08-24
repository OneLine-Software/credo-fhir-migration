from django.db import models


class Patient(models.Model):
    """Simplified patient model, migrated from FHIR R4 Patient resources."""

    fhir_id = models.CharField(max_length=100, primary_key=True)
    identifier_system = models.CharField(max_length=255, null=True, blank=True)
    identifier_value = models.CharField(max_length=100, null=True, blank=True)
    active = models.BooleanField(default=True)
    family_name = models.CharField(max_length=200, null=True, blank=True)
    given_name = models.CharField(max_length=200, null=True, blank=True)
    full_name = models.CharField(max_length=400, null=True, blank=True)
    gender = models.CharField(max_length=20, null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    migrated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fhir_id"]

    def __str__(self):
        return self.full_name or self.fhir_id


class Observation(models.Model):
    """Simplified observation model, migrated from FHIR R4 Observation resources."""

    fhir_id = models.CharField(max_length=100, primary_key=True)
    patient = models.ForeignKey(
        Patient,
        related_name="observations",
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=30, default="unknown")
    category = models.CharField(max_length=50, null=True, blank=True)
    code_system = models.CharField(max_length=255, null=True, blank=True)
    code = models.CharField(max_length=50, null=True, blank=True)
    code_display = models.CharField(max_length=255, null=True, blank=True)
    code_text = models.CharField(max_length=255, null=True, blank=True)
    effective_date = models.DateTimeField(null=True, blank=True)
    # Wide enough for the full range of UCUM-coded lab values (e.g. cell counts in
    # the millions and concentrations with six decimal places) without silently
    # rounding or overflowing on write.
    value = models.DecimalField(max_digits=19, decimal_places=6, null=True, blank=True)
    value_unit = models.CharField(max_length=50, null=True, blank=True)
    value_system = models.CharField(max_length=255, null=True, blank=True)
    value_code = models.CharField(max_length=50, null=True, blank=True)
    migrated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.code_display or self.code} ({self.patient.fhir_id})"


class MigrationRun(models.Model):
    """
    Checkpoint for one execution of the migration, so an interrupted run resumes
    instead of restarting.

    `patients_offset` is a source-side cursor: the number of Patient resources
    already consumed, passed back as `_offset` on the next run. Paired with
    `_sort=_id` it identifies the same position in the result set on a later run,
    which an opaque `_getpages` link cannot do — those are server-side caches
    that expire.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    status = models.CharField(max_length=20, choices=Status, default=Status.RUNNING)
    patients_offset = models.PositiveIntegerField(default=0)
    patients_written = models.PositiveIntegerField(default=0)
    patients_skipped = models.PositiveIntegerField(default=0)
    observations_written = models.PositiveIntegerField(default=0)
    observations_skipped = models.PositiveIntegerField(default=0)
    expected_patients = models.PositiveIntegerField(null=True, blank=True)
    expected_observations = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        get_latest_by = "started_at"

    def __str__(self):
        return f"{self.status} run from {self.started_at:%Y-%m-%d %H:%M} (offset {self.patients_offset})"

    @classmethod
    def resumable(cls):
        """
        The most recent run that did not finish, if any.

        A `running` row whose process was killed looks identical to one still in
        flight; both are resumable, and resuming is safe either way because every
        write is an idempotent upsert.
        """
        return cls.objects.filter(
            status__in=[cls.Status.RUNNING, cls.Status.FAILED]
        ).first()
