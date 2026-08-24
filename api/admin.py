from django.contrib import admin

from api.models import MigrationRun, Observation, Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("fhir_id", "full_name", "gender", "birth_date")
    search_fields = ("fhir_id", "full_name", "identifier_value")
    list_filter = ("gender", "active")


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("fhir_id", "patient", "code_display", "value", "value_unit", "effective_date")
    search_fields = ("fhir_id", "code_display", "code")
    list_filter = ("category", "status")


@admin.register(MigrationRun)
class MigrationRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at", "status", "patients_offset", "patients_written", "observations_written",
    )
    list_filter = ("status",)
    readonly_fields = ("started_at", "updated_at")
