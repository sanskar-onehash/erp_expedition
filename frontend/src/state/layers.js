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
import {
  applyParsedMapSearchToFeatureCollection,
  countParsedMapSearchMatches,
  describeParsedMapSearch,
  parseMapSearch,
} from '../lib/mapSearch.js'
import { viewportBoundsForServer } from '../lib/geo.js'

export const useLayersStore = defineStore('layers', () => {
  const layers = ref([])            // array of {name, title, style, ...}
  const masters = ref([])           // Expedition Layer rows with map=NULL (master mappings)
  const features = ref({})          // layer.name -> GeoJSON FeatureCollection
  const territoryFeatures = ref({}) // layer/group cache for territory drawing; unbounded by viewport
  const unfilteredFeatures = ref({}) // layer.name -> original GeoJSON while search is active
  const loading = ref({})           // layer.name -> bool
  const lastFetched = ref({})       // layer.name -> timestamp (for debugging + future cache busting)
  const lastBounds = ref({})        // layer.name -> {south, west, north, east} from the most recent fetch
  const mapGeneration = ref(0)      // increments whenever the active map's layer set is replaced
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
  const activeSearch = ref(null)    // { raw, parsed, summary, total, layerCounts }

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
      if (f && f.properties && f.id != null && f.properties._id == null) {
        f.properties._id = f.id
      }
    }
    return fc
  }

  function _baseFeatures(layerName) {
    return unfilteredFeatures.value[layerName] || features.value[layerName]
  }

  function _filteredFeatureCollection(layerName, fc) {
    const parsed = activeSearch.value?.parsed
    if (!parsed) return fc
    const parentLayerName = String(layerName || '').split('__grp__')[0]
    const layer = layers.value.find((l) => l.name === layerName || l.name === parentLayerName)
    const fields = sourceFields.value[layer?.source_doctype] || []
    return applyParsedMapSearchToFeatureCollection(fc, layer, fields, parsed, _searchContext(parentLayerName))
  }

  function _searchContext(layerName) {
    return {
      textMatchesByQuery: activeSearch.value?.textMatches?.[layerName] || {},
    }
  }

  function _setFeatures(layerName, fc) {
    if (activeSearch.value?.parsed) {
      unfilteredFeatures.value = { ...unfilteredFeatures.value, [layerName]: fc }
      features.value[layerName] = _filteredFeatureCollection(layerName, fc)
    } else {
      features.value[layerName] = fc
    }
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
    mapGeneration.value += 1
    layers.value = []
    features.value = {}
    territoryFeatures.value = {}
    unfilteredFeatures.value = {}
    loading.value = {}
    lastFetched.value = {}
    lastBounds.value = {}
    bounds.value = {}
    activeSearch.value = null
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
    const advanced = groupConfig?.__grouping?.version >= 2
    const groups = advanced && groupConfig.groups && typeof groupConfig.groups === 'object'
      ? groupConfig.groups
      : {}
    const layerStyle = getLayerStyle(layerName)
    const separator = '\x1f'
    const pathKey = (values) => values.map((v) => String(v || '(blank)')).join(separator)
    const displayValue = (value) => (value == null || value === '') ? '(blank)' : String(value)
    const resolveBandValue = (value, level) => {
      const comparable = Number(value)
      for (const band of level.bands || []) {
        const min = band.min === '' || band.min == null ? null : Number(band.min)
        const max = band.max === '' || band.max == null ? null : Number(band.max)
        if (Number.isFinite(comparable)) {
          if (min != null && comparable < min) continue
          if (max != null && comparable >= max) continue
        }
        return band.label || displayValue(band.key)
      }
      return 'Other'
    }
    const nextFeatures = fc.features.map((feature) => {
      const props = { ...(feature.properties || {}) }
      delete props._color
      delete props._icon
      delete props._icon_disabled
      if (advanced) {
        let path = Array.isArray(props._group_path) ? props._group_path : []
        if (!path.length && Array.isArray(groupConfig.__grouping?.levels)) {
          path = groupConfig.__grouping.levels.map((level) =>
            level.mode === 'bands'
              ? resolveBandValue(props[level.field], level)
              : displayValue(props[level.field])
          )
        }
        if (!path.length) return { ...feature, properties: props }
        const style = {
          color: layerStyle.color || '',
          icon: layerStyle.icon || '',
        }
        for (let i = 0; i < path.length; i++) {
          const cfg = groups[pathKey(path.slice(0, i + 1))]
          if (!cfg || typeof cfg !== 'object') continue
          if (cfg.color) style.color = cfg.color
          if (Object.prototype.hasOwnProperty.call(cfg, 'icon')) style.icon = cfg.icon || ''
        }
        if (style.color) props._color = style.color
        if (style.icon === '__none') {
          props._icon_disabled = 1
        } else if (style.icon) {
          props._icon = style.icon
        }
        return { ...feature, properties: props }
      }
      const groupValue = props._group_value
      if (groupValue == null) return feature
      const cfg = groupConfig?.[String(groupValue)]
      if (cfg?.color) props._color = cfg.color
      if (cfg?.icon === '__none') {
        props._icon_disabled = 1
      } else if (cfg?.icon) {
        props._icon = cfg.icon
      }
      return { ...feature, properties: props }
    })
    _setFeatures(layerName, { ...fc, features: nextFeatures })
    _emitFeaturesUpdated(layerName)
  }

  function _styleFromDto(d) {
    return {
      color: d.color, icon: d.icon, size: d.size,
      pin_min_zoom: d.pin_min_zoom ?? 0,
      cluster: d.cluster, heatmap: d.heatmap,
      heatmap_config: d.heatmap_config,
      territory_enabled: d.territory_enabled,
      territory_color: d.territory_color,
      territory_opacity: d.territory_opacity,
      territory_padding_meters: d.territory_padding_meters,
      stroke_color: d.stroke_color, stroke_width: d.stroke_width,
      fill_opacity: d.fill_opacity,
    }
  }

  function _mergeFeatureCollections(collections) {
    const valid = (collections || []).filter((fc) => fc && Array.isArray(fc.features))
    if (!valid.length) return { type: 'FeatureCollection', features: [], total: 0, truncated: false }
    const merged = { ...valid[0], features: [] }
    const seen = new Set()
    let total = 0
    let truncated = false
    for (const fc of valid) {
      total += Number(fc.total || fc.features.length || 0)
      truncated = truncated || !!fc.truncated
      for (const feature of fc.features || []) {
        const id = feature?._id ?? feature?.id ?? feature?.properties?._name ?? JSON.stringify(feature?.geometry)
        if (id != null && seen.has(id)) continue
        if (id != null) seen.add(id)
        merged.features.push(feature)
      }
    }
    merged.total = total
    merged.truncated = truncated
    return merged
  }

  async function _callFeatures(layerName, bounds, options = {}) {
    const args = { layer: layerName, bounds }
    if (options.groupKey != null) args.group_key = options.groupKey
    if (Array.isArray(options.extraFields) && options.extraFields.length) {
      args.extra_fields = options.extraFields
    }
    return call('expedition.api.layer.get_features', args)
  }

  async function fetchFeatures(layerName, bounds, options = {}) {
    const cacheKey = options.cacheKey || layerName
    if (loading.value[cacheKey]) return
    const generation = mapGeneration.value
    loading.value[cacheKey] = true
    useUiStore().beginFetch()
    try {
      const boundList = Array.isArray(bounds) ? bounds : [bounds]
      const responses = await Promise.all(boundList.map((b) => _callFeatures(layerName, b, options)))
      if (generation !== mapGeneration.value) return
      const fc = _mergeFeatureCollections(responses)
      if (activeSearch.value?.parsed && !options.allowDuringSearch) return
      _promoteIds(fc)
      _setFeatures(cacheKey, fc)
      lastFetched.value[cacheKey] = Date.now()
      if (bounds) lastBounds.value[layerName] = bounds
      _emitFeaturesUpdated(cacheKey)
    } finally {
      if (generation === mapGeneration.value) loading.value[cacheKey] = false
      useUiStore().endFetch()
    }
  }

  async function fetchVirtualGroupFeatures(layerName, bounds, groups) {
    const items = Array.isArray(groups) ? groups : []
    const pending = items.filter((item) => item?.groupKey != null && item?.cacheKey)
    if (!pending.length) return
    const batchKey = `${layerName}::groups`
    if (loading.value[batchKey]) return
    const generation = mapGeneration.value
    loading.value[batchKey] = true
    for (const item of pending) loading.value[item.cacheKey] = true
    useUiStore().beginFetch()
    try {
      const boundList = Array.isArray(bounds) ? bounds : [bounds]
      const responses = await Promise.all(boundList.map((b) => call('expedition.api.layer.get_virtual_group_features', {
        layer: layerName,
        bounds: b,
        group_keys: pending.map((item) => item.groupKey),
      })))
      if (generation !== mapGeneration.value) return
      const byGroup = {}
      for (const resp of responses) {
        for (const [groupKey, fc] of Object.entries(resp?.groups || {})) {
          byGroup[groupKey] = _mergeFeatureCollections([byGroup[groupKey], fc])
        }
      }
      if (activeSearch.value?.parsed) return
      for (const item of pending) {
        const fc = byGroup[item.groupKey]
        if (!fc) continue
        _promoteIds(fc)
        _setFeatures(item.cacheKey, fc)
        lastFetched.value[item.cacheKey] = Date.now()
        _emitFeaturesUpdated(item.cacheKey)
      }
      if (bounds) lastBounds.value[layerName] = bounds
    } finally {
      if (generation === mapGeneration.value) {
        for (const item of pending) loading.value[item.cacheKey] = false
        loading.value[batchKey] = false
      }
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
        await fetchFeatures(layerName, viewportBoundsForServer(m.getBounds()))
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

  async function _ensureSearchMetadata(targetLayers) {
    const doctypes = [...new Set((targetLayers || [])
      .map((layer) => layer?.source_doctype)
      .filter(Boolean))]
    await Promise.all(doctypes.map((doctype) => getSourceFields(doctype).catch(() => [])))
  }

  function _searchCounts(parsed, targetLayers) {
    const layerCounts = {}
    let total = 0
    for (const layer of targetLayers || []) {
      const fc = _baseFeatures(layer.name)
      const fields = sourceFields.value[layer.source_doctype] || []
      const count = countParsedMapSearchMatches(fc, layer, fields, parsed, _searchContext(layer.name))
      layerCounts[layer.name] = count
      total += count
    }
    return { layerCounts, total }
  }

  function _structuredSearchFields(parsed, layer = null) {
    const fields = []
    for (const expression of parsed?.expressions || []) {
      if (expression?.mode === 'field' || expression?.mode === 'doctype_field') {
        if (expression.mode === 'doctype_field' && layer?.source_doctype) {
          const queryDoctype = String(expression.doctype || '').trim().toLowerCase()
          const layerDoctype = String(layer.source_doctype || '').trim().toLowerCase()
          if (queryDoctype !== layerDoctype) continue
        }
        fields.push(expression.field)
      }
    }
    return fields
  }

  function _textSearchValues(parsed) {
    return (parsed?.expressions || [])
      .filter((expression) => expression?.mode === 'text')
      .map((expression) => String(expression.value || '').trim())
      .filter(Boolean)
      .filter((value, idx, arr) => arr.indexOf(value) === idx)
  }

  function _featureCollectionHasField(fc, fields, wantedField) {
    if (!wantedField) return true
    if (!fc || !Array.isArray(fc.features)) return false
    const fieldname = _resolveFieldname(fields, wantedField) || wantedField
    return fc.features.some((feature) =>
      Object.prototype.hasOwnProperty.call(feature?.properties || {}, fieldname)
    )
  }

  function _resolveFieldname(fields, wantedField) {
    const wanted = String(wantedField || '').trim().toLowerCase()
    if (!wanted) return ''
    const meta = (fields || []).find((field) =>
      String(field?.fieldname || '').toLowerCase() === wanted
      || String(field?.label || '').toLowerCase() === wanted
    )
    return meta?.fieldname || wantedField
  }

  async function _refreshLayersMissingSearchFields(parsed, targetLayers) {
    const missing = []
    const hasTextSearch = _textSearchValues(parsed).length > 0
    for (const layer of targetLayers || []) {
      const searchFields = _structuredSearchFields(parsed, layer)
      const fields = sourceFields.value[layer.source_doctype] || []
      const fc = _baseFeatures(layer.name)
      const extraFields = searchFields
        .map((field) => _resolveFieldname(fields, field))
        .filter(Boolean)
        .filter((field, idx, arr) => arr.indexOf(field) === idx)
      const needsRefresh = searchFields.some((field) => !_featureCollectionHasField(fc, fields, field))
      if (hasTextSearch || needsRefresh) missing.push({ layerName: layer.name, extraFields })
    }
    await Promise.all(missing.map((item) =>
      fetchFeatures(item.layerName, null, {
        allowDuringSearch: true,
        extraFields: item.extraFields,
      })
    ))
  }

  async function _fetchTextSearchMatches(parsed, targetLayers) {
    const textValues = _textSearchValues(parsed)
    if (!textValues.length) return {}
    const out = {}
    await Promise.all((targetLayers || []).flatMap((layer) =>
      textValues.map(async (text) => {
        try {
          const resp = await call('expedition.api.layer.get_text_search_matches', {
            layer: layer.name,
            text,
          })
          const names = Array.isArray(resp?.names) ? resp.names : []
          out[layer.name] = {
            ...(out[layer.name] || {}),
            [text]: new Set(names.map(String)),
          }
        } catch (e) {
          out[layer.name] = {
            ...(out[layer.name] || {}),
            [text]: new Set(),
          }
        }
      })
    ))
    return out
  }

  async function applySearch(raw) {
    const parsed = parseMapSearch(raw)
    if (!parsed) {
      clearSearch()
      return null
    }
    if (activeSearch.value) clearSearch()
    const targetLayers = visibleLayers.value
    await _ensureSearchMetadata(targetLayers)
    await _refreshLayersMissingSearchFields(parsed, targetLayers)
    const textMatches = await _fetchTextSearchMatches(parsed, targetLayers)
    const nextUnfiltered = { ...unfilteredFeatures.value }
    for (const layer of targetLayers) {
      if (!nextUnfiltered[layer.name] && features.value[layer.name]) {
        nextUnfiltered[layer.name] = features.value[layer.name]
      }
    }
    unfilteredFeatures.value = nextUnfiltered

    activeSearch.value = {
      raw: parsed.raw,
      parsed,
      summary: describeParsedMapSearch(parsed),
      chips: parsed.expressions,
      textMatches,
      layerCounts: {},
      total: 0,
    }
    const counts = _searchCounts(parsed, targetLayers)
    activeSearch.value = { ...activeSearch.value, ...counts }
    for (const layer of targetLayers) {
      const base = _baseFeatures(layer.name)
      if (!base) continue
      features.value[layer.name] = _filteredFeatureCollection(layer.name, base)
      _emitFeaturesUpdated(layer.name)
    }
    return activeSearch.value
  }

  function clearSearch() {
    if (!activeSearch.value && !Object.keys(unfilteredFeatures.value).length) return
    const restore = unfilteredFeatures.value
    activeSearch.value = null
    unfilteredFeatures.value = {}
    for (const [layerName, fc] of Object.entries(restore)) {
      features.value[layerName] = fc
      _emitFeaturesUpdated(layerName)
    }
  }

  function clearFeatures(layerName) {
    delete features.value[layerName]
    delete territoryFeatures.value[layerName]
    delete unfilteredFeatures.value[layerName]
    delete loading.value[layerName]
    delete lastFetched.value[layerName]
    const groupPrefix = `${layerName}__grp__`
    for (const key of Object.keys(features.value)) {
      if (!key.startsWith(groupPrefix)) continue
      delete features.value[key]
      delete territoryFeatures.value[key]
      delete unfilteredFeatures.value[key]
      delete loading.value[key]
      delete lastFetched.value[key]
    }
    _emitFeaturesUpdated(layerName)
  }

  async function fetchTerritoryFeatures(layerName, options = {}) {
    const force = !!options.force
    const cacheKey = `territory:${layerName}`
    if (!force && territoryFeatures.value[layerName]) return territoryFeatures.value[layerName]
    if (loading.value[cacheKey]) return territoryFeatures.value[layerName] || null
    const generation = mapGeneration.value
    loading.value[cacheKey] = true
    try {
      const fc = await _callFeatures(layerName, null, { groupKey: options.groupKey })
      if (generation !== mapGeneration.value) return null
      _promoteIds(fc)
      territoryFeatures.value = { ...territoryFeatures.value, [layerName]: fc }
      _emitFeaturesUpdated(layerName)
      return fc
    } finally {
      if (generation === mapGeneration.value) loading.value[cacheKey] = false
    }
  }

  async function fetchVirtualGroupTerritoryFeatures(layerName, groups, options = {}) {
    const items = (Array.isArray(groups) ? groups : [])
      .filter((item) => item?.groupKey != null && item?.cacheKey)
      .filter((item) => options.force || !territoryFeatures.value[item.cacheKey])
    if (!items.length) return
    const batchKey = `territory:${layerName}:groups`
    if (loading.value[batchKey]) return
    const generation = mapGeneration.value
    loading.value[batchKey] = true
    try {
      const resp = await call('expedition.api.layer.get_virtual_group_features', {
        layer: layerName,
        bounds: null,
        group_keys: items.map((item) => item.groupKey),
      })
      if (generation !== mapGeneration.value) return
      const next = { ...territoryFeatures.value }
      for (const item of items) {
        const fc = resp?.groups?.[item.groupKey]
        if (!fc) continue
        _promoteIds(fc)
        next[item.cacheKey] = fc
      }
      territoryFeatures.value = next
      _emitFeaturesUpdated(layerName)
    } finally {
      if (generation === mapGeneration.value) loading.value[batchKey] = false
    }
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
    const generation = mapGeneration.value
    bounds.value = { ...bounds.value, [layerName]: null }
    try {
      const b = await call(
        'expedition.api.layer.get_layer_bounds',
        { layer: layerName },
      )
      if (generation !== mapGeneration.value) return null
      const safe = b && typeof b.south === 'number'
        ? b
        : { south: 0, west: 0, north: 0, east: 0, count: 0 }
      bounds.value = { ...bounds.value, [layerName]: safe }
      return safe
    } catch (e) {
      if (generation !== mapGeneration.value) return null
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
    territoryFeatures,
    unfilteredFeatures,
    loading,
    lastFetched,
    visibleLayers,
    activeSearch,
    applySearch,
    clearSearch,
    fetchFeatures,
    fetchVirtualGroupFeatures,
    fetchTerritoryFeatures,
    fetchVirtualGroupTerritoryFeatures,
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
