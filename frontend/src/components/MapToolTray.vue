<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { useZonesStore } from '../state/zones.js'

const ui = useUiStore()
const mapStore = useMapStore()
const zoneStore = useZonesStore()
const tray = ref(null)
const styleOpen = ref(false)
const editOpen = ref(false)
const tiltOpen = ref(false)
const tiltPad = ref(null)
const tiltDrag = ref(false)
const tiltBearing = ref(0)
const tiltPuck = ref({ x: 0, y: 0 })
const tiltGesture = ref(null)
const suppressEditAutoOpen = ref(false)

const tools = computed(() => [
  { id: 'select', label: 'Select / pan', glyph: 'M5 4l10 16 2-7 6-2L5 4z' },
  { id: 'polygon', label: 'Draw polygon zone', glyph: 'M6 5l11 2 3 9-8 5-8-6 2-10z M6 5h0 M17 7h0 M20 16h0 M12 21h0 M4 15h0' },
  { id: 'circle', label: 'Draw circle zone', glyph: 'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z M12 12h.01' },
  { id: 'rectangle', label: 'Draw rectangle zone', glyph: 'M5 6h14v12H5z' },
  { id: 'measure-line', label: 'Measure distance', glyph: 'M4 19L19 4 M7 16l1 1 M10 13l1 1 M13 10l1 1 M16 7l1 1' },
  { id: 'measure-area', label: 'Measure area', glyph: 'M6 7l9-3 5 8-4 8-10-2-2-8z' },
])
const presets = ['#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6', '#06B6D4', '#F97316', '#84CC16', '#EF4444', '#6366F1', '#14B8A6', '#A855F7']
const selectedZone = computed(() => ui.selectedZone)
const edit = ref({})

watch(selectedZone, (zone) => {
  if (suppressEditAutoOpen.value) {
    suppressEditAutoOpen.value = false
  } else {
    editOpen.value = !!zone
  }
  edit.value = zone ? {
    title: zone.title || '',
    color: zone.color || '#3B82F6',
    fill_opacity: zone.fill_opacity ?? 0.22,
    stroke_color: zone.stroke_color || '#1E40AF',
    stroke_width: zone.stroke_width ?? 2,
    stroke_style: zone.stroke_style || 'solid',
    tag: zone.tag || '',
  } : {}
}, { immediate: true })

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown, true)
  document.addEventListener('keydown', onDocumentKeyDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown, true)
  document.removeEventListener('keydown', onDocumentKeyDown)
})

function closeEditPanel() {
  if (!editOpen.value && !ui.selectedZone) return
  editOpen.value = false
  suppressEditAutoOpen.value = true
  ui.clearSelectedZone()
}

function closePopovers({ clearZone = true } = {}) {
  styleOpen.value = false
  tiltOpen.value = false
  if (clearZone) closeEditPanel()
  else editOpen.value = false
}

function onDocumentPointerDown(event) {
  if (tray.value?.contains?.(event.target)) return
  closePopovers({ clearZone: true })
}

function onDocumentKeyDown(event) {
  if (event.key === 'Escape') closePopovers({ clearZone: true })
}

function active(id) {
  if (id === 'select') return ui.drawMode === 'off' && !ui.measureMode
  if (id === 'measure-line') return ui.measureMode === 'line'
  if (id === 'measure-area') return ui.measureMode === 'polygon'
  return ui.drawMode === id
}

function trigger(id) {
  if (id === 'select') {
    ui.cancelDraw()
    ui.cancelMeasure()
    return
  }
  if (id === 'measure-line') {
    ui.cancelDraw()
    ui.startMeasure('line')
    return
  }
  if (id === 'measure-area') {
    ui.cancelDraw()
    ui.startMeasure('polygon')
    return
  }
  ui.cancelMeasure()
  ui.startDrawTool(id)
}

function toggleZoneEditMode() {
  const next = !ui.zoneEditMode
  ui.setZoneEditMode(next)
  if (!next) editOpen.value = false
}

function finishDrawing() {
  window.expeditionMap?.finishDrawing?.()
}

function clearDrawing() {
  ui.cancelDraw()
  ui.cancelMeasure()
}

async function saveSelectedZone() {
  const zone = selectedZone.value
  const mapName = mapStore.activeMap?.map?.name
  if (!zone?.name || !mapName) return
  closeEditPanel()
  const updated = await zoneStore.updateZone(mapName, zone.name, edit.value)
}

async function deleteSelectedZone() {
  const zone = selectedZone.value
  const mapName = mapStore.activeMap?.map?.name
  if (!zone?.name || !mapName) return
  closeEditPanel()
  await zoneStore.deleteZone(mapName, zone.name)
}

function applyPreset(color) {
  ui.setDrawingColor(color)
  styleOpen.value = false
}

function nativeColorValue(value, fallback = '#3B82F6') {
  return /^#[0-9a-f]{6}$/i.test(String(value || '')) ? value : fallback
}

function resetTilt() {
  const m = window.expeditionMap?.getMap?.()
  window.removeEventListener('pointermove', updateTiltFromPointer)
  tiltDrag.value = false
  tiltBearing.value = 0
  tiltPuck.value = { x: 0, y: 0 }
  tiltGesture.value = null
  ui.setPitch(0)
  if (m) {
    m.stop?.()
    m.jumpTo({ pitch: 0, bearing: 0 })
  }
}

function updateTiltFromPointer(event) {
  const m = window.expeditionMap?.getMap?.()
  const gesture = tiltGesture.value
  if (!gesture || !m) return
  const dx = event.clientX - gesture.startX
  const dy = event.clientY - gesture.startY
  const radius = 34
  const dampedX = Math.max(-radius, Math.min(radius, dx * 0.55))
  const dampedY = Math.max(-radius, Math.min(radius, dy * 0.55))
  const axis = ui.prefs.tiltJoystickInverted === false ? 1 : -1
  const pitch = Math.max(0, Math.min(75, Math.round(gesture.startPitch - dy * 0.45 * axis)))
  const bearing = Math.round(gesture.startBearing + dx * 0.55 * axis)
  tiltPuck.value = { x: dampedX, y: dampedY }
  tiltBearing.value = bearing
  ui.setPitch(pitch)
  m.easeTo({ pitch, bearing, duration: 120 })
}

function startTilt(event) {
  if (event.detail > 1) return
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  tiltDrag.value = true
  tiltGesture.value = {
    startX: event.clientX,
    startY: event.clientY,
    startPitch: m.getPitch?.() ?? ui.pitchDegrees ?? 0,
    startBearing: m.getBearing?.() ?? tiltBearing.value,
  }
  tiltPuck.value = { x: 0, y: 0 }
  window.addEventListener('pointermove', updateTiltFromPointer)
  window.addEventListener('pointerup', stopTilt, { once: true })
}

function stopTilt() {
  tiltDrag.value = false
  tiltGesture.value = null
  tiltPuck.value = { x: 0, y: 0 }
  window.removeEventListener('pointermove', updateTiltFromPointer)
}

const puckStyle = computed(() => {
  return {
    left: '50%',
    top: '50%',
    transform: `translate(${tiltPuck.value.x}px, ${tiltPuck.value.y}px)`,
  }
})

function setEditColor(field, color) {
  edit.value = { ...edit.value, [field]: color }
}

function commitDrawingColor(event) {
  ui.setDrawingColor(event.target.value)
  styleOpen.value = false
}
</script>

<template>
  <div ref="tray" class="mtt chrome-hideable">
    <div class="mtt__group" role="toolbar" aria-label="Map drawing tools">
      <button
        v-for="tool in tools"
        :key="tool.id"
        type="button"
        class="mtt__btn"
        :class="{ 'mtt__btn--active': active(tool.id) }"
        :title="tool.label"
        :aria-label="tool.label"
        @click="trigger(tool.id)"
      >
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path :d="tool.glyph" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>

    <div class="mtt__group">
      <button type="button" class="mtt__btn" title="Drawing color" aria-label="Drawing color" @click="styleOpen = !styleOpen">
        <span class="mtt__swatch" :style="{ background: ui.drawingColor }" />
      </button>
      <button
        type="button"
        class="mtt__btn"
        :class="{ 'mtt__btn--active': ui.zoneEditMode }"
        title="Zone edit mode"
        aria-label="Zone edit mode"
        :aria-pressed="ui.zoneEditMode ? 'true' : 'false'"
        @click="toggleZoneEditMode"
      >
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path d="M5 6h14v12H5z M8 9h8v6H8z M4 4l3 3 M20 4l-3 3 M4 20l3-3 M20 20l-3-3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <button type="button" class="mtt__btn" title="Tilt / rotate" aria-label="Tilt / rotate" @click="tiltOpen = !tiltOpen">
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path d="M12 14a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M12 14v5 M8 19h8 M5 12h2 M17 12h2 M12 5V3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <button type="button" class="mtt__btn" title="Undo vertex" aria-label="Undo vertex" :disabled="!ui.draftVertices.length" @click="ui.undoDraftVertex()">
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path d="M9 7H4v5 M4 12a8 8 0 1 0 2.3-5.7L4 8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <button type="button" class="mtt__btn" title="Finish drawing" aria-label="Finish drawing" :disabled="ui.drawMode === 'off'" @click="finishDrawing">
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path d="M5 12l4 4L19 6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <button type="button" class="mtt__btn" title="Cancel tool" aria-label="Cancel tool" @click="clearDrawing">
        <svg viewBox="0 0 24 24" class="mtt__icon" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
      </button>
    </div>

    <div v-if="styleOpen" class="mtt__pop mtt__pop--style">
      <div class="mtt__swatches">
        <button v-for="color in presets" :key="color" type="button" class="mtt__preset" :style="{ background: color }" @click="applyPreset(color)" />
      </div>
      <div class="mtt__color-row">
        <input :value="nativeColorValue(ui.drawingColor)" class="mtt__color" type="color" @change="commitDrawingColor" />
        <input
          :value="ui.drawingColor"
          class="mtt__color-code"
          type="text"
          @input="ui.setDrawingColor($event.target.value)"
          @keydown.enter.prevent="styleOpen = false"
        />
      </div>
    </div>

    <div v-if="editOpen && selectedZone" class="mtt__pop mtt__pop--edit">
      <label class="mtt__field">
        <span>Name</span>
        <input v-model="edit.title" type="text" placeholder="Zone name" />
      </label>
      <label class="mtt__field">
        <span>Tag</span>
        <input v-model="edit.tag" type="text" placeholder="Optional tag" />
      </label>
      <label class="mtt__field">
        <span>Fill</span>
        <input v-model="edit.color" type="text" placeholder="#3B82F6 or rgba(...)" />
      </label>
      <div class="mtt__swatches mtt__swatches--edit">
        <button v-for="color in presets" :key="'fill-' + color" type="button" class="mtt__preset" :style="{ background: color }" @click="setEditColor('color', color)" />
      </div>
      <label class="mtt__field">
        <span>Picker</span>
        <input :value="nativeColorValue(edit.color)" type="color" @input="edit.color = $event.target.value" />
      </label>
      <label class="mtt__field">
        <span>Opacity</span>
        <input v-model.number="edit.fill_opacity" type="number" min="0" max="1" step="0.05" />
      </label>
      <label class="mtt__field">
        <span>Border</span>
        <input v-model="edit.stroke_color" type="text" placeholder="#1E40AF or rgba(...)" />
      </label>
      <div class="mtt__swatches mtt__swatches--edit">
        <button v-for="color in presets" :key="'border-' + color" type="button" class="mtt__preset" :style="{ background: color }" @click="setEditColor('stroke_color', color)" />
      </div>
      <label class="mtt__field">
        <span>Picker</span>
        <input :value="nativeColorValue(edit.stroke_color, '#1E40AF')" type="color" @input="edit.stroke_color = $event.target.value" />
      </label>
      <label class="mtt__field">
        <span>Width</span>
        <input v-model.number="edit.stroke_width" type="number" min="1" max="16" step="1" />
      </label>
      <label class="mtt__field">
        <span>Type</span>
        <select v-model="edit.stroke_style">
          <option value="solid">Solid</option>
          <option value="dashed">Dashed</option>
          <option value="dotted">Dotted</option>
        </select>
      </label>
      <div class="mtt__actions">
        <button type="button" class="mtt__save" @click="saveSelectedZone">Save</button>
        <button type="button" class="mtt__delete" @click="deleteSelectedZone">Delete</button>
      </div>
    </div>

    <div v-if="tiltOpen" class="mtt__pop mtt__pop--tilt">
      <div ref="tiltPad" class="mtt__tilt" @dblclick.prevent="resetTilt">
        <span class="mtt__tilt-cross mtt__tilt-cross--h" />
        <span class="mtt__tilt-cross mtt__tilt-cross--v" />
        <span
          class="mtt__puck"
          :class="{ 'mtt__puck--dragging': tiltDrag }"
          :style="puckStyle"
          @pointerdown.prevent="startTilt"
          @dblclick.stop.prevent="resetTilt"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.mtt {
  position: absolute;
  left: 12px;
  top: 76px;
  z-index: 24;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mtt__group {
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: rgba(11, 14, 20, 0.74);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 4px;
  backdrop-filter: blur(20px) saturate(160%);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
}
.mtt__btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 8px;
  color: rgba(230, 232, 236, 0.88);
  cursor: pointer;
}
.mtt__btn:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.mtt__btn--active { background: rgba(59, 130, 246, 0.20); color: #93C5FD; }
.mtt__btn:disabled { opacity: 0.35; cursor: default; }
.mtt__icon { width: 17px; height: 17px; }
.mtt__swatch {
  width: 17px;
  height: 17px;
  border-radius: 5px;
  border: 2px solid rgba(255, 255, 255, 0.8);
}
.mtt__pop {
  position: absolute;
  left: 48px;
  pointer-events: auto;
  min-width: 156px;
  background: rgba(11, 14, 20, 0.88);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 10px;
  backdrop-filter: blur(20px) saturate(160%);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
}
.mtt__pop--style { top: 252px; width: 156px; }
.mtt__pop--edit { top: 252px; min-width: 220px; }
.mtt__pop--tilt { top: 340px; min-width: auto; padding: 8px; }
.mtt__swatches {
  display: grid;
  grid-template-columns: repeat(4, 20px);
  gap: 6px;
  margin-bottom: 10px;
}
.mtt__swatches--edit {
  grid-template-columns: repeat(6, 18px);
  margin: -2px 0 8px 62px;
}
.mtt__preset {
  width: 20px;
  height: 20px;
  border-radius: 5px;
  border: 1px solid rgba(255, 255, 255, 0.65);
  cursor: pointer;
}
.mtt__swatches--edit .mtt__preset {
  width: 18px;
  height: 18px;
}
.mtt__field {
  display: grid;
  grid-template-columns: 54px 1fr;
  align-items: center;
  gap: 8px;
  color: rgba(230, 232, 236, 0.62);
  font-size: 11px;
  margin-bottom: 8px;
}
.mtt__field:last-child { margin-bottom: 0; }
.mtt__field input[type="text"],
.mtt__field input[type="number"] {
  min-width: 0;
  background: rgba(0, 0, 0, 0.25);
  color: #E6E8EC;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  padding: 5px 7px;
  font: inherit;
}
.mtt__field input[type="number"] {
  width: 74px;
}
.mtt__field select {
  min-width: 0;
  background: rgba(0, 0, 0, 0.25);
  color: #E6E8EC;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  padding: 5px 7px;
  font: inherit;
}
.mtt__field input[type="color"] {
  width: 34px;
  height: 26px;
  background: transparent;
  border: 0;
  padding: 0;
}
.mtt__color-row {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 7px;
  align-items: center;
}
.mtt__color {
  width: 34px;
  height: 28px;
  padding: 0;
  background: transparent;
  border: 0;
}
.mtt__color::-webkit-color-swatch-wrapper,
.mtt__field input[type="color"]::-webkit-color-swatch-wrapper {
  padding: 3px;
}
.mtt__color::-webkit-color-swatch,
.mtt__field input[type="color"]::-webkit-color-swatch {
  border: 0;
  border-radius: 4px;
}
.mtt__color-code {
  width: 96px;
  min-width: 0;
  background: rgba(0, 0, 0, 0.25);
  color: #E6E8EC;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  padding: 5px 7px;
  font: inherit;
  font-size: 11px;
}
.mtt__actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 10px;
}
.mtt__save,
.mtt__delete {
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 6px;
  padding: 5px 9px;
  color: #E6E8EC;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.mtt__save {
  background: rgba(16, 185, 129, 0.16);
  border-color: rgba(16, 185, 129, 0.35);
}
.mtt__delete {
  background: rgba(239, 68, 68, 0.14);
  border-color: rgba(239, 68, 68, 0.35);
}
.mtt__tilt {
  position: relative;
  width: 92px;
  height: 92px;
  border-radius: 999px;
  background: radial-gradient(circle at 50% 50%, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
  border: 1px solid rgba(255, 255, 255, 0.12);
  cursor: default;
  touch-action: none;
}
.mtt__tilt-cross {
  position: absolute;
  background: rgba(255, 255, 255, 0.12);
}
.mtt__tilt-cross--h { left: 14px; right: 14px; top: 50%; height: 1px; }
.mtt__tilt-cross--v { top: 14px; bottom: 14px; left: 50%; width: 1px; }
.mtt__puck {
  position: absolute;
  width: 20px;
  height: 20px;
  margin: -10px 0 0 -10px;
  border-radius: 999px;
  background: #93C5FD;
  cursor: grab;
  transition: box-shadow 120ms ease, transform 120ms ease;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.16), 0 4px 16px rgba(0, 0, 0, 0.35);
}
.mtt__puck--dragging {
  cursor: grabbing;
  transition: none;
  box-shadow: 0 0 0 6px rgba(59, 130, 246, 0.20), 0 3px 12px rgba(0, 0, 0, 0.42);
}
</style>
