/**
 * Layer store — owns the map's layers and per-layer feature caches.
 *
 * v1.1: layer CRUD. The store now exposes addLayer, updateLayer,
 * removeLayer, and reorderLayers which call the server and update the
 * local state in one place. UI components bind to `layers` and never
 * mutate it directly.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { call } from '../api/client.js'
import { useUiStore } from './ui.js'

export const useLayersStore = defineStore('layers', () => {
  const layers = ref([])            // array of {name, title, style, ...}
  const masters = ref([])           // Expedition Layer rows with map=NULL (master mappings)
  const features = ref({})          // layer.name -> GeoJSON FeatureCollection
  const loading = ref({})           // layer.name -> bool
  const lastFetched = ref({})       // layer.name -> timestamp (for debugging + future cache busting)
  const lastBounds = ref({})        // layer.name -> {south, west, north, east} from the most recent fetch
  // Per-layer lat/lng envelope of every row in the source DocType
  // (after the layer's filter). Sourced from the server's
  // get_layer_bounds endpoint, cached for the session. Lets
  // `fitToData(mode='all')` frame the full data without pulling
  // every row across the wire.
  const bounds = ref({})            // layer.name -> {south, west, north, east, count} | null = in-flight
  // Source-field cache, keyed by DocType name. The LayerEditor reads
  // this so opening / closing / re-opening the editor for the same
  // source_doctype doesn't re-hit the server. Invalidate via
  // `invalidateSourceFields(dt)` if the schema changes mid-session.
  const sourceFields = ref({})      // doctype -> [{fieldname, fieldtype, label}] | null = pending

  const visibleLayers = computed(() => layers.value.filter(l => l.enabled !== 0))

  const featureListeners = new Set()
  function onFeaturesUpdated(fn) {
    featureListeners.add(fn)
    return () => featureListeners.delete(fn)
  }
  function _emitFeaturesUpdated(layerName) {
    for (const fn of featureListeners) {
      try { fn(layerName) } catch (e) { console.error('[layers] listener error', e) }
    }
  }
  function _emitLayersChanged() {
    for (const fn of featureListeners) {
      try { fn(null) } catch (e) { console.error('[layers] listener error', e) }
    }
  }

  function _promoteIds(fc) {
    if (!fc || !Array.isArray(fc.features)) return fc
    for (const f of fc.features) {
      if (f && f.properties && f.properties._name != null && f.id == null) {
        f.id = f.properties._name
      }
    }
    return fc
  }

  function _replaceLayer(dto) {
    const idx = layers.value.findIndex((l) => l.name === dto.name)
    if (idx >= 0) {
      layers.value.splice(idx, 1, { ...layers.value[idx], ...dto, style: dto.style || layers.value[idx].style })
    } else {
      layers.value.push({ ...dto, style: dto.style || _styleFromDto(dto) })
    }
    // Reorder by sequence so the visible order is correct.
    layers.value.sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
    // Sync radius halo state from the layer doc. Each layer's
    // radius_* fields are the source of truth; we mirror them into
    // the ui store so the canvas can read them without coupling.
    const ui = useUiStore()
    if (dto.radius_enabled != null) ui.setRadius(dto.name, !!dto.radius_enabled)
    if (dto.radius_field != null) ui.setRadiusField(dto.name, dto.radius_field)
    if (dto.radius_meters != null) ui.setRadiusMeters(dto.name, dto.radius_meters)
    if (dto.heatmap != null) ui.setHeatmap(dto.name, !!dto.heatmap)
  }

  function replaceMapLayers(dtos) {
    layers.value = []
    for (const dto of dtos || []) _replaceLayer(dto)
    _emitLayersChanged()
  }

  function previewLayerFields(layerName, fields) {
    const idx = layers.value.findIndex((l) => l.name === layerName)
    if (idx < 0) return
    const current = layers.value[idx]
    const nextStyle = {
      ...(current.style || _styleFromDto(current)),
      ..._styleFromDto({ ...current, ...fields }),
    }
    layers.value.splice(idx, 1, { ...current, ...fields, style: nextStyle })
    _emitLayersChanged()
  }

  function previewGroupStyles(layerName, groupConfig) {
    const fc = features.value[layerName]
    if (!fc || !Array.isArray(fc.features)) return
    const nextFeatures = fc.features.map((feature) => {
      const props = { ...(feature.properties || {}) }
      const groupValue = props._group_value
      if (groupValue == null) return feature
      const cfg = groupConfig?.[String(groupValue)]
      delete props._color
      delete props._icon
      delete props._icon_disabled
      if (cfg?.color) props._color = cfg.color
      if (cfg?.icon === '__none') {
        props._icon_disabled = 1
      } else if (cfg?.icon) {
        props._icon = cfg.icon
      }
      return { ...feature, properties: props }
    })
    features.value[layerName] = { ...fc, features: nextFeatures }
    _emitFeaturesUpdated(layerName)
  }

  function _styleFromDto(d) {
    return {
      color: d.color, icon: d.icon, size: d.size,
      cluster: d.cluster, heatmap: d.heatmap,
      stroke_color: d.stroke_color, stroke_width: d.stroke_width,
      fill_opacity: d.fill_opacity,
    }
  }

  async function fetchFeatures(layerName, bounds) {
    if (loading.value[layerName]) return
    loading.value[layerName] = true
    useUiStore().beginFetch()
    try {
      const fc = await call('expedition.api.layer.get_features', { layer: layerName, bounds })
      _promoteIds(fc)
      features.value[layerName] = fc
      lastFetched.value[layerName] = Date.now()
      if (bounds) lastBounds.value[layerName] = bounds
      _emitFeaturesUpdated(layerName)
    } finally {
      loading.value[layerName] = false
      useUiStore().endFetch()
    }
  }

  // Re-fetch using the bounds from the most recent viewport (if any).
  // Used after a filter / config change so the map reflects server-side
  // truth without the user having to pan. Falls back to the live
  // map viewport so the layer refreshes even on first paint (e.g.
  // after the user edits popup_fields and saves).
  async function refetchLayer(layerName) {
    const b = lastBounds.value[layerName]
    if (b) {
      await fetchFeatures(layerName, b)
    } else if (window.expeditionMap?.getMap) {
      const m = window.expeditionMap.getMap()
      if (m && m.getBounds) {
        const b2 = m.getBounds()
        await fetchFeatures(layerName, {
          south: b2.getSouth(),
          west: b2.getWest(),
          north: b2.getNorth(),
          east: b2.getEast(),
        })
      }
    }
  }

  function getLayerStyle(layerName) {
    const layer = layers.value.find((l) => l.name === layerName)
    if (layer?.style) return layer.style
    const fc = features.value[layerName]
    if (fc && fc.layer && fc.layer.style) return fc.layer.style
    return {}
  }

  /**
   * Return the cached source-field list for a DocType, fetching from
   * the server on first call. Subsequent calls (including from
   * LayerEditor) hit the cache. Set `force=true` to bypass the cache.
   * Returns [] for empty / unknown DocTypes; null while in-flight.
   */
  async function getSourceFields(sourceDoctype, { force = false } = {}) {
    if (!sourceDoctype) return []
    if (!force && sourceFields.value[sourceDoctype] !== undefined) {
      return sourceFields.value[sourceDoctype] || []
    }
    // Mark as in-flight (null) so concurrent callers don't all hit
    // the server.
    sourceFields.value = { ...sourceFields.value, [sourceDoctype]: null }
    try {
      const fields = await call('expedition.api.layer.list_source_fields', {
        source_doctype: sourceDoctype,
      })
      sourceFields.value = { ...sourceFields.value, [sourceDoctype]: Array.isArray(fields) ? fields : [] }
      return sourceFields.value[sourceDoctype]
    } catch (e) {
      // Drop the in-flight marker on error so a retry can try again.
      const next = { ...sourceFields.value }
      delete next[sourceDoctype]
      sourceFields.value = next
      throw e
    }
  }

  function invalidateSourceFields(sourceDoctype) {
    if (!sourceDoctype) return
    const next = { ...sourceFields.value }
    delete next[sourceDoctype]
    sourceFields.value = next
  }

  function clearFeatures(layerName) {
    delete features.value[layerName]
    delete loading.value[layerName]
    delete lastFetched.value[layerName]
    _emitFeaturesUpdated(layerName)
  }

  /**
   * Return the cached lat/lng envelope for a layer, fetching from
   * the server on first call. Subsequent calls (including from
   * `fitToData(mode='all')`) hit the cache. Set `force=true` to
   * bypass.
   *
   * Tri-state matches `getSourceFields`:
   *   undefined key = never fetched
   *   null value    = in flight
   *   object value  = { south, west, north, east, count }
   */
  async function fetchBounds(layerName, { force = false } = {}) {
    if (!layerName) return null
    if (!force && bounds.value[layerName] !== undefined) {
      return bounds.value[layerName] || null
    }
    bounds.value = { ...bounds.value, [layerName]: null }
    try {
      const b = await call(
        'expedition.api.layer.get_layer_bounds',
        { layer: layerName },
      )
      const safe = b && typeof b.south === 'number'
        ? b
        : { south: 0, west: 0, north: 0, east: 0, count: 0 }
      bounds.value = { ...bounds.value, [layerName]: safe }
      return safe
    } catch (e) {
      // Drop the in-flight marker so a retry can try again.
      const next = { ...bounds.value }
      delete next[layerName]
      bounds.value = next
      throw e
    }
  }

  function clearBounds(layerName) {
    const next = { ...bounds.value }
    delete next[layerName]
    bounds.value = next
  }

  // ---- CRUD ----

  async function addLayer(payload) {
    const dto = await call('expedition.api.layer.create', payload)
    _replaceLayer(dto)
    _emitLayersChanged()
    return dto
  }

  async function updateLayer(layerName, fields) {
    const dto = await call('expedition.api.layer.update', { layer_name: layerName, ...fields })
    _replaceLayer(dto)
    // Filter / color / etc. changed — re-fetch so the map reflects them.
    // The server may return a different feature set after filter changes,
    // so we drop the cache and refetch with the last known bounds. If
    // we've never fetched (e.g. map just opened), we just clear and
    // let the next moveend populate it.
    clearFeatures(layerName)
    // Same for the bounds envelope — filter / source / lat-lng
    // changes invalidate it. Fire-and-forget; UI falls back to the
    // feature cache while the new bounds are in flight.
    clearBounds(layerName)
    fetchBounds(layerName).catch(() => {})
    _emitLayersChanged()
    await refetchLayer(layerName)
    return dto
  }

  async function removeLayer(layerName) {
    await call('expedition.api.layer.delete', { layer_name: layerName })
    layers.value = layers.value.filter((l) => l.name !== layerName)
    clearFeatures(layerName)
    clearBounds(layerName)
    _emitLayersChanged()
  }

  async function reorderLayers(mapName, layerNames) {
    await call('expedition.api.layer.reorder', { map_name: mapName, layer_names: layerNames })
    // Re-apply the new order locally
    const map = new Map(layerNames.map((n, i) => [n, i + 1]))
    layers.value.forEach((l) => {
      if (map.has(l.name)) l.sequence = map.get(l.name)
    })
    layers.value.sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
    _emitLayersChanged()
  }

  // ---- Master mappings (template layers, map=NULL) ----
  //
  // Masters are reusable layer definitions. Attaching a master to a map
  // creates a per-map instance that inherits the master's display fields
  // (color, size, icon, etc.). The instance is an independent copy — edits
  // to the master do not cascade.

  async function loadMasters() {
    masters.value = await call('expedition.api.layer.list_masters', {})
    return masters.value
  }

  async function addMaster(payload) {
    const dto = await call('expedition.api.layer.create_master', payload)
    masters.value = [...masters.value, dto].sort(
      (a, b) =>
        (a.source_doctype || '').localeCompare(b.source_doctype || '') ||
        (a.title || '').localeCompare(b.title || '')
    )
    return dto
  }

  async function updateMaster(name, fields) {
    const dto = await call('expedition.api.layer.update', { layer_name: name, ...fields })
    const idx = masters.value.findIndex((m) => m.name === name)
    if (idx >= 0) masters.value.splice(idx, 1, { ...masters.value[idx], ...dto })
    return dto
  }

  async function removeMaster(name) {
    await call('expedition.api.layer.delete', { layer_name: name })
    masters.value = masters.value.filter((m) => m.name !== name)
  }

  async function attachToMap(masterName, mapName) {
    let dto = await call('expedition.api.layer.attach_to_map', {
      master_name: masterName,
      map_name: mapName,
    })
    _replaceLayer(dto)
    _emitLayersChanged()
    if (dto.enabled === 0 || dto.enabled === false) {
      dto = await updateLayer(dto.name, { enabled: 1 })
    }
    if (dto.enabled !== 0 && dto.enabled !== false) {
      clearFeatures(dto.name)
      clearBounds(dto.name)
      fetchBounds(dto.name).catch(() => {})
      await fetchFeatures(dto.name, null)
    }
    return dto
  }

  return {
    layers,
    masters,
    features,
    loading,
    lastFetched,
    visibleLayers,
    fetchFeatures,
    refetchLayer,
    getLayerStyle,
    clearFeatures,
    bounds,
    fetchBounds,
    clearBounds,
    onFeaturesUpdated,
    addLayer,
    updateLayer,
    removeLayer,
    reorderLayers,
    replaceMapLayers,
    previewLayerFields,
    previewGroupStyles,
    loadMasters,
    addMaster,
    updateMaster,
    removeMaster,
    attachToMap,
    getSourceFields,
    invalidateSourceFields,
    sourceFields,
  }
})
