/**
 * Insights store — server-computed chips that float on the canvas.
 *
 * Lifecycle: loadActiveFor(mapName) is called after a map's loadFull
 * resolves. The store keeps a single record keyed by map so switching
 * maps back and forth doesn't re-fetch.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { call } from '../api/client.js'

const FETCH_TTL_MS = 60_000 // 1 min — server re-computes hourly

export const useInsightsStore = defineStore('insights', () => {
  /** @type {import('vue').Ref<Array<object>>} */
  const active = ref([])
  const loading = ref(false)
  const lastFetched = ref(0)
  const lastMapName = ref(null)
  const error = ref(null)

  async function loadActiveFor(mapName) {
    if (!mapName) {
      active.value = []
      lastMapName.value = null
      return
    }
    const fresh =
      mapName === lastMapName.value &&
      Date.now() - lastFetched.value < FETCH_TTL_MS
    if (fresh) return
    lastMapName.value = mapName
    loading.value = true
    error.value = null
    try {
      const rows = await call(
        'expedition.api.insight.get_active_for_map',
        { map_name: mapName }
      )
      active.value = Array.isArray(rows) ? rows : []
      lastFetched.value = Date.now()
    } catch (e) {
      error.value = e
      // Insights are an enhancement — never block the canvas.
      active.value = []
    } finally {
      loading.value = false
    }
  }

  function clear() {
    active.value = []
    lastFetched.value = 0
    lastMapName.value = null
    error.value = null
  }

  return { active, loading, error, loadActiveFor, clear }
})
