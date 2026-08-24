"""Transform FHIR R4 resources into our simplified internal model dicts."""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# Mirrors Observation.value = DecimalField(max_digits=19, decimal_places=6).
VALUE_MAX_DIGITS = 19
VALUE_DECIMAL_PLACES = 6
VALUE_MAX_MAGNITUDE = Decimal(10) ** (VALUE_MAX_DIGITS - VALUE_DECIMAL_PLACES)


def _safe_get(obj, *keys, default=None):
    """Traverse nested dict/list structures safely, returning default if any key is missing."""
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def _reference_id(reference, resource_type):
    """
    Extract the logical id from a FHIR reference.

    Handles the relative form ("Patient/123"), the absolute form
    ("http://host/baseR4/Patient/123") and versioned references
    ("Patient/123/_history/2"). Contained references ("#p1") and
    identifier-only references (no `reference` element at all) have no
    resolvable id here, so they return None and the caller skips the resource.
    """
    if not isinstance(reference, str) or not reference or reference.startswith("#"):
        return None
    prefix = f"{resource_type}/"
    without_version = reference.split("/_history/")[0]
    if prefix not in without_version:
        return None
    return without_version.rsplit(prefix, 1)[1].strip("/") or None


def _parse_fhir_date(date_str):
    """Parse a FHIR date string (YYYY-MM-DD) into a date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_fhir_datetime(dt_str):
    """Parse a FHIR dateTime/instant string into a timezone-aware datetime."""
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


def _parse_decimal(value, observation_id=None):
    """
    Convert a FHIR decimal into a Decimal the model can store.

    Goes via str() rather than passing the float straight to the ORM: Django
    would otherwise build the Decimal from the binary float and inherit its
    representation error. Values too large for the column are dropped rather
    than raising mid-batch — the value itself is never logged, only the id.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("Observation %s has a non-numeric value; storing null", observation_id)
        return None
    if not parsed.is_finite() or abs(parsed) >= VALUE_MAX_MAGNITUDE:
        logger.warning(
            "Observation %s value is outside the storable range; storing null", observation_id
        )
        return None
    return parsed


def transform_patient(fhir_patient):
    """
    Convert a FHIR R4 Patient resource into a flat dict matching our model.

    FHIR Patient fields are nested arrays (name, identifier). We take
    the first element of each — the primary name/identifier. A production
    system would need separate tables for multiple names/identifiers.
    """
    name = _safe_get(fhir_patient, "name", 0, default={}) or {}
    identifier = _safe_get(fhir_patient, "identifier", 0, default={}) or {}

    given_names = [part for part in (name.get("given") or []) if part]
    given_name = given_names[0] if given_names else None
    family_name = _safe_get(name, "family")
    full_name = _safe_get(name, "text") or " ".join(given_names + [family_name or ""]).strip() or None

    # FHIR treats an absent `active` as "no assertion made" rather than false; we
    # follow the model default. An explicit null must not reach the NOT NULL column.
    active = fhir_patient.get("active")

    return {
        "fhir_id": fhir_patient.get("id"),
        "identifier_system": identifier.get("system"),
        "identifier_value": identifier.get("value"),
        "active": True if active is None else bool(active),
        "family_name": family_name,
        "given_name": given_name,
        "full_name": full_name,
        "gender": fhir_patient.get("gender"),
        "birth_date": _parse_fhir_date(fhir_patient.get("birthDate")),
    }


def transform_observation(fhir_observation):
    """
    Convert a FHIR R4 Observation resource into a flat dict matching our model.

    The subject reference gives us the patient foreign key. Not all
    observations carry a valueQuantity (panel observations such as blood
    pressure use component arrays instead), so value is nullable.
    """
    observation_id = fhir_observation.get("id")
    subject_ref = _safe_get(fhir_observation, "subject", "reference")

    coding = _safe_get(fhir_observation, "code", "coding", 0, default={}) or {}
    category = _safe_get(fhir_observation, "category", 0, "coding", 0, default={}) or {}
    value_quantity = _safe_get(fhir_observation, "valueQuantity", default={}) or {}

    return {
        "fhir_id": observation_id,
        "patient_id": _reference_id(subject_ref, "Patient"),
        "status": fhir_observation.get("status") or "unknown",
        "category": category.get("code"),
        "code_system": coding.get("system"),
        "code": coding.get("code"),
        "code_display": coding.get("display"),
        "code_text": _safe_get(fhir_observation, "code", "text"),
        "effective_date": _observation_effective_date(fhir_observation),
        "value": _parse_decimal(value_quantity.get("value"), observation_id),
        "value_unit": value_quantity.get("unit"),
        "value_system": value_quantity.get("system"),
        "value_code": value_quantity.get("code"),
    }


def _observation_effective_date(fhir_observation):
    """
    Resolve the clinically relevant timestamp for an observation.

    R4 allows effective[x] to be a dateTime, Period, Timing or instant. Reading
    only effectiveDateTime leaves period-based results (common for timed
    collections) with a null date, which then sort to the bottom of the
    patient timeline. Timing is deliberately not supported — it describes a
    schedule rather than a single point in time.
    """
    return (
        _parse_fhir_datetime(fhir_observation.get("effectiveDateTime"))
        or _parse_fhir_datetime(fhir_observation.get("effectiveInstant"))
        or _parse_fhir_datetime(_safe_get(fhir_observation, "effectivePeriod", "start"))
    )
