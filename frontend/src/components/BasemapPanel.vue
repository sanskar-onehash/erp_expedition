<script setup>
/**
 * BasemapPanel — skin picker popover.
 *
 * Replaces BasemapSkinPicker.vue. Renders as a popover anchored to a
 * trigger button in the bottom-right toolbar. Same hover-to-preview /
 * click-to-commit behavior as the old picker, with a 2-column grid
 * of skin cards so users can compare at a glance.
 */
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { getSkin, DEFAULT_SKIN_ID } from '../api/skins.js'
import { call } from '../api/client.js'

// Hover-to-preview delay. Fires 250ms after the cursor enters a cell so
// brushing past a card en route to another one doesn't trigger a
// map.setStyle() rebuild. 250ms is the standard hover-intent threshold
// (Nielsen, W3C mouse-event timing) — below ~200ms feels accidental,
// above ~500ms feels broken.
const PREVIEW_DELAY_MS = 250

const ui = useUiStore()
const mapStore = useMapStore()
const open = ref(false)
const rootEl = ref(null)
const saving = ref(false)

// Size class mirrors FloatingToolbar's `.ft--{size}` scale so the
// basemap trigger resizes in step with every other corner button
// when the user changes ui.prefs.toolbarSize.
const sizeClass = computed(() => 'bp--' + (ui.prefs.toolbarSize || 'm'))

// Hover-preview: skin swap fires PREVIEW_DELAY_MS after the cursor settles
// on a cell (see onCellEnter). The preview stays live as the user glides
// across cells — leaving a cell just cancels the pending timer for the
// next one — and only reverts when the cursor leaves the popover entirely
// (see onMouseLeaveRoot). The delay prevents brushing past a card en
// route to another one from triggering a map.setStyle() rebuild.
const activeSkin = computed(() => getSkin(ui.currentSkinId || DEFAULT_SKIN_ID))
const previewSkin = computed(() => (ui.previewSkinId ? getSkin(ui.previewSkinId) : null))
const displayedSkin = computed(() => previewSkin.value || activeSkin.value)

// Timer for the pending preview. Keyed by skinId so entering cell A and
// then cell B before A's timer fires results in B's preview, not A's.
let pendingPreviewTimer = null
let pendingPreviewSkinId = null

function clearPendingPreview() {
  if (pendingPreviewTimer != null) {
    clearTimeout(pendingPreviewTimer)
    pendingPreviewTimer = null
    pendingPreviewSkinId = null
  }
}

function toggle() { open.value = !open.value; if (open.value) ui.cancelPreviewSkin() }
function close() { open.value = false; ui.cancelPreviewSkin() }
function onCellEnter(skinId) {
  // already previewing this skin → no-op
  if (ui.previewSkinId === skinId) { clearPendingPreview(); return }
  // already waiting to preview this skin → no-op
  if (pendingPreviewSkinId === skinId) return
  // swap pending preview to the new skin (cancels any prior timer)
  clearPendingPreview()
  pendingPreviewSkinId = skinId
  pendingPreviewTimer = setTimeout(() => {
    ui.setPreviewSkin(skinId)
    pendingPreviewTimer = null
    pendingPreviewSkinId = null
  }, PREVIEW_DELAY_MS)
}
function onCellLeave() {
  // cancel the pending timer when leaving a cell, but don't cancel an
  // already-active preview — that way gliding across the grid doesn't
  // snap back to the active skin between cells. The preview reverts
  // only when the cursor leaves the popover entirely (onMouseLeaveRoot).
  clearPendingPreview()
}
async function onCommit() {
  const skinId = ui.previewSkinId || ui.currentSkinId
  if (!skinId) { open.value = false; return }
  ui.commitPreviewSkin()
  open.value = false
  const mapName = mapStore.activeMap?.map?.name
  if (!mapName) return
  saving.value = true
  try {
    await call('expedition.api.map.update_basemap_style', { name: mapName, basemap_style: skinId })
  } catch (e) {
    console.warn('[expedition] failed to persist basemap_style', e)
  } finally { saving.value = false }
}
function onMouseLeaveRoot() {
  clearPendingPreview()
  if (ui.previewSkinId) ui.cancelPreviewSkin()
}
function onDocClick(e) {
  if (!open.value) return
  if (rootEl.value && !rootEl.value.contains(e.target)) close()
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  clearPendingPreview()
})

defineExpose({ open, close, toggle })
</script>

<template>
  <div ref="rootEl" class="bp" :class="sizeClass" @mouseleave="onMouseLeaveRoot">
    <button type="button" class="bp__trigger" :aria-label="'Basemap: ' + displayedSkin.label" :title="displayedSkin.label" @click="toggle">
      <svg viewBox="0 0 24 24" class="bp__icon" aria-hidden="true">
        <!-- basemap glyph: stacked rounded rectangles (like map layers) -->
        <path d="M3 7l9-4 9 4-9 4-9-4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M3 12l9 4 9-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" opacity="0.7"/>
        <path d="M3 17l9 4 9-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" opacity="0.4"/>
      </svg>
    </button>
    <div v-if="open" class="bp__popover" role="dialog" aria-label="Basemap picker">
      <div class="bp__head">
        <span class="bp__head-label">Basemap</span>
        <span class="bp__head-current">{{ displayedSkin.label }}</span>
      </div>
      <div class="bp__grid">
        <button
          v-for="skin in ui.basemapSkins"
          :key="skin.id"
          type="button"
          class="bp__cell"
          :class="{ 'bp__cell--active': skin.id === ui.currentSkinId, 'bp__cell--previewing': skin.id === ui.previewSkinId }"
          @mouseenter="onCellEnter(skin.id)"
          @mouseleave="onCellLeave"
          @click="onCommit"
          :title="skin.label"
        >
          <span class="bp__swatch" :data-kind="skin.kind" />
          <span class="bp__cell-label">{{ skin.label }}</span>
        </button>
      </div>
      <div class="bp__foot">Hover to preview · click to commit</div>
    </div>
  </div>
</template>

<style scoped>
.bp {
  position: relative;
  display: inline-flex;
  /* Matches FloatingToolbar's .ft chrome (same blur, border, radius,
     padding, shadow) so the basemap trigger reads as part of the same
     family as TL/TR/CR/BR corner pills. */
  gap: 4px;
  background: rgba(11, 14, 20, 0.72);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 4px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  pointer-events: auto;
}

/* Size scale mirrors FloatingToolbar's `.ft--{size}` — the bp--{size}
   class is applied at runtime from ui.prefs.toolbarSize. Same button
   edge and icon sizes as every other corner button so swapping
   toolbarSize in Settings resizes everything together. */
.bp--xs  { --ft-size: 22px; --ft-icon: 12px; }
.bp--s   { --ft-size: 28px; --ft-icon: 14px; }
.bp--m   { --ft-size: 32px; --ft-icon: 16px; }
.bp--lg  { --ft-size: 40px; --ft-icon: 18px; }
.bp--xlg { --ft-size: 48px; --ft-icon: 20px; }

.bp__trigger {
  width: var(--ft-size, 32px);
  height: var(--ft-size, 32px);
  display: flex; align-items: center; justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 8px;
  color: rgba(230, 232, 236, 0.88);
  cursor: pointer;
  font-family: inherit;
  transition: background 100ms ease, color 100ms ease;
  padding: 0;
}
.bp__trigger:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.bp__icon {
  width: var(--ft-icon, 16px);
  height: var(--ft-icon, 16px);
  flex: none;
}
.bp__popover {
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  width: 355px;
  background: rgba(11, 14, 20, 0.94);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  z-index: 30;
  overflow: hidden;
}
.bp__head {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 10px 12px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.bp__head-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(230, 232, 236, 0.5); }
.bp__head-current { font-size: 11px; color: #fff; font-weight: 500; }
.bp__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 8px;
}
.bp__cell {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  color: #E6E8EC;
  font-family: inherit;
  font-size: 11px;
  transition: background 100ms ease, border-color 100ms ease;
}
.bp__cell:hover { background: rgba(255, 255, 255, 0.08); }
.bp__cell--previewing { background: rgba(255, 255, 255, 0.10); border-color: rgba(255, 255, 255, 0.16); }
.bp__cell--active { border-color: #3B82F6; }
.bp__swatch {
  width: 18px; height: 18px; border-radius: 4px; flex: none;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background:
    linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #6B7A99 0%, #2E3A55 100%);
}
.bp__swatch[data-kind="raster"] {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #4A4A4A 0%, #1A1A1A 100%);
}
.bp__cell-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bp__foot {
  font-size: 10px; color: rgba(230, 232, 236, 0.45);
  padding: 8px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  text-align: center;
}
</style>
