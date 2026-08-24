import { computed, ref, toValue, watch } from 'vue'
import { fetchPatient } from '@/api'
import { observationLabel } from '@/lib/format'

/**
 * A single patient with their observations, grouped by type.
 *
 * @param {import('vue').MaybeRefOrGetter<string>} id Patient FHIR id; reactive
 *   so navigating between patients refetches without remounting the view.
 * @returns {{
 *   patient: import('vue').Ref<object|null>,
 *   loading: import('vue').Ref<boolean>,
 *   error: import('vue').Ref<string|null>,
 *   observationGroups: import('vue').ComputedRef<Map<string, object[]>>,
 *   observationCount: import('vue').ComputedRef<number>,
 *   load: () => Promise<void>,
 * }}
 */
export function usePatient(id) {
  const patient = ref(null)
  const loading = ref(true)
  const error = ref(null)

  // Identifies the most recent request so a slower earlier response can't
  // overwrite a newer one when the id changes mid-flight.
  let currentRequest = 0

  /**
   * Observations grouped by type. Derived from `patient`, so it can never drift
   * out of sync with the loaded record. A Map keeps insertion order explicit —
   * the API returns observations newest first, and that order is meaningful.
   */
  const observationGroups = computed(() => {
    const groups = new Map()
    for (const observation of patient.value?.observations ?? []) {
      const key = observationLabel(observation)
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(observation)
    }
    return groups
  })

  const observationCount = computed(() => patient.value?.observations?.length ?? 0)

  async function load() {
    const requestId = ++currentRequest
    loading.value = true
    error.value = null
    try {
      const data = await fetchPatient(toValue(id))
      if (requestId !== currentRequest) return
      patient.value = data
    } catch (e) {
      if (requestId !== currentRequest) return
      // Drop the previous patient so a stale record can't render under the error.
      patient.value = null
      error.value = e.message
    } finally {
      if (requestId === currentRequest) loading.value = false
    }
  }

  // `immediate` covers the initial load, so the view needs no onMounted hook.
  watch(() => toValue(id), load, { immediate: true })

  return { patient, loading, error, observationGroups, observationCount, load }
}
