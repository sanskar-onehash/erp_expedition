/**
 * Map store — owns the active Expedition Map and the viewport.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { call } from '../api/client.js'
import { wrapLng } from '../lib/geo.js'
import { useLayersStore } from './layers.js'
import { useUiStore } from './ui.js'
import { useInsightsStore } from './insights.js'
import { useZonesStore } from './zones.js'
import { SKINS } from '../api/skins.js'

// Maps the legacy `basemap_style` enum (light / dark / monochrome) onto
// the new gallery. Older Expedition Map docs may still hold these values.
const LEGACY_TO_SKIN = {
  light: 'ofm-positron',
  dark: 'ofm-dark',
  monochrome: 'ofm-liberty',
}
const GALLERY_IDS = new Set(SKINS.map((s) => s.id))
function resolveSkinIdFromMap(mapRow) {
  const v = mapRow?.basemap_style
  if (!v) return null
  if (GALLERY_IDS.has(v)) return v
  return LEGACY_TO_SKIN[v] || null
}

export const useMapStore = defineStore('map', () => {
  const activeMap = ref(null)
  const templates = ref([])
  const recent = ref([])
  const sharedUsers = ref([])

  async function refreshMaps(search = '') {
    recent.value = await call('expedition.api.map.list_for_user', {
      include_public: 1,
      search,
    })
    return recent.value
  }

  async function bootstrap() {
    const ui = useUiStore()
    // Try the user's "recent" list first; if anything goes wrong (e.g.
    // a guest visitor where list_for_user returns 403) fall through to
    // the public templates so the canvas never lands empty.
    try {
      await refreshMaps()
    } catch (e) {
      console.warn('[expedition] list_for_user unavailable; using templates', e)
      recent.value = []
    }

    const localRecentName = ui.prefs.openRecentOnLaunch
      ? ui.recent?.[0]?.name
      : null
    if (localRecentName) {
      try {
        await switchMap(localRecentName)
        return
      } catch (e) {
        console.warn('[expedition] remembered map unavailable; falling back', e)
      }
    }

    const fallbackName = recent.value.find((row) => row?.name && row.name !== localRecentName)?.name
    if (fallbackName) {
      await switchMap(fallbackName)
    } else {
      // Fall back to the first template so the canvas never lands empty.
      templates.value = await call('expedition.api.map.list_templates')
      if (templates.value.length > 0) {
        await switchMap(templates.value[0].name)
      }
    }
  }

  async function switchMap(name) {
    /**
     * Open a map and reset the per-map state. Used both for the initial
     * bootstrap and for the map switcher in the left rail.
     *
     * load_full returns the full envelope: { map, layers, zones }.
     * We stash the whole envelope on activeMap AND seed the layer
     * store in the same step.
     */
    const layerStore = useLayersStore()
    const ui = useUiStore()
    const insights = useInsightsStore()
    const zones = useZonesStore()
    const payload = await call('expedition.api.map.load_full', { name })
    activeMap.value = payload
    if (payload && Array.isArray(payload.layers)) {
      layerStore.replaceMapLayers(payload.layers)
    }
    // Zones: load_full ships geometry as a JSON string; parse once
    // so the canvas can pass it to MapLibre without re-decoding.
    if (payload && Array.isArray(payload.zones)) {
      zones.setForMap(
        payload.map?.name || name,
        payload.zones.map((z) => ({
          ...z,
          geometry:
            typeof z.geometry === 'string' ? JSON.parse(z.geometry) : z.geometry,
        }))
      )
    }
    // Clear per-map transient state so the right rail / hover rings
    // don't carry over from the previous map.
    ui.selectedFeature = null
    ui.clearSelection()
    // Sync basemap skin from the map doc, if it points at a gallery id.
    const skinId = resolveSkinIdFromMap(payload?.map)
    if (skinId) ui.currentSkinId = skinId
    // Remember this map in the user's recent list (left-rail switcher).
    if (payload?.map) ui.rememberRecent(payload.map)
    // Refresh server-computed insights for this map. Fire-and-forget —
    // never block the canvas on insight fetch.
    insights.loadActiveFor(payload?.map?.name || name)
    loadSharedUsers(payload?.map?.name || name).catch(() => {
      sharedUsers.value = []
    })
  }

  /** Clone a template map into the user's account. Switches to the clone. */
  async function cloneTemplate(templateName, title) {
    const dto = await call('expedition.api.map.clone_template', {
      template_name: templateName,
      title,
    })
    await switchMap(dto.name)
    return dto
  }

  function currentViewport() {
    const m = window.expeditionMap?.getMap?.()
    if (!m) return null
    const center = m.getCenter?.()
    return {
      center: center ? [wrapLng(center.lng), center.lat] : undefined,
      zoom: m.getZoom?.(),
      bearing: m.getBearing?.(),
      pitch: m.getPitch?.(),
    }
  }

  async function createBlankMap({ title, basemap_style, is_public = 0, public_access = 'Read Only' } = {}) {
    const dto = await call('expedition.api.map.create_blank_map', {
      title,
      basemap_style,
      is_public,
      public_access,
      viewport: currentViewport(),
    })
    await refreshMaps()
    await switchMap(dto.name)
    return dto
  }

  async function updateActiveMap(fields = {}) {
    const name = activeMap.value?.map?.name
    if (!name) throw new Error('No active map')
    const dto = await call('expedition.api.map.update_map', {
      name,
      ...fields,
      viewport: fields.viewport === undefined ? currentViewport() : fields.viewport,
    })
    activeMap.value = {
      ...activeMap.value,
      map: {
        ...activeMap.value.map,
        ...dto,
      },
    }
    await refreshMaps()
    return dto
  }

  async function loadSharedUsers(name = activeMap.value?.map?.name) {
    if (!name) {
      sharedUsers.value = []
      return sharedUsers.value
    }
    sharedUsers.value = await call('expedition.api.map.get_shared_users', { name })
    return sharedUsers.value
  }

  async function shareActiveMap(users) {
    const name = activeMap.value?.map?.name
    if (!name) throw new Error('No active map')
    sharedUsers.value = await call('expedition.api.map.share_map', { name, users })
    return sharedUsers.value
  }

  return {
    activeMap,
    templates,
    recent,
    sharedUsers,
    bootstrap,
    refreshMaps,
    switchMap,
    cloneTemplate,
    createBlankMap,
    updateActiveMap,
    loadSharedUsers,
    shareActiveMap,
  }
})
