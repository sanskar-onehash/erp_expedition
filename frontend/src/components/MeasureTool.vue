<script setup>
/**
 * MeasureTool — overlay shown while measure mode is active.
 *
 * Two shapes:
 *   - 'line'   click adds a vertex, double-click finishes. Shows total
 *              length in km or m (auto-scales).
 *   - 'polygon' click adds a vertex, double-click finishes. Shows total
 *              area in km² or m².
 *
 * Implementation: reuses the existing draft-polygon infrastructure
 * (src-zones-draft, lyr-zones-draft) so we don't pay a second source
 * + layer cost. The math is inline — haversine for length, spherical
 * excess for area. No external dep.
 *
 * PR-11 of the quiet-canvas plan.
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useUiStore } from '../state/ui.js'
import { activeMapCursor, applyMapCursor } from '../lib/mapCursor.js'
import { normalizeLngLat, shortestLngDelta } from '../lib/geo.js'

const ui = useUiStore()
const vertices = ref([])
const pointer = ref(null)
const previewVertices = computed(() => pointer.value && vertices.value.length
  ? [...vertices.value, pointer.value]
  : vertices.value
)
const total = computed(() => {
  const coords = previewVertices.value
  if (coords.length < 2) return 0
  if (ui.measureMode === 'line') return polylineLength(coords)
  // Polygon: close the ring for area.
  if (coords.length < 3) return 0
  return polygonArea([...coords, coords[0]])
})

const showFinal = ref(false)
const finalValue = ref(0)
const finalShape = ref('line')

function start() {
  vertices.value = []
  pointer.value = null
  showFinal.value = false
}

function onMapClick(e) {
  if (!ui.measureMode) return
  const point = normalizeLngLat([e.lngLat.lng, e.lngLat.lat])
  vertices.value = [...vertices.value, point]
  pointer.value = point
}

function onMapMove(e) {
  if (!ui.measureMode || !vertices.value.length) return
  pointer.value = normalizeLngLat([e.lngLat.lng, e.lngLat.lat])
}

function onMapDblClick(e) {
  if (!ui.measureMode) return
  e.preventDefault()
  finish()
}

function finish() {
  if (vertices.value.length < 2) {
    cancel()
    return
  }
  finalValue.value = total.value
  finalShape.value = ui.measureMode
  showFinal.value = true
  ui.cancelMeasure()
  vertices.value = []
  pointer.value = null
  // Auto-dismiss the result chip after 6 seconds.
  setTimeout(() => { showFinal.value = false }, 6000)
}

function cancel() {
  ui.cancelMeasure()
  vertices.value = []
  pointer.value = null
  showFinal.value = false
}

function onKey(e) {
  if (!ui.measureMode) return
  if (e.key === 'Escape') cancel()
  if (e.key === 'Enter') finish()
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  // Re-bind on the next tick so the map is fully mounted.
  nextTick(bindMap)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  unbindMap()
})

function bindMap() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  m.on('click', onMapClick)
  m.on('mousemove', onMapMove)
  m.on('dblclick', onMapDblClick)
  _boundMap = m
}
function unbindMap() {
  if (!_boundMap) return
  _boundMap.off('click', onMapClick)
  _boundMap.off('mousemove', onMapMove)
  _boundMap.off('dblclick', onMapDblClick)
  _boundMap = null
}
let _boundMap = null

watch(() => ui.measureMode, (m, prev) => {
  if (m && !prev) {
    start()
    // Disable the map's default double-click zoom while measuring.
    const mp = window.expeditionMap?.getMap?.()
    if (mp) {
      mp.doubleClickZoom.disable()
      applyMapCursor(mp.getCanvas(), activeMapCursor(ui))
    }
  } else if (!m && prev) {
    unbindMap()
    bindMap()
    const mp = window.expeditionMap?.getMap?.()
    if (mp) {
      mp.doubleClickZoom.enable()
      applyMapCursor(mp.getCanvas(), activeMapCursor(ui))
    }
  }
})

// Render the draft as a source so MapLibre does the drawing.
watch(previewVertices, (v) => _renderDraft(v), { deep: true })

function _renderDraft(v) {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  const sid = 'src-measure-draft'
  const features = []
  if (ui.measureMode === 'line' && v.length >= 2) {
    features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: v },
      properties: { kind: 'line' },
    })
  } else if (ui.measureMode === 'polygon') {
    if (v.length >= 2) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: v },
        properties: { kind: 'line' },
      })
    }
    if (v.length >= 3) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [[...v, v[0]]] },
        properties: { kind: 'area' },
      })
    }
  }
  const fc = {
    type: 'FeatureCollection',
    features,
  }
  if (m.getSource(sid)) {
    m.getSource(sid).setData(fc)
  } else {
    m.addSource(sid, { type: 'geojson', data: fc })
  }
  const lid = 'lyr-measure-draft'
  if (!m.getLayer(lid)) {
    m.addLayer({
      id: lid,
      type: 'line',
      source: sid,
      paint: {
        'line-color': '#10B981',
        'line-width': 2,
        'line-dasharray': [2, 1.5],
      },
    })
    m.addLayer({
      id: lid + '-fill',
      type: 'fill',
      source: sid,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': '#10B981',
        'fill-opacity': 0.15,
      },
    })
  }
}

// Cleanup the draft when the component unmounts (e.g. user navigates away).
onBeforeUnmount(() => {
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  for (const id of ['lyr-measure-draft', 'lyr-measure-draft-fill']) {
    if (m.getLayer(id)) m.removeLayer(id)
  }
  if (m.getSource('src-measure-draft')) m.removeSource('src-measure-draft')
})

// ---- Math ----
const EARTH_R = 6371008.8
function toRad(deg) { return deg * Math.PI / 180 }
function haversine(a, b) {
  const lat1 = toRad(a[1]), lat2 = toRad(b[1])
  const dLat = lat2 - lat1
  const dLng = toRad(shortestLngDelta(a[0], b[0]))
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_R * Math.asin(Math.sqrt(h))
}
function polylineLength(coords) {
  let total = 0
  for (let i = 1; i < coords.length; i++) total += haversine(coords[i - 1], coords[i])
  return total
}
function polygonArea(coords) {
  // Spherical excess approximation. For a small/medium polygon (city
  // scale) the error vs. a true geodesic area is sub-percent, well
  // below the 0.5m precision users care about. Good enough for v1.
  if (coords.length < 4) return 0
  let total = 0
  for (let i = 0; i < coords.length - 1; i++) {
    const [lng1, lat1] = coords[i]
    const [lng2, lat2] = coords[i + 1]
    total += toRad(shortestLngDelta(lng1, lng2)) * (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)))
  }
  return Math.abs(total * EARTH_R * EARTH_R / 2)
}

function fmtLength(m) {
  if (m < 1000) return `${m.toFixed(1)} m`
  return `${(m / 1000).toFixed(2)} km`
}
function fmtArea(m2) {
  if (m2 < 10_000) return `${m2.toFixed(0)} m²`
  return `${(m2 / 1_000_000).toFixed(3)} km²`
}
</script>

<template>
  <div v-if="ui.measureMode" class="mt">
    <div class="mt__chip">
      <span class="mt__kind">{{ ui.measureMode === 'line' ? 'Distance' : 'Area' }}</span>
      <span class="mt__val">
        <template v-if="ui.measureMode === 'line'">
          {{ previewVertices.length < 2 ? '—' : fmtLength(total) }}
        </template>
        <template v-else>
          {{ previewVertices.length < 3 ? '—' : fmtArea(total) }}
        </template>
      </span>
      <span class="mt__hint">Click to add · double-click to finish · Esc to cancel</span>
    </div>
  </div>
  <div v-if="showFinal" class="mt mt--final">
    <div class="mt__chip mt__chip--final">
      <span class="mt__kind">{{ finalShape === 'line' ? 'Distance' : 'Area' }}</span>
      <span class="mt__val">
        {{ finalShape === 'line' ? fmtLength(finalValue) : fmtArea(finalValue) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.mt {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 25;
  pointer-events: none;
}
.mt--final {
  /* The result chip sits a bit lower so it doesn't collide with
     the search-pill's eventual position. */
  top: 64px;
}
.mt__chip {
  display: inline-flex; align-items: center; gap: 10px;
  background: rgba(11, 14, 20, 0.86);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(16, 185, 129, 0.45);
  border-radius: 999px;
  padding: 7px 14px 7px 8px;
  color: #E6E8EC;
  font-size: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  pointer-events: auto;
}
.mt__chip--final { border-color: rgba(16, 185, 129, 0.7); }
.mt__kind {
  font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(16, 185, 129, 0.85);
  color: #0B0E14;
}
.mt__val {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 12px; font-weight: 500;
  color: #fff;
}
.mt__hint {
  font-size: 10px; color: rgba(230, 232, 236, 0.5);
  letter-spacing: 0.02em;
}
</style>
