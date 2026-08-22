from django.db import models


class Patient(models.Model):
    """Simplified patient model, migrated from FHIR R4 Patient resources."""

    fhir_id = models.CharField(max_length=100, unique=True, primary_key=True)
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

    fhir_id = models.CharField(max_length=100, unique=True, primary_key=True)
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
    value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    value_unit = models.CharField(max_length=50, null=True, blank=True)
    value_system = models.CharField(max_length=255, null=True, blank=True)
    value_code = models.CharField(max_length=50, null=True, blank=True)
    migrated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.code_display or self.code} ({self.patient.fhir_id})"
