"""Transform FHIR R4 resources into our simplified internal model dicts."""

from datetime import datetime, timezone


def _safe_get(obj, *keys, default=None):
    """Traverse nested dict keys safely, returning default if any key is missing."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _parse_fhir_date(date_str):
    """Parse a FHIR date string (YYYY-MM-DD) into a date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_fhir_datetime(dt_str):
    """Parse a FHIR dateTime string into a timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        # FHIR datetimes can be "2024-01-01" or "2024-01-01T12:00:00+00:00"
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        # Ensure timezone-aware (Django USE_TZ=True expects aware datetimes)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def transform_patient(fhir_patient):
    """
    Convert a FHIR R4 Patient resource into a flat dict matching our model.

    FHIR Patient fields are nested arrays (name, identifier). We take
    the first element of each — the primary name/identifier. A production
    system would need separate tables for multiple names/identifiers.
    """
    name = _safe_get(fhir_patient, "name", 0, default={}) or {}
    identifier = _safe_get(fhir_patient, "identifier", 0, default={}) or {}

    given_name = _safe_get(name, "given", 0)
    family_name = _safe_get(name, "family")
    full_name = _safe_get(name, "text") or (
        f"{given_name} {family_name}".strip() if (given_name or family_name) else None
    )

    return {
        "fhir_id": fhir_patient.get("id"),
        "identifier_system": identifier.get("system"),
        "identifier_value": identifier.get("value"),
        "active": fhir_patient.get("active", True),
        "family_name": family_name,
        "given_name": given_name,
        "full_name": full_name,
        "gender": fhir_patient.get("gender"),
        "birth_date": _parse_fhir_date(fhir_patient.get("birthDate")),
    }


def transform_observation(fhir_observation):
    """
    Convert a FHIR R4 Observation resource into a flat dict matching our model.

    The subject.reference field is "Patient/{id}" — we strip the prefix
    to get the patient foreign key. Not all observations have valueQuantity
    (panel observations may use component arrays), so value is nullable.
    """
    subject_ref = _safe_get(fhir_observation, "subject", "reference", default="")
    patient_id = subject_ref.replace("Patient/", "") if subject_ref else None

    coding = _safe_get(fhir_observation, "code", "coding", 0, default={}) or {}
    category = _safe_get(fhir_observation, "category", 0, "coding", 0, default={}) or {}
    value_quantity = _safe_get(fhir_observation, "valueQuantity", default={}) or {}

    return {
        "fhir_id": fhir_observation.get("id"),
        "patient_id": patient_id,
        "status": fhir_observation.get("status", "unknown"),
        "category": category.get("code"),
        "code_system": coding.get("system"),
        "code": coding.get("code"),
        "code_display": coding.get("display"),
        "code_text": _safe_get(fhir_observation, "code", "text"),
        "effective_date": _parse_fhir_datetime(fhir_observation.get("effectiveDateTime")),
        "value": value_quantity.get("value"),
        "value_unit": value_quantity.get("unit"),
        "value_system": value_quantity.get("system"),
        "value_code": value_quantity.get("code"),
    }
