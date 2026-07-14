<script setup>
/**
 * CoordReadout — bottom-left cursor tracker.
 *
 * Shows the live `lat, lng` under the mouse plus the current zoom.
 * Updates on mousemove (throttled to rAF), zoom, and pitch. Click the
 * pill to copy the current coords to the clipboard. A small "format"
 * cycle button toggles between DMS / decimal / decimal-minutes.
 *
 * PR-10 of the quiet-canvas plan.
 */
import { onMounted, onBeforeUnmount, ref, computed, watch } from 'vue'
import { useUiStore } from '../state/ui.js'
import { wrapLng } from '../lib/geo.js'

const ui = useUiStore()
const lat = ref(null)
const lng = ref(null)
const zoom = ref(null)
const format = ref(ui.prefs.coordUnits || 'decimal') // 'decimal' | 'dms' | 'dm'

watch(() => ui.prefs.coordUnits, (val) => {
  if (val) format.value = val
})
const copied = ref(false)
let rootEl = null

// rAF throttle so we don't re-render on every mousemove.
let pending = null
function onMove(e) {
  if (pending) return
  pending = requestAnimationFrame(() => {
    pending = null
    const m = window.expeditionMap?.getMap?.()
    if (!m) return
    // Track the cursor as long as it's anywhere over the page root —
    // including translucent panels, toolbars, popups, and the readout
    // pill itself, which all sit *on top of* the map (siblings of
    // `.expedition__basemap`, not children). We unproject from the
    // canvas underneath the pointer using elementsFromPoint, so the
    // readout shows the lat/lng that *would be* under the cursor on
    // the map even when an overlay is in the way. The map's
    // pixel space differs from client space only if the canvas has
    // non-zero CSS transforms, which MapLibre does not apply.
    const stack = document.elementsFromPoint(e.clientX, e.clientY)
    const canvas = stack.find(el => el.classList?.contains('maplibregl-canvas'))
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const ll = m.unproject([e.clientX - rect.left, e.clientY - rect.top])
    lat.value = ll.lat
    lng.value = wrapLng(ll.lng)
    zoom.value = m.getZoom()
  })
}

function onLeave() {
  lat.value = null
  lng.value = null
  zoom.value = null
}

onMounted(() => {
  // Bind to the full-bleed Expedition root (`.expedition`) rather than
  // the canvas or map container. Floating panels, corner toolbars, and
  // the readout pill itself are siblings of the basemap, so the cursor
  // leaves `.maplibregl-map` the moment it touches a button. mouseleave
  // on `.expedition` only fires when the cursor exits the whole
  // viewport, which is the only time the readout should blank out.
  requestAnimationFrame(() => {
    rootEl = document.querySelector('.expedition')
    if (!rootEl) return
    rootEl.addEventListener('mousemove', onMove)
    rootEl.addEventListener('mouseleave', onLeave)
  })
})
onBeforeUnmount(() => {
  if (rootEl) {
    rootEl.removeEventListener('mousemove', onMove)
    rootEl.removeEventListener('mouseleave', onLeave)
  }
})

function fmt() {
  if (lat.value == null || lng.value == null) return ''
  if (format.value === 'dms') return `${toDMS(lat.value, 'lat')}  ${toDMS(lng.value, 'lng')}`
  if (format.value === 'dm') return `${toDM(lat.value, 'lat')}  ${toDM(lng.value, 'lng')}`
  return `${lat.value.toFixed(5)}°, ${lng.value.toFixed(5)}°`
}

function toDMS(deg, axis) {
  const abs = Math.abs(deg)
  const d = Math.floor(abs)
  const mFloat = (abs - d) * 60
  const m = Math.floor(mFloat)
  const s = ((mFloat - m) * 60).toFixed(1)
  const hemi = axis === 'lat' ? (deg >= 0 ? 'N' : 'S') : (deg >= 0 ? 'E' : 'W')
  return `${d}° ${m}' ${s}" ${hemi}`
}
function toDM(deg, axis) {
  const abs = Math.abs(deg)
  const d = Math.floor(abs)
  const m = ((abs - d) * 60).toFixed(3)
  const hemi = axis === 'lat' ? (deg >= 0 ? 'N' : 'S') : (deg >= 0 ? 'E' : 'W')
  return `${d}° ${m}' ${hemi}`
}

async function copy() {
  if (lat.value == null || lng.value == null) return
  const text = `${lat.value.toFixed(6)}, ${lng.value.toFixed(6)}`
  const fallbackCopy = () => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
    } finally {
      document.body.removeChild(ta)
    }
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text)
      } catch (_) {
        fallbackCopy()
      }
    } else {
      fallbackCopy()
    }
    copied.value = true
    setTimeout(() => { copied.value = false }, 1200)
  } catch (e) {
    console.warn('[expedition] coordinate copy failed', e)
  }
}

function cycleFormat() {
  const next = { decimal: 'dms', dms: 'dm', dm: 'decimal' }
  const nextVal = next[format.value] || 'decimal'
  format.value = nextVal
  ui.setPref('coordUnits', nextVal)
}

const hasValue = computed(() => lat.value != null && lng.value != null)
</script>

<template>
  <div class="cr">
    <span class="cr__zoom" :title="'Zoom level'">z {{ zoom != null ? zoom.toFixed(1) : '–' }}</span>
    <span class="cr__sep">·</span>
    <button class="cr__coords" type="button" @click="copy" :title="'Click to copy ' + (hasValue ? `${lat.toFixed(5)}, ${lng.toFixed(5)}` : '')">
      <span v-if="hasValue">{{ fmt() }}</span>
      <span v-else class="cr__placeholder">— hover map —</span>
    </button>
    <button class="cr__format" type="button" @click="cycleFormat" :title="'Cycle format (current: ' + format + ')'" :aria-label="'Format: ' + format">
      {{ format === 'dms' ? 'DMS' : format === 'dm' ? 'DM' : 'DEC' }}
    </button>
    <span v-if="copied" class="cr__copied" aria-live="polite">copied</span>
  </div>
</template>

<style scoped>
.cr {
  display: inline-flex; align-items: center; gap: 6px;
  /* Background is driven by the parent's --cr-bg-opacity custom property
     so the wrapper (in App.vue) can dim it when chrome-hidden mode
     is on without duplicating the whole backdrop stack here. */
  background: rgba(11, 14, 20, var(--cr-bg-opacity, 0.72));
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 5px 6px 5px 12px;
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  /* Text opacity also driven by wrapper so dimming is coordinated. */
  color: rgba(230, 232, 236, var(--cr-text-opacity, 0.9));
  font-variant-numeric: tabular-nums;
  pointer-events: auto;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.32);
}
.cr__zoom { color: rgba(230, 232, 236, 0.6); }
.cr__sep { color: rgba(230, 232, 236, 0.3); }
.cr__coords {
  background: transparent; border: 0;
  color: inherit;
  font-family: inherit; font-size: 10px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 999px;
  letter-spacing: 0.02em;
  /* Pin a stable min-width so the panel doesn't pulse as the
     placeholder ("— hover map —", ~95px) and the populated DMS
     string (~180px) swap places. The DEC button sits flush on
     the right and stays clickable at all times. */
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cr__coords:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.cr__placeholder { color: rgba(230, 232, 236, 0.4); font-style: italic; }
.cr__format {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 999px;
  color: rgba(230, 232, 236, 0.85);
  font-family: inherit; font-size: 9px; font-weight: 600;
  padding: 2px 6px; cursor: pointer;
  letter-spacing: 0.04em;
}
.cr__format:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }
.cr__copied {
  color: #10B981;
  font-size: 9px; font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-left: 2px;
}
</style>
