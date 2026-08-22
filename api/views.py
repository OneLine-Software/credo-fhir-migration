"""DRF viewsets for Patient and Observation API endpoints."""

from django.db.models import Count
from rest_framework import viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.models import Patient, Observation
from api.serializers import (
    PatientListSerializer,
    PatientDetailSerializer,
    ObservationSerializer,
)


class PatientViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for patients.

    GET /api/patients/           — paginated list (lightweight serializer)
    GET /api/patients/{fhir_id}/ — single patient with nested observations
    """

    queryset = Patient.objects.all()

    def get_serializer_class(self):
        if self.action == "list":
            return PatientListSerializer
        return PatientDetailSerializer

    def get_queryset(self):
        if self.action == "list":
            return Patient.objects.annotate(observation_count=Count("observations"))
        return Patient.objects.all()


class ObservationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for observations.

    GET /api/observations/              — paginated list
    GET /api/observations/{fhir_id}/    — single observation
    GET /api/observations/?patient={id} — filter by patient
    """

    serializer_class = ObservationSerializer

    def get_queryset(self):
        queryset = Observation.objects.select_related("patient")
        patient_id = self.request.query_params.get("patient")
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)
        return queryset


@api_view(["GET"])
def migration_status(request):
    """Quick endpoint to check migration progress from the frontend."""
    patient_count = Patient.objects.count()
    observation_count = Observation.objects.count()
    return Response({
        "patients": patient_count,
        "observations": observation_count,
    })
