<script setup>
/**
 * Expedition — top-level component.
 *
 * Layout (PR-1, quiet-canvas): the map fills the viewport. Three
 * corner floating toolbars (TL = layers, TR = tools, BR = basemap)
 * are the only chrome at rest. The old LeftRail / BottomDrawer
 * stay mounted but hidden behind `oldChrome` so we can iterate
 * without regressions during the PR-1 through PR-6 migration.
 *
 * Phase 1 plan: ~/.claude-work/plans/quiet-canvas-quokka.md
 */
import { ref, computed, onMounted, onBeforeUnmount, provide } from 'vue'
import { useMapStore } from './state/map.js'
import { useUiStore } from './state/ui.js'
import { useLayersStore } from './state/layers.js'
import { useZonesStore } from './state/zones.js'
import { useIconsStore } from './state/icons.js'
import Basemap from './components/Basemap.vue'
import CommandPalette from './components/CommandPalette.vue'
import MapPopup from './components/MapPopup.vue'
import LoadingOverlay from './components/LoadingOverlay.vue'
import LayerEditor from './components/LayerEditor.vue'
import FloatingToolbar from './components/FloatingToolbar.vue'
import BasemapPanel from './components/BasemapPanel.vue'
import SearchBar from './components/SearchBar.vue'
import Legend from './components/Legend.vue'
import LayerPanel from './components/LayerPanel.vue'
import MapToolTray from './components/MapToolTray.vue'
import CoordReadout from './components/CoordReadout.vue'
import MeasureTool from './components/MeasureTool.vue'
import ContextMenu from './components/ContextMenu.vue'
import UserSettings from './components/UserSettings.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import { isEditableTarget, shortcutLabel } from './lib/keymaps.js'

const mapStore = useMapStore()
const ui = useUiStore()
const layers = useLayersStore()
const zoneStore = useZonesStore()
const iconStore = useIconsStore()
const chromeRoot = ref(null)
const layoutItemEls = {}
const layoutViewport = ref({ width: 0, height: 0 })
const layoutItemSizes = ref({})
const layoutDrag = ref(null)
let _layoutResizeObserver = null
let _layoutMeasureFrame = 0

const LAYOUT_COLS = 24
const LAYOUT_ROWS = 12
const LAYOUT_GAP = 12
const LAYOUT_MARGIN = 12
const LAYOUT_STEP = 12
const LAYOUT_ANCHOR = { start: 0, center: 0.5, end: 1 }
const LAYOUT_LABELS = {
  map: 'Map panel',
  toolsPrimary: 'Draw and measure',
  toolsStyle: 'Style and zone tools',
  search: 'Search',
  settings: 'Settings',
  fit: 'Fit controls',
  tilt: 'Reset tilt',
  layout: 'Layout',
  visibility: 'Hide UI',
  basemap: 'Basemap',
  coords: 'Coordinates',
  legend: 'Legend',
}
const layoutDragId = computed(() => layoutDrag.value?.id || null)

function onFitDataEvent(event) {
  fitToData(event?.detail?.mode || 'all')
}

function dispatchShortcut(id) {
  window.dispatchEvent(new CustomEvent('expedition:shortcut', { detail: { id } }))
}

function runShortcut(id) {
  if (id === 'shortcuts') ui.openSettingsTab('shortcuts')
  else if (id === 'layers') onToolbarTrigger('layers')
  else if (id === 'search') onToolbarTrigger('search')
  else if (id === 'settings') ui.toggleSettingsTab('map')
  else if (id === 'hide-ui') ui.toggleChrome()
  else if (id === 'layout') ui.toggleLayoutCustomizing()
  else if (id === 'fit-all') onToolbarTrigger('fit-all')
  else if (id === 'fit-visible') onToolbarTrigger('fit-visible')
  else if (id === 'tilt-reset') onToolbarTrigger('tilt-reset')
  else dispatchShortcut(id)
}

function onGlobalKeydown(e) {
  ui.setShortcutModifiers(e)

  const key = e.key.toLowerCase()
  if (!isEditableTarget(e.target) && e.altKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault()
    e.stopImmediatePropagation?.()
    return
  }
  if ((e.metaKey || e.ctrlKey) && key === 'k') {
    e.preventDefault()
    ui.commandKOpen = !ui.commandKOpen
    return
  }
  if ((e.metaKey || e.ctrlKey) && key === 'f') {
    e.preventDefault()
    runShortcut('search')
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key === ',') {
    e.preventDefault()
    runShortcut('settings')
    return
  }
  if (isEditableTarget(e.target)) return
  if (e.ctrlKey || e.metaKey) return

  let id = null
  if (e.key === '?') id = 'shortcuts'
  else if (key === 'm') id = 'layers'
  else if (key === 'h') id = 'hide-ui'
  else if (key === 'l') id = 'layout'
  else if (key === 'b') id = 'basemap'
  else if (key === 'f') id = e.shiftKey ? 'fit-all' : 'fit-visible'
  else if (e.key === '0') id = 'tilt-reset'
  else if (key === 'v') id = 'select'
  else if (key === 'd') id = e.shiftKey ? 'choose-shape' : 'draw-shape'
  else if (key === 'r') id = e.shiftKey ? 'measure-area' : 'measure-line'
  else if (key === 'c') id = 'drawing-color'
  else if (key === 'e') id = 'zone-edit'
  else if (key === 't') id = 'tilt-rotate'
  else if (key === 'z') id = 'undo-vertex'
  else if (e.key === 'Enter') id = 'finish-drawing'
  else if (e.key === 'Escape') id = 'cancel-tool'

  if (!id) return
  e.preventDefault()
  runShortcut(id)
}

function onGlobalKeyup(e) {
  ui.setShortcutModifiers(e)
  if (!isEditableTarget(e.target) && (e.key === 'Alt' || e.altKey)) {
    e.preventDefault()
    e.stopImmediatePropagation?.()
  }
}

function onWindowBlur() {
  ui.clearShortcutModifiers()
}

onMounted(async () => {
  updateLayoutViewport()
  window.addEventListener('resize', updateLayoutViewport)
  _layoutResizeObserver = new ResizeObserver(scheduleMeasuredLayout)
  if (chromeRoot.value) _layoutResizeObserver.observe(chromeRoot.value)
  await iconStore.loadIcons().catch((e) => {
    console.warn('[expedition] custom icons unavailable', e)
  })
  await mapStore.bootstrap()
  window.addEventListener('expedition:fit-data', onFitDataEvent)
  document.addEventListener('keydown', onGlobalKeydown, true)
  document.addEventListener('keyup', onGlobalKeyup, true)
  window.addEventListener('blur', onWindowBlur)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateLayoutViewport)
  window.removeEventListener('pointermove', onLayoutPointerMove)
  window.removeEventListener('pointerup', onLayoutPointerUp)
  if (_layoutMeasureFrame) window.cancelAnimationFrame(_layoutMeasureFrame)
  _layoutResizeObserver?.disconnect?.()
  window.removeEventListener('expedition:fit-data', onFitDataEvent)
  document.removeEventListener('keydown', onGlobalKeydown, true)
  document.removeEventListener('keyup', onGlobalKeyup, true)
  window.removeEventListener('blur', onWindowBlur)
})

// Enabled-layer count for the layers toolbar badge.
const enabledLayerCount = computed(() =>
  (layers.layers || []).filter((l) => l.enabled !== false && l.enabled !== 0).length
)

// Glyphs as 24x24 path strings. Lifted out of vue template for readability.
const GLYPHS = {
  layers: 'M12 3l9 4-9 4-9-4 9-4z M3 12l9 4 9-4 M3 17l9 4 9-4',
  tools: 'M4 7h16 M4 12h16 M4 17h16 M7 7a2 2 0 1 0 4 0 2 2 0 1 0-4 0z M15 12a2 2 0 1 0 4 0 2 2 0 1 0-4 0z M5 17a2 2 0 1 0 4 0 2 2 0 1 0-4 0z',
  search: 'M11 4a7 7 0 1 0 4.95 11.95L21 21 M11 4a7 7 0 0 1 7 7',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  fit: 'M4 10V5a1 1 0 0 1 1-1h5 M14 4h5a1 1 0 0 1 1 1v5 M20 14v5a1 1 0 0 1-1 1h-5 M10 20H5a1 1 0 0 1-1-1v-5',
  // Viewport rectangle with a centered reticle — reads as "fit
  // whatever is currently in the visible viewport" (the opposite
  // mode from `fit`, which expands to all data).
  fitVisible: 'M4 8h16v8H4z M12 8v8 M8 12h8',
  // Top-down map plate (parallelogram) with a downward chevron —
  // reads as "flatten / reset to top view". The plate's slant hints
  // that we're collapsing from 3D to 2D.
  tiltReset: 'M4 18 L20 18 L17 10 L7 10 Z M12 3 v6 M9 6 l3 3 3-3',
  // Open eye (visible mode). Almond outline + pupil. Read as
  // "everything is shown".
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  // Eye with a diagonal slash — "currently hidden". Mirrors the open
  // glyph but with a strikethrough line so the active/inactive states
  // read as the same icon, just struck through.
  eyeOff: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M4 4l16 16',
  layout: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
}

// Top-left: map workspace. The badge still shows enabled layers because
// that is the most useful at-rest status for the active map.
const tlButtons = computed(() => [
  { id: 'layers', label: 'Map', glyph: GLYPHS.layers, badge: enabledLayerCount.value || '', shortcut: shortcutLabel('layers') },
])
// Top-right split into two separate toolbars (different visual
// groups) so a user can distinguish at a glance: search is on the
// left of the corner, settings sits at the very corner.
const trSearchButtons = computed(() => [
  { id: 'search', label: 'Search', glyph: GLYPHS.search, shortcut: shortcutLabel('search') },
])
const trSettingsButtons = computed(() => [
  { id: 'settings', label: 'Settings', glyph: GLYPHS.settings, active: ui.settingsOpen, shortcut: shortcutLabel('settings') },
])
// Bottom-right: two standard FloatingToolbars. The fit tray holds
// both fit modes (fit-all + fit-visible) as buttons stacked
// vertically inside a single pill. Tilt-reset is its own tray
// below. No active state on either — both fit buttons are
// one-shot camera actions (re-fit on the current mode just
// re-anchors the camera); the bluish active state would imply
// "this mode is on", which is misleading for a fit action.
//
// The eye-toggle pill sits to the LEFT of the basemap (Liberty)
// trigger (see .expedition__br-row in the template). It is always
// mounted; in chrome-hidden mode every chrome element fades out
// except this one (which stays dim) so the user can always toggle
// UI back on.
const brFitButtons = computed(() => [
  { id: 'fit-all', label: 'Fit to all enabled data', glyph: GLYPHS.fit, shortcut: shortcutLabel('fit-all') },
  { id: 'fit-visible', label: 'Fit to visible features', glyph: GLYPHS.fitVisible, shortcut: shortcutLabel('fit-visible') },
])
const brTiltButtons = computed(() => [
  { id: 'tilt-reset', label: 'Reset tilt (top view)', glyph: GLYPHS.tiltReset, shortcut: shortcutLabel('tilt-reset') },
])
const toolbarButtonSizes = { xs: 22, s: 28, m: 32, lg: 40, xlg: 48 }
const chromeStyle = computed(() => {
  const size = toolbarButtonSizes[ui.prefs.toolbarSize || 'm'] || toolbarButtonSizes.m
  return { '--exp-layer-panel-left': `${size + 28}px` }
})

function updateLayoutViewport() {
  const rect = chromeRoot.value?.getBoundingClientRect?.()
  const next = {
    width: rect?.width || window.innerWidth || 0,
    height: rect?.height || window.innerHeight || 0,
  }
  if (next.width !== layoutViewport.value.width || next.height !== layoutViewport.value.height) {
    layoutViewport.value = next
  }
  scheduleMeasuredLayout()
}

function registerLayoutItem(id, el) {
  if (el) {
    if (layoutItemEls[id] === el) return
    layoutItemEls[id] = el
    if (_layoutResizeObserver) _layoutResizeObserver.observe(el)
  } else {
    delete layoutItemEls[id]
  }
  scheduleMeasuredLayout()
}

function scheduleMeasuredLayout() {
  if (_layoutMeasureFrame) return
  _layoutMeasureFrame = window.requestAnimationFrame(() => {
    _layoutMeasureFrame = 0
    updateMeasuredLayout()
  })
}

function updateMeasuredLayout() {
  const next = {}
  for (const [id, el] of Object.entries(layoutItemEls)) {
    const rect = el?.getBoundingClientRect?.()
    if (!rect) continue
    next[id] = { width: rect.width || 0, height: rect.height || 0 }
  }
  if (sameLayoutSizes(layoutItemSizes.value, next)) return
  layoutItemSizes.value = next
}

function sameLayoutSizes(a, b) {
  const aKeys = Object.keys(a || {})
  const bKeys = Object.keys(b || {})
  if (aKeys.length !== bKeys.length) return false
  for (const key of aKeys) {
    const av = a[key] || {}
    const bv = b[key] || {}
    if (Math.round(av.width) !== Math.round(bv.width)) return false
    if (Math.round(av.height) !== Math.round(bv.height)) return false
  }
  return true
}

function cellPoint(col, row) {
  return {
    x: LAYOUT_MARGIN + col * LAYOUT_STEP,
    y: LAYOUT_MARGIN + row * LAYOUT_STEP,
  }
}

function anchorWeight(value) {
  return LAYOUT_ANCHOR[value] ?? 0
}

function clampLayoutPx(id, x, y, anchorX, anchorY) {
  const size = layoutItemSizes.value[id] || { width: 0, height: 0 }
  const rawLeft = x - size.width * anchorWeight(anchorX)
  const rawTop = y - size.height * anchorWeight(anchorY)
  const maxLeft = Math.max(LAYOUT_MARGIN, layoutViewport.value.width - LAYOUT_MARGIN - size.width)
  const maxTop = Math.max(LAYOUT_MARGIN, layoutViewport.value.height - LAYOUT_MARGIN - size.height)
  return {
    left: Math.max(LAYOUT_MARGIN, Math.min(maxLeft, rawLeft)),
    top: Math.max(LAYOUT_MARGIN, Math.min(maxTop, rawTop)),
  }
}

function layoutItemStyle(id) {
  const item = ui.prefs.chromeLayout?.[id]
  const active = layoutDrag.value?.id === id ? layoutDrag.value.preview : null
  const anchorX = active?.anchorX || item?.anchorX || 'start'
  const anchorY = active?.anchorY || item?.anchorY || 'start'
  const point = active || layoutPointFor(item?.col ?? 0, item?.row ?? 0, anchorX, anchorY)
  return {
    left: `${point.x}px`,
    top: `${point.y}px`,
    transform: `translate(${-anchorWeight(anchorX) * 100}%, ${-anchorWeight(anchorY) * 100}%)`,
  }
}

function layoutPointFor(col, row, anchorX, anchorY) {
  const fromLeft = LAYOUT_MARGIN + col * LAYOUT_STEP
  const fromTop = LAYOUT_MARGIN + row * LAYOUT_STEP
  return {
    x: anchorX === 'end'
      ? layoutViewport.value.width - fromLeft
      : anchorX === 'center'
        ? (layoutViewport.value.width / 2) + col * LAYOUT_STEP
        : fromLeft,
    y: anchorY === 'end'
      ? layoutViewport.value.height - fromTop
      : anchorY === 'center'
        ? (layoutViewport.value.height / 2) + row * LAYOUT_STEP
        : fromTop,
  }
}

function layerPanelStyle() {
  const anchor = layoutItemStyle('map')
  const size = layoutItemSizes.value.map || { width: 40, height: 40 }
  const panelW = 300
  const leftPx = Number.parseFloat(anchor.left) || LAYOUT_MARGIN
  const topPx = Number.parseFloat(anchor.top) || LAYOUT_MARGIN
  const opensRight = leftPx + size.width + LAYOUT_GAP + panelW <= layoutViewport.value.width - LAYOUT_MARGIN
  const left = opensRight
    ? leftPx + size.width + LAYOUT_GAP
    : Math.max(LAYOUT_MARGIN, leftPx - panelW - LAYOUT_GAP)
  const top = Math.max(LAYOUT_MARGIN, Math.min(layoutViewport.value.height - 160, topPx))
  return {
    left: `${left}px`,
    top: `${top}px`,
    bottom: `${LAYOUT_MARGIN}px`,
  }
}

function layoutAnchorFor(col, row) {
  const maxCol = Math.max(0, Math.round((layoutViewport.value.width - (LAYOUT_MARGIN * 2)) / LAYOUT_STEP))
  const maxRow = Math.max(0, Math.round((layoutViewport.value.height - (LAYOUT_MARGIN * 2)) / LAYOUT_STEP))
  return {
    anchorX: col <= maxCol * 0.33 ? 'start' : col >= maxCol * 0.67 ? 'end' : 'center',
    anchorY: row <= maxRow * 0.33 ? 'start' : row >= maxRow * 0.67 ? 'end' : 'center',
  }
}

function gridCellFromPointer(event) {
  const rect = chromeRoot.value?.getBoundingClientRect?.()
  const localX = (event.clientX - (rect?.left || 0)) - LAYOUT_MARGIN
  const localY = (event.clientY - (rect?.top || 0)) - LAYOUT_MARGIN
  const rawCol = Math.max(0, Math.round(localX / LAYOUT_STEP))
  const rawRow = Math.max(0, Math.round(localY / LAYOUT_STEP))
  const maxCol = Math.max(0, Math.round((layoutViewport.value.width - (LAYOUT_MARGIN * 2)) / LAYOUT_STEP))
  const maxRow = Math.max(0, Math.round((layoutViewport.value.height - (LAYOUT_MARGIN * 2)) / LAYOUT_STEP))
  const screenCol = Math.min(maxCol, rawCol)
  const screenRow = Math.min(maxRow, rawRow)
  const anchor = layoutAnchorFor(screenCol, screenRow)
  return {
    col: anchor.anchorX === 'end' ? maxCol - screenCol : anchor.anchorX === 'center' ? screenCol - Math.round(maxCol / 2) : screenCol,
    row: anchor.anchorY === 'end' ? maxRow - screenRow : anchor.anchorY === 'center' ? screenRow - Math.round(maxRow / 2) : screenRow,
    ...anchor,
  }
}

function previewForCell(cell) {
  const point = layoutPointFor(cell.col, cell.row, cell.anchorX, cell.anchorY)
  return { ...cell, x: point.x, y: point.y }
}

function onLayoutPointerDown(id, event) {
  if (!ui.layoutCustomizing || event.button !== 0) return
  if (id === 'layout' && event.target?.closest?.('button')) return
  event.preventDefault()
  event.stopPropagation()
  const cell = gridCellFromPointer(event)
  layoutDrag.value = { id, preview: previewForCell(cell) }
  window.addEventListener('pointermove', onLayoutPointerMove)
  window.addEventListener('pointerup', onLayoutPointerUp, { once: true })
}

function onLayoutPointerMove(event) {
  if (!layoutDrag.value) return
  event.preventDefault()
  const cell = gridCellFromPointer(event)
  layoutDrag.value = {
    ...layoutDrag.value,
    preview: previewForCell(cell),
  }
}

function onLayoutPointerUp(event) {
  window.removeEventListener('pointermove', onLayoutPointerMove)
  const drag = layoutDrag.value
  layoutDrag.value = null
  if (!drag) return
  const cell = gridCellFromPointer(event)
  ui.setChromeLayoutItem(drag.id, cell)
}

provide('expeditionLayout', {
  registerLayoutItem,
  itemStyle: layoutItemStyle,
  onPointerDown: onLayoutPointerDown,
  labels: LAYOUT_LABELS,
  customizing: computed(() => ui.layoutCustomizing),
  dragId: layoutDragId,
})

// Reset-tilt: snap pitch and bearing back to a flat top-down view,
// keeping the current center, zoom, and any active filters intact.
// Cheap no-op when already flat (MapLibre will not animate if the
// values match the current camera).
function resetTilt() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  m.easeTo({ pitch: 0, bearing: 0, duration: 500 })
}

// Toolbar handler — routes button ids from any FloatingToolbar in
// the chrome tree to their actions. Each id maps to exactly one
// effect; no state machine, no toggle-vs-action ambiguity.
function onToolbarTrigger(id) {
  if (id === 'layers') ui.toggleLeftPanel('layers')
  else if (id === 'search') ui.toggleSearch()
  else if (id === 'settings') ui.toggleSettings()
  else if (id === 'fit-all') fitToData('all')
  else if (id === 'fit-visible') fitToData('visible')
  else if (id === 'tilt-reset') resetTilt()
}

/**
 * Flatten a GeoJSON geometry's coordinates to a flat array of
 * [lng, lat] pairs. Handles Point, Polygon, MultiPolygon. Other
 * geometry types are ignored by callers and return [].
 */
function _coordsOf(geom) {
  if (!geom) return []
  if (geom.type === 'Point') return [geom.coordinates]
  if (geom.type === 'Polygon') return geom.coordinates.flat()
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2)
  return []
}

/**
 * Build a [west, south, east, north] envelope from a list of
 * GeoJSON features. Returns null when no features have usable
 * coordinates — caller falls back to a saved viewport in that case.
 */
function _envelopeOf(features) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  let saw = false
  for (const f of features || []) {
    for (const [x, y] of _coordsOf(f?.geometry)) {
      if (typeof x !== 'number' || typeof y !== 'number') continue
      if (x < minX) minX = x; if (y < minY) minY = y
      if (x > maxX) maxX = x; if (y > maxY) maxY = y
      saw = true
    }
  }
  return saw ? { minX, minY, maxX, maxY } : null
}

/**
 * True if any coordinate of `feature` lies inside `bounds` (a
 * MapLibre LngLatBounds). Conservative — polygon overlap is
 * approximated by vertex containment, which is good enough for
 * "is this thing on screen?" decisions.
 */
function _intersectsBounds(feature, bounds) {
  for (const c of _coordsOf(feature?.geometry)) {
    if (bounds.contains(c)) return true
  }
  return false
}

/**
 * Collect every feature that counts as "data" for fit-to-data:
 * enabled layers' GeoJSON FeatureCollections plus the active map's
 * zones. Zones are wrapped in a minimal Feature shape so the
 * envelope helpers can treat them uniformly.
 */
function _collectAllData({ includeZones = true } = {}) {
  const out = []
  // layerStore.features is keyed by layer.name; each value is a
  // GeoJSON FeatureCollection ({ features: [...] }).
  for (const lyr of layers.layers || []) {
    if (lyr.enabled === false || lyr.enabled === 0) continue
    const fc = layers.features?.[lyr.name]
    if (fc && Array.isArray(fc.features)) out.push(...fc.features)
  }
  // Zones live in zoneStore.byMap keyed by map name.
  const activeMapName = includeZones ? mapStore.activeMap?.map?.name : null
  if (activeMapName) {
    const list = zoneStore.byMap?.[activeMapName] || []
    for (const z of list) {
      if (z && z.geometry) out.push({ type: 'Feature', geometry: z.geometry })
    }
  }
  return out
}

function _fitEnvelope(m, env) {
  if (!m || !env) return false
  m.fitBounds([[env.minX, env.minY], [env.maxX, env.maxY]], {
    padding: 60, duration: 800, maxZoom: 14,
  })
  return true
}

/**
 * Fit camera to data. The mode is decided by the toolbar button
 * the user clicked (fit-all vs fit-visible); no preference is
 * persisted — both modes are always available in the chrome.
 *   'all'    — every currently rendered feature from enabled layers,
 *              plus zones when search is not active. Falls back to
 *              server bounds only when no rendered features are cached.
 *   'visible' (default) — only features inside the current viewport
 *              so the user zooms IN toward whatever is on screen.
 *              If no features are in the viewport, it does nothing.
 */
function fitToData(mode = 'visible') {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return

  if (mode === 'all') {
    _fitAllBounds(m)
    return
  }

  // 'visible' — scan in-memory features, filter to viewport
  const all = _collectAllData({ includeZones: !layers.activeSearch })
  const candidates = all.filter((f) => _intersectsBounds(f, m.getBounds()))
  const env = _envelopeOf(candidates)
  _fitEnvelope(m, env)
}

/**
 * Fit enabled layer data first. Zones are only a fallback when there
 * are no rendered/cached layer bounds. Fires bounds fetches in parallel for any layer
 * that isn't cached yet — the union waits for the slowest. On
 * total failure (no perms, network), falls back to the saved
 * viewport or a global envelope.
 */
async function _fitAllBounds(m) {
  const renderedEnv = _envelopeOf(_collectAllData({ includeZones: false }))
  if (_fitEnvelope(m, renderedEnv)) return
  if (layers.activeSearch) return

  const layerNames = (layers.visibleLayers || []).map((l) => l.name)
  // Fire all bounds fetches in parallel — they're served from
  // frappe.cache after the first call so most return instantly.
  const results = await Promise.all(
    layerNames.map((n) => layers.fetchBounds(n).catch(() => null)),
  )
  let south = Infinity
  let west = Infinity
  let north = -Infinity
  let east = -Infinity
  for (const b of results) {
    if (!b || typeof b.south !== 'number') continue
    if (b.south < south) south = b.south
    if (b.north > north) north = b.north
    if (b.west < west) west = b.west
    if (b.east > east) east = b.east
  }
  if (isFinite(south)) {
    m.fitBounds([[west, south], [east, north]], {
      padding: 60, duration: 800, maxZoom: 14,
    })
    return
  }

  // Fall back to zones only when no layer data is available.
  const zones = []
  const activeMapName = mapStore.activeMap?.map?.name
  if (activeMapName) {
    const list = zoneStore.byMap?.[activeMapName] || []
    for (const z of list) {
      if (z && z.geometry) zones.push({ type: 'Feature', geometry: z.geometry })
    }
  }
  const zEnv = _envelopeOf(zones)
  if (_fitEnvelope(m, zEnv)) return

  // No bounds in scope — fall back to saved viewport, then global.
  const v = mapStore.activeMap?.map?.viewport
  if (v?.center) {
    m.flyTo({ center: v.center, zoom: v.zoom ?? 4, duration: 800 })
  } else {
    m.fitBounds([[-180, -85], [180, 85]], { padding: 60, duration: 800 })
  }
}
</script>

<template>
  <div
    ref="chromeRoot"
    class="expedition"
    :class="{
      'expedition--chrome-hidden': ui.chromeHidden,
      'expedition--layout-editing': ui.layoutCustomizing,
      'expedition--shortcut-hover': ui.shortcutAltDown,
      'expedition--shortcut-all': ui.shortcutHintsAll,
    }"
    :style="chromeStyle"
  >
    <Basemap class="expedition__basemap" />
    <LoadingOverlay />

    <!-- Left-edge map workspace panel. Slides in from left. -->
    <Transition name="lp-slide">
      <div v-if="ui.leftPanel === 'layers'" class="expedition__left" :style="layerPanelStyle()">
        <LayerPanel @close="ui.closeLeftPanel()" />
      </div>
    </Transition>

    <MapToolTray />

    <!-- Top-left toolbar: layers -->
    <div
      :ref="(el) => registerLayoutItem('map', el)"
      class="expedition__tl expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'map' }"
      :style="layoutItemStyle('map')"
      @pointerdown.capture="(e) => onLayoutPointerDown('map', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.map }}</div>
      <FloatingToolbar :buttons="tlButtons" @trigger="onToolbarTrigger" />
    </div>

    <!-- Top-right toolbars: search (separate group, on the left) +
         settings (separate group, pinned to the corner). A gap
         between them keeps the groups visually distinct. -->
    <div
      :ref="(el) => registerLayoutItem('search', el)"
      class="expedition__tr expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'search' }"
      :style="layoutItemStyle('search')"
      @pointerdown.capture="(e) => onLayoutPointerDown('search', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.search }}</div>
      <FloatingToolbar :buttons="trSearchButtons" @trigger="onToolbarTrigger" />
    </div>
    <div
      :ref="(el) => registerLayoutItem('settings', el)"
      class="expedition__tr expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'settings' }"
      :style="layoutItemStyle('settings')"
      @pointerdown.capture="(e) => onLayoutPointerDown('settings', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.settings }}</div>
      <FloatingToolbar :buttons="trSettingsButtons" @trigger="onToolbarTrigger" />
    </div>

    <!-- Bottom-right: two standard FloatingToolbars stacked
         vertically — a fit tray with both fit modes (fit-all +
         fit-visible) as buttons stacked inside one pill, then
         reset-tilt as its own tray below. Each tray uses the same
         `.ft` chrome and button rhythm as TL/TR/CR. A row below
         holds the eye-toggle on the LEFT and the basemap (Liberty)
         panel on the right. In chrome-hidden mode the eye pill is
         the only chrome element that stays mounted (dimmed). -->
    <div
      :ref="(el) => registerLayoutItem('fit', el)"
      class="expedition__br expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'fit' }"
      :style="layoutItemStyle('fit')"
      @pointerdown.capture="(e) => onLayoutPointerDown('fit', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.fit }}</div>
        <FloatingToolbar :buttons="brFitButtons" @trigger="onToolbarTrigger" />
    </div>
    <div
      :ref="(el) => registerLayoutItem('tilt', el)"
      class="expedition__br expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'tilt' }"
      :style="layoutItemStyle('tilt')"
      @pointerdown.capture="(e) => onLayoutPointerDown('tilt', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.tilt }}</div>
        <FloatingToolbar :buttons="brTiltButtons" @trigger="onToolbarTrigger" />
    </div>
          <div
      :ref="(el) => registerLayoutItem('layout', el)"
      class="expedition__br-eye expedition__layout-item expedition__layout-item--clickable"
      :class="[
        'expedition__br-eye--' + (ui.prefs.toolbarSize || 'm'),
        { 'expedition__layout-item--dragging': layoutDrag?.id === 'layout' },
      ]"
      :style="layoutItemStyle('layout')"
      role="toolbar"
      aria-label="Layout customization"
      @pointerdown.capture="(e) => onLayoutPointerDown('layout', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.layout }}</div>
      <button
        type="button"
        class="expedition__br-eye-btn"
        :class="{ 'expedition__br-eye-btn--active': ui.layoutCustomizing }"
        :aria-label="ui.layoutCustomizing ? 'Finish customizing layout' : 'Customize layout'"
        :title="ui.layoutCustomizing ? 'Finish customizing layout' : 'Customize layout'"
        :aria-pressed="ui.layoutCustomizing ? 'true' : 'false'"
        @click="ui.toggleLayoutCustomizing()"
      >
        <svg viewBox="0 0 24 24" class="expedition__br-eye-icon" aria-hidden="true">
          <path
            :d="GLYPHS.layout"
            fill="none" stroke="currentColor" stroke-width="1.6"
            stroke-linecap="round" stroke-linejoin="round"
          />
        </svg>
        <span class="expedition__keycap" aria-hidden="true">{{ shortcutLabel('layout') }}</span>
      </button>
    </div>
    <div
      :ref="(el) => registerLayoutItem('visibility', el)"
      class="expedition__br-eye expedition__br-eye--visibility expedition__layout-item"
      :class="[
        'expedition__br-eye--' + (ui.prefs.toolbarSize || 'm'),
        { 'expedition__layout-item--dragging': layoutDrag?.id === 'visibility' },
      ]"
      :style="layoutItemStyle('visibility')"
      role="toolbar"
      aria-label="UI visibility"
      @pointerdown.capture="(e) => onLayoutPointerDown('visibility', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.visibility }}</div>
            <button
              type="button"
              class="expedition__br-eye-btn"
              :class="{ 'expedition__br-eye-btn--active': ui.chromeHidden }"
              :aria-label="ui.chromeHidden ? 'Show UI' : 'Hide UI'"
              :title="ui.chromeHidden ? 'Show UI' : 'Hide UI'"
              :aria-pressed="ui.chromeHidden ? 'true' : 'false'"
              @click="ui.toggleChrome()"
            >
              <svg viewBox="0 0 24 24" class="expedition__br-eye-icon" aria-hidden="true">
                <path
                  :d="ui.chromeHidden ? GLYPHS.eyeOff : GLYPHS.eye"
                  fill="none" stroke="currentColor" stroke-width="1.6"
                  stroke-linecap="round" stroke-linejoin="round"
                />
              </svg>
              <span class="expedition__keycap" aria-hidden="true">{{ shortcutLabel('hide-ui') }}</span>
            </button>
          </div>
    <div
      :ref="(el) => registerLayoutItem('basemap', el)"
      class="expedition__br expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'basemap' }"
      :style="layoutItemStyle('basemap')"
      @pointerdown.capture="(e) => onLayoutPointerDown('basemap', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.basemap }}</div>
      <BasemapPanel />
    </div>

    <!-- Bottom-left: lat/lng + zoom readout. Sits clear of MapLibre's scale
         bar (which lives at the very bottom-left). -->
    <div
      :ref="(el) => registerLayoutItem('coords', el)"
      class="expedition__bl expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'coords' }"
      :style="layoutItemStyle('coords')"
      @pointerdown.capture="(e) => onLayoutPointerDown('coords', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.coords }}</div>
      <CoordReadout />
    </div>

    <!-- Bottom-center: legend of enabled layers (hidden when empty). -->
    <div
      :ref="(el) => registerLayoutItem('legend', el)"
      class="expedition__bc expedition__layout-item"
      :class="{ 'expedition__layout-item--dragging': layoutDrag?.id === 'legend' }"
      :style="layoutItemStyle('legend')"
      @pointerdown.capture="(e) => onLayoutPointerDown('legend', e)"
    >
      <div v-if="ui.layoutCustomizing" class="expedition__layout-handle">{{ LAYOUT_LABELS.legend }}</div>
      <Legend />
    </div>

    <div v-if="ui.layoutCustomizing" class="expedition__layout-overlay" aria-hidden="true">
      <span
        v-for="n in LAYOUT_ROWS * LAYOUT_COLS"
        :key="n"
        class="expedition__layout-cell"
      />
    </div>
    <div v-if="ui.layoutCustomizing" class="expedition__layout-actions">
      <button type="button" class="expedition__layout-action" @click="ui.resetChromeLayout()">Reset</button>
      <button type="button" class="expedition__layout-action expedition__layout-action--primary" @click="ui.setLayoutCustomizing(false)">Done</button>
    </div>

    <MapPopup />
    <span class="chrome-hideable"><MeasureTool /></span>
    <span class="chrome-hideable"><SearchBar /></span>
    <CommandPalette v-if="ui.commandKOpen" class="chrome-hideable" />
    <span class="chrome-hideable"><LayerEditor /></span>
    <span class="chrome-hideable"><ContextMenu /></span>
    <div class="expedition__settings-anchor chrome-hideable">
      <UserSettings :open="ui.settingsOpen" @close="ui.closeSettings()" />
    </div>
    <!-- Global confirm dialog. Mounted at the root so any component
         can call ui.ask(...) and have it surface here. -->
    <ConfirmModal />
  </div>
</template>

<style scoped>
:global(html),
:global(body),
:global(#expedition-root) {
  width: 100%;
  height: 100%;
  margin: 0;
  overflow: hidden;
  background: #0B0E14;
}
:global(body) {
  overscroll-behavior: none;
}

.expedition {
  position: fixed;
  inset: 0;
  overflow: hidden;
  background: #0B0E14;
  color: #E6E8EC;
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  font-feature-settings: 'cv11', 'ss01', 'ss03';
}

/* Scrollbars are global within the Expedition map only. The parent
   component is scoped, so :deep keeps child panel scroll areas covered
   without leaking these rules into Desk or other Frappe pages. */
.expedition,
.expedition :deep(*) {
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}
.expedition:hover,
.expedition :deep(*:hover) {
  scrollbar-color: rgba(255, 255, 255, 0.32) transparent;
}
.expedition::-webkit-scrollbar,
.expedition :deep(*)::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.expedition::-webkit-scrollbar-track,
.expedition :deep(*)::-webkit-scrollbar-track {
  background: transparent;
}
.expedition::-webkit-scrollbar-thumb,
.expedition :deep(*)::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}
.expedition:hover::-webkit-scrollbar-thumb,
.expedition :deep(*:hover)::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.32);
}
.expedition::-webkit-scrollbar-thumb:vertical,
.expedition :deep(*)::-webkit-scrollbar-thumb:vertical {
  min-height: 24px;
}

.expedition__basemap { position: absolute; inset: 0; }

/* Left-edge panel placement + slide-in transition. */
.expedition__left {
  position: absolute;
  top: 12px; left: var(--exp-layer-panel-left, 60px); bottom: 12px;
  z-index: 35;
  pointer-events: none; /* the panel itself turns events back on */
}
.expedition__left > * { pointer-events: auto; }
.lp-slide-enter-active, .lp-slide-leave-active {
  transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1), opacity 220ms ease;
}
.lp-slide-enter-from { transform: translateX(-20px); opacity: 0; }
.lp-slide-leave-to { transform: translateX(-20px); opacity: 0; }

/* Right-edge panel placement + slide-in transition.
   Anchored to the center-right tools button (top: 25%, button is
   32px tall) so the panel "grows out" of the button that opened
   it instead of dropping from the top of the viewport. */
.expedition__right {
  position: absolute;
  top: calc(25% - 21px); right: 12px; bottom: 12px;
  z-index: 15;
  pointer-events: none;
}
.expedition__right > * { pointer-events: auto; }
.tp-slide-enter-active, .tp-slide-leave-active {
  transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1), opacity 220ms ease;
}
.tp-slide-enter-from { transform: translateX(20px); opacity: 0; }
.tp-slide-leave-to { transform: translateX(20px); opacity: 0; }

/* Corner placements. pointer-events: none so the map stays pannable
   when the cursor is over the empty space around each toolbar. */
.expedition__tl, .expedition__tr, .expedition__br, .expedition__bl, .expedition__bc, .expedition__cr {
  position: absolute;
  pointer-events: none;
  z-index: 20;
}
.expedition__layout-item {
  position: absolute;
  right: auto;
  bottom: auto;
  transform: none;
  z-index: 22;
  transition: left 160ms cubic-bezier(0.16, 1, 0.3, 1), top 160ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 120ms ease;
}
.expedition__layout-handle {
  display: none;
}
.expedition--layout-editing .expedition__layout-item {
  pointer-events: auto;
  cursor: grab;
  border-radius: 10px;
  outline: 1px solid rgba(147, 197, 253, 0.72);
  background: rgba(8, 10, 15, 0.20);
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.10), 0 12px 34px rgba(0, 0, 0, 0.32);
}
.expedition--layout-editing .expedition__layout-item > :not(.expedition__layout-handle) {
  pointer-events: none;
}
.expedition--layout-editing .expedition__layout-item--clickable > :not(.expedition__layout-handle) {
  pointer-events: auto;
}
.expedition--layout-editing .expedition__layout-item--dragging {
  cursor: grabbing;
  transition: none;
  z-index: 45;
  outline-color: #93C5FD;
  box-shadow: 0 0 0 5px rgba(59, 130, 246, 0.18), 0 18px 46px rgba(0, 0, 0, 0.48);
}
.expedition--layout-editing .expedition__layout-handle {
  position: absolute;
  top: -18px;
  left: 0;
  right: 0;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #BFDBFE;
  font-size: 10px;
  font-weight: 700;
  line-height: 14px;
  letter-spacing: 0;
  text-transform: uppercase;
  pointer-events: none;
}
.expedition__layout-overlay {
  position: absolute;
  inset: 12px;
  z-index: 18;
  display: grid;
  grid-template-columns: repeat(24, minmax(0, 1fr));
  grid-template-rows: repeat(12, minmax(0, 1fr));
  gap: 12px;
  pointer-events: none;
}
.expedition__layout-cell {
  border: 1px dashed rgba(147, 197, 253, 0.24);
  border-radius: 6px;
  background: rgba(59, 130, 246, 0.035);
}
.expedition__layout-actions {
  position: absolute;
  left: 50%;
  top: 18px;
  z-index: 60;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
  padding: 5px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(11, 14, 20, 0.84);
  backdrop-filter: blur(18px) saturate(150%);
  box-shadow: 0 12px 34px rgba(0, 0, 0, 0.42);
}
.expedition__layout-action {
  border: 0;
  border-radius: 8px;
  padding: 7px 11px;
  background: rgba(255, 255, 255, 0.08);
  color: #E6E8EC;
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}
.expedition__layout-action:hover {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
}
.expedition__layout-action--primary {
  background: rgba(59, 130, 246, 0.30);
  color: #DBEAFE;
}
.expedition__layout-action--primary:hover {
  background: rgba(59, 130, 246, 0.42);
}
.expedition__tl { top: 12px; left: 12px; }
/* Top-right is now a flex row of two toolbars: search sits to the
   left, settings is pinned to the corner. A 12px gap keeps the
   groups visually distinct. */
.expedition__tr {
  top: 12px; right: 12px;
  display: flex;
  flex-direction: row;
  gap: 12px;
  align-items: flex-start;
}
/* Center-right: tools button anchored 25% from the top of the
   viewport. top: 25% works regardless of viewport height. */
.expedition__cr {
  top: 25%;
  right: 12px;
  transform: translateY(-50%);  /* center the toolbar ON 25% */
}
.expedition__br { right: 12px; bottom: 12px; display: flex; gap: 8px; align-items: flex-end; }
.expedition__br-stack { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }

/* Bottom row of the BR stack: eye-toggle on the left, basemap
   (Liberty) trigger on the right. Sits flush with the viewport
   edge so the eye button is the absolute last thing the cursor
   reaches in the bottom-right corner. */
.expedition__br-row {
  display: flex;
  flex-direction: row;
  gap: 8px;
  align-items: center;
}
/* The eye pill mirrors the .ft chrome used by FloatingToolbar
   (same background/border/blur) so it sits at the same visual
   weight as the basemap trigger next to it. Self-contained here
   because FloatingToolbar's .ft class is scoped to that component
   and can't be reused.

   Sizing mirrors FloatingToolbar's `.ft--{size}` scale and the
   basemap trigger's `.bp--{size}` scale — same button edge and
   icon sizes. `expedition__br-eye--{size}` is applied at runtime
   from ui.prefs.toolbarSize so swapping the size in Settings
   resizes the eye pill in step with TL/TR/CR/BR buttons. */
.expedition__br-eye {
  display: inline-flex;
  background: rgba(11, 14, 20, 0.72);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 4px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  pointer-events: auto;
}
.expedition__br-eye--xs  { --ft-size: 22px; --ft-icon: 12px; }
.expedition__br-eye--s   { --ft-size: 28px; --ft-icon: 14px; }
.expedition__br-eye--m   { --ft-size: 32px; --ft-icon: 16px; }
.expedition__br-eye--lg  { --ft-size: 40px; --ft-icon: 18px; }
.expedition__br-eye--xlg { --ft-size: 48px; --ft-icon: 20px; }

.expedition__br-eye-btn {
  position: relative;
  width: var(--ft-size, 32px);
  height: var(--ft-size, 32px);
  display: flex; align-items: center; justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 8px;
  color: rgba(230, 232, 236, 0.88);
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
  font-family: inherit;
  padding: 0;
}
.expedition__br-eye-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.expedition__br-eye-btn--active {
  background: rgba(59, 130, 246, 0.18);
  color: #93C5FD;
}
.expedition__br-eye-icon {
  width: var(--ft-icon, 16px);
  height: var(--ft-icon, 16px);
  flex: none;
}
.expedition__keycap {
  position: absolute;
  right: calc(100% + 6px);
  top: 50%;
  transform: translateY(-50%) scale(0.96);
  opacity: 0;
  pointer-events: none;
  padding: 3px 6px;
  border-radius: 6px;
  background: rgba(8, 10, 15, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 10px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: 0;
  white-space: nowrap;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.38);
  transition: opacity 80ms ease, transform 80ms ease;
  z-index: 3;
}
.expedition__tr :deep(.ft__key),
.expedition__br :deep(.ft__key) {
  left: auto;
  right: calc(100% + 6px);
}

/* The basemap panel trigger needs pointer-events on, even though its
   container is none. The component itself owns the click. */
.expedition__br > * { pointer-events: auto; }

/* Bottom-left: lat/lng readout. Same 12px edge inset as the
   corner toolbars so all bottom chrome sits on one rail. */
.expedition__bl {
  left: 12px;
  bottom: 12px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.expedition__bl > * { pointer-events: auto; }

/* Bottom-center: legend. Same 12px bottom inset as the corner
   toolbars. translateX(-50%) centers the pill on the horizontal
   midline of the viewport. */
.expedition__bc {
  left: 50%;
  bottom: 12px;
  transform: translateX(-50%);
}
.expedition__bc > * { pointer-events: auto; }
.expedition__layout-item {
  right: auto;
  bottom: auto;
  transform: none;
}

/* Settings modal — the UserSettings component owns its own
   fixed-position overlay (click backdrop to dismiss). We just
   ensure pointer-events pass through and it's above everything. */
.expedition__settings-anchor {
  position: absolute;
  inset: 0;
  z-index: 40;
  pointer-events: none;
}
.expedition__settings-anchor > * { pointer-events: auto; }

/* Chrome-hidden mode. When the eye-toggle is on, every chrome
   surface fades to invisible AND stops receiving pointer events —
   the map and data own the canvas. The eye button itself is the
   one exception: it stays mounted at low opacity so the user can
   tap it to bring everything back.

   The map basemap (.expedition__basemap), LoadingOverlay, and
   MapPopup are NOT hidden — they're data/feedback, not chrome.
   Child components that mount with their own positioning
   (MeasureTool, SearchBar, UserSettings, LayerEditor, ContextMenu,
   CommandPalette) are wrapped in .chrome-hideable spans and
   hidden via the unscoped <style> block below — scoped CSS can't
   reach them. */
.expedition--chrome-hidden .expedition__layout-item:not(.expedition__br-eye--visibility):not(.expedition__bl),
.expedition--chrome-hidden .expedition__left,
.expedition--chrome-hidden .expedition__right {
  opacity: 0;
  pointer-events: none;
  transition: opacity 220ms ease;
}
/* The eye button: dimmed but visible. opacity:0.22 is faint enough
   to not register as chrome, high enough that the user can find it.
   transition matches the 220ms used elsewhere for symmetric fade
   in / out. */
.expedition--chrome-hidden .expedition__br-eye--visibility {
  opacity: 0.22;
  transition: opacity 220ms ease;
}
.expedition--chrome-hidden .expedition__br-eye--visibility:hover {
  opacity: 0.7;
}

/* CoordReadout (.expedition__bl, bottom-left): stays mounted in
   chrome-hidden mode so the user can still read the cursor lat/lng
   and zoom. Dimmed (not faded out) — opacity 0.4 is faint enough
   to recede behind the map as ambient info, bright enough to read
   the numbers at a glance. Hover lifts to 0.85 in case the user
   wants to copy or cycle the format. Pointer events stay on so the
   pill remains interactive. transition matches the 220ms used
   elsewhere. */
.expedition--chrome-hidden .expedition__bl {
  opacity: 0.4;
  pointer-events: auto;
  transition: opacity 220ms ease;
}
.expedition--chrome-hidden .expedition__bl:hover {
  opacity: 0.85;
}

/* Old chrome styles removed in PR-6. */
</style>

<!-- Unscoped styles. The .chrome-hideable wrappers live around
     child components (MeasureTool, SearchBar, UserSettings) — Vue
     scoped CSS can't reach those children, so this block hides
     them globally when chrome-hidden is on. Scoped rules above
     cover everything else. -->
<style>
.expedition .expedition__layout-item {
  position: absolute;
  right: auto;
  bottom: auto;
  transform: none;
  z-index: 22;
  transition: left 160ms cubic-bezier(0.16, 1, 0.3, 1), top 160ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 120ms ease;
}
.expedition .expedition__layout-handle {
  display: none;
}
.expedition.expedition--layout-editing .expedition__layout-item {
  pointer-events: auto;
  cursor: grab;
  border-radius: 10px;
  outline: 1px solid rgba(147, 197, 253, 0.72);
  background: rgba(8, 10, 15, 0.20);
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.10), 0 12px 34px rgba(0, 0, 0, 0.32);
}
.expedition.expedition--layout-editing .expedition__layout-item > :not(.expedition__layout-handle) {
  pointer-events: none;
}
.expedition.expedition--layout-editing .expedition__layout-item--clickable > :not(.expedition__layout-handle) {
  pointer-events: auto;
}
.expedition.expedition--layout-editing .expedition__layout-item--dragging {
  cursor: grabbing;
  transition: none;
  z-index: 45;
  outline-color: #93C5FD;
  box-shadow: 0 0 0 5px rgba(59, 130, 246, 0.18), 0 18px 46px rgba(0, 0, 0, 0.48);
}
.expedition.expedition--layout-editing .expedition__layout-handle {
  position: absolute;
  top: -18px;
  left: 0;
  right: 0;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #BFDBFE;
  font-size: 10px;
  font-weight: 700;
  line-height: 14px;
  letter-spacing: 0;
  text-transform: uppercase;
  pointer-events: none;
}

.expedition--chrome-hidden .chrome-hideable {
  opacity: 0;
  pointer-events: none;
  transition: opacity 220ms ease;
}

.expedition--shortcut-hover .expedition__br-eye-btn:hover .expedition__keycap,
.expedition--shortcut-all .expedition__keycap,
.expedition--shortcut-hover .mtt__btn:hover .mtt__key,
.expedition--shortcut-hover .mtt__shape-more:hover .mtt__key,
.expedition--shortcut-all .mtt__key {
  opacity: 1;
  transform: translateY(-50%) scale(1);
}

.expedition--shortcut-hover .bp__trigger:hover .bp__key,
.expedition--shortcut-all .bp__key {
  opacity: 1;
  transform: translateX(50%) scale(1);
}
</style>
