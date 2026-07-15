<script setup>
/**
 * Basemap — the MapLibre canvas. v1 visual: Felt-tier pins, hover/select
 * via feature-state, flyTo on map open, scale bar, loading overlay.
 *
 * Source / layer id convention (deterministic, stable across renders):
 *   source:           src-<layerName>           (with promoteId: '_id')
 *   shadow:           lyr-<layerName>-shadow    (dark, offset (0, 2), blurred)
 *   white ring:       lyr-<layerName>-ring      (crisp 1.5px white border)
 *   pin body:         lyr-<layerName>           (vibrant fill)
 *   cluster circle:   lyr-<layerName>-clusters
 *   cluster count:    lyr-<layerName>-cluster-count
 *
 * Each feature gets a stable _id so feature-state can track "selected".
 * get_features already returns _id on every feature (we add it server-
 * side if missing). When a pin is clicked we set feature-state.selected
 * = true on it; the halo layer's stroke-width keys on that state.
 *
 * Click handling:
 *   - pin click  → set ui.selectedFeature → open right rail
 *   - cluster    → zoom to cluster expansion
 *
 * Skin / basemap:
 *   - The current skin comes from ui.currentSkinId (or ui.previewSkinId
 *     during hover-preview). The skin is resolved to a MapLibre style
 *     spec via the SKINS gallery.
 *   - We rebuild the style with map.setStyle() and re-add all our sources
 *     on 'styledata'. setStyle wipes our custom layers.
 */
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { useZonesStore } from '../state/zones.js'
import { useIconsStore } from '../state/icons.js'
import { getSkin, resolveStyleUrl } from '../api/skins.js'
import { coloredIconImageId, registerColoredIcons } from '../api/icons.js'
import { activeMapCursor, applyMapCursor } from '../lib/mapCursor.js'
import { openDeskDoc } from '../lib/desk.js'
import {
  normalizeGeometryLngs,
  normalizeLngLat,
  shortestLngDelta,
  viewportBoundsForServer,
  wrapLng,
} from '../lib/geo.js'
import {
  RAMP_PRESETS,
  buildHeatmapColor,
  buildHeatmapIntensity,
  buildHeatmapRadius,
  buildHeatmapWeight,
  parseRampJson,
  parseWeightStopsJson,
} from '../api/heatmap.js'

// Pin radii. Apple/Google-tier: default `m` is 11px (22px disk
// visually with the white ring). xs/s are for dense layers; l/xl
// for primary layers.
const SIZE_TO_RADIUS = { xs: 7, s: 9, m: 11, l: 14, xl: 18 }
const ICON_IMAGE_SIZE = 36
const CLUSTER_MAX_ZOOM = 12
const MAPLIBRE_MAX_ZOOM = 24
// Punchy Apple-red fallback so even unconfigured layers read as
// primary data points.
const FALLBACK_COLOR = '#FF3B30'

/**
 * Compute the lowest zoom where one Mercator world is at least as wide
 * as the viewport. With renderWorldCopies enabled, this is the critical
 * globe invariant: horizontal pan can wrap seamlessly, but the screen
 * can never contain the same longitude twice.
 */
function computeGlobeFitZoom(width) {
  if (!width) return 0
  const TILE_PX = 512
  const EPSILON = 0.002
  return Math.max(0, Math.log2(width / TILE_PX) + EPSILON)
}

const VIRTUAL_GROUP_SEPARATOR = '__grp__'

function sourceId(layerName) { return `src-${layerName}` }
function shadowLayerId(layerName) { return `lyr-${layerName}-shadow` }
function ringLayerId(layerName) { return `lyr-${layerName}-ring` }
function layerId(layerName) { return `lyr-${layerName}` }
function clusterLayerId(layerName) { return `lyr-${layerName}-clusters` }
function clusterCountId(layerName) { return `lyr-${layerName}-cluster-count` }
function iconLayerId(layerName) { return `lyr-${layerName}-icon` }
function heatmapLayerId(layerName) { return `lyr-${layerName}-heatmap` }
function territorySourceId(layerName) { return `src-${layerName}-territory` }
function territoryLayerId(layerName) { return `lyr-${layerName}-territory` }
function territorySurfaceSourceId() { return 'src-territory-surface' }
function territorySurfaceLayerId() { return 'lyr-territory-surface' }
function haloSourceId(layerName) { return `src-${layerName}-halo` }
function haloLayerId(layerName) { return `lyr-${layerName}-halo` }
function haloStrokeLayerId(layerName) { return `lyr-${layerName}-halo-stroke` }
function extrusionLayerId(layerName) { return `lyr-${layerName}-extrude` }
function extrusionSourceId(layerName) { return `src-${layerName}-extrude` }
function virtualGroupName(layerName, groupKey) {
  const safeKey = encodeURIComponent(String(groupKey ?? 'empty')).replace(/[^a-zA-Z0-9_-]/g, '_')
  return `${layerName}${VIRTUAL_GROUP_SEPARATOR}${safeKey}`
}
function parentLayerName(renderName) {
  return String(renderName || '').split(VIRTUAL_GROUP_SEPARATOR)[0]
}

function pinMinZoom(layerDoc, style = {}) {
  const raw = style.pin_min_zoom ?? layerDoc?.pin_min_zoom ?? 0
  const value = Number(raw)
  if (!Number.isFinite(value) || value <= 0) return 0
  return Math.min(MAPLIBRE_MAX_ZOOM, Math.max(0, value))
}

function layerZoomOptions(minZoom) {
  return minZoom > 0 ? { minzoom: minZoom } : {}
}

function applyLayerZoomRange(layerId, minZoom) {
  if (!map?.getLayer?.(layerId) || typeof map.setLayerZoomRange !== 'function') return
  map.setLayerZoomRange(layerId, minZoom || 0, MAPLIBRE_MAX_ZOOM)
}

// MapLibre's fill-extrusion only renders polygons, but Expedition
// layers are Point sources. To get a 3D column per pin, we synthesize
// a tiny 10m×10m square footprint polygon at each point's lat/lng and
// feed those to the extrusion layer. The conversion uses a fixed
// degree offset, which is good enough at the spatial scales we render
// (city / region) and keeps the column footprint visually constant.
const EXTRUSION_FOOTPRINT_DEG = 0.0001   // ≈ 11m at the equator
function _extrusionPolygonForPoint(lng, lat) {
  const d = EXTRUSION_FOOTPRINT_DEG
  return {
    type: 'Polygon',
    coordinates: [[
      [lng - d, lat - d],
      [lng + d, lat - d],
      [lng + d, lat + d],
      [lng - d, lat + d],
      [lng - d, lat - d],
    ]],
  }
}
function _extrusionFeatureCollection(pointFc, layerName) {
  const field = ui.extrusionField[layerName]
  return {
    type: 'FeatureCollection',
    features: (pointFc.features || []).map((f) => {
      const [lng, lat] = (f.geometry && f.geometry.coordinates) || []
      if (lng == null || lat == null) return null
      const props = { ...(f.properties || {}) }
      if (f._id != null) props._id = f._id
      return { type: 'Feature', geometry: _extrusionPolygonForPoint(lng, lat), properties: props }
    }).filter(Boolean),
  }
}

function _removeGroupedArtifacts(layerName) {
  if (!map || !map.getStyle()) return
  const layerPrefixes = [
    `lyr-${layerName}${VIRTUAL_GROUP_SEPARATOR}`,
    `lyr-${layerName}-grp-`,
  ]
  const sourcePrefixes = [
    `src-${layerName}${VIRTUAL_GROUP_SEPARATOR}`,
    `src-${layerName}-grp-`,
  ]
  for (const layer of [...(map.getStyle().layers || [])].reverse()) {
    if (layer.id && layerPrefixes.some((prefix) => layer.id.startsWith(prefix))) {
      if (map.getLayer(layer.id)) map.removeLayer(layer.id)
      _interactivePointLayers.delete(layer.id)
    }
  }
  for (const id of Object.keys(map.getStyle().sources || {})) {
    if (sourcePrefixes.some((prefix) => id.startsWith(prefix)) && map.getSource(id)) {
      map.removeSource(id)
      delete _sourceClusterState[id]
    }
  }
}

function _removeBasePointArtifacts(layerName) {
  if (!map) return
  _removeRenderArtifacts(layerName)
}

function _removeRenderArtifacts(renderName) {
  if (!map) return
  for (const id of [
    heatmapLayerId(renderName),
    territoryLayerId(renderName),
    haloStrokeLayerId(renderName),
    haloLayerId(renderName),
    extrusionLayerId(renderName),
    clusterCountId(renderName),
    clusterLayerId(renderName),
    iconLayerId(renderName),
    layerId(renderName),
    ringLayerId(renderName),
    shadowLayerId(renderName),
  ]) {
    if (map.getLayer(id)) map.removeLayer(id)
    _interactivePointLayers.delete(id)
  }
  for (const id of [extrusionSourceId(renderName), territorySourceId(renderName), haloSourceId(renderName), sourceId(renderName)]) {
    if (map.getSource(id)) map.removeSource(id)
    delete _sourceClusterState[id]
  }
  _renderedLayerNames.delete(renderName)
}

function _pruneStaleRenderedLayers() {
  if (!map || !map.getStyle()) return
  const activeBaseNames = new Set((layerStore.layers || []).map((layer) => layer.name))
  for (const renderName of [..._renderedLayerNames]) {
    if (activeBaseNames.has(parentLayerName(renderName))) continue
    _removeLayerFromMap(parentLayerName(renderName))
    _removeRenderArtifacts(renderName)
  }
  for (const key of Object.keys(lastLoadedFeatures || {})) {
    if (!activeBaseNames.has(parentLayerName(key))) delete lastLoadedFeatures[key]
  }
}

function _pruneGroupedArtifacts(layerName, activeRenderNames) {
  if (!map || !map.getStyle()) return
  const activePrefixes = new Set([...activeRenderNames].map((name) => `lyr-${name}`))
  const activeSources = new Set()
  for (const name of activeRenderNames) {
    activeSources.add(sourceId(name))
    activeSources.add(territorySourceId(name))
    activeSources.add(haloSourceId(name))
    activeSources.add(extrusionSourceId(name))
  }
  const layerPrefixes = [
    `lyr-${layerName}${VIRTUAL_GROUP_SEPARATOR}`,
    `lyr-${layerName}-grp-`,
  ]
  const sourcePrefixes = [
    `src-${layerName}${VIRTUAL_GROUP_SEPARATOR}`,
    `src-${layerName}-grp-`,
  ]
  for (const layer of [...(map.getStyle().layers || [])].reverse()) {
    if (!layer.id || !layerPrefixes.some((prefix) => layer.id.startsWith(prefix))) continue
    const keep = [...activePrefixes].some((prefix) => layer.id === prefix || layer.id.startsWith(`${prefix}-`))
    if (!keep && map.getLayer(layer.id)) {
      map.removeLayer(layer.id)
      _interactivePointLayers.delete(layer.id)
    }
  }
  for (const id of Object.keys(map.getStyle().sources || {})) {
    if (sourcePrefixes.some((prefix) => id.startsWith(prefix)) && !activeSources.has(id) && map.getSource(id)) {
      map.removeSource(id)
      delete _sourceClusterState[id]
    }
  }
}

function layerNameFromRenderedLayerId(id) {
  const grouped = id.match(/^lyr-(.+)-grp-[^-]+(?:-(shadow|ring|icon|clusters|cluster-count|halo|territory))?$/)
  if (grouped) return grouped[1]
  let name = String(id || '').replace(/^lyr-/, '')
  for (const suffix of [
    '-cluster-count',
    '-halo-stroke',
    '-clusters',
    '-heatmap',
    '-territory',
    '-extrude',
    '-shadow',
    '-ring',
    '-icon',
    '-halo',
  ]) {
    if (name.endsWith(suffix)) return name.slice(0, -suffix.length)
  }
  return name
}

const mapEl = ref(null)
const ui = useUiStore()
const mapStore = useMapStore()
const layerStore = useLayersStore()
const zoneStore = useZonesStore()
const iconStore = useIconsStore()
let map = null
let unsubscribeFeatures = null
let lastLoadedFeatures = {}  // layer.name -> last FeatureCollection, for re-add on styledata
let unsubscribeZones = null
let unsubscribeZoneTags = null
let _virtualGroupFetchKeys = {}
let _sourceClusterState = {}
let _zoneHandlersBound = false
let _zoneDrag = null
let _mapPointerStart = null
let _mapWasDragged = false
let _homeFitDone = false
let _homeFitSeq = 0
let _homeFitTimer = null

// Tracks whether each visible layer has ever been fetched with the
// current viewport. On first paint we deliberately pass `null` bounds
// to `get_features` so the server returns up to 5000 rows from the
// whole source table (the viewport is just `[78.96, 20.59] z4` at
// boot — almost certainly outside the data envelope, so a bounded
// first fetch returns zero features and the canvas stays empty
// until the user moves the map).
let _layerFetchedOnce = new Set()
let _interactivePointLayers = new Set()
let _renderedLayerNames = new Set()

function _setMapCursor(kind = activeMapCursor(ui)) {
  if (_zoneDrag) kind = 'grabbing'
  if (ui.drawMode === 'polygon' || ui.drawMode === 'rectangle' || ui.drawMode === 'circle' || ui.drawMode === 'freehand') {
    kind = 'crosshair'
  } else if (ui.drawMode === 'text') {
    kind = 'text'
  } else if (ui.drawMode === 'note') {
    kind = 'dot'
  } else if (ui.measureMode) {
    kind = 'cross'
  }
  if (map) applyMapCursor(map.getCanvas(), kind)
}

function _resetFirstFetch() {
  _layerFetchedOnce = new Set()
  _virtualGroupFetchKeys = {}
  _sourceClusterState = {}
  _homeFitDone = false
  _homeFitSeq += 1
  if (_homeFitTimer) {
    clearTimeout(_homeFitTimer)
    _homeFitTimer = null
  }
}

function _bindPointLayerHandlers(layerIdValue) {
  if (!map || !map.getLayer(layerIdValue) || _interactivePointLayers.has(layerIdValue)) return
  map.on('click', layerIdValue, onPointClick)
  map.on('mouseenter', layerIdValue, onPointMouseEnter)
  map.on('mouseleave', layerIdValue, onPointMouseLeave)
  _interactivePointLayers.add(layerIdValue)
}

function _hasRenderedLayer(layerName) {
  if (!map || !map.getStyle()) return false
  if (map.getLayer(layerId(layerName)) || map.getSource(sourceId(layerName))) return true
  const layerPrefix = `lyr-${layerName}${VIRTUAL_GROUP_SEPARATOR}`
  const sourcePrefix = `src-${layerName}${VIRTUAL_GROUP_SEPARATOR}`
  return (map.getStyle().layers || []).some((layer) => layer.id?.startsWith(layerPrefix))
    || Object.keys(map.getStyle().sources || {}).some((id) => id.startsWith(sourcePrefix))
}

function _hasInteractivePointAt(point) {
  if (!map || !point) return false
  const layers = [..._interactivePointLayers].filter((id) => map.getLayer(id))
  if (!layers.length) return false
  try {
    return map.queryRenderedFeatures(point, { layers }).length > 0
  } catch (_) {
    return false
  }
}

// Zone source/layer ids. All zones live in a single source so we
// don't pay addSource/removeSource overhead per draw.
function zoneSourceId() { return 'src-zones' }
function zoneFillLayerId() { return 'lyr-zones-fill' }
function zoneStrokeLayerId() { return 'lyr-zones-stroke' }
function zoneDashedStrokeLayerId() { return 'lyr-zones-stroke-dashed' }
function zoneDottedStrokeLayerId() { return 'lyr-zones-stroke-dotted' }
function zoneLabelLayerId() { return 'lyr-zones-label' }
function zoneSelectedLayerId() { return 'lyr-zones-selected' }
function zoneDraftLayerId() { return 'lyr-zones-draft' }
function zoneDraftFillLayerId() { return 'lyr-zones-draft-fill' }
function zoneDraftVertexLayerId() { return 'lyr-zones-draft-verts' }

// True while a polygon draw is in progress and doubleClickZoom has been
// disabled. Module-scope so the bare `map.on('click', ...)` handler and
// the `watch(() => ui.drawMode, ...)` cleanup branch share state.
let _dblClickDisabled = false

const activeSkinId = computed(() => ui.previewSkinId || ui.currentSkinId)

/**
 * Resolve a skin entry to a MapLibre style spec URL or a full inline
 * style object. Vector-style and local-style return a URL (the
 * browser fetches the spec). Raster entries are wrapped into a
 * minimal inline style spec.
 */
function resolveStyle(skin) {
  if (!skin) return null
  if (skin.kind === 'vector-style' || skin.kind === 'local-style') {
    return resolveStyleUrl(skin)
  }
  // Raster: build a minimal style spec.
  return {
    version: 8,
    sources: skin.sources,
    layers: skin.layers,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  }
}

/**
 * Pin paint — Apple/Google-tier.
 *
 *   Layer order (bottom → top):
 *     1. shadow        — dark circle, radius+3, offset (0, 2) in pixels,
 *                        blur 1.2, opacity 0.45. Real drop shadow, not
 *                        a halo. Without this the pin looks "stuck on"
 *                        the map.
 *     2. white ring    — circle at radius+1.5, color #fff, full opacity.
 *                        Apple-style crisp border that separates the
 *                        pin from the basemap regardless of skin.
 *     3. pin body      — radius, vibrant color, full opacity. The
 *                        "wow" surface.
 *
 *   Selected: pin body scales 1.25× (radius*1.25) and white ring widens
 *   from 1.5px to 2.5px. No glow — that's iOS 6, not iOS 18.
 */
function shadowPaint(radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      (radius + 3) * 1.18,
      radius + 3,
    ],
    'circle-color': '#000000',
    'circle-opacity': 0.45,
    'circle-blur': 1.2,
    'circle-translate': [0, 2],
    'circle-translate-anchor': 'viewport',
  }
}

function ringPaint(radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      (radius + 1.5) * 1.18,
      radius + 1.5,
    ],
    'circle-color': '#FFFFFF',
    'circle-stroke-color': 'rgba(0,0,0,0.12)',
    'circle-stroke-width': 0.5,
    'circle-opacity': 1,
  }
}

function pinPaint(color, radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      radius * 1.18,
      radius,
    ],
    'circle-color': pinColorExpression(color),
    'circle-opacity': 1,
  }
}

function pinColorExpression(fallback) {
  return [
    'case',
    ['has', '_color'],
    ['to-color', ['get', '_color']],
    fallback,
  ]
}

function pointFilter(wantsCluster) {
  return wantsCluster ? ['!', ['has', 'point_count']] : ['all']
}

function circlePointFilter(wantsCluster) {
  return wantsCluster
    ? ['all', ['!', ['has', 'point_count']], ['!', ['has', '_display_icon_image']]]
    : ['!', ['has', '_display_icon_image']]
}

function iconPointFilter(wantsCluster) {
  return wantsCluster
    ? ['all', ['!', ['has', 'point_count']], ['has', '_display_icon_image']]
    : ['has', '_display_icon_image']
}

function featureDisplayColor(feature, fallback) {
  return feature?.properties?._color || fallback
}

function featureDisplayIcon(feature, layerIcon) {
  if (feature?.properties?._icon_disabled) return ''
  return feature?.properties?._icon || layerIcon || ''
}

function featureGroupKey(feature) {
  const props = feature?.properties || {}
  if (props._group_value != null && props._group_value !== '') return String(props._group_value)
  if (Object.prototype.hasOwnProperty.call(props, '_group_value')) return '(blank)'
  if (Array.isArray(props._group_path) && props._group_path.length) return props._group_path.join('\x1f')
  return null
}

function featureGroupLabel(feature, fallback) {
  const props = feature?.properties || {}
  return props._group_label || props._group_value || fallback
}

function groupConfigForKey(layerDoc, key) {
  const config = layerDoc?.group_config
  if (!config || typeof config !== 'object') return null
  if (config.__grouping?.version >= 2) {
    return config.groups?.[String(key)] || null
  }
  return config[String(key)] || null
}

function buildVirtualGroupLayers(layerName, fc, layerDoc, style, color) {
  if (Array.isArray(fc?.virtual_groups) && fc.virtual_groups.length) {
    return fc.virtual_groups.map((group) => {
      const renderName = virtualGroupName(layerName, group.key)
      const groupFc = layerStore.features[renderName]
      return {
        renderName,
        parentName: layerName,
        groupKey: group.key,
        groupLabel: group.label || group.key,
        fc: groupFc || null,
        layerDoc,
        style: {
          ...style,
          ...(group.style || {}),
        },
      }
    })
  }

  const groups = new Map()
  for (const feature of fc?.features || []) {
    const key = featureGroupKey(feature)
    if (!key) continue
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(feature)
  }
  if (!groups.size) return []
  return [...groups.entries()].map(([key, features]) => {
    const first = features[0]
    const groupColor = featureDisplayColor(first, color)
    const groupCfg = groupConfigForKey(layerDoc, key)
    const inheritedIcon = style.icon || layerDoc.icon || ''
    const groupIcon = featureDisplayIcon(first, inheritedIcon)
    const renderName = virtualGroupName(layerName, key)
    return {
      renderName,
      parentName: layerName,
      groupKey: key,
      fc: {
        ...fc,
        features,
        layer: {
          ...(fc.layer || {}),
          name: layerName,
          title: fc.layer?.title || layerDoc.title || layerName,
          _virtual_group_key: key,
          _virtual_group_label: featureGroupLabel(first, key),
        },
      },
      layerDoc,
      style: {
        ...style,
        color: groupColor,
        territory_color: groupCfg?.territory_color || '',
        icon: groupIcon,
      },
    }
  })
}

function iconRenderSpec(iconKey, color) {
  const icon = iconStore.byKey.get(iconKey)
  if (!icon) return null
  const imageKey = icon?.source === 'custom' && icon.modified
    ? `${iconKey}:${icon.modified}`
    : iconKey
  const isRasterCustom = icon?.source === 'custom' && icon.icon_format === 'Image'
  return {
    id: iconKey,
    color,
    imageId: isRasterCustom ? imageKey : coloredIconImageId(imageKey, color),
    svg: icon?.source === 'custom' && !isRasterCustom ? icon.svg_content : null,
    imageDataUrl: isRasterCustom ? icon.image_data_url : null,
  }
}

function iconSizeForRadius(radius) {
  return (radius * 2) / ICON_IMAGE_SIZE
}

/**
 * Cluster paint — Apple-style rounded-square equivalent.
 *
 * For v1 we still use a circle (MapLibre `symbol` with a text label).
 * We use a step radius that grows with point count so 5000+ pins
 * don't dwarf 50, and we keep the white ring + drop shadow so clusters
 * feel like the same family as single pins (Apple's visual rule).
 */
function clusterPaint(color) {
  return {
    'circle-radius': [
      'step',
      ['get', 'point_count'],
      16, 10,
      20, 50,
      26, 200,
      32,
    ],
    'circle-color': color,
    'circle-stroke-color': '#FFFFFF',
    'circle-stroke-width': 2.5,
    'circle-opacity': 1,
    'circle-translate': [0, 1],
    'circle-translate-anchor': 'viewport',
  }
}

function heatmapPaint(color, layerDoc, style = {}) {
  const config = {
    ...(style.heatmap_config || {}),
    ...(layerDoc.heatmap_config || {}),
  }
  const mode = layerDoc.heatmap_mode || config.mode || 'count'
  const weightField = mode === 'sum'
    ? (layerDoc.heatmap_weight_field || config.weight_field || '')
    : ''
  const weightMin = Number(layerDoc.heatmap_weight_min ?? config.weight_min)
  const weightMax = Number(layerDoc.heatmap_weight_max ?? config.weight_max)
  const radiusMin = Number(layerDoc.heatmap_radius_min ?? config.radius_min ?? 10)
  const radiusMax = Number(layerDoc.heatmap_radius_max ?? config.radius_max ?? 30)
  const intensityMin = Number(layerDoc.heatmap_intensity_min ?? config.intensity_min ?? 1)
  const intensityMax = Number(layerDoc.heatmap_intensity_max ?? config.intensity_max ?? 2.5)
  const opacity = Number(layerDoc.heatmap_opacity ?? config.opacity ?? 0.75)
  const ramp = parseRampJson(layerDoc.heatmap_ramp_json)
    || (Array.isArray(config.ramp) && config.ramp.length ? config.ramp : null)
    || RAMP_PRESETS.monochrome.build(color || FALLBACK_COLOR)
  const stops = parseWeightStopsJson(layerDoc.heatmap_weight_stops_json)
    || (Array.isArray(config.weight_stops) ? config.weight_stops : null)
  return {
    'heatmap-weight': buildHeatmapWeight({
      field: weightField,
      log: (layerDoc.heatmap_weight_scale || config.weight_scale) === 'log',
      min: Number.isFinite(weightMin) ? weightMin : null,
      max: Number.isFinite(weightMax) ? weightMax : null,
      stops,
    }),
    'heatmap-intensity': buildHeatmapIntensity(
      Number.isFinite(intensityMin) ? intensityMin : 1,
      Number.isFinite(intensityMax) ? intensityMax : 2.5,
    ),
    'heatmap-radius': buildHeatmapRadius(
      Number.isFinite(radiusMin) ? radiusMin : 10,
      Number.isFinite(radiusMax) ? radiusMax : 30,
    ),
    'heatmap-opacity': Math.max(0, Math.min(1, Number.isFinite(opacity) ? opacity : 0.75)),
    'heatmap-color': buildHeatmapColor(ramp),
  }
}

/**
 * Extrusion paint — PR-9 3D / pitch.
 *
 * A `fill-extrusion` layer that turns each point into a column. The
 * base height is either a constant (set via ui.extrusionHeight) or a
 * per-feature numeric property (ui.extrusionField). The fill uses the
 * layer's color at ~85% opacity; the top face is slightly brighter so
 * the column reads as a solid object, not a flat rectangle.
 *
 * `fill-extrusion-base` is at 0 so the columns stand on the ground.
 * `fill-extrusion-vertical-gradient` lights the side facing the
 * camera (MapLibre's approximation of "sun from above").
 */
function extrusionPaint(color, layerName) {
  const field = ui.extrusionField[layerName]
  const fallback = ui.extrusionHeight[layerName] || 200
  return {
    'fill-extrusion-color': color,
    'fill-extrusion-opacity': 0.85,
    'fill-extrusion-height': field
      ? ['coalesce', ['to-number', ['get', field]], fallback]
      : fallback,
    'fill-extrusion-base': 0,
    'fill-extrusion-vertical-gradient': true,
  }
}

/**
 * Halo rendering — PR-8 radius rendering.
 *
 * The halo is a translucent ground footprint drawn behind the pin
 * stack. It is generated as a polygon, not a screen-space circle, so
 * pitched camera views naturally turn it into a perspective ellipse.
 * The radius comes from a per-feature numeric property or the fallback
 * ui.radiusMeters value, with ui.radiusScale applied as a multiplier.
 */
function haloStyle(color, layerName) {
  const displayColor = pinColorExpression(color)
  const layerDoc = layerStore.layers.find((l) => l.name === layerName)
  const rawOpacity = (layerDoc && layerDoc.radius_opacity != null)
    ? Number(layerDoc.radius_opacity)
    : 0.18
  const opacity = Number.isFinite(rawOpacity) ? Math.max(0, Math.min(1, rawOpacity)) : 0.18
  return {
    fill: {
      'fill-color': displayColor,
      'fill-opacity': opacity,
    },
    stroke: {
      'line-color': displayColor,
      'line-opacity': Math.min(1, opacity * 2.5),
      'line-width': 1.25,
    },
  }
}

function haloCirclePaint(color, layerName) {
  const halo = haloStyle(color, layerName)
  return {
    'circle-radius': [
      'interpolate',
      ['exponential', 2],
      ['zoom'],
      4, ['*', ['coalesce', ['to-number', ['get', '_halo_radius_meters']], 5000], 0.000102],
      8, ['*', ['coalesce', ['to-number', ['get', '_halo_radius_meters']], 5000], 0.00163],
      12, ['*', ['coalesce', ['to-number', ['get', '_halo_radius_meters']], 5000], 0.0262],
      16, ['*', ['coalesce', ['to-number', ['get', '_halo_radius_meters']], 5000], 0.419],
      20, ['*', ['coalesce', ['to-number', ['get', '_halo_radius_meters']], 5000], 6.71],
    ],
    'circle-color': halo.fill['fill-color'],
    'circle-opacity': halo.fill['fill-opacity'],
    'circle-stroke-color': halo.stroke['line-color'],
    'circle-stroke-opacity': halo.stroke['line-opacity'],
    'circle-stroke-width': halo.stroke['line-width'],
    'circle-pitch-scale': 'map',
    'circle-pitch-alignment': 'map',
  }
}

function haloRadiusMeters(feature, layerName) {
  const field = ui.radiusField[layerName]
  const fallback = ui.radiusMeters[layerName] || 5000
  const scale = ui.radiusScale[layerName] || 1
  const value = field
    ? Number(feature?.properties?.[field] ?? fallback)
    : Number(fallback)
  const radius = (Number.isFinite(value) && value > 0 ? value : Number(fallback)) * scale
  return Math.max(1, radius)
}

function haloPolygon(lng, lat, radiusMeters, steps = 64) {
  const earthRadius = 6378137
  const latRad = lat * Math.PI / 180
  const lngRad = lng * Math.PI / 180
  const angularDistance = radiusMeters / earthRadius
  const coordinates = []
  for (let i = 0; i <= steps; i += 1) {
    const bearing = (i / steps) * Math.PI * 2
    const pointLat = Math.asin(
      Math.sin(latRad) * Math.cos(angularDistance)
      + Math.cos(latRad) * Math.sin(angularDistance) * Math.cos(bearing),
    )
    const pointLng = lngRad + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latRad),
      Math.cos(angularDistance) - Math.sin(latRad) * Math.sin(pointLat),
    )
    coordinates.push([pointLng * 180 / Math.PI, pointLat * 180 / Math.PI])
  }
  return { type: 'Polygon', coordinates: [coordinates] }
}

function distanceMeters(a, b) {
  if (!a || !b) return 0
  const earth = 6378137
  const toRad = (deg) => deg * Math.PI / 180
  const lat1 = toRad(a[1])
  const lat2 = toRad(b[1])
  const dLat = toRad(b[1] - a[1])
  const dLng = toRad(shortestLngDelta(a[0], b[0]))
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2
  return earth * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h))
}

function rectanglePolygon(a, b) {
  if (!a || !b) return null
  const west = Math.min(a[0], b[0])
  const east = Math.max(a[0], b[0])
  const south = Math.min(a[1], b[1])
  const north = Math.max(a[1], b[1])
  return {
    type: 'Polygon',
    coordinates: [[
      [west, south],
      [east, south],
      [east, north],
      [west, north],
      [west, south],
    ]],
  }
}

function draftColor() {
  return ui.drawingColor || '#3B82F6'
}

function draftStrokeColor() {
  return ui.drawingStrokeColor || '#1E40AF'
}

function draftFillOpacity() {
  return Number(ui.drawingFillOpacity ?? 0.22)
}

function draftStrokeWidth() {
  return Number(ui.drawingStrokeWidth ?? 2)
}

function draftStrokeStyle() {
  return ui.drawingStrokeStyle || 'solid'
}

function haloFeatureCollection(features, layerName) {
  return {
    type: 'FeatureCollection',
    features: (features || []).map((feature) => {
      const [lng, lat] = (feature.geometry && feature.geometry.coordinates) || []
      if (lng == null || lat == null) return null
      const featureId = feature.id ?? feature._id ?? feature.properties?._id
      return {
        type: 'Feature',
        id: featureId,
        geometry: haloPolygon(lng, lat, haloRadiusMeters(feature, layerName)),
        properties: { ...(feature.properties || {}), _id: featureId },
      }
    }).filter(Boolean),
  }
}

function clamp01(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : fallback
}

function territoryEnabled(layerDoc, style) {
  return !!(style?.territory_enabled ?? layerDoc?.territory_enabled)
}

function territoryOpacity(layerDoc, style) {
  return clamp01(style?.territory_opacity ?? layerDoc?.territory_opacity, 0.18)
}

function territoryPaddingMeters(layerDoc, style) {
  const n = Number(style?.territory_padding_meters ?? layerDoc?.territory_padding_meters)
  return Number.isFinite(n) && n > 0 ? n : 2500
}

function territoryPaintColor(color, layerDoc, style) {
  return style?.territory_color || layerDoc?.territory_color || color || FALLBACK_COLOR
}

function territoryWaterBeforeId() {
  const layers = map?.getStyle?.()?.layers || []
  const exact = layers.find((layer) => layer.id === 'water')
  if (exact) return exact.id
  const fillWater = layers.find((layer) =>
    layer.type === 'fill' && String(layer.id || '').toLowerCase().includes('water')
  )
  return fillWater?.id || null
}

function collectTerritoryCandidates() {
  const candidates = []
  for (const layerDoc of layerStore.layers || []) {
    const enabled = (layerDoc.enabled !== false && layerDoc.enabled !== 0) && !layerStore.locallyHidden.has(layerDoc.name)
    const baseStyle = layerStore.getLayerStyle(layerDoc.name) || {}
    if (!enabled || !territoryEnabled(layerDoc, baseStyle)) continue
    const layerColor = baseStyle.color || layerDoc.color || FALLBACK_COLOR
    const groupPrefix = `${layerDoc.name}${VIRTUAL_GROUP_SEPARATOR}`
    const groupEntries = Object.entries(layerStore.territoryFeatures || layerStore.features || {})
      .filter(([key, fc]) => key.startsWith(groupPrefix) && Array.isArray(fc?.features) && fc.features.length)

    if (groupEntries.length) {
      const allGroupFeatures = []
      for (const [, fc] of groupEntries) allGroupFeatures.push(...(fc.features || []))
      if (allGroupFeatures.length) {
        candidates.push({
          key: `${layerDoc.name}:base`,
          features: allGroupFeatures,
          color: territoryPaintColor(layerColor, layerDoc, baseStyle),
          opacity: territoryOpacity(layerDoc, baseStyle),
          radius: territoryPaddingMeters(layerDoc, baseStyle),
          priority: 0,
        })
      }
      for (const [key, fc] of groupEntries) {
        const style = fc?.layer?.style || {}
        const color = style.color || layerColor
        candidates.push({
          key,
          features: fc.features || [],
          color: territoryPaintColor(color, layerDoc, style),
          opacity: territoryOpacity(layerDoc, style),
          radius: territoryPaddingMeters(layerDoc, style),
          priority: 1000000,
        })
      }
      continue
    }

    const fc = layerStore.territoryFeatures?.[layerDoc.name] || layerStore.features?.[layerDoc.name]
    if (!Array.isArray(fc?.features) || !fc.features.length) continue
    candidates.push({
      key: layerDoc.name,
      features: fc.features,
      color: territoryPaintColor(layerColor, layerDoc, baseStyle),
      opacity: territoryOpacity(layerDoc, baseStyle),
      radius: territoryPaddingMeters(layerDoc, baseStyle),
      priority: 500000,
    })
  }
  return candidates
}

function featureCoord(feature) {
  const coord = feature?.geometry?.coordinates
  const lng = Number(coord?.[0])
  const lat = Number(coord?.[1])
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null
  return [lng, lat]
}

function meterScalesForLat(lat) {
  const latScale = 110540
  const lngScale = Math.max(1, 111320 * Math.cos((Number(lat) * Math.PI) / 180))
  return { lngScale, latScale }
}

function projectedPoint(coord, center) {
  const { lngScale, latScale } = meterScalesForLat(center[1])
  return {
    lng: coord[0],
    lat: coord[1],
    x: shortestLngDelta(center[0], coord[0]) * lngScale,
    y: (coord[1] - center[1]) * latScale,
  }
}

function unprojectedPoint(point, center) {
  const { lngScale, latScale } = meterScalesForLat(center[1])
  return [center[0] + point.x / lngScale, center[1] + point.y / latScale]
}

function convexHull(points) {
  const sorted = [...points].sort((a, b) => a.x === b.x ? a.y - b.y : a.x - b.x)
  if (sorted.length <= 1) return sorted
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
  const lower = []
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop()
    lower.push(p)
  }
  const upper = []
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    const p = sorted[i]
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop()
    upper.push(p)
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1))
}

function polygonAreaMeters(ring, center) {
  if (!Array.isArray(ring) || ring.length < 4) return 0
  const projected = ring.slice(0, -1).map((coord) => projectedPoint(coord, center))
  let area = 0
  for (let i = 0; i < projected.length; i += 1) {
    const a = projected[i]
    const b = projected[(i + 1) % projected.length]
    area += a.x * b.y - b.x * a.y
  }
  return Math.abs(area / 2)
}

function circlePolygonAround(center, radiusMeters, steps = 48) {
  return haloPolygon(center[0], center[1], Math.max(1, radiusMeters), steps)
}

function lineCapsulePolygon(a, b, radiusMeters) {
  const center = [(a[0] + shortestLngDelta(a[0], b[0]) / 2), (a[1] + b[1]) / 2]
  const pa = projectedPoint(a, center)
  const pb = projectedPoint(b, center)
  const dx = pb.x - pa.x
  const dy = pb.y - pa.y
  const len = Math.sqrt(dx * dx + dy * dy) || 1
  const nx = -dy / len
  const ny = dx / len
  const ring = [
    unprojectedPoint({ x: pa.x + nx * radiusMeters, y: pa.y + ny * radiusMeters }, center),
    unprojectedPoint({ x: pb.x + nx * radiusMeters, y: pb.y + ny * radiusMeters }, center),
    unprojectedPoint({ x: pb.x - nx * radiusMeters, y: pb.y - ny * radiusMeters }, center),
    unprojectedPoint({ x: pa.x - nx * radiusMeters, y: pa.y - ny * radiusMeters }, center),
  ]
  ring.push(ring[0])
  return { type: 'Polygon', coordinates: [ring] }
}

function territoryPolygonForCoords(coords, fallbackRadiusMeters) {
  const clean = (coords || [])
    .map((coord) => [Number(coord?.[0]), Number(coord?.[1])])
    .filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat))
  if (!clean.length) return null
  const center = [
    clean.reduce((sum, coord) => sum + coord[0], 0) / clean.length,
    clean.reduce((sum, coord) => sum + coord[1], 0) / clean.length,
  ]
  if (clean.length === 1) return circlePolygonAround(center, Math.min(Math.max(250, fallbackRadiusMeters), 2500))
  if (clean.length === 2) return lineCapsulePolygon(clean[0], clean[1], Math.min(Math.max(180, fallbackRadiusMeters / 3), 1800))
  const hull = convexHull(clean.map((coord) => projectedPoint(coord, center)))
  if (hull.length < 3) return circlePolygonAround(center, Math.min(Math.max(250, fallbackRadiusMeters), 2500))
  const ring = hull.map((point) => unprojectedPoint(point, center))
  ring.push(ring[0])
  return {
    type: 'Polygon',
    coordinates: [ring],
  }
}

function territoryComponents(coords, spreadMeters) {
  const clean = (coords || [])
    .map((coord) => [Number(coord?.[0]), Number(coord?.[1])])
    .filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat))
  if (clean.length <= 1) return clean.length ? [clean] : []
  const center = [
    clean.reduce((sum, coord) => sum + coord[0], 0) / clean.length,
    clean.reduce((sum, coord) => sum + coord[1], 0) / clean.length,
  ]
  const { lngScale, latScale } = meterScalesForLat(center[1])
  const radius = Math.max(1, Number(spreadMeters) || 2500)
  const buckets = new Map()
  const parent = clean.map((_, idx) => idx)
  const find = (idx) => {
    while (parent[idx] !== idx) {
      parent[idx] = parent[parent[idx]]
      idx = parent[idx]
    }
    return idx
  }
  const unite = (a, b) => {
    const ra = find(a)
    const rb = find(b)
    if (ra !== rb) parent[rb] = ra
  }
  const bucketKey = (x, y) => `${x},${y}`
  const projected = clean.map((coord, idx) => {
    const point = {
      x: shortestLngDelta(center[0], coord[0]) * lngScale,
      y: (coord[1] - center[1]) * latScale,
      coord,
      idx,
    }
    const bx = Math.floor(point.x / radius)
    const by = Math.floor(point.y / radius)
    for (let ox = -1; ox <= 1; ox += 1) {
      for (let oy = -1; oy <= 1; oy += 1) {
        const nearby = buckets.get(bucketKey(bx + ox, by + oy)) || []
        for (const other of nearby) {
          const dx = point.x - other.x
          const dy = point.y - other.y
          if ((dx * dx + dy * dy) <= radius * radius) unite(idx, other.idx)
        }
      }
    }
    const key = bucketKey(bx, by)
    buckets.set(key, [...(buckets.get(key) || []), point])
    return point
  })
  const groups = new Map()
  for (const point of projected) {
    const root = find(point.idx)
    if (!groups.has(root)) groups.set(root, [])
    groups.get(root).push(point.coord)
  }
  return [...groups.values()]
}

function expandHex(hex) {
  const raw = String(hex || '').trim()
  const match = raw.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (!match) return null
  const value = match[1]
  const full = value.length === 3
    ? value.split('').map((c) => c + c).join('')
    : value
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ]
}

function territorySolidColor(color, opacity) {
  const rgb = expandHex(color)
  if (!rgb) return color || FALLBACK_COLOR
  const mix = 1 - clamp01(opacity, 0.18)
  const channels = rgb.map((value) => Math.round(value * (1 - mix) + 255 * mix))
  return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`
}

function buildTerritorySurfaceFeatureCollection() {
  const features = []
  for (const candidate of collectTerritoryCandidates()) {
    const coords = (candidate.features || []).map(featureCoord).filter(Boolean)
    for (const component of territoryComponents(coords, candidate.radius)) {
      const geometry = territoryPolygonForCoords(component, candidate.radius)
      if (!geometry) continue
      const center = [
        component.reduce((sum, coord) => sum + coord[0], 0) / component.length,
        component.reduce((sum, coord) => sum + coord[1], 0) / component.length,
      ]
      const area = polygonAreaMeters(geometry.coordinates?.[0], center)
      features.push({
        type: 'Feature',
        geometry,
        properties: {
          color: territorySolidColor(candidate.color, candidate.opacity),
          sort_key: candidate.priority - area / 1000000,
        },
      })
    }
  }
  return { type: 'FeatureCollection', features }
}

let territoryRebuildFrame = 0
function scheduleTerritorySurfaceRebuild() {
  if (!map || !map.getStyle()) return
  if (territoryRebuildFrame) return
  territoryRebuildFrame = window.requestAnimationFrame(() => {
    territoryRebuildFrame = 0
    syncTerritorySurface()
  })
}

function syncTerritorySurface() {
  if (!map || !map.getStyle()) return
  const sid = territorySurfaceSourceId()
  const lid = territorySurfaceLayerId()
  const data = buildTerritorySurfaceFeatureCollection()
  if (!data.features.length) {
    if (map.getLayer(lid)) map.removeLayer(lid)
    if (map.getSource(sid)) map.removeSource(sid)
    return
  }
  const src = map.getSource(sid)
  if (src && src.type === 'geojson') {
    src.setData(data)
  } else {
    map.addSource(sid, { type: 'geojson', data })
  }
  const paint = {
    'fill-color': ['get', 'color'],
    'fill-opacity': 1,
  }
  const beforeId = territoryWaterBeforeId()
  if (!map.getLayer(lid)) {
    map.addLayer({
      id: lid,
      type: 'fill',
      source: sid,
      layout: {
        'fill-sort-key': ['coalesce', ['to-number', ['get', 'sort_key']], 0],
      },
      paint,
    }, beforeId || undefined)
  } else {
    map.setLayoutProperty(lid, 'fill-sort-key', ['coalesce', ['to-number', ['get', 'sort_key']], 0])
    for (const [key, value] of Object.entries(paint)) map.setPaintProperty(lid, key, value)
    if (beforeId) {
      try { map.moveLayer(lid, beforeId) } catch (_) {}
    }
  }
}

function _addLayerOnMap(layerName, renderContext = null) {
  // `isStyleLoaded()` returns false when basemap tile sources are
  // still loading, which can take 1-2s after a `setStyle`. But
  // addSource/addLayer only need the style *object* to be loaded,
  // not the sources. `map.getStyle()` returns null until the style
  // is parseable; once it returns a non-null object we can safely
  // add sources and layers to it.
  if (!map || !map.getStyle()) {
    return
  }
  const fc = renderContext?.fc || layerStore.getDisplayFeatures(layerName)
  if (!fc) return
  const parentName = renderContext?.parentName || parentLayerName(layerName)
  const renderName = renderContext?.renderName || layerName
  _renderedLayerNames.add(renderName)
  const layerDoc = renderContext?.layerDoc || layerStore.layers.find((l) => l.name === parentName)
  if (!layerDoc) return
  const style = renderContext?.style || (renderName !== parentName ? fc?.layer?.style : null) || layerStore.getLayerStyle(parentName) || {}
  const color = style.color || layerDoc.color || FALLBACK_COLOR
  const sizeKey = style.size || layerDoc.size || 'm'
  const radius = SIZE_TO_RADIUS[sizeKey] || SIZE_TO_RADIUS.m
  const wantsCluster = !!(style.cluster || layerDoc.cluster)
  const enabled = (layerDoc.enabled !== false && layerDoc.enabled !== 0) && !layerStore.locallyHidden.has(layerDoc.name)
  const heatOn = enabled && ui.isHeatmapOn(parentName)
  const minPinZoom = pinMinZoom(layerDoc, style)

  if (!renderContext && renderName === parentName) {
    const virtualGroups = buildVirtualGroupLayers(layerName, fc, layerDoc, style, color)
    if (virtualGroups.length) {
      _removeBasePointArtifacts(layerName)
      if (enabled && territoryEnabled(layerDoc, style)) {
        layerStore.fetchVirtualGroupTerritoryFeatures(layerName, virtualGroups.map((group) => ({
          cacheKey: group.renderName,
          groupKey: group.groupKey,
        }))).then(() => {
          scheduleTerritorySurfaceRebuild()
        }).catch((e) => {
          console.warn('[expedition] territory group fetch failed', layerName, e)
        })
      }
      const activeRenderNames = new Set()
      const groupsToFetch = []
      for (const group of virtualGroups) {
        activeRenderNames.add(group.renderName)
        if (group.fc) {
          _addLayerOnMap(group.renderName, group)
        }
        const fetchKey = JSON.stringify({
          parentFetched: layerStore.lastFetched?.[layerName] || 0,
          bounds: layerStore.lastBounds?.[layerName] || null,
          groupKey: group.groupKey,
        })
        if (_virtualGroupFetchKeys[group.renderName] !== fetchKey && !layerStore.loading?.[group.renderName]) {
          _virtualGroupFetchKeys[group.renderName] = fetchKey
          groupsToFetch.push({
            cacheKey: group.renderName,
            groupKey: group.groupKey,
          })
        }
      }
      if (groupsToFetch.length) {
        layerStore.fetchVirtualGroupFeatures(
          layerName,
          layerStore.lastBounds?.[layerName] || null,
          groupsToFetch,
        ).catch((e) => {
          for (const group of groupsToFetch) delete _virtualGroupFetchKeys[group.cacheKey]
          console.warn('[expedition] virtual group batch fetch failed', layerName, e)
        })
      }
      _pruneGroupedArtifacts(layerName, activeRenderNames)
      scheduleTerritorySurfaceRebuild()
      lastLoadedFeatures[layerName] = fc
      return
    }
    _removeGroupedArtifacts(layerName)
  }

  const sid = sourceId(renderName)
  const lid = layerId(renderName)
  const shid = shadowLayerId(renderName)
  const rid = ringLayerId(renderName)

  const iconSpecs = new Map()
  const sourceFeatures = (fc.features || []).map((feature) => {
    const [lng, lat] = (feature.geometry && feature.geometry.coordinates) || []
    const icon = featureDisplayIcon(feature, style.icon || layerDoc.icon)
    const haloProps = {
      _halo_radius_meters: haloRadiusMeters(feature, parentName),
      _halo_lat: Number.isFinite(Number(lat)) ? Number(lat) : 0,
    }
    if (!icon) {
      return {
        ...feature,
        properties: {
          ...(feature.properties || {}),
          ...haloProps,
        },
      }
    }
    const iconColor = featureDisplayColor(feature, color)
    const spec = iconRenderSpec(icon, iconColor)
    if (!spec) return feature
    iconSpecs.set(spec.imageId, spec)
    return {
      ...feature,
      properties: {
        ...(feature.properties || {}),
        ...haloProps,
        _display_icon_image: spec.imageId,
      },
    }
  })

  const sourceSpec = {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: sourceFeatures },
    promoteId: '_id',
  }
  if (wantsCluster) {
    sourceSpec.cluster = true
    sourceSpec.clusterRadius = 40
    sourceSpec.clusterMaxZoom = CLUSTER_MAX_ZOOM
  }

  let existing = map.getSource(sid)
  const existingStyleSource = map.getStyle()?.sources?.[sid]
  const existingWantsCluster = existingStyleSource
    ? !!existingStyleSource.cluster
    : _sourceClusterState[sid]
  if (existing && existingWantsCluster !== undefined && existingWantsCluster !== wantsCluster) {
    _removeRenderArtifacts(renderName)
    existing = null
  }
  if (existing && existing.type === 'geojson') {
    existing.setData(sourceSpec.data)
  } else {
    map.addSource(sid, sourceSpec)
  }
  _sourceClusterState[sid] = wantsCluster
  lastLoadedFeatures[layerName] = fc

  if (!renderContext && enabled && territoryEnabled(layerDoc, style)) {
    layerStore.fetchTerritoryFeatures(layerName).then(() => {
      scheduleTerritorySurfaceRebuild()
    }).catch((e) => {
      console.warn('[expedition] territory fetch failed', layerName, e)
    })
  }
  scheduleTerritorySurfaceRebuild()

  // Z-order: shadow → ring → pin. Shadow first so it renders behind.
  // 1. Shadow
  if (!map.getLayer(shid)) {
    map.addLayer({
      id: shid,
      type: 'circle',
      source: sid,
      ...layerZoomOptions(minPinZoom),
      filter: circlePointFilter(wantsCluster),
      paint: shadowPaint(radius),
    })
  } else {
    applyLayerZoomRange(shid, minPinZoom)
    map.setFilter(shid, circlePointFilter(wantsCluster))
    map.setPaintProperty(shid, 'circle-radius', shadowPaint(radius)['circle-radius'])
  }
  // 2. White ring
  if (!map.getLayer(rid)) {
    map.addLayer({
      id: rid,
      type: 'circle',
      source: sid,
      ...layerZoomOptions(minPinZoom),
      filter: circlePointFilter(wantsCluster),
      paint: ringPaint(radius),
    })
  } else {
    applyLayerZoomRange(rid, minPinZoom)
    map.setFilter(rid, circlePointFilter(wantsCluster))
    map.setPaintProperty(rid, 'circle-radius', ringPaint(radius)['circle-radius'])
  }
  // 3. Pin body
  if (!map.getLayer(lid)) {
    map.addLayer({
      id: lid,
      type: 'circle',
      source: sid,
      ...layerZoomOptions(minPinZoom),
      filter: circlePointFilter(wantsCluster),
      paint: pinPaint(color, radius),
    })
  } else {
    applyLayerZoomRange(lid, minPinZoom)
    map.setFilter(lid, circlePointFilter(wantsCluster))
    map.setPaintProperty(lid, 'circle-color', pinColorExpression(color))
    map.setPaintProperty(lid, 'circle-radius', pinPaint(color, radius)['circle-radius'])
  }
  // Toggle via opacity (cheap; no add/remove).
  if (enabled) {
    map.setPaintProperty(lid, 'circle-opacity', 1)
    map.setPaintProperty(rid, 'circle-opacity', 1)
    map.setPaintProperty(shid, 'circle-opacity', 0.45)
  } else {
    map.setPaintProperty(lid, 'circle-opacity', 0)
    map.setPaintProperty(rid, 'circle-opacity', 0)
    map.setPaintProperty(shid, 'circle-opacity', 0)
  }

  // 4. Icon glyph (optional). Sits above the pin body so it reads as
  // "icon over dot". On clustered sources, the filter keeps glyphs on
  // individual pins only; cluster bubbles keep their own visual language.
  const iid = iconLayerId(renderName)
  const iconSpecList = [...iconSpecs.values()]
  if (iconSpecList.length) {
    const filter = iconPointFilter(wantsCluster)
    // Register the sprite image on the map (no-op if already present).
    const iconSize = iconSizeForRadius(radius)
    registerColoredIcons(map, iconSpecList, ICON_IMAGE_SIZE).then(() => {
      if (!map.getLayer(iid)) {
        map.addLayer({
          id: iid,
          type: 'symbol',
          source: sid,
          ...layerZoomOptions(minPinZoom),
          filter,
          layout: {
            'icon-image': ['get', '_display_icon_image'],
            // Match the same pixel footprint as the circle pin body:
            // xs/s/m/l/xl map to radius*2 pixels.
            'icon-size': iconSize,
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
            'icon-anchor': 'center',
          },
          paint: {
            'icon-opacity': enabled ? 1 : 0,
          },
        })
      } else {
        // Layer exists; refresh the icon image and color.
        applyLayerZoomRange(iid, minPinZoom)
        map.setFilter(iid, filter)
        map.setLayoutProperty(iid, 'icon-image', ['get', '_display_icon_image'])
        map.setLayoutProperty(iid, 'icon-size', iconSize)
        map.setPaintProperty(iid, 'icon-opacity', enabled ? 1 : 0)
      }
    }).catch((e) => {
      console.warn('[expedition] icon register failed', iconSpecList, e)
    })
  } else if (map.getLayer(iid)) {
    map.removeLayer(iid)
  }

  // Cluster layers
  const cid = clusterLayerId(renderName)
  const ccid = clusterCountId(renderName)
  if (wantsCluster) {
    if (!map.getLayer(cid)) {
      map.addLayer({
        id: cid,
        type: 'circle',
        source: sid,
        ...layerZoomOptions(minPinZoom),
        filter: ['has', 'point_count'],
        paint: clusterPaint(color),
      })
    } else {
      applyLayerZoomRange(cid, minPinZoom)
      map.setPaintProperty(cid, 'circle-color', color)
    }
    if (!map.getLayer(ccid)) {
      map.addLayer({
        id: ccid,
        type: 'symbol',
        source: sid,
        ...layerZoomOptions(minPinZoom),
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 11,
          'text-font': ['Noto Sans Regular'],
        },
        paint: { 'text-color': '#FFFFFF' },
      })
    } else {
      applyLayerZoomRange(ccid, minPinZoom)
    }
    map.off('click', cid, onClusterClick)
    map.on('click', cid, onClusterClick)
  } else {
    if (map.getLayer(ccid)) map.removeLayer(ccid)
    if (map.getLayer(cid)) map.removeLayer(cid)
    map.off('click', cid, onClusterClick)
  }

  // Extrusion / 3D columns (PR-9). Sits BELOW everything else so the
  // pin stack + halos + heatmap all read on top of the columns.
  // MapLibre's fill-extrusion needs polygon geometry, so we maintain
  // a parallel polygon source (one 10m square per point).
  const eid = extrusionLayerId(renderName)
  const esid = extrusionSourceId(renderName)
  const extrudeOn = enabled && ui.pitchEnabled
  if (extrudeOn) {
    const polyFc = _extrusionFeatureCollection(fc, parentName)
    const esrc = map.getSource(esid)
    if (esrc && esrc.type === 'geojson') {
      esrc.setData(polyFc)
    } else {
      map.addSource(esid, { type: 'geojson', data: polyFc, promoteId: '_id' })
    }
    if (!map.getLayer(eid)) {
      map.addLayer({
        id: eid,
        type: 'fill-extrusion',
        source: esid,
        ...layerZoomOptions(minPinZoom),
        paint: extrusionPaint(color, parentName),
      })
    } else {
      applyLayerZoomRange(eid, minPinZoom)
      map.setPaintProperty(eid, 'fill-extrusion-color', color)
      map.setPaintProperty(eid, 'fill-extrusion-height', extrusionPaint(color, parentName)['fill-extrusion-height'])
    }
  } else if (map.getLayer(eid)) {
    map.removeLayer(eid)
    if (map.getSource(esid)) map.removeSource(esid)
  }

  // Halo / radius rendering (PR-8). Uses the same clustered point
  // source as the pins so halo visibility matches pin visibility in
  // the same render pass, including mixed cluster/unclustered states.
  const haloId = haloLayerId(renderName)
  const haloStrokeId = haloStrokeLayerId(renderName)
  const legacyHaloSid = haloSourceId(renderName)
  if (map.getLayer(haloStrokeId)) map.removeLayer(haloStrokeId)
  if (map.getLayer(haloId) && map.getStyle()?.layers?.find((layer) => layer.id === haloId)?.type !== 'circle') {
    map.removeLayer(haloId)
  }
  if (map.getSource(legacyHaloSid)) map.removeSource(legacyHaloSid)
  const radiusOn = enabled && ui.isRadiusOn(parentName)
  if (radiusOn) {
    const haloPaint = haloCirclePaint(color, parentName)
    if (!map.getLayer(haloId)) {
      map.addLayer({
        id: haloId,
        type: 'circle',
        source: sid,
        ...layerZoomOptions(minPinZoom),
        filter: pointFilter(wantsCluster),
        paint: haloPaint,
      }, shid)
    } else {
      applyLayerZoomRange(haloId, minPinZoom)
      map.setFilter(haloId, pointFilter(wantsCluster))
      for (const [key, value] of Object.entries(haloPaint)) map.setPaintProperty(haloId, key, value)
    }
  } else {
    if (map.getLayer(haloId)) map.removeLayer(haloId)
  }

  // Heatmap (PR-7). Adds a heatmap layer above the source and
  // (importantly) above the cluster circles when those exist, so the
  // density visualization dominates. When heatmap is on, the pin body
  // layers go invisible (they would otherwise crowd the gradient).
  const hid = heatmapLayerId(renderName)
  if (heatOn) {
    const paint = heatmapPaint(color, layerDoc, style)
    if (!map.getLayer(hid)) {
      map.addLayer({
        id: hid,
        type: 'heatmap',
        source: sid,
        paint,
      })
    } else {
      for (const [key, value] of Object.entries(paint)) {
        map.setPaintProperty(hid, key, value)
      }
    }
    // Hide the pin-stack layers — heatmap replaces them at low zoom.
    map.setPaintProperty(lid, 'circle-opacity', 0)
    map.setPaintProperty(rid, 'circle-opacity', 0)
    map.setPaintProperty(shid, 'circle-opacity', 0)
    if (map.getLayer(iid)) map.setPaintProperty(iid, 'icon-opacity', 0)
    // Clusters show on top of the heatmap at very low zooms (clickable),
    // but only when the layer itself is enabled.
    if (map.getLayer(cid)) map.setLayoutProperty(cid, 'visibility', enabled ? 'visible' : 'none')
    if (map.getLayer(ccid)) map.setLayoutProperty(ccid, 'visibility', enabled ? 'visible' : 'none')
  } else {
    if (map.getLayer(hid)) map.removeLayer(hid)
    // Toggle all pin-stack layers via visibility so they're fully removed
    // from hit-testing when the layer is hidden (opacity:0 still fires events).
    const vis = enabled ? 'visible' : 'none'
    map.setLayoutProperty(lid, 'visibility', vis)
    map.setLayoutProperty(rid, 'visibility', vis)
    map.setLayoutProperty(shid, 'visibility', vis)
    if (map.getLayer(iid)) map.setLayoutProperty(iid, 'visibility', vis)
    if (map.getLayer(cid)) map.setLayoutProperty(cid, 'visibility', vis)
    if (map.getLayer(ccid)) map.setLayoutProperty(ccid, 'visibility', vis)
  }

  _bindPointLayerHandlers(lid)
  _bindPointLayerHandlers(iid)

  // Execute the layer-level custom JS script if present
  if (layerDoc && layerDoc.layer_script) {
    try {
      const runLayerScript = new Function('Expedition', 'map', 'layer', 'event', layerDoc.layer_script)
      runLayerScript(window.Expedition, map, layerDoc, 'add')
    } catch (err) {
      console.error('[expedition] Failed to execute layer script (add):', layerDoc.name, err)
    }
  }

  // Force a redraw. Without this, on first paint the canvas can
  // appear blank even though the source + layers exist — MapLibre
  // has been observed to skip the next render frame when a source
  // is added during an idle tick.
  if (typeof map.triggerRepaint === 'function') map.triggerRepaint()
}

function onPointClick(e) {
  const f = e.features && e.features[0]
  if (!f) return
  e.preventDefault?.()
  // The clicked layer id is one of: lyr-<name>, lyr-<name>-ring,
  // lyr-<name>-shadow, lyr-<name>-clusters. Strip the prefix and
  // any of the suffixes to recover the layer name.
  const rawId = e.targetLayer || (f.layer && f.layer.id) || ''
  const renderName = layerNameFromRenderedLayerId(rawId)
  const layerName = parentLayerName(renderName)
  const fc = layerStore.getDisplayFeatures(layerName)
  const layerMeta = fc?.layer || layerStore.layers.find((l) => l.name === layerName)
  const action = layerMeta?.click_action || 'popup'
  if (action === 'none') return
  if (action === 'open_form' || action === 'redirect') {
    const props = f.properties || {}
    if (props._doctype && props._name) {
      openDeskDoc(props._doctype, props._name)
      return
    }
  }
  // Clear previous selection on the source, then mark the clicked one.
  const sid = sourceId(renderName)
  const src = map.getSource(sid)
  if (src && f.id != null) {
    // Clear all features' selected state.
    for (const feat of layerStore.getDisplayFeatures(renderName)?.features || layerStore.getDisplayFeatures(layerName)?.features || []) {
      if (feat._id != null && feat._id !== f.id) {
        try { map.setFeatureState({ source: sid, id: feat._id }, { selected: false }) } catch (_) {}
      }
    }
    try { map.setFeatureState({ source: sid, id: f.id }, { selected: true }) } catch (_) {}
  }
  ui.selectedFeature = {
    layer: layerMeta || { name: layerName },
    properties: f.properties || {},
    _id: f.id,
    // Capture the click point so MapPopup can anchor + camera-follow.
    _lngLat: { lng: e.lngLat.lng, lat: e.lngLat.lat },
  }
}

function onPointMouseEnter() {
  _setMapCursor('pointer')
}

function onPointMouseLeave() {
  _setMapCursor()
}

async function onClusterClick(e) {
  const cid = (e.targetLayer || (e.features && e.features[0]?.layer?.id) || '')
  const renderName = layerNameFromRenderedLayerId(cid)
  const queryLayer = map.getLayer(cid) ? cid : clusterLayerId(renderName)
  if (!map.getLayer(queryLayer)) return
  const rendered = map.queryRenderedFeatures(e.point, { layers: [queryLayer] })
  const activeFeature = rendered[0]
  const activeClusterId = activeFeature?.properties?.cluster_id
  if (activeClusterId == null) return
  const src = map.getSource(sourceId(renderName))
  if (!src) return
  try {
    const zoom = await src.getClusterExpansionZoom(activeClusterId)
    map.easeTo({
      center: activeFeature.geometry.coordinates,
      zoom,
      duration: 500,
      easing: t => 1 - Math.pow(1 - t, 3),
    })
  } catch (_) { /* cluster can race — safe to ignore */ }
}

function _removeLayerFromMap(layerName) {
  if (!map) return
  // Fetch layerDoc to execute its lifecycle script for 'remove'
  const layerDoc = layerStore.layers.find((l) => l.name === layerName)
  if (layerDoc && layerDoc.layer_script) {
    try {
      const runLayerScript = new Function('Expedition', 'map', 'layer', 'event', layerDoc.layer_script)
      runLayerScript(window.Expedition, map, layerDoc, 'remove')
    } catch (err) {
      console.error('[expedition] Failed to execute layer script (remove):', layerDoc.name, err)
    }
  }
  _removeGroupedArtifacts(layerName)
  const groupPrefix = `${layerName}${VIRTUAL_GROUP_SEPARATOR}`
  for (const key of Object.keys(_virtualGroupFetchKeys)) {
    if (key.startsWith(groupPrefix)) delete _virtualGroupFetchKeys[key]
  }
  _removeRenderArtifacts(layerName)
  scheduleTerritorySurfaceRebuild()
}

function _fetchAllVisibleBounds() {
  if (!map || !layerStore.visibleLayers.length) return
  // Search is a session-local filtered feature set. Viewport refetches
  // would replace those filtered sources with whatever happens to be
  // inside the new viewport, so preserve the search result until the
  // user clears search.
  if (layerStore.activeSearch) return
  for (const layer of layerStore.visibleLayers) {
    // First fetch per layer per map is unbounded (null) so the
    // server returns up to 5000 rows from the whole source table.
    // Subsequent moveend fetches are viewport-bounded. Without this,
    // the boot-time viewport is almost certainly outside the data
    // envelope and the server returns zero features, leaving the
    // canvas empty until the user moves the map.
    if (!_layerFetchedOnce.has(layer.name)) {
      _layerFetchedOnce.add(layer.name)
      layerStore.fetchFeatures(layer.name, null)
      continue
    }
    layerStore.fetchFeatures(layer.name, viewportBoundsForServer(map.getBounds()))
  }
}

function _applyIndiaBoundaryFix() {
  if (!map || !map.isStyleLoaded()) return

  // 1. Define the exclusion expression to hide boundary lines touching India
  const excludeIndiaExpression = [
    '!',
    [
      'any',
      ['==', ['get', 'adm0_l'], 'IN'],
      ['==', ['get', 'adm0_r'], 'IN'],
      ['==', ['get', 'adm0_l'], 'IND'],
      ['==', ['get', 'adm0_r'], 'IND'],
      ['==', ['get', 'adm0_l'], 'India'],
      ['==', ['get', 'adm0_r'], 'India'],
      ['==', ['get', 'adm0_l'], 'in'],
      ['==', ['get', 'adm0_r'], 'in'],
      ['==', ['get', 'adm0_l'], 'ind'],
      ['==', ['get', 'adm0_r'], 'ind'],
      ['==', ['get', 'adm0_l'], 'india'],
      ['==', ['get', 'adm0_r'], 'india'],
      ['==', ['get', 'adm0_left'], 'IN'],
      ['==', ['get', 'adm0_right'], 'IN'],
      ['==', ['get', 'adm0_left'], 'IND'],
      ['==', ['get', 'adm0_right'], 'IND'],
      ['==', ['get', 'adm0_left'], 'India'],
      ['==', ['get', 'adm0_right'], 'India']
    ]
  ]

  // Helper to safely append the exclusion to an existing filter
  const applyFilterExclusion = (layerId) => {
    if (!map.getLayer(layerId)) return
    try {
      let currentFilter = map.getFilter(layerId)
      if (!currentFilter) {
        currentFilter = ['all']
      } else if (!Array.isArray(currentFilter)) {
        currentFilter = ['all', currentFilter]
      } else if (currentFilter[0] !== 'all') {
        currentFilter = ['all', currentFilter]
      }
      
      // Check if we already added the exclusion to avoid duplicate additions
      const alreadyAdded = currentFilter.some(subExpr => 
        Array.isArray(subExpr) && 
        subExpr[0] === '!' && 
        Array.isArray(subExpr[1]) && 
        subExpr[1][0] === 'any' &&
        subExpr[1].some(valExpr => Array.isArray(valExpr) && valExpr[1]?.[1] === 'adm0_l' && (valExpr[2] === 'IN' || valExpr[2] === 'IND'))
      )

      if (!alreadyAdded) {
        currentFilter.push(excludeIndiaExpression)
        map.setFilter(layerId, currentFilter)
      }
    } catch (e) {
      console.error(`Error applying filter exclusion to ${layerId}:`, e)
    }
  }

  // Apply the exclusion to both boundary_2 and boundary_disputed layers
  applyFilterExclusion('boundary_2')
  applyFilterExclusion('boundary_disputed')

  // 2. Add the custom official Indian land boundary GeoJSON source and layer
  const sourceId = 'india-boundary-source'
  const layerId = 'india-boundary-layer'

  if (!map.getSource(sourceId)) {
    try {
      const geojsonUrl = `${window.location.origin}/assets/expedition/india-land-simplified.geojson`
      map.addSource(sourceId, {
        type: 'geojson',
        data: geojsonUrl
      })
    } catch (e) {
      console.error('Error adding india-boundary-source:', e)
    }
  }

  if (map.getSource(sourceId) && !map.getLayer(layerId)) {
    try {
      // Standard Mapbox/OpenMapTiles styling values for boundary_2 (fallback values)
      let paintProps = {
        'line-color': 'hsl(248,1%,41%)',
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 0, 0.4, 4, 1],
        'line-width': ['interpolate', ['linear'], ['zoom'], 3, 1, 5, 1.2, 12, 3]
      }

      // If boundary_2 exists, dynamically extract its style properties to match the current skin
      const boundary2 = map.getLayer('boundary_2')
      if (boundary2) {
        const color = map.getPaintProperty('boundary_2', 'line-color')
        if (color) paintProps['line-color'] = color
        const opacity = map.getPaintProperty('boundary_2', 'line-opacity')
        if (opacity) paintProps['line-opacity'] = opacity
        const width = map.getPaintProperty('boundary_2', 'line-width')
        if (width) paintProps['line-width'] = width
      }

      // Find the first symbol/label layer to insert our border layer before it
      // so that city and country labels are drawn on top of the border line
      const layers = map.getStyle().layers || []
      const firstSymbol = layers.find(l => l.type === 'symbol')
      const beforeId = firstSymbol ? firstSymbol.id : undefined

      map.addLayer({
        id: layerId,
        type: 'line',
        source: sourceId,
        layout: {
          'line-cap': 'round',
          'line-join': 'round'
        },
        paint: paintProps
      }, beforeId)
    } catch (e) {
      console.error('Error adding india-boundary-layer:', e)
    }
  }
}

function _reAddAllLayers() {
  if (!map || !map.isStyleLoaded()) return
  _pruneStaleRenderedLayers()
  for (const layer of layerStore.layers) {
    if (layerStore.features[layer.name] || layerStore.getDisplayFeatures(layer.name)) {
      _addLayerOnMap(layer.name)
    }
  }
  scheduleTerritorySurfaceRebuild()
}

// -------- Zones (drawn regions) --------
//
// All zones are merged into a single GeoJSON FeatureCollection per
// map. The fill color is per-feature via `data-color`; the stroke is
// `data-stroke`. We use data-driven paint expressions so each zone
// keeps its own color without needing a layer per zone.

function _zonesFeatureCollection() {
  const mapName = mapStore.activeMap?.map?.name
  const list = (mapName && zoneStore.byMap[mapName]) || []
  // Apply the tag filter: if `ui.zoneTags` is non-empty, only zones
  // whose tag is in the filter list pass through. Empty filter means
  // "show all" (the default).
  const tagFilter = ui.zoneTags || []
  return {
    type: 'FeatureCollection',
    features: list
      .filter((z) => {
        if (!z.geometry || z.zone_type !== 'polygon') return false
        if (tagFilter.length === 0) return true
        if (!z.tag) return false
        return tagFilter.includes(z.tag)
      })
      .map((z) => ({
        type: 'Feature',
        id: z.name,
        geometry: z.geometry,
        properties: {
          name: z.name,
          title: z.title,
          color: z.color || '#3B82F6',
          stroke: z.stroke_color || '#1E40AF',
          fill_opacity: z.fill_opacity ?? 0.25,
          stroke_width: z.stroke_width ?? 2,
          stroke_style: z.stroke_style || 'solid',
          selected: ui.selectedZone?.name === z.name ? 1 : 0,
        },
      })),
  }
}

function _reAddZones() {
  if (!map || !map.getStyle()) return
  const fc = _zonesFeatureCollection()
  const sid = zoneSourceId()
  if (map.getSource(sid)) {
    map.getSource(sid).setData(fc)
  } else {
    map.addSource(sid, { type: 'geojson', data: fc, promoteId: 'name' })
  }
  // Fill layer (below labels / pins)
  if (!map.getLayer(zoneFillLayerId())) {
    map.addLayer({
      id: zoneFillLayerId(),
      type: 'fill',
      source: sid,
      paint: {
        'fill-color': ['coalesce', ['get', 'color'], '#3B82F6'],
        'fill-opacity': ['coalesce', ['get', 'fill_opacity'], 0.25],
      },
    })
  }
  // Stroke layers. `line-dasharray` is not consistently data-driven
  // across MapLibre versions, so style variants are separate layers.
  const strokePaint = {
    'line-color': ['coalesce', ['get', 'stroke'], '#1E40AF'],
    'line-width': ['coalesce', ['get', 'stroke_width'], 2],
  }
  const strokeLayers = [
    {
      id: zoneStrokeLayerId(),
      filter: ['any', ['!', ['has', 'stroke_style']], ['==', ['get', 'stroke_style'], 'solid']],
      dash: [1, 0],
    },
    {
      id: zoneDashedStrokeLayerId(),
      filter: ['==', ['get', 'stroke_style'], 'dashed'],
      dash: [2, 1.5],
    },
    {
      id: zoneDottedStrokeLayerId(),
      filter: ['==', ['get', 'stroke_style'], 'dotted'],
      dash: [0.5, 1.5],
    },
  ]
  for (const layer of strokeLayers) {
    if (!map.getLayer(layer.id)) {
      map.addLayer({
        id: layer.id,
        type: 'line',
        source: sid,
        filter: layer.filter,
        paint: { ...strokePaint, 'line-dasharray': layer.dash },
      })
    } else {
      map.setFilter(layer.id, layer.filter)
      map.setPaintProperty(layer.id, 'line-color', strokePaint['line-color'])
      map.setPaintProperty(layer.id, 'line-width', strokePaint['line-width'])
      map.setPaintProperty(layer.id, 'line-dasharray', layer.dash)
    }
  }
  if (!map.getLayer(zoneSelectedLayerId())) {
    map.addLayer({
      id: zoneSelectedLayerId(),
      type: 'line',
      source: sid,
      filter: ['==', ['get', 'selected'], 1],
      paint: {
        'line-color': '#F59E0B',
        'line-width': ['+', ['coalesce', ['get', 'stroke_width'], 2], 3],
      },
    })
  } else {
    map.setFilter(zoneSelectedLayerId(), ['==', ['get', 'selected'], 1])
  }
  // Label layer (centroid + title) — uses the centroid we already store
  // server-side so labels stay anchored when the polygon is irregular.
  const mapName = mapStore.activeMap?.map?.name
  const list = (mapName && zoneStore.byMap[mapName]) || []
  // Reuse the same tag filter as the polygon layer.
  const tagFilter = ui.zoneTags || []
  const labelFC = {
    type: 'FeatureCollection',
    features: list
      .filter((z) => {
        if (z.zone_type !== 'polygon') return false
        if (z.centroid_lat == null || z.centroid_lng == null) return false
        if (tagFilter.length === 0) return true
        if (!z.tag) return false
        return tagFilter.includes(z.tag)
      })
      .map((z) => ({
        type: 'Feature',
        id: z.name + '-label',
        geometry: {
          type: 'Point',
          coordinates: [z.centroid_lng, z.centroid_lat],
        },
        // Carry color so the symbol paint expression can pick it up.
        properties: { title: z.title || z.name, color: z.color || '#8B9DB8' },
      })),
  }
  const labelSourceId = 'src-zones-labels'
  if (map.getSource(labelSourceId)) {
    map.getSource(labelSourceId).setData(labelFC)
  } else {
    map.addSource(labelSourceId, {
      type: 'geojson',
      data: labelFC,
    })
  }
  // Build a color lookup so labels can match their zone.
  const zoneColorMap = {}
  for (const z of list) {
    if (z.color) zoneColorMap[z.name] = z.color
  }
  if (!map.getLayer(zoneLabelLayerId())) {
    map.addLayer({
      id: zoneLabelLayerId(),
      type: 'symbol',
      source: labelSourceId,
      layout: {
        'text-field': ['get', 'title'],
        'text-size': 11,
        'text-font': ['Open Sans Regular'],
        'text-allow-overlap': false,
        'text-anchor': 'center',
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.06,
      },
      paint: {
        // Label color matches zone's border color (fallback to gray)
        'text-color': ['coalesce', ['get', 'color'], '#8B9DB8'],
        'text-halo-color': 'rgba(11, 14, 20, 0.7)',
        'text-halo-width': 1.6,
      },
    })
  }
  _bindZoneHandlers()
}

function _bindZoneHandlers() {
  if (!map || _zoneHandlersBound || !map.getLayer(zoneFillLayerId())) return
  map.on('mousedown', zoneFillLayerId(), onZoneMouseDown)
  map.on('click', zoneFillLayerId(), onZoneClick)
  map.on('contextmenu', zoneFillLayerId(), onZoneContextMenu)
  map.on('mouseenter', zoneFillLayerId(), () => _setMapCursor('pointer'))
  map.on('mouseleave', zoneFillLayerId(), () => _setMapCursor())
  _zoneHandlersBound = true
}

function onZoneClick(e) {
  if (ui.drawMode !== 'off' || ui.measureMode) return
  const f = e.features?.[0]
  if (!f?.properties?.name) return
  const mapName = mapStore.activeMap?.map?.name
  const zone = (mapName && zoneStore.byMap?.[mapName] || []).find((z) => z.name === f.properties.name)
  if (!zone) return

  if (ui.zoneEditMode) {
    ui.selectZone(zone)
    _reAddZones()
  } else {
    ui.selectZone(zone)
    ui.selectedFeature = {
      _lngLat: e.lngLat,
      properties: {
        _name: zone.name,
        _doctype: 'Expedition Zone',
        title: zone.title || zone.name,
        tag: zone.tag || '',
        color: zone.color,
        zone_type: zone.zone_type,
      },
      layer: {
        name: 'Expedition Zone',
        title: 'Zone',
        click_action: 'popup',
      }
    }
  }
}

function onZoneContextMenu(e) {
  if (ui.drawMode !== 'off' || ui.measureMode) return
  e.preventDefault?.()
  const f = e.features?.[0]
  if (!f?.properties?.name) return
  const mapName = mapStore.activeMap?.map?.name
  const zone = (mapName && zoneStore.byMap?.[mapName] || []).find((z) => z.name === f.properties.name)
  if (!zone) return

  ui.selectZone(zone)
  ui.selectedFeature = {
    _lngLat: e.lngLat,
    properties: {
      _name: zone.name,
      _doctype: 'Expedition Zone',
      title: zone.title || zone.name,
      tag: zone.tag || '',
      color: zone.color,
      zone_type: zone.zone_type,
    },
    layer: {
      name: 'Expedition Zone',
      title: 'Zone',
      click_action: 'popup',
    }
  }
}

function selectZoneFeature(e) {
  const f = e.features?.[0]
  if (!f?.properties?.name) return
  const mapName = mapStore.activeMap?.map?.name
  const zone = (mapName && zoneStore.byMap?.[mapName] || []).find((z) => z.name === f.properties.name)
  if (zone) {
    ui.selectZone(zone)
    _reAddZones()
  }
}

function offsetPosition(position, deltaLng, deltaLat) {
  if (!Array.isArray(position)) return position
  return [wrapLng(Number(position[0]) + deltaLng), Number(position[1]) + deltaLat]
}

function offsetGeometry(geometry, deltaLng, deltaLat) {
  if (!geometry) return null
  if (geometry.type === 'Point') {
    return { ...geometry, coordinates: offsetPosition(geometry.coordinates, deltaLng, deltaLat) }
  }
  if (geometry.type === 'LineString') {
    return { ...geometry, coordinates: geometry.coordinates.map((p) => offsetPosition(p, deltaLng, deltaLat)) }
  }
  if (geometry.type === 'Polygon') {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map((ring) => ring.map((p) => offsetPosition(p, deltaLng, deltaLat))),
    }
  }
  if (geometry.type === 'MultiPolygon') {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map((poly) => poly.map((ring) => ring.map((p) => offsetPosition(p, deltaLng, deltaLat)))),
    }
  }
  return normalizeGeometryLngs(geometry)
}

function centroidForGeometry(geometry) {
  const coords = _flatCoords(geometry)
  if (!coords.length) return null
  let lng = 0
  let lat = 0
  for (const point of coords) {
    lng += Number(point[0])
    lat += Number(point[1])
  }
  return { centroid_lng: lng / coords.length, centroid_lat: lat / coords.length }
}

function replaceLocalZone(mapName, updated) {
  const list = zoneStore.byMap?.[mapName] || []
  zoneStore.setForMap(mapName, list.map((z) => z.name === updated.name ? { ...z, ...updated } : z))
}

function onZoneMouseDown(e) {
  if (!ui.zoneEditMode) return
  if (ui.drawMode !== 'off' || ui.measureMode) return
  if (!e.originalEvent || e.originalEvent.button !== 0) return
  const f = e.features?.[0]
  if (!f?.properties?.name) return
  const mapName = mapStore.activeMap?.map?.name
  const zone = (mapName && zoneStore.byMap?.[mapName] || []).find((z) => z.name === f.properties.name)
  if (!mapName || !zone?.geometry) return
  e.preventDefault?.()
  ui.selectZone(zone)
  _zoneDrag = {
    mapName,
    zone,
    startLngLat: normalizeLngLat([e.lngLat.lng, e.lngLat.lat]),
    currentGeometry: zone.geometry,
  }
  map.dragPan.disable()
  _setMapCursor('grabbing')
  map.on('mousemove', onZoneDragMove)
  map.once('mouseup', onZoneDragEnd)
}

function onZoneDragMove(e) {
  if (!_zoneDrag) return
  const current = normalizeLngLat([e.lngLat.lng, e.lngLat.lat])
  const deltaLng = shortestLngDelta(_zoneDrag.startLngLat[0], current[0])
  const deltaLat = e.lngLat.lat - _zoneDrag.startLngLat[1]
  const geometry = offsetGeometry(_zoneDrag.zone.geometry, deltaLng, deltaLat)
  if (!geometry) return
  _zoneDrag.currentGeometry = geometry
  const updated = { ..._zoneDrag.zone, geometry, ...(centroidForGeometry(geometry) || {}) }
  replaceLocalZone(_zoneDrag.mapName, updated)
  ui.selectZone(updated)
  _reAddZones()
}

async function onZoneDragEnd() {
  if (!_zoneDrag) return
  const drag = _zoneDrag
  _zoneDrag = null
  map.off('mousemove', onZoneDragMove)
  map.dragPan.enable()
  _setMapCursor()
  if (!drag.currentGeometry) return
  try {
    const updated = await zoneStore.updateZone(drag.mapName, drag.zone.name, { geometry: normalizeGeometryLngs(drag.currentGeometry) })
    ui.selectZone(updated)
  } catch (err) {
    console.error('[expedition] zone move failed', err)
    replaceLocalZone(drag.mapName, drag.zone)
    ui.selectZone(drag.zone)
  }
}

function _flatCoords(geom) {
  if (!geom) return []
  if (geom.type === 'Point') return [geom.coordinates]
  if (geom.type === 'Polygon') return geom.coordinates.flat()
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2)
  return []
}

function _emptyEnvelope() {
  return { west: Infinity, south: Infinity, east: -Infinity, north: -Infinity }
}

function _extendEnvelopeWithGeometry(env, geometry) {
  for (const [lng, lat] of _flatCoords(geometry)) {
    if (typeof lng !== 'number' || typeof lat !== 'number') continue
    if (lng < env.west) env.west = lng
    if (lng > env.east) env.east = lng
    if (lat < env.south) env.south = lat
    if (lat > env.north) env.north = lat
  }
  return env
}

function _extendEnvelopeWithBounds(env, b) {
  if (!b || typeof b.south !== 'number' || b.count === 0) return env
  if (b.west < env.west) env.west = b.west
  if (b.east > env.east) env.east = b.east
  if (b.south < env.south) env.south = b.south
  if (b.north > env.north) env.north = b.north
  return env
}

function _hasUsableEnvelope(env) {
  return isFinite(env.west) && isFinite(env.east) && isFinite(env.south) && isFinite(env.north)
}

function _fitEnvelope(env) {
  if (!map || !_hasUsableEnvelope(env)) return false
  const lngPad = Math.max(0.001, (env.east - env.west) * 0.04)
  const latPad = Math.max(0.001, (env.north - env.south) * 0.04)
  const west = env.east === env.west ? env.west - lngPad : env.west
  const east = env.east === env.west ? env.east + lngPad : env.east
  const south = env.north === env.south ? env.south - latPad : env.south
  const north = env.north === env.south ? env.north + latPad : env.north
  map.fitBounds(
    [[west, south], [east, north]],
    { padding: 64, duration: 1200, maxZoom: 12, essential: true }
  )
  return true
}

function _pinFeatureEnvelope() {
  const env = _emptyEnvelope()
  const visible = new Set((layerStore.visibleLayers || []).map((lyr) => lyr.name))
  for (const [layerName, fc] of Object.entries(layerStore.features || {})) {
    if (!visible.has(parentLayerName(layerName))) continue
    for (const f of (fc && fc.features) || []) {
      _extendEnvelopeWithGeometry(env, f?.geometry)
    }
  }
  return env
}

function _zoneEnvelope() {
  const env = _emptyEnvelope()
  const activeMapName = mapStore.activeMap?.map?.name
  const list = (activeMapName && zoneStore.byMap?.[activeMapName]) || []
  for (const z of list) {
    if (z && z.geometry) _extendEnvelopeWithGeometry(env, z.geometry)
  }
  return env
}

function _cachedLayerBoundsEnvelope() {
  const env = _emptyEnvelope()
  for (const lyr of layerStore.visibleLayers || []) {
    _extendEnvelopeWithBounds(env, layerStore.bounds?.[lyr.name])
  }
  return env
}

function _hasPendingHomeData() {
  if (ui.fetchingFeatures > 0) return true
  for (const lyr of layerStore.visibleLayers || []) {
    if (layerStore.bounds?.[lyr.name] === null) return true
    if (layerStore.loading?.[lyr.name]) return true
  }
  return false
}

function _scheduleHomeFitRetry(delay = 160) {
  if (_homeFitDone || _homeFitTimer) return
  const seq = _homeFitSeq
  _homeFitTimer = setTimeout(() => {
    _homeFitTimer = null
    if (seq === _homeFitSeq && !_homeFitDone) _flyToHome()
  }, delay)
}

/**
 * Frame the active map's data on first render and on map switch.
 * Pins are authoritative. Server layer bounds are the next-best
 * metadata when pins are still loading. Zones are only a fallback
 * when there are no pin rows and no layer bounds to frame.
 */
async function _flyToHome() {
  if (!map || !mapStore.activeMap || _homeFitDone) return
  const seq = _homeFitSeq

  const pinEnv = _pinFeatureEnvelope()
  if (_fitEnvelope(pinEnv)) {
    _homeFitDone = true
    return
  }

  const cachedBoundsEnv = _cachedLayerBoundsEnvelope()
  if (_fitEnvelope(cachedBoundsEnv)) {
    _homeFitDone = true
    return
  }

  const layers = layerStore.visibleLayers || []
  if (layers.length) {
    const results = await Promise.all(
      layers.map((lyr) => layerStore.fetchBounds(lyr.name).catch(() => null))
    )
    if (seq !== _homeFitSeq || _homeFitDone || !map) return

    const latestPinEnv = _pinFeatureEnvelope()
    if (_fitEnvelope(latestPinEnv)) {
      _homeFitDone = true
      return
    }

    const fetchedBoundsEnv = _emptyEnvelope()
    for (const b of results) _extendEnvelopeWithBounds(fetchedBoundsEnv, b)
    if (_fitEnvelope(fetchedBoundsEnv)) {
      _homeFitDone = true
      return
    }
  }

  if (seq !== _homeFitSeq || _homeFitDone || !map) return
  if (_hasPendingHomeData()) {
    _scheduleHomeFitRetry()
    return
  }
  if (_fitEnvelope(_zoneEnvelope())) {
    _homeFitDone = true
  } else {
    const zoom = typeof ui.prefs.defaultZoom === 'number' ? ui.prefs.defaultZoom : map.getZoom()
    const pitch = typeof ui.prefs.defaultPitch === 'number' ? ui.prefs.defaultPitch : map.getPitch()
    map.easeTo({ center: [78.9629, 20.5937], zoom, pitch, duration: 1200 })
    _homeFitDone = true
  }
}

function _flyToInitialViewport() {
  _flyToHome()
}

function applySkin(skinId) {
  if (!map) return
  const skin = getSkin(skinId)
  const spec = resolveStyle(skin)
  if (!spec) return
  ui.basemapLoading = true
  if (typeof spec === 'string') {
    map.setStyle(spec, { diff: false })
  } else {
    map.setStyle(spec, { diff: false })
  }
}

function _runActiveMapCustomScript() {
  if (!map || !map.isStyleLoaded()) return
  const mapDoc = mapStore.activeMap?.map
  if (mapDoc && mapDoc.custom_script) {
    try {
      const runMapScript = new Function('Expedition', 'map', mapDoc.custom_script)
      runMapScript(window.Expedition, map)
    } catch (err) {
      console.error('[expedition] Failed to execute map custom script:', err)
    }
  }
}

onMounted(() => {
  const startCenter = [78.9629, 20.5937]
  const initialSkin = getSkin(activeSkinId.value)
  // Floor zoom-out so a single world is always at least viewport-wide.
  // With world copies enabled this gives horizontal globe wrapping
  // without ever showing the same longitude twice in one view.
  const rect = mapEl.value.getBoundingClientRect()
  const minZoom = computeGlobeFitZoom(rect.width)
  const startZoom = typeof ui.prefs.defaultZoom === 'number' ? ui.prefs.defaultZoom : minZoom
  const startPitch = typeof ui.prefs.defaultPitch === 'number' ? ui.prefs.defaultPitch : 0
  map = new maplibregl.Map({
    container: mapEl.value,
    style: resolveStyle(initialSkin),
    center: startCenter,
    zoom: startZoom,
    pitch: startPitch,
    minZoom,
    renderWorldCopies: true,
    attributionControl: { compact: true },
  })
  map.on('resize', () => {
    const r = mapEl.value.getBoundingClientRect()
    map.setMinZoom(computeGlobeFitZoom(r.width))
  })
  // Geolocate — Google's "where am I" blue dot.
  map.addControl(new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true, timeout: 6000 },
    trackUserLocation: false,           // one-shot, not constant tracking
    showUserHeading: false,
    showAccuracyCircle: true,
  }), 'bottom-right')

  // The toolbar (App.vue) owns the Fit / Reset-tilt camera controls.
  // No in-map recenter control — keeps the bottom-right cluster
  // single-source-of-truth and avoids duplicating fit behaviour.

  // Sources/layers re-add hooks. setStyle wipes everything, so we
  // re-add on every styledata.
  map.on('styledata', () => {
    if (ui.basemapLoading) ui.basemapLoading = false
    _applyIndiaBoundaryFix()
    _reAddAllLayers()
    _reAddZones()
  })

  // Initial bootstrap: paint any layers that already have features.
  map.on('load', () => {
    _applyIndiaBoundaryFix()
    _reAddAllLayers()
    _reAddZones()
    _fetchAllVisibleBounds()
    _flyToInitialViewport()
    _runActiveMapCustomScript()
  })

  // Right-click context menu. We use MapLibre's `contextmenu` event
  // because it gives us the geographic coordinates directly and
  // suppresses the browser's default context menu over the canvas.
  // We also suppress the browser menu on the canvas DOM in case the
  // event fires before this listener is attached.
  map.on('contextmenu', (e) => {
    // e.point gives canvas-relative coords; e.lngLat gives lat/lng.
    if (map.getLayer(zoneFillLayerId())) {
      const zones = map.queryRenderedFeatures(e.point, { layers: [zoneFillLayerId()] })
      if (zones.length) return
    }
    e.preventDefault?.()
    ui.openContextMenu(e.point.x, e.point.y, e.lngLat.lat, wrapLng(e.lngLat.lng))
  })

  // PR-13 — modifier-drag rotate/tilt.
  //   Alt  + drag horizontal → rotate (bearing)
  //   Ctrl + drag vertical   → tilt (pitch)
  // Default scroll-wheel zoom and shift-drag pan remain in effect.
  // We track the gesture manually so we don't have to add a second
  // DragPan handler (which would fight MapLibre's own).
  //
  // The gesture is bidirectional: drag-down raises pitch, drag-up
  // lowers it. To avoid the "stuck at 0" trap when starting from
  // a flat view, we shift the pitch up by 30° on mousedown (still
  // clamped to 75) so the user is always in a useful gesture range
  // from the first frame. The up/down drag then feels symmetric
  // around the click position.
  const canvas = map.getCanvas()
  // Prevent the browser's context menu over the canvas DOM too, so a
  // right-click anywhere on the map (not just on a feature) is
  // captured by the contextmenu handler above.
  canvas.addEventListener('contextmenu', (e) => e.preventDefault())
  const keepPreferredCursor = () => {
    window.requestAnimationFrame(() => _setMapCursor())
  }
  map.on('dragstart', keepPreferredCursor)
  map.on('drag', keepPreferredCursor)
  map.on('dragend', keepPreferredCursor)
  let gesture = null
  canvas.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return
    _mapPointerStart = { x: e.clientX, y: e.clientY }
    _mapWasDragged = false
    if (e.altKey) {
      gesture = { kind: 'rotate', x: e.clientX, y: e.clientY, startBearing: map.getBearing() }
      e.preventDefault()
      _setMapCursor()
    } else if (e.ctrlKey) {
      // Shift up by 30° on start so the gesture is bidirectional
      // from any starting pitch (including 0°).
      const startPitch = Math.min(75, map.getPitch() + 30)
      map.setPitch(startPitch)
      gesture = { kind: 'pitch', x: e.clientX, y: e.clientY, startPitch }
      e.preventDefault()
      _setMapCursor('ns-resize')
    }
  })
  window.addEventListener('mousemove', (e) => {
    if (_mapPointerStart) {
      const dx = e.clientX - _mapPointerStart.x
      const dy = e.clientY - _mapPointerStart.y
      if ((dx * dx) + (dy * dy) > 16) _mapWasDragged = true
    }
    if (!gesture) return
    const dx = e.clientX - gesture.x
    const dy = e.clientY - gesture.y
    if (gesture.kind === 'rotate') {
      // 1 px ≈ 0.4° of bearing — feels close to Google Earth.
      map.setBearing(gesture.startBearing + dx * 0.4)
    } else if (gesture.kind === 'pitch') {
      // Pitch 0–75°; drag-down (dy > 0) raises the camera, drag-up
      // (dy < 0) lowers it. Anchor to startPitch (set on mousedown
      // with the +30° shift) so the gesture is symmetric.
      const next = Math.max(0, Math.min(75, gesture.startPitch + dy * 0.4))
      map.setPitch(next)
    }
  })
  const endGesture = () => {
    if (!gesture) return
    gesture = null
    _setMapCursor()
  }
  window.addEventListener('mouseup', endGesture)
  // Cancel if the page loses focus mid-gesture.
  window.addEventListener('blur', endGesture)
  // Debounce viewport-based fetches: a fast pinch/wheel otherwise
  // spams the server with one request per intermediate frame. 180ms
  // feels instant to the user and saves the last call if multiple
  // moveend events fire in quick succession.
  let _moveEndTimer = null
  map.on('moveend', () => {
    if (_moveEndTimer) clearTimeout(_moveEndTimer)
    _moveEndTimer = setTimeout(() => {
      _moveEndTimer = null
      _fetchAllVisibleBounds()
    }, 180)
  })

  // Subscribe to feature updates from the layer store. We retry on
// every `styledata` until `_addLayerOnMap` actually adds the source
// (signalled by `map.getSource(sid)` being truthy). This handles
// the race where features arrive while the basemap style is still
// parsing — `isStyleLoaded()` would block us, but `_addLayerOnMap`
// guards on `map.getStyle()` (the parseable style object) instead,
// which becomes truthy on the next `styledata` event.
  unsubscribeFeatures = layerStore.onFeaturesUpdated((layerName) => {
    if (!map) return
    if (!layerName) {
      _reAddAllLayers()
      _fetchAllVisibleBounds()
      _flyToHome()
      return
    }
    const tryAdd = () => {
      if (!map) return true
      _addLayerOnMap(layerName)
      if (typeof map.triggerRepaint === 'function') map.triggerRepaint()
      return _hasRenderedLayer(layerName)
    }
    if (tryAdd()) {
      _flyToHome()
      return
    }
    const onStyle = () => {
      if (tryAdd()) {
        map.off('styledata', onStyle)
        _flyToHome()
      }
    }
    map.on('styledata', onStyle)
  })

  // Subscribe to zone updates. We don't have a real event channel
  // for the store yet; instead we watch the byMap object reference
  // and re-paint zones whenever it changes. Cheap because zone
  // counts are small (single-digit dozens for v1).
  unsubscribeZones = watch(
    () => zoneStore.byMap,
    () => {
      const tryAdd = () => {
        if (!map) return true
        _reAddZones()
        if (typeof map.triggerRepaint === 'function') map.triggerRepaint()
        return !!map.getSource(zoneSourceId())
      }
      if (tryAdd()) return
      const onStyle = () => {
        if (tryAdd()) map.off('styledata', onStyle)
      }
      map.on('styledata', onStyle)
    },
    { deep: true }
  )

  // Re-paint zones when the tag filter changes (ui.zoneTags).
  unsubscribeZoneTags = watch(
    () => ui.zoneTags,
    () => {
      if (map) _reAddZones()
    },
    { deep: true }
  )

  watch(
    () => ui.selectedZone?.name,
    () => {
      if (map) _reAddZones()
    }
  )

  // Expose a thin facade on window for non-Vue consumers (e.g. insight
  // chips in BottomDrawer). The native maplibre instance is large and
  // has a dozen methods; we expose only what the canvas-overlays need.
  if (typeof window !== 'undefined') {
    window.expeditionMap = {
      flyTo: (opts) => map && map.flyTo(opts),
      easeTo: (opts) => map && map.easeTo(opts),
      jumpTo: (opts) => map && map.jumpTo(opts),
      getCenter: () => map && normalizeLngLat(map.getCenter()),
      getZoom: () => map && map.getZoom(),
      getMap: () => map,
      finishDrawing: () => _finishDrawing(),
      getLayerDiagnostics: () => {
        if (!map) return []
        const styleSources = map.getStyle()?.sources || {}
        return Object.keys(layerStore.features || {}).map((name) => {
          const sid = sourceId(name)
          return {
            name,
            source: sid,
            parent: parentLayerName(name),
            isVirtualGroup: name.includes(VIRTUAL_GROUP_SEPARATOR),
            featureCount: layerStore.features[name]?.features?.length || 0,
            styleCluster: !!styleSources[sid]?.cluster,
            trackedCluster: _sourceClusterState[sid],
            hasSource: !!map.getSource(sid),
            hasClusterLayer: !!map.getLayer(clusterLayerId(name)),
            hasClusterCountLayer: !!map.getLayer(clusterCountId(name)),
          }
        })
      },
      // Return the active map's zones as a GeoJSON FeatureCollection.
      // Used by fit-to-data in App.vue so custom territories count
      // as "data" alongside pin layers.
      getZones: () => {
        const activeMapName = mapStore.activeMap?.map?.name
        if (!activeMapName) return { type: 'FeatureCollection', features: [] }
        const list = zoneStore.byMap?.[activeMapName] || []
        return {
          type: 'FeatureCollection',
          features: list
            .filter((z) => z && z.geometry)
            .map((z) => ({
              type: 'Feature',
              geometry: z.geometry,
              properties: { name: z.name, title: z.title, kind: 'zone' },
            })),
        }
      },
    }
  }

  // ----- Canvas drawing / zones -----
  map.on('click', (e) => {
    if (ui.drawMode === 'off') {
      if (ui.selectedFeature && !_mapWasDragged && !_hasInteractivePointAt(e.point)) {
        ui.selectedFeature = null
      }
      _mapPointerStart = null
      _mapWasDragged = false
      return
    }
    e.preventDefault?.()
    _handleDrawClick(e)
  })
  map.on('dblclick', async (e) => {
    if (ui.drawMode === 'off') return
    e.preventDefault() // suppress default dblclick zoom
    await _finishDrawing()
  })
  map.on('mousemove', (e) => {
    if (ui.drawMode === 'off') return
    const snapIndex = _snapIndexForPoint(e.point)
    const snapped = snapIndex >= 0 ? ui.draftVertices[snapIndex] : normalizeLngLat([e.lngLat.lng, e.lngLat.lat])
    ui.setDraftPointer(snapped, snapIndex)
    _renderDraft()
  })
  _setMapCursor()

  const onKey = (e) => {
    if (ui.drawMode === 'off') return
    if (e.key === 'Escape') {
      e.preventDefault()
      ui.cancelDraw()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      _finishDrawing()
    } else if (e.key === 'Backspace' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z')) {
      e.preventDefault()
      ui.undoDraftVertex()
    }
  }
  window.addEventListener('keydown', onKey)
  // Tear down on unmount via closure capture.
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
})

// Toggle the canvas cursor + draft rendering as drawMode flips.
watch(
  () => ui.drawMode,
  (mode) => {
    if (!map) return
    if (mode === 'polygon') {
      _setMapCursor()
    } else {
      _setMapCursor()
      map.doubleClickZoom.enable()
      _dblClickDisabled = false
      _clearDraft()
    }
  }
)

watch(
  () => [ui.measureMode, ui.prefs.cursor],
  () => _setMapCursor()
)


// Re-render the draft polygon whenever the vertex list changes.
watch(
  () => ui.draftVertices,
  () => _renderDraft(),
  { deep: true }
)

function _draftFeatureCollection() {
  const v = ui.draftVertices
  const pointer = ui.draftPointer
  const features = []
  if (!v || v.length < 1) return { type: 'FeatureCollection', features }

  if (ui.drawMode === 'circle' && v[0] && pointer) {
    features.push({
      type: 'Feature',
      geometry: haloPolygon(v[0][0], v[0][1], distanceMeters(v[0], pointer)),
      properties: { kind: 'draft-area' },
    })
  } else if (ui.drawMode === 'rectangle' && v[0] && pointer) {
    const geometry = rectanglePolygon(v[0], pointer)
    if (geometry) features.push({ type: 'Feature', geometry, properties: { kind: 'draft-area' } })
  } else {
    const line = pointer && v.length ? [...v, pointer] : v
    if (line.length >= 2) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: line },
        properties: { kind: 'draft-line' },
      })
    }
    if (ui.drawMode === 'polygon' && v.length >= 3 && pointer && ui.draftSnapIndex === 0) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [[...v, v[0]]] },
        properties: { kind: 'draft-area' },
      })
    }
  }

  for (let i = 0; i < v.length; i += 1) {
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: v[i] },
      properties: { kind: 'draft-vertex', index: i, snap: ui.draftSnapIndex === i ? 1 : 0 },
    })
  }
  return { type: 'FeatureCollection', features }
}

function _snapIndexForPoint(point) {
  if (!map || ui.drawMode !== 'polygon' || ui.draftVertices.length < 3) return -1
  const first = ui.draftVertices[0]
  const projected = map.project(first)
  const dx = projected.x - point.x
  const dy = projected.y - point.y
  return Math.sqrt(dx * dx + dy * dy) <= 14 ? 0 : -1
}

function _handleDrawClick(e) {
  if (!_dblClickDisabled) {
    map.doubleClickZoom.disable()
    _dblClickDisabled = true
  }
  const point = normalizeLngLat([e.lngLat.lng, e.lngLat.lat])
  if (ui.drawMode === 'polygon') {
    if (ui.draftSnapIndex === 0 && ui.draftVertices.length >= 3) {
      _finishDrawing()
      return
    }
    ui.pushDraftVertex(point)
    _renderDraft()
    return
  }
  if (ui.drawMode === 'circle' || ui.drawMode === 'rectangle') {
    if (!ui.draftVertices.length) {
      ui.pushDraftVertex(point)
      ui.setDraftPointer(point)
      _renderDraft()
    } else {
      ui.setDraftPointer(point)
      _finishDrawing()
    }
    return
  }
  if (ui.drawMode === 'text' || ui.drawMode === 'note') {
    ui.pushDraftVertex(point)
    _renderDraft()
  }
}

function _geometryFromDraft() {
  const v = ui.draftVertices
  const pointer = ui.draftPointer
  if (ui.drawMode === 'polygon') {
    if (!v || v.length < 3) return null
    return { type: 'Polygon', coordinates: [[...v, v[0]]] }
  }
  if (ui.drawMode === 'circle') {
    if (!v?.[0] || !pointer || distanceMeters(v[0], pointer) <= 1) return null
    return haloPolygon(v[0][0], v[0][1], distanceMeters(v[0], pointer))
  }
  if (ui.drawMode === 'rectangle') {
    if (!v?.[0] || !pointer) return null
    return rectanglePolygon(v[0], pointer)
  }
  return null
}

async function _finishDrawing() {
  const geometry = normalizeGeometryLngs(_geometryFromDraft())
  if (!geometry) return
  const mapName = mapStore.activeMap?.map?.name
  if (!mapName) return
  try {
    const title = (ui.zoneDraftTitle || '').trim() || 'Zone'
    const created = await zoneStore.createZone(mapName, {
      title,
      zone_type: 'polygon',
      geometry,
      color: draftColor(),
      fill_opacity: draftFillOpacity(),
      stroke_color: draftStrokeColor(),
      stroke_width: draftStrokeWidth(),
      stroke_style: draftStrokeStyle(),
    })
    ui.setZoneEditMode(true)
    ui.selectZone(created)
    _reAddZones()
  } catch (err) {
    console.error('[expedition] zone create failed', err)
  } finally {
    ui.cancelDraw()
    _clearDraft()
  }
}

function _renderDraft() {
  if (!map) return
  const sid = 'src-zones-draft'
  if (!map.getSource(sid)) {
    map.addSource(sid, { type: 'geojson', data: _draftFeatureCollection() })
  } else {
    map.getSource(sid).setData(_draftFeatureCollection())
  }
  const lid = zoneDraftLayerId()
  const fillId = zoneDraftFillLayerId()
  if (!map.getLayer(fillId)) {
    map.addLayer({
      id: fillId,
      type: 'fill',
      source: sid,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': draftColor(),
        'fill-opacity': draftFillOpacity(),
      },
    })
  } else {
    map.setPaintProperty(fillId, 'fill-color', draftColor())
    map.setPaintProperty(fillId, 'fill-opacity', draftFillOpacity())
  }
  if (!map.getLayer(lid)) {
    map.addLayer({
      id: lid,
      type: 'line',
      source: sid,
      filter: ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'Polygon']],
      paint: {
        'line-color': draftStrokeColor(),
        'line-width': draftStrokeWidth(),
        'line-dasharray': [2, 1],
      },
    })
    map.addLayer({
      id: zoneDraftVertexLayerId(),
      type: 'circle',
      source: sid,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': ['case', ['==', ['get', 'snap'], 1], 7, 4.5],
        'circle-color': ['case', ['==', ['get', 'snap'], 1], '#F59E0B', draftColor()],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5,
      },
    })
  } else {
    map.setPaintProperty(lid, 'line-color', draftStrokeColor())
    map.setPaintProperty(lid, 'line-width', draftStrokeWidth())
    if (map.getLayer(zoneDraftVertexLayerId())) {
      map.setPaintProperty(zoneDraftVertexLayerId(), 'circle-color', ['case', ['==', ['get', 'snap'], 1], '#F59E0B', draftColor()])
    }
  }
}

function _clearDraft() {
  if (!map) return
  const sid = 'src-zones-draft'
  if (map.getLayer(zoneDraftVertexLayerId()))
    map.removeLayer(zoneDraftVertexLayerId())
  if (map.getLayer(zoneDraftLayerId()))
    map.removeLayer(zoneDraftLayerId())
  if (map.getLayer(zoneDraftFillLayerId()))
    map.removeLayer(zoneDraftFillLayerId())
  if (map.getSource(sid)) map.removeSource(sid)
}

onBeforeUnmount(() => {
  if (_homeFitTimer) {
    clearTimeout(_homeFitTimer)
    _homeFitTimer = null
  }
  if (territoryRebuildFrame) {
    window.cancelAnimationFrame(territoryRebuildFrame)
    territoryRebuildFrame = 0
  }
  if (unsubscribeFeatures) unsubscribeFeatures()
  if (unsubscribeZones) unsubscribeZones()
  if (unsubscribeZoneTags) unsubscribeZoneTags()
  if (typeof window !== 'undefined' && window.expeditionMap) {
    window.expeditionMap = null
  }
  if (map) map.remove()
})

// Skin change → swap basemap.
watch(activeSkinId, (id) => {
  if (!map) return
  applySkin(id)
})

// Layer list changes (e.g. map switched) → reconcile.
watch(
  () => layerStore.layers.map((l) => ({
    name: l.name,
    enabled: (l.enabled !== false && l.enabled !== 0) && !layerStore.locallyHidden.has(l.name),
    color: l.color || l.style?.color || '',
    icon: l.icon || l.style?.icon || '',
    size: l.size || l.style?.size || '',
    cluster: !!(l.cluster || l.style?.cluster),
    heatmap: !!(l.heatmap || l.style?.heatmap),
    heatmap_mode: l.heatmap_mode || '',
    heatmap_weight_field: l.heatmap_weight_field || '',
    heatmap_weight_min: l.heatmap_weight_min ?? null,
    heatmap_weight_max: l.heatmap_weight_max ?? null,
    heatmap_weight_scale: l.heatmap_weight_scale || '',
    heatmap_radius_min: l.heatmap_radius_min ?? null,
    heatmap_radius_max: l.heatmap_radius_max ?? null,
    heatmap_intensity_min: l.heatmap_intensity_min ?? null,
    heatmap_intensity_max: l.heatmap_intensity_max ?? null,
    heatmap_opacity: l.heatmap_opacity ?? null,
    heatmap_ramp_json: l.heatmap_ramp_json || '',
    territory_enabled: !!(l.territory_enabled || l.style?.territory_enabled),
    territory_color: l.territory_color || l.style?.territory_color || '',
    territory_opacity: l.territory_opacity ?? l.style?.territory_opacity ?? null,
    territory_padding_meters: l.territory_padding_meters ?? l.style?.territory_padding_meters ?? null,
    iconVersion: iconStore.version,
  })),
  () => {
    if (!map || !map.isStyleLoaded()) return
    _pruneStaleRenderedLayers()
    for (const layer of layerStore.layers) {
      if (_hasRenderedLayer(layer.name)) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// Heatmap toggle (PR-7). When a layer's heatmap flag flips, re-add
// that layer's source layers so the heatmap layer is created or
// removed and the pin stack is shown/hidden accordingly.
watch(
  () => ({ ...ui.heatmapEnabled }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (_hasRenderedLayer(layer.name)) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// Radius toggle (PR-8). When a layer's radius flag flips, or its
// radiusField / radiusMeters changes, re-add so the halo layer is
// created/removed/updated with the new paint expression.
watch(
  () => ({
    enabled: { ...ui.radiusEnabled },
    field: { ...ui.radiusField },
    meters: { ...ui.radiusMeters },
    scale: { ...ui.radiusScale },
    opacity: layerStore.layers.map((l) => [l.name, l.radius_opacity]),
  }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (_hasRenderedLayer(layer.name)) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// 3D pitch toggle (PR-9). Drives the camera (pitch is a camera
// property, not a layer property). On → ease to pitchDegrees; off
// → ease back to 0.
watch(
  () => ({ enabled: ui.pitchEnabled, deg: ui.pitchDegrees }),
  ({ enabled, deg }) => {
    if (!map) return
    map.easeTo({ pitch: enabled ? deg : 0, duration: 600 })
  },
)

// Extrusion per-layer state (PR-9). Re-add layers so the
// fill-extrusion paint updates.
watch(
  () => ({
    heights: { ...ui.extrusionHeight },
    fields: { ...ui.extrusionField },
    on: ui.pitchEnabled,
  }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (_hasRenderedLayer(layer.name)) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// When the active map changes, fly to the new viewport (or fitBounds).
watch(
  () => mapStore.activeMap && mapStore.activeMap.map && mapStore.activeMap.map.name,
  () => {
    if (!map) return
    _resetFirstFetch()
    _fetchAllVisibleBounds()
    _flyToInitialViewport()
    if (map.isStyleLoaded()) {
      _runActiveMapCustomScript()
    }
  },
)

// When the selection changes (e.g. cleared by Esc), clear the feature state
// so the halo doesn't linger.
watch(
  () => ui.selectedFeature,
  (sel) => {
    if (!map) return
    if (!sel) {
      // Clear all features in the previously selected source.
      for (const layer of layerStore.layers) {
        const sourceIds = [sourceId(layer.name)]
        const sourcePrefix = `src-${layer.name}${VIRTUAL_GROUP_SEPARATOR}`
        for (const id of Object.keys(map.getStyle()?.sources || {})) {
          if (id.startsWith(sourcePrefix)) sourceIds.push(id)
        }
        for (const feat of layerStore.features[layer.name]?.features || []) {
          if (feat._id != null) {
            for (const sid of sourceIds) {
              if (!map.getSource(sid)) continue
              try { map.setFeatureState({ source: sid, id: feat._id }, { selected: false }) } catch (_) {}
            }
          }
        }
      }
    }
  }
)
</script>

<template>
  <div class="basemap-shell">
    <div ref="mapEl" class="basemap" />
    <!-- Subtle radial vignette so the canvas feels framed. -->
    <div class="basemap__vignette" aria-hidden="true" />
    <!-- Fetch indicator. Visible while at least one layer fetch is in
         flight. Sits in the bottom-right, just above MapLibre's controls. -->
    <Transition name="exp-fade">
      <div v-if="ui.fetchingFeatures > 0" class="basemap__fetch-pill" aria-live="polite">
        <span class="basemap__fetch-dot" />
        Loading…
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.basemap-shell { position: absolute; inset: 0; overflow: hidden; }
.basemap { position: absolute; inset: 0; }
.basemap__vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    ellipse at center,
    transparent 55%,
    rgba(0, 0, 0, 0.10) 85%,
    rgba(0, 0, 0, 0.22) 100%
  );
  z-index: 1;
}
/* Fetch indicator pill — scoped (it's inside the Vue root). */
.basemap__fetch-pill {
  position: absolute;
  right: 50px;
  bottom: 12px;
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px;
  background: rgba(11, 14, 20, 0.78);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: rgba(230, 232, 236, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  z-index: 5;
  pointer-events: none;
}
.basemap__fetch-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #3B82F6;
  box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.55);
  animation: exp-pulse 1.2s ease-out infinite;
}
@keyframes exp-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.55); }
  70%  { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
  100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
}
.exp-fade-enter-active, .exp-fade-leave-active {
  transition: opacity 180ms ease;
}
.exp-fade-enter-from, .exp-fade-leave-to { opacity: 0; }
</style>
<style>
/* MapLibre controls live outside the Vue component root (appended
   to the map container), so these cannot use scoped data-v attributes.
   Nesting under .basemap-shell keeps them from leaking to Frappe Desk. */
.basemap-shell .maplibregl-ctrl-attrib { font-size: 10px; opacity: 0.65; }
.basemap-shell .maplibregl-ctrl-attrib a { color: rgba(255, 255, 255, 0.7); }
.basemap-shell .maplibregl-ctrl-scale {
  background: rgba(11, 14, 20, 0.7);
  border-color: rgba(255, 255, 255, 0.2);
  color: rgba(255, 255, 255, 0.8);
  font-size: 10px;
  padding: 1px 4px;
}

</style>
