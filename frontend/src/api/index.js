const API_BASE = '/api'

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

export function fetchPatients(page = 1) {
  return fetchJson(`${API_BASE}/patients/?page=${page}`)
}

export function fetchPatient(id) {
  return fetchJson(`${API_BASE}/patients/${id}/`)
}

export function fetchMigrationStatus() {
  return fetchJson(`${API_BASE}/migration-status/`)
}
