/**
 * Map store — owns the active Expedition Map and the viewport.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { call } from '../api/client.js'
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

  async function bootstrap() {
    // Try the user's "recent" list first; if anything goes wrong (e.g.
    // a guest visitor where list_for_user returns 403) fall through to
    // the public templates so the canvas never lands empty.
    try {
      recent.value = await call('expedition.api.map.list_for_user', { include_public: 1 })
    } catch (e) {
      console.warn('[expedition] list_for_user unavailable; using templates', e)
      recent.value = []
    }
    if (recent.value.length > 0) {
      await switchMap(recent.value[0].name)
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
      layerStore.layers = payload.layers
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
    layerStore.features = {}
    layerStore.loading = {}
    layerStore.lastFetched = {}
    layerStore.bounds = {}
    // Sync basemap skin from the map doc, if it points at a gallery id.
    const skinId = resolveSkinIdFromMap(payload?.map)
    if (skinId) ui.currentSkinId = skinId
    // Remember this map in the user's recent list (left-rail switcher).
    if (payload?.map) ui.rememberRecent(payload.map)
    // Refresh server-computed insights for this map. Fire-and-forget —
    // never block the canvas on insight fetch.
    insights.loadActiveFor(payload?.map?.name || name)
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

  return { activeMap, templates, recent, bootstrap, switchMap, cloneTemplate }
})
