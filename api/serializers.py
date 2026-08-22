"""DRF serializers for Patient and Observation models."""

from rest_framework import serializers

from api.models import Patient, Observation


class ObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Observation
        fields = [
            "fhir_id",
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
        ]


class PatientListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the patient list view — excludes heavy fields."""

    observation_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "fhir_id",
            "full_name",
            "given_name",
            "family_name",
            "gender",
            "birth_date",
            "observation_count",
        ]


class PatientDetailSerializer(serializers.ModelSerializer):
    """Full patient serializer with nested observations for the detail view."""

    observations = ObservationSerializer(many=True, read_only=True)

    class Meta:
        model = Patient
        fields = [
            "fhir_id",
            "identifier_system",
            "identifier_value",
            "active",
            "full_name",
            "given_name",
            "family_name",
            "gender",
            "birth_date",
            "migrated_at",
            "observations",
        ]
