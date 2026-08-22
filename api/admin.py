from django.contrib import admin

from api.models import Patient, Observation


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
