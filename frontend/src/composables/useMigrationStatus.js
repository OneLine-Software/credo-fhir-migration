import { computed, onMounted, ref } from 'vue'
import { fetchMigrationStatus } from '@/api'

/**
 * Migration counts and the state of the most recent run.
 *
 * Failures are swallowed deliberately: the status line is supplementary, and a
 * blank header is better than an error banner over a page that loaded fine.
 *
 * @returns {{
 *   status: import('vue').Ref<object|null>,
 *   unfinishedRun: import('vue').ComputedRef<object|null>,
 *   load: () => Promise<void>,
 * }}
 */
export function useMigrationStatus() {
  const status = ref(null)

  // Only surfaced when the last run didn't finish — otherwise the counts
  // already tell the whole story.
  const unfinishedRun = computed(() => {
    const run = status.value?.last_run
    return run && run.status !== 'complete' ? run : null
  })

  async function load() {
    try {
      status.value = await fetchMigrationStatus()
    } catch {
      status.value = null
    }
  }

  onMounted(load)

  return { status, unfinishedRun, load }
}
