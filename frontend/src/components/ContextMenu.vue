<script setup>
/**
 * ContextMenu — right-click quick actions on the map. Translucent
 * overlay anchored at the mouse position with actions:
 *   - Save current view as map card
 *   - Drop a visit pin (opens MapPopup in "log visit" mode)
 *   - Copy coordinates to clipboard
 *   - Start polygon draw from here (seeds the first vertex)
 *   - Cancel / dismiss
 *
 * Listens to ui.contextMenu (set by Basemap's `contextmenu` handler).
 * Auto-dismisses on click outside, Esc, or any item action.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { call } from '../api/client.js'
import { wrapLng } from '../lib/geo.js'

const ui = useUiStore()
const mapStore = useMapStore()

const rootEl = ref(null)
const actionError = ref('')

const menu = computed(() => ui.contextMenu)
const isOpen = computed(() => !!menu.value)

// Keep the menu on-screen: if the click was near the right or
// bottom edge, shift the panel back into the viewport.
const positionStyle = computed(() => {
  const m = menu.value
  if (!m) return {}
  const w = 220, h = 230 // estimated panel size
  let x = m.x, y = m.y
  if (typeof window !== 'undefined') {
    if (x + w + 8 > window.innerWidth) x = Math.max(8, window.innerWidth - w - 8)
    if (y + h + 8 > window.innerHeight) y = Math.max(8, window.innerHeight - h - 8)
  }
  return { top: y + 'px', left: x + 'px' }
})

function dismiss() {
  actionError.value = ''
  ui.closeContextMenu()
}

async function onSaveView() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) { actionError.value = 'Map not ready.'; return }
  const center = m.getCenter()
  const title = window.prompt('Save current view as map card. Title?', 'Untitled view')
  if (!title) return
  try {
    await call('expedition.api.map.save_map_card', {
      title,
      viewport: {
        center: [wrapLng(center.lng), center.lat],
        zoom: m.getZoom(),
        bearing: m.getBearing(),
        pitch: m.getPitch(),
      },
      basemap_style: mapStore.activeMap?.map?.basemap_style || 'light',
    })
    dismiss()
  } catch (e) {
    actionError.value = e.message || String(e)
  }
}

async function onDropPin() {
  // Open the MapPopup in "log visit" mode by selecting an empty
  // point on the map at the click position. The simplest path:
  // create a placeholder activity row (lat/lng only), then the
  // popup's log-visit form will pick it up. v1 we just log an
  // untitled visit at the click.
  const m = menu.value
  if (!m) return
  try {
    await call('expedition.api.activity.log_activity', {
      activity_type: 'visit',
      title: `Visit at ${m.lat.toFixed(4)}, ${wrapLng(m.lng).toFixed(4)}`,
      latitude: m.lat,
      longitude: wrapLng(m.lng),
      map_name: mapStore.activeMap?.map?.name,
      occurred_at: new Date().toISOString().slice(0, 19),
    })
    dismiss()
  } catch (e) {
    actionError.value = e.message || String(e)
  }
}

async function onCopyCoords() {
  const m = menu.value
  if (!m) return
  const text = `${m.lat.toFixed(6)}, ${wrapLng(m.lng).toFixed(6)}`
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      // Fallback for older browsers / non-https contexts.
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    dismiss()
  } catch (e) {
    actionError.value = 'Copy failed: ' + (e.message || e)
  }
}

function onStartPolygon() {
  const m = menu.value
  if (!m) return
  ui.startDrawPolygon()
  // Seed the first vertex so the user starts mid-draw.
  ui.pushDraftVertex([wrapLng(m.lng), m.lat])
  dismiss()
}

function onKey(e) {
  if (e.key === 'Escape' && isOpen.value) dismiss()
}

function onClickOutside(e) {
  if (!isOpen.value || !rootEl.value) return
  if (!rootEl.value.contains(e.target)) dismiss()
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  // Use capture so we dismiss before any pin click handler fires.
  window.addEventListener('mousedown', onClickOutside, true)
  window.addEventListener('contextmenu', onClickOutside, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('mousedown', onClickOutside, true)
  window.removeEventListener('contextmenu', onClickOutside, true)
})
</script>

<template>
  <Transition name="ctx">
    <div
      v-if="isOpen && menu"
      ref="rootEl"
      class="ctx"
      :style="positionStyle"
      role="menu"
      aria-label="Map quick actions"
      @contextmenu.prevent
    >
      <div class="ctx__head">
        <span class="ctx__title">Quick actions</span>
        <span class="ctx__coords">{{ menu.lat.toFixed(4) }}, {{ menu.lng.toFixed(4) }}</span>
      </div>
      <button class="ctx__item" type="button" role="menuitem" @click="onSaveView">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z M17 21v-8H7v8 M7 3v5h8"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>Save view as map card</span>
      </button>
      <button class="ctx__item" type="button" role="menuitem" @click="onDropPin">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7z M12 11.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
        <span>Drop visit pin here</span>
      </button>
      <button class="ctx__item" type="button" role="menuitem" @click="onCopyCoords">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
          <path d="M5 15V5a2 2 0 0 1 2-2h10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span>Copy coordinates</span>
      </button>
      <button v-if="mapStore.activeMap" class="ctx__item" type="button" role="menuitem" @click="onStartPolygon">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M4 4l6 2 8-3 2 6-3 8 2 6-6 2-8-3-6 2 2-6 3-8-2-6z M4 4l16 16"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
        </svg>
        <span>Start polygon from here</span>
      </button>
      <button class="ctx__item ctx__item--ghost" type="button" role="menuitem" @click="dismiss">
        Cancel
      </button>
      <p v-if="actionError" class="ctx__err">{{ actionError }}</p>
    </div>
  </Transition>
</template>

<style scoped>
.ctx {
  position: fixed;
  z-index: 9500;
  width: 220px;
  background: rgba(11, 14, 20, 0.92);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 10px;
  padding: 6px;
  display: flex; flex-direction: column; gap: 2px;
  box-shadow: 0 10px 36px rgba(0, 0, 0, 0.55);
  color: #E6E8EC;
  font-family: inherit;
}
.ctx__head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  padding: 4px 6px 6px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  margin-bottom: 4px;
}
.ctx__title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
  color: rgba(230, 232, 236, 0.6);
}
.ctx__coords {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  font-variant-numeric: tabular-nums;
}
.ctx__item {
  display: flex; align-items: center; gap: 8px;
  background: transparent;
  border: 0;
  padding: 6px 8px;
  border-radius: 6px;
  color: #E6E8EC;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  width: 100%;
}
.ctx__item:hover { background: rgba(255, 255, 255, 0.07); }
.ctx__item--ghost {
  color: rgba(230, 232, 236, 0.6);
  margin-top: 2px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 0;
  border-top-left-radius: 0;
  border-top-right-radius: 0;
  padding-top: 8px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 10px;
  justify-content: center;
}
.ctx__item--ghost:hover { color: #fff; background: rgba(255, 255, 255, 0.04); }
.ctx__err {
  margin: 4px 0 0;
  font-size: 10px;
  color: #FCA5A5;
  padding: 0 4px;
}

/* Transition */
.ctx-enter-active, .ctx-leave-active { transition: opacity 120ms ease, transform 120ms ease; }
.ctx-enter-from, .ctx-leave-to { opacity: 0; transform: scale(0.96); }
</style>
