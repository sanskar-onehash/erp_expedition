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
import { ref, computed, onMounted } from 'vue'
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

const mapStore = useMapStore()
const ui = useUiStore()
const layers = useLayersStore()
const zoneStore = useZonesStore()
const iconStore = useIconsStore()

onMounted(async () => {
  await iconStore.loadIcons().catch((e) => {
    console.warn('[expedition] custom icons unavailable', e)
  })
  await mapStore.bootstrap()
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      ui.commandKOpen = !ui.commandKOpen
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
      // Toggle the cross-layer search bar (different from Ctrl+K,
      // which toggles the command palette). e.preventDefault()
      // suppresses the browser's native find-in-page.
      e.preventDefault()
      ui.toggleSearch()
    }
  })
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
}

// Top-left: just layers now (search moved to top-right).
const tlButtons = computed(() => [
  { id: 'layers', label: 'Layers', glyph: GLYPHS.layers, badge: enabledLayerCount.value || '' },
])
// Top-right split into two separate toolbars (different visual
// groups) so a user can distinguish at a glance: search is on the
// left of the corner, settings sits at the very corner.
const trSearchButtons = computed(() => [
  { id: 'search', label: 'Search (Ctrl+F)', glyph: GLYPHS.search },
])
const trSettingsButtons = computed(() => [
  { id: 'settings', label: 'Settings', glyph: GLYPHS.settings, active: ui.settingsOpen },
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
  { id: 'fit-all', label: 'Fit to all enabled data', glyph: GLYPHS.fit },
  { id: 'fit-visible', label: 'Fit to visible features', glyph: GLYPHS.fitVisible },
])
const brTiltButtons = computed(() => [
  { id: 'tilt-reset', label: 'Reset tilt (top view)', glyph: GLYPHS.tiltReset },
])

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
function _collectAllData() {
  const out = []
  // layerStore.features is keyed by layer.name; each value is a
  // GeoJSON FeatureCollection ({ features: [...] }).
  for (const lyr of layers.layers || []) {
    if (lyr.enabled === false || lyr.enabled === 0) continue
    const fc = layers.features?.[lyr.name]
    if (fc && Array.isArray(fc.features)) out.push(...fc.features)
  }
  // Zones live in zoneStore.byMap keyed by map name.
  const activeMapName = mapStore.activeMap?.map?.name
  if (activeMapName) {
    const list = zoneStore.byMap?.[activeMapName] || []
    for (const z of list) {
      if (z && z.geometry) out.push({ type: 'Feature', geometry: z.geometry })
    }
  }
  return out
}

/**
 * Fit camera to data. The mode is decided by the toolbar button
 * the user clicked (fit-all vs fit-visible); no preference is
 * persisted — both modes are always available in the chrome.
 *   'all'    — every enabled layer's full lat/lng envelope (from
 *              the server's `get_layer_bounds` cache) plus zones,
 *              regardless of viewport.
 *   'visible' (default) — only features inside the current viewport
 *              so the user zooms IN toward whatever is on screen,
 *              never teleports away. If no features are in the
 *              viewport, escalates to fit-all instead of zooming
 *              out to a globe view (the user always lands on data).
 *
 * Mode 'all' does NOT pull the full feature set across the wire —
 * it just reads the cached per-layer bounds envelope. The
 * feature-cache envelope would be wrong: features are bounds-
 * bounded by `_fetchAllVisibleBounds`, so the cache is only ever
 * as wide as the current viewport.
 */
function fitToData(mode = 'visible') {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return

  if (mode === 'all') {
    _fitAllBounds(m)
    return
  }

  // 'visible' — scan in-memory features, filter to viewport
  const all = _collectAllData()
  const candidates = all.filter((f) => _intersectsBounds(f, m.getBounds()))
  const env = _envelopeOf(candidates)
  if (env) {
    m.fitBounds([[env.minX, env.minY], [env.maxX, env.maxY]], {
      padding: 60, duration: 800, maxZoom: 14,
    })
    return
  }
  // No features inside the current viewport — escalate to fit-all
  // so the user always lands on data instead of zooming out to a
  // globe view. _fitAllBounds handles its own saved-viewport /
  // global-envelope fallbacks.
  _fitAllBounds(m)
}

/**
 * Union every visible layer's cached bounds + the active map's
 * zones, then fit. Fires bounds fetches in parallel for any layer
 * that isn't cached yet — the union waits for the slowest. On
 * total failure (no perms, network), falls back to the saved
 * viewport or a global envelope.
 */
async function _fitAllBounds(m) {
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
  // Union in zones (typically few rows, in-memory is fine).
  const zones = []
  const activeMapName = mapStore.activeMap?.map?.name
  if (activeMapName) {
    const list = zoneStore.byMap?.[activeMapName] || []
    for (const z of list) {
      if (z && z.geometry) zones.push({ type: 'Feature', geometry: z.geometry })
    }
  }
  const zEnv = _envelopeOf(zones)
  if (zEnv) {
    if (zEnv.minY < south) south = zEnv.minY
    if (zEnv.maxY > north) north = zEnv.maxY
    if (zEnv.minX < west) west = zEnv.minX
    if (zEnv.maxX > east) east = zEnv.maxX
  }
  if (isFinite(south)) {
    m.fitBounds([[west, south], [east, north]], {
      padding: 60, duration: 800, maxZoom: 14,
    })
    return
  }
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
  <div class="expedition" :class="{ 'expedition--chrome-hidden': ui.chromeHidden }">
    <Basemap class="expedition__basemap" />
    <LoadingOverlay />

    <!-- Left-edge panel (LayerPanel). Slides in from left. -->
    <Transition name="lp-slide">
      <div v-if="ui.leftPanel === 'layers'" class="expedition__left">
        <LayerPanel @close="ui.closeLeftPanel()" />
      </div>
    </Transition>

    <MapToolTray />

    <!-- Top-left toolbar: layers -->
    <div class="expedition__tl">
      <FloatingToolbar :buttons="tlButtons" @trigger="onToolbarTrigger" />
    </div>

    <!-- Top-right toolbars: search (separate group, on the left) +
         settings (separate group, pinned to the corner). A gap
         between them keeps the groups visually distinct. -->
    <div class="expedition__tr">
      <FloatingToolbar :buttons="trSearchButtons" @trigger="onToolbarTrigger" />
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
    <div class="expedition__br">
      <div class="expedition__br-stack">
        <FloatingToolbar :buttons="brFitButtons" @trigger="onToolbarTrigger" />
        <FloatingToolbar :buttons="brTiltButtons" @trigger="onToolbarTrigger" />
        <div class="expedition__br-row">
          <div
            class="expedition__br-eye"
            :class="'expedition__br-eye--' + (ui.prefs.toolbarSize || 'm')"
            role="toolbar"
            aria-label="UI visibility"
          >
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
            </button>
          </div>
          <BasemapPanel />
        </div>
      </div>
    </div>

    <!-- Bottom-left: lat/lng + zoom readout. Sits clear of MapLibre's scale
         bar (which lives at the very bottom-left). -->
    <div class="expedition__bl">
      <CoordReadout />
    </div>

    <!-- Bottom-center: legend of enabled layers (hidden when empty). -->
    <div class="expedition__bc">
      <Legend />
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
  top: 12px; left: 12px; bottom: 12px;
  z-index: 15;
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
.expedition--chrome-hidden .expedition__tl,
.expedition--chrome-hidden .expedition__tr,
.expedition--chrome-hidden .expedition__cr,
.expedition--chrome-hidden .expedition__bc,
.expedition--chrome-hidden .expedition__left,
.expedition--chrome-hidden .expedition__right,
.expedition--chrome-hidden .expedition__br > .expedition__br-stack > .expedition__br-row > :not(.expedition__br-eye),
.expedition--chrome-hidden .expedition__br > .expedition__br-stack > :not(.expedition__br-row) {
  opacity: 0;
  pointer-events: none;
  transition: opacity 220ms ease;
}
/* The eye button: dimmed but visible. opacity:0.22 is faint enough
   to not register as chrome, high enough that the user can find it.
   transition matches the 220ms used elsewhere for symmetric fade
   in / out. */
.expedition--chrome-hidden .expedition__br-eye {
  opacity: 0.22;
  transition: opacity 220ms ease;
}
.expedition--chrome-hidden .expedition__br-eye:hover {
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
.expedition--chrome-hidden .chrome-hideable {
  opacity: 0;
  pointer-events: none;
  transition: opacity 220ms ease;
}
</style>
