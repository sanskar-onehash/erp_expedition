/**
 * Manual map pins — lightweight annotations on top of data layers.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { call } from '../api/client.js'

export const usePinsStore = defineStore('pins', () => {
  const byMap = ref({})
  const saving = ref(false)
  const error = ref(null)

  function setForMap(mapName, pins) {
    if (!mapName) return
    byMap.value = { ...byMap.value, [mapName]: pins || [] }
  }

  async function createPin(mapName, payload) {
    if (!mapName) throw new Error('mapName required')
    saving.value = true
    error.value = null
    try {
      const created = await call('expedition.api.pin.create_pin', {
        map_name: mapName,
        ...payload,
      })
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

  async function updatePin(mapName, name, fields) {
    if (!mapName || !name) throw new Error('pin name required')
    saving.value = true
    error.value = null
    try {
      const updated = await call('expedition.api.pin.update_pin', { name, ...fields })
      const list = byMap.value[mapName] || []
      byMap.value = {
        ...byMap.value,
        [mapName]: list.map((pin) => pin.name === name ? { ...pin, ...updated } : pin),
      }
      return updated
    } catch (e) {
      error.value = e
      throw e
    } finally {
      saving.value = false
    }
  }

  async function deletePin(mapName, name) {
    if (!mapName || !name) return
    saving.value = true
    error.value = null
    const list = byMap.value[mapName] || []
    const deleted = list.find((pin) => pin.name === name) || null
    byMap.value = {
      ...byMap.value,
      [mapName]: list.filter((pin) => pin.name !== name),
    }
    try {
      await call('expedition.api.pin.delete_pin', { name })
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

  return {
    byMap,
    saving,
    error,
    setForMap,
    createPin,
    updatePin,
    deletePin,
  }
})
