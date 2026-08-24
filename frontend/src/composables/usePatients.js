import { computed, onMounted, ref } from 'vue'
import { fetchPatients } from '@/api'

/**
 * Paginated patient list.
 *
 * Owns fetching, pagination and error state so the view only renders. Page
 * navigation goes through `nextPage`/`previousPage` rather than exposing the
 * page number as a writable ref — the page and the data it describes have to
 * change together, and a caller setting `page` directly could desync them.
 *
 * @returns {{
 *   patients: import('vue').Ref<object[]>,
 *   loading: import('vue').Ref<boolean>,
 *   error: import('vue').Ref<string|null>,
 *   page: import('vue').Ref<number>,
 *   count: import('vue').Ref<number>,
 *   isEmpty: import('vue').ComputedRef<boolean>,
 *   hasNextPage: import('vue').ComputedRef<boolean>,
 *   hasPreviousPage: import('vue').ComputedRef<boolean>,
 *   load: (targetPage?: number) => Promise<void>,
 *   reload: () => Promise<void>,
 *   nextPage: () => Promise<void>,
 *   previousPage: () => Promise<void>,
 * }}
 */
export function usePatients() {
  const patients = ref([])
  const loading = ref(true)
  const error = ref(null)
  const page = ref(1)
  const count = ref(0)
  const nextUrl = ref(null)

  const hasNextPage = computed(() => Boolean(nextUrl.value))
  const hasPreviousPage = computed(() => page.value > 1)
  const isEmpty = computed(() => !loading.value && !error.value && !patients.value.length)

  async function load(targetPage = 1) {
    loading.value = true
    error.value = null
    try {
      const data = await fetchPatients(targetPage)
      patients.value = data.results
      count.value = data.count
      nextUrl.value = data.next
      page.value = targetPage
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  const reload = () => load(page.value)
  const nextPage = () => (hasNextPage.value ? load(page.value + 1) : Promise.resolve())
  const previousPage = () => (hasPreviousPage.value ? load(page.value - 1) : Promise.resolve())

  onMounted(load)

  return {
    patients,
    loading,
    error,
    page,
    count,
    isEmpty,
    hasNextPage,
    hasPreviousPage,
    load,
    reload,
    nextPage,
    previousPage,
  }
}
