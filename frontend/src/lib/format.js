export const EM_DASH = '—'

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/**
 * Parse an API date string into a Date, or null if it is missing/invalid.
 *
 * A bare `YYYY-MM-DD` is parsed as UTC midnight by the Date constructor, so
 * rendering it with a local-time formatter shifts it to the previous day for
 * anyone west of UTC (a 1952-01-01 birth date displayed as Dec 31, 1951).
 * Appending a time forces local-time parsing instead.
 */
function parseApiDate(value) {
  if (!value) return null
  const date = new Date(DATE_ONLY.test(value) ? `${value}T00:00:00` : value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDate(value) {
  const date = parseApiDate(value)
  if (!date) return EM_DASH
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(value) {
  const date = parseApiDate(value)
  if (!date) return EM_DASH
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Best available display name for a patient.
 * Synthetic records often carry no name at all, so the id is the last resort.
 */
export function patientDisplayName(patient) {
  return patient?.full_name || patient?.family_name || patient?.fhir_id || 'Unknown'
}

/**
 * Human label for an observation, preferring the coded display over free text.
 * `code_text` is often localised in the sandbox (e.g. "Glucosa en ayunas"),
 * so the LOINC display is the more consistent grouping key.
 */
export function observationLabel(observation) {
  return (
    observation.code_display ||
    observation.code_text ||
    observation.code ||
    'Uncoded observation'
  )
}

/** Render an observation value with its unit, e.g. `126 mg/dL`. */
export function formatQuantity(value, unit) {
  if (value === null || value === undefined) return EM_DASH
  const number = Number(value)
  const formatted = Number.isFinite(number)
    ? number.toLocaleString('en-US', { maximumFractionDigits: 6 })
    : String(value)
  return unit ? `${formatted} ${unit}` : formatted
}
