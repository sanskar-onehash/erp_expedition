<script setup>
/**
 * TimelineSlider — GPS route playback with date+time range, trail line,
 * and dwell-density circles.
 *
 * Layer convention (opt-in via GeoJSON feature properties):
 *   _time      (required) — timestamp for each point.
 *              Accepted: "YYYY-MM-DD HH:MM:SS", ISO datetime, epoch-ms,
 *              or bare "HH:MM" / "HH:MM:SS" (date defaults to today).
 *   _track_id  (optional) — groups features into separate tracks
 *              (e.g. technician ID, vehicle plate). One unnamed track
 *              per layer when absent.
 *
 * Trail rendering uses window.expeditionMap.getMap() to inject two
 * temporary MapLibre sources/layers that are cleaned up on close.
 *
 * All internal time values are epoch-ms so multi-day ranges work correctly.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLayersStore } from '../state/layers.js'

const layerStore = useLayersStore()
const emit = defineEmits(['close'])

// ---- MapLibre trail layer IDs ----
const TRAIL_LINE_SOURCE  = 'expedition-tl-lines'
const TRAIL_DWELL_SOURCE = 'expedition-tl-dwells'
const TRAIL_LINE_LAYER        = 'expedition-tl-line'
const TRAIL_DWELL_LAYER       = 'expedition-tl-dwell'
const TRAIL_DWELL_LAYER_RING  = 'expedition-tl-dwell-ring'

// ---- Timestamp parsing → epoch ms ----
function parseTs(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return value
  const s = String(value).trim()
  // Full datetime (Frappe "YYYY-MM-DD HH:MM:SS" or ISO)
  const full = new Date(s.replace(' ', 'T'))
  if (!isNaN(full.getTime())) return full.getTime()
  // Bare "HH:MM[:SS]" — pin to today
  const t = s.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/)
  if (t) {
    const d = new Date()
    d.setHours(parseInt(t[1]), parseInt(t[2]), parseInt(t[3] || '0'), 0)
    return d.getTime()
  }
  return null
}

// ---- Formatting helpers ----
// Convert epoch ms to the value string expected by <input type="datetime-local">
// which is "YYYY-MM-DDTHH:MM" (local time, no seconds, no timezone suffix).
function toInputValue(ms) {
  if (!ms) return ''
  const d = new Date(ms)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// Convert "YYYY-MM-DDTHH:MM" (input value) → epoch ms
function fromInputValue(s) {
  if (!s) return null
  const d = new Date(s)  // local datetime
  return isNaN(d.getTime()) ? null : d.getTime()
}

// Display label: "15 Jan 08:30" or just "08:30" if same day as range start
function fmtTs(ms, refMs) {
  if (!ms) return '--'
  const d = new Date(ms)
  const ref = refMs ? new Date(refMs) : null
  const sameDay = ref && d.toDateString() === ref.toDateString()
  const time = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
  if (sameDay) return time
  const day = d.toLocaleDateString('en', { day: '2-digit', month: 'short' })
  return `${day} ${time}`
}

// ---- Auto-detect range from loaded feature timestamps ----
const detectedRange = computed(() => {
  let lo = Infinity, hi = -Infinity
  for (const layer of layerStore.visibleLayers) {
    const fc = layerStore.features[layer.name]
    for (const f of fc?.features || []) {
      const t = parseTs(f?.properties?._time)
      if (t === null) continue
      if (t < lo) lo = t
      if (t > hi) hi = t
    }
  }
  if (!isFinite(lo)) {
    // No timestamped data yet — default to today 06:00–18:00
    const now = new Date(); now.setSeconds(0, 0)
    const start = new Date(now); start.setHours(6, 0, 0, 0)
    const end   = new Date(now); end.setHours(18, 0, 0, 0)
    return { lo: start.getTime(), hi: end.getTime() }
  }
  // Pad 2 min on each side so endpoints are reachable
  return { lo: lo - 2 * 60_000, hi: hi + 2 * 60_000 }
})

// Manual start / end — overrides auto-detect when set
const startMs = ref(null)   // null → use detectedRange.lo
const endMs   = ref(null)   // null → use detectedRange.hi

const rangeMin = computed(() => startMs.value ?? detectedRange.value.lo)
const rangeMax = computed(() => endMs.value   ?? detectedRange.value.hi)

// Seed input values when data first loads (only if not manually set)
watch(detectedRange, (r) => {
  if (startMs.value === null) startInputVal.value = toInputValue(r.lo)
  if (endMs.value   === null) endInputVal.value   = toInputValue(r.hi)
}, { immediate: true })

// The string values bound to <input type="datetime-local"> elements
const startInputVal = ref('')
const endInputVal   = ref('')

function onStartChange(e) {
  const ms = fromInputValue(e.target.value)
  startMs.value = ms
  startInputVal.value = e.target.value
  if (current.value < rangeMin.value) current.value = rangeMin.value
  pushTimeline()
}

function onEndChange(e) {
  const ms = fromInputValue(e.target.value)
  endMs.value = ms
  endInputVal.value = e.target.value
  if (current.value > rangeMax.value) current.value = rangeMax.value
  pushTimeline()
}

// ---- Playback head (epoch ms) ----
const current = ref(0)
watch(rangeMin, (v) => { if (!current.value || current.value < v) current.value = v }, { immediate: true })
watch(rangeMax, (v) => { if (current.value > v) current.value = v })

// Window size in ms (default 30 min)
const windowMs = ref(30 * 60_000)
const windowStart = computed(() => Math.max(rangeMin.value, current.value - windowMs.value))

const fillPct = computed(() => {
  const span = rangeMax.value - rangeMin.value
  if (span <= 0) return 0
  return Math.min(100, Math.max(0, ((current.value - rangeMin.value) / span) * 100))
})

// Window size presets (ms)
const WIN_PRESETS = [
  { label: '5m',  ms: 5 * 60_000 },
  { label: '15m', ms: 15 * 60_000 },
  { label: '30m', ms: 30 * 60_000 },
  { label: '1h',  ms: 60 * 60_000 },
  { label: '2h',  ms: 120 * 60_000 },
  { label: 'All', ms: 0 },  // 0 = no window limit (show all past)
]

function setWindow(preset) {
  windowMs.value = preset.ms === 0 ? (rangeMax.value - rangeMin.value + 1) : preset.ms
  pushTimeline()
}

const activeWindowLabel = computed(() => {
  const match = WIN_PRESETS.find((p) => p.ms !== 0 && Math.abs(windowMs.value - p.ms) < 60_000)
  return match ? match.label : 'Custom'
})

// ---- Playback ----
const playing = ref(false)
let playTimer = null

function step() {
  const span = rangeMax.value - rangeMin.value
  // ~120 steps to traverse the full range at 150 ms/tick
  const stepMs = Math.max(30_000, span / 120)
  current.value = current.value + stepMs
  if (current.value > rangeMax.value) current.value = rangeMin.value
  pushTimeline()
}

function startPlay() { playing.value = true; playTimer = setInterval(step, 150) }
function stopPlay()  { playing.value = false; clearInterval(playTimer); playTimer = null }
function togglePlay() { playing.value ? stopPlay() : startPlay() }

function reset() {
  stopPlay()
  current.value = rangeMin.value
  pushTimeline()
}

// ---- Sync filter to layers store ----
function pushTimeline() {
  layerStore.setTimeline(true, windowStart.value, current.value, '_time')
  updateTrailLayers()
}

// ---- Trail + dwell-density rendering via MapLibre ----
function getMap() { return window.expeditionMap?.getMap?.() || null }

function collectTracks() {
  const tracks = new Map()
  for (const layer of layerStore.visibleLayers) {
    const fc = layerStore.features[layer.name]
    if (!fc?.features) continue
    const color = layerStore.getLayerStyle(layer.name)?.color || layer.color || '#3B82F6'
    for (const f of fc.features) {
      const props = f?.properties || {}
      const t = parseTs(props._time)
      if (t === null) continue
      const geom = f.geometry
      if (geom?.type !== 'Point' || !Array.isArray(geom.coordinates)) continue
      const [lng, lat] = geom.coordinates
      if (typeof lng !== 'number' || typeof lat !== 'number') continue
      const key = String(props._track_id ?? layer.name)
      if (!tracks.has(key)) tracks.set(key, { points: [], color })
      tracks.get(key).points.push({ lng, lat, t })
    }
  }
  for (const tr of tracks.values()) tr.points.sort((a, b) => a.t - b.t)
  return tracks
}

function buildTrailGeoJSON(tracks) {
  const lineFeats = [], dwellFeats = []
  for (const [trackId, tr] of tracks) {
    const past = tr.points.filter((p) => p.t <= current.value)
    if (past.length < 2) continue
    lineFeats.push({
      type: 'Feature',
      properties: { color: tr.color },
      geometry: { type: 'LineString', coordinates: past.map((p) => [p.lng, p.lat]) },
    })
    for (let i = 0; i < past.length; i++) {
      const p = past[i], nx = past[i + 1]
      // Dwell in minutes, capped at 60 min
      const dwellMin = nx ? Math.min(60, Math.max(1, (nx.t - p.t) / 60_000)) : 2
      dwellFeats.push({
        type: 'Feature',
        id: trackId + '_' + i,
        properties: { color: tr.color, _dwell: dwellMin },
        geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
      })
    }
  }
  return {
    lines:  { type: 'FeatureCollection', features: lineFeats },
    dwells: { type: 'FeatureCollection', features: dwellFeats },
  }
}

function updateTrailLayers() {
  const map = getMap()
  if (!map?.isStyleLoaded()) return
  const { lines, dwells } = buildTrailGeoJSON(collectTracks())

  // Upsert sources
  const ls = map.getSource(TRAIL_LINE_SOURCE)
  const ds = map.getSource(TRAIL_DWELL_SOURCE)
  if (ls) ls.setData(lines); else map.addSource(TRAIL_LINE_SOURCE,  { type: 'geojson', data: lines })
  if (ds) ds.setData(dwells); else map.addSource(TRAIL_DWELL_SOURCE, { type: 'geojson', data: dwells })

  // Add MapLibre layers once
  if (!map.getLayer(TRAIL_LINE_LAYER)) {
    map.addLayer({
      id: TRAIL_LINE_LAYER, type: 'line', source: TRAIL_LINE_SOURCE,
      paint: {
        'line-color': ['coalesce', ['get', 'color'], '#3B82F6'],
        'line-width': 2.5,
        'line-opacity': 0.72,
      },
      layout: { 'line-join': 'round', 'line-cap': 'round' },
    })
  }
  // White halo ring behind dwell circles (legibility on dark/satellite maps)
  if (!map.getLayer(TRAIL_DWELL_LAYER_RING)) {
    map.addLayer({
      id: TRAIL_DWELL_LAYER_RING, type: 'circle', source: TRAIL_DWELL_SOURCE,
      paint: {
        'circle-radius': ['+', 5, ['*', ['/', ['min', ['get', '_dwell'], 60], 60], 10]],
        'circle-color': '#FFFFFF',
        'circle-opacity': 0.28,
      },
    })
  }
  // Coloured dwell circles — radius encodes dwell time (larger = stayed longer)
  if (!map.getLayer(TRAIL_DWELL_LAYER)) {
    map.addLayer({
      id: TRAIL_DWELL_LAYER, type: 'circle', source: TRAIL_DWELL_SOURCE,
      paint: {
        'circle-radius': ['+', 4, ['*', ['/', ['min', ['get', '_dwell'], 60], 60], 8]],
        'circle-color': ['coalesce', ['get', 'color'], '#3B82F6'],
        'circle-opacity': 0.55,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#FFFFFF',
        'circle-stroke-opacity': 0.72,
      },
    })
  }
}

function removeTrailLayers() {
  const map = getMap()
  if (!map) return
  for (const id of [TRAIL_DWELL_LAYER, TRAIL_DWELL_LAYER_RING, TRAIL_LINE_LAYER]) {
    try { if (map.getLayer(id)) map.removeLayer(id) } catch (_) {}
  }
  for (const id of [TRAIL_LINE_SOURCE, TRAIL_DWELL_SOURCE]) {
    try { if (map.getSource(id)) map.removeSource(id) } catch (_) {}
  }
}

function close() {
  stopPlay()
  layerStore.setTimeline(false, 0, 0, '_time')
  removeTrailLayers()
  emit('close')
}

onMounted(() => {
  current.value = rangeMin.value
  pushTimeline()
})

onBeforeUnmount(() => {
  stopPlay()
  layerStore.setTimeline(false, 0, 0, '_time')
  removeTrailLayers()
})
</script>

<template>
  <div class="tls" role="region" aria-label="Timeline playback">

    <!-- Date+time range row -->
    <div class="tls__range-row">
      <div class="tls__dt-field">
        <label class="tls__lbl" for="tls-start">From</label>
        <input
          id="tls-start"
          class="tls__dt-input"
          type="datetime-local"
          :value="startInputVal"
          @change="onStartChange"
          aria-label="Playback start date and time"
        />
      </div>

      <!-- Scrubber track -->
      <div class="tls__track-wrap">
        <div class="tls__track">
          <div class="tls__fill" :style="{ width: fillPct + '%' }" />
        </div>
        <input
          class="tls__scrubber"
          type="range"
          :min="rangeMin"
          :max="rangeMax"
          step="60000"
          :value="current"
          @input="(e) => { current = parseFloat(e.target.value); pushTimeline() }"
          aria-label="Timeline position"
        />
      </div>

      <div class="tls__dt-field tls__dt-field--end">
        <label class="tls__lbl" for="tls-end">To</label>
        <input
          id="tls-end"
          class="tls__dt-input"
          type="datetime-local"
          :value="endInputVal"
          @change="onEndChange"
          aria-label="Playback end date and time"
        />
      </div>
    </div>

    <!-- Current window display -->
    <div class="tls__status-row">
      <span class="tls__status-val">
        {{ fmtTs(windowStart, rangeMin) }}
        <span class="tls__arrow">→</span>
        {{ fmtTs(current, rangeMin) }}
      </span>
      <span class="tls__window-presets">
        <button
          v-for="p in WIN_PRESETS"
          :key="p.label"
          type="button"
          class="tls__preset"
          :class="{ 'tls__preset--active': p.label === activeWindowLabel }"
          :title="'Window: ' + p.label"
          @click="setWindow(p)"
        >{{ p.label }}</button>
      </span>
    </div>

    <!-- Controls -->
    <div class="tls__controls">
      <button type="button" class="tls__btn" title="Reset to start" @click="reset">
        <svg viewBox="0 0 24 24" class="tls__icon" aria-hidden="true">
          <path d="M4 12V8m0 4 3-3m-3 3 3 3M20 12a8 8 0 1 1-8-8"
                fill="none" stroke="currentColor" stroke-width="1.6"
                stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <button
        type="button"
        class="tls__btn tls__btn--play"
        :class="{ 'tls__btn--active': playing }"
        :title="playing ? 'Pause' : 'Play'"
        @click="togglePlay"
      >
        <svg v-if="!playing" viewBox="0 0 24 24" class="tls__icon" aria-hidden="true">
          <path d="M5 3l14 9-14 9V3z" fill="currentColor" stroke="none"/>
        </svg>
        <svg v-else viewBox="0 0 24 24" class="tls__icon" aria-hidden="true">
          <path d="M6 4h4v16H6zM14 4h4v16h-4z" fill="currentColor" stroke="none"/>
        </svg>
      </button>

      <button type="button" class="tls__btn tls__btn--close" title="Close timeline" @click="close">
        <svg viewBox="0 0 24 24" class="tls__icon" aria-hidden="true">
          <path d="M18 6 6 18M6 6l12 12" fill="none" stroke="currentColor"
                stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

  </div>
</template>

<style scoped>
.tls {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px 10px;
  background: rgba(11, 14, 20, 0.86);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 14px;
  box-shadow: 0 6px 28px rgba(0, 0, 0, 0.40);
  min-width: 460px;
  max-width: min(680px, 94vw);
  pointer-events: auto;
  color: rgba(230, 232, 236, 0.92);
  font-family: inherit;
  user-select: none;
}

/* ---- [From datetime] [scrubber] [To datetime] ---- */
.tls__range-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tls__dt-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: none;
}
.tls__dt-field--end { align-items: flex-end; }

.tls__lbl {
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: rgba(230, 232, 236, 0.38);
}

.tls__dt-input {
  appearance: none;
  -webkit-appearance: none;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  color: rgba(230, 232, 236, 0.90);
  font-family: inherit;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  padding: 4px 6px;
  width: 148px;
  outline: none;
  cursor: text;
  transition: border-color 100ms ease;
}
.tls__dt-input:focus { border-color: rgba(59, 130, 246, 0.60); }
.tls__dt-input::-webkit-calendar-picker-indicator {
  filter: invert(0.55);
  cursor: pointer;
  padding: 0;
  margin-left: 2px;
}

/* ---- Scrubber ---- */
.tls__track-wrap {
  position: relative;
  flex: 1;
  height: 20px;
  display: flex;
  align-items: center;
  min-width: 0;
}
.tls__track {
  position: absolute;
  left: 0; right: 0;
  height: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.10);
  overflow: hidden;
  pointer-events: none;
}
.tls__fill {
  height: 100%;
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.55), #3B82F6);
  border-radius: 999px;
  transition: width 80ms linear;
}
.tls__scrubber {
  position: absolute;
  left: 0; right: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  margin: 0;
}

/* ---- Status / window row ---- */
.tls__status-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.tls__status-val {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #93C5FD;
  flex: 1;
  min-width: 0;
}
.tls__arrow {
  font-weight: 400;
  color: rgba(147, 197, 253, 0.5);
  margin: 0 2px;
}

/* Window presets */
.tls__window-presets {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: none;
}
.tls__preset {
  padding: 2px 7px;
  border-radius: 5px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: transparent;
  color: rgba(230, 232, 236, 0.48);
  font-family: inherit;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: background 80ms ease, color 80ms ease;
}
.tls__preset:hover { background: rgba(255,255,255,0.07); color: rgba(230,232,236,0.80); }
.tls__preset--active {
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.35);
  color: #93C5FD;
}

/* ---- Controls ---- */
.tls__controls {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tls__btn {
  width: 30px; height: 30px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  color: rgba(230, 232, 236, 0.80);
  cursor: pointer;
  flex: none;
  transition: background 100ms ease, color 100ms ease;
}
.tls__btn:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }
.tls__btn--play {
  width: 34px; height: 34px;
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.30);
  color: #93C5FD;
}
.tls__btn--play:hover { background: rgba(59, 130, 246, 0.30); color: #fff; }
.tls__btn--active { background: rgba(59, 130, 246, 0.28); color: #DBEAFE; }
.tls__btn--close {
  margin-left: auto;
  background: transparent; border-color: transparent;
  color: rgba(230, 232, 236, 0.38);
}
.tls__btn--close:hover { background: rgba(255,255,255,0.07); color: rgba(230,232,236,0.80); }
.tls__icon { width: 14px; height: 14px; flex: none; }
.tls__btn--play .tls__icon { width: 12px; height: 12px; }
</style>
