/**
 * Zones store — drawn regions on the active map.
 *
 * v1: read from load_full, write through createZone/deleteZone. The
 * canvas component reads `byMap[mapName]` to render the GeoJSON
 * source.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { call } from '../api/client.js'

export const useZonesStore = defineStore('zones', () => {
  /** Map name -> array of zone envelopes (geometry parsed to object). */
  const byMap = ref({})
  const saving = ref(false)
  const error = ref(null)

  function setForMap(mapName, zones) {
    if (!mapName) return
    byMap.value = { ...byMap.value, [mapName]: zones || [] }
  }

  function clearForMap(mapName) {
    if (!mapName) return
    const next = { ...byMap.value }
    delete next[mapName]
    byMap.value = next
  }

  const forActiveMap = computed(() => {
    return () => [] // bound at the component level via map store
  })

  async function createZone(mapName, payload) {
    if (!mapName) throw new Error('mapName required')
    saving.value = true
    error.value = null
    try {
      const created = await call(
        'expedition.api.zone.create_zone',
        { map_name: mapName, ...payload }
      )
      const list = byMap.value[mapName] || []
      byMap.value = { ...byMap.value, [mapName]: [...list, created] }
      return created
    } catch (e) {
      error.value = e
      throw e
    } finally {
      saving.value = false
    }
  }

  async function deleteZone(mapName, name) {
    if (!mapName || !name) return
    saving.value = true
    error.value = null
    const list = byMap.value[mapName] || []
    const deleted = list.find((z) => z.name === name) || null
    byMap.value = {
      ...byMap.value,
      [mapName]: list.filter((z) => z.name !== name),
    }
    try {
      await call('expedition.api.zone.delete_zone', { name })
    } catch (e) {
      if (deleted) {
        const current = byMap.value[mapName] || []
        byMap.value = { ...byMap.value, [mapName]: [...current, deleted] }
      }
      error.value = e
      throw e
    } finally {
      saving.value = false
    }
  }

  async function updateZone(mapName, name, fields) {
    if (!mapName || !name) throw new Error('zone name required')
    saving.value = true
    error.value = null
    try {
      const updated = await call('expedition.api.zone.update_zone', { name, ...fields })
      const list = byMap.value[mapName] || []
      byMap.value = {
        ...byMap.value,
        [mapName]: list.map((z) => z.name === name ? { ...z, ...updated } : z),
      }
      return updated
    } catch (e) {
      error.value = e
      throw e
    } finally {
      saving.value = false
    }
  }

  return {
    byMap,
    saving,
    error,
    setForMap,
    clearForMap,
    forActiveMap,
    createZone,
    deleteZone,
    updateZone,
  }
})
