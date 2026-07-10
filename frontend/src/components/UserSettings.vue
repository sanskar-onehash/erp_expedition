<script setup>
/**
 * UserSettings — centered modal with a left-side tab rail.
 *
 * Preview / commit / discard flow:
 *   - On open we snapshot `ui.prefs` into `snapshot`. All controls
 *     read/write a local `draft` ref. The "previewable" subset of
 *     the draft is mirrored into `ui.prefs` after each change so the
 *     map and chrome react live (the user sees the effect immediately
 *     through the backdrop).
 *   - Save commits the draft into `ui.prefs` (which the existing
 *     watcher persists to localStorage) and closes the panel.
 *   - Discard restores the snapshot into both the draft and
 *     `ui.prefs`, so everything reverts to the pre-open state.
 *   - The Map section (defaultZoom, defaultPitch) is NOT previewable —
 *     those are "next-time-load" settings. The slider still updates
 *     the draft and shows the new value, but the live map doesn't
 *     re-zoom.
 *   - Esc and the × button discard (with a confirm if dirty).
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useUiStore } from '../state/ui.js'
import { SHORTCUTS, formatShortcut } from '../lib/keymaps.js'

const ui = useUiStore()

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

// Active tab. Defaults to the first group on open.
const activeKey = ref(null)

// Snapshot of ui.prefs at the moment the panel was opened. Used to
// restore on discard. Deep clone so mutations to draft can't leak
// back into snapshot.
const snapshot = ref(null)

// Live draft. The controls bind to this. Mirrored into ui.prefs for
// the previewable subset on every change.
const draft = ref(null)

// Tracks whether the draft differs from the snapshot — drives the
// "unsaved changes" hint and the close-confirm dialog.
const isDirty = computed(() => {
  if (!snapshot.value || !draft.value) return false
  return JSON.stringify(snapshot.value) !== JSON.stringify(draft.value)
})

// Group definitions for the settings list. The order here drives
// the tab rail order top-to-bottom, so put the most-used group first.
const groups = computed(() => [
  {
    key: 'map',
    title: 'Map',
    items: [
      { kind: 'zoom' },
      { kind: 'pitch' },
      { kind: 'switch', key: 'tiltJoystickInverted', label: 'Invert tilt joystick', help: 'Reverse horizontal and vertical joystick movement.' },
    ],
  },
  {
    key: 'panels',
    title: 'Panels',
    items: [
      { kind: 'switch', key: 'blurOnPanel', label: 'Blur map behind panels', help: 'Dim the map while editing layers or browsing tools.' },
      { kind: 'switch', key: 'autoCloseOthers', label: 'Auto-close other panels', help: 'Opening Layers closes Tools and vice versa.' },
      { kind: 'switch', key: 'showLegend', label: 'Show legend', help: 'Bottom-center legend chip with active layers.' },
    ],
  },
  {
    key: 'pin',
    title: 'Pins & popups',
    items: [
      { kind: 'select', key: 'labelDensity', label: 'Pin labels',
        options: [
          { value: 'off', label: 'Off' },
          { value: 'hover', label: 'On hover' },
          { value: 'always', label: 'Always' },
        ],
      },
      { kind: 'select', key: 'popupAnchor', label: 'Popup position',
        options: [
          { value: 'auto', label: 'Auto' },
          { value: 'top', label: 'Top' },
          { value: 'right', label: 'Right' },
          { value: 'bottom', label: 'Bottom' },
          { value: 'left', label: 'Left' },
        ],
      },
      { kind: 'select', key: 'clickBehavior', label: 'Click on pin',
        options: [
          { value: 'popup', label: 'Open popup' },
          { value: 'select', label: 'Select' },
          { value: 'fly', label: 'Fly to' },
        ],
      },
    ],
  },
  {
    key: 'overlays',
    title: 'Map overlays',
    items: [
      { kind: 'switch', key: 'showCompass', label: 'Compass', help: 'Top-right rotation indicator.' },
      { kind: 'switch', key: 'showScale', label: 'Scale bar', help: 'Bottom-left distance scale.' },
      { kind: 'switch', key: 'showMinimap', label: 'Minimap', help: 'Bottom-right overview map (Phase 2).', disabled: true },
    ],
  },
  {
    key: 'units',
    title: 'Units & cursor',
    items: [
      { kind: 'select', key: 'coordUnits', label: 'Coordinate units',
        options: [
          { value: 'decimal', label: 'Decimal degrees' },
          { value: 'dms', label: 'Degrees / minutes / seconds' },
        ],
      },
      { kind: 'select', key: 'distanceUnits', label: 'Distance units',
        options: [
          { value: 'km', label: 'Kilometers' },
          { value: 'mi', label: 'Miles' },
          { value: 'nm', label: 'Nautical miles' },
        ],
      },
      { kind: 'select', key: 'cursor', label: 'Cursor',
        options: [
          { value: 'crosshair', label: 'Crosshair' },
          { value: 'cross', label: 'Cross' },
          { value: 'circle', label: 'Circle' },
          { value: 'dot', label: 'Dot' },
          { value: 'pointer', label: 'Pointer' },
          { value: 'default', label: 'Default arrow' },
        ],
      },
    ],
  },
  {
    key: 'ui',
    title: 'UI',
    items: [
      { kind: 'chip', key: 'toolbarSize', label: 'Toolbar size',
        help: 'Resize the floating toolbar buttons.',
        options: [
          { value: 'xs',  label: 'XS' },
          { value: 's',   label: 'S' },
          { value: 'm',   label: 'M' },
          { value: 'lg',  label: 'L' },
          { value: 'xlg', label: 'XL' },
        ],
      },
      { kind: 'layout' },
    ],
  },
  {
    key: 'shortcuts',
    title: 'Shortcuts',
    items: [],
  },
  {
    key: 'startup',
    title: 'Startup',
    items: [
      { kind: 'switch', key: 'openRecentOnLaunch', label: 'Open most recent map', help: 'Skip the templates screen and go straight to your last map.' },
      { kind: 'switch', key: 'showTemplatesOnEmpty', label: 'Show templates on empty', help: 'When you have no maps yet, show public templates to clone.' },
    ],
  },
])

// Active group is whichever tab is selected, falling back to the
// first group so a stale `activeKey` (e.g. after a group is removed
// in a future code change) still renders something.
const activeGroup = computed(() =>
  groups.value.find((g) => g.key === activeKey.value) || groups.value[0]
)

const shortcutGroups = computed(() => {
  const out = []
  for (const shortcut of SHORTCUTS) {
    let group = out.find((item) => item.title === shortcut.group)
    if (!group) {
      group = { title: shortcut.group, items: [] }
      out.push(group)
    }
    group.items.push(shortcut)
  }
  return out
})

// Subset of prefs that should mirror to live ui state immediately
// (visible through the backdrop). Map defaults (defaultZoom,
// defaultPitch) are intentionally excluded — they only apply on the
// next map load. Keep the list explicit so adding a new pref forces
// the author to choose preview behavior.
const PREVIEWABLE_KEYS = new Set([
  'blurOnPanel', 'autoCloseOthers', 'showLegend',
  'labelDensity', 'popupAnchor', 'clickBehavior',
  'showCompass', 'showScale', 'showMinimap',
  'coordUnits', 'distanceUnits', 'cursor',
  'toolbarSize',
  'tiltJoystickInverted',
  'openRecentOnLaunch', 'showTemplatesOnEmpty',
])

// On open: snapshot current prefs and seed the draft. On close:
// nothing — we keep the snapshot/draft around so a reopen is fast,
// but we'll resnapshot on next open.
watch(() => props.open, (isOpen) => {
  if (isOpen) {
    snapshot.value = JSON.parse(JSON.stringify(ui.prefs))
    draft.value = JSON.parse(JSON.stringify(ui.prefs))
    activeKey.value = ui.settingsInitialTab || groups.value[0]?.key || null
  }
})

watch(() => [ui.settingsInitialTab, ui.settingsTabRequest], ([tab]) => {
  if (props.open && tab) activeKey.value = tab
})

// Live preview — whenever the draft changes, push the previewable
// subset into ui.prefs. Components that read ui.prefs.* (and the
// blur watcher in particular) will react through the existing
// reactivity, so the user sees the effect through the backdrop
// while they tweak.
watch(draft, (next) => {
  if (!next) return
  for (const key of PREVIEWABLE_KEYS) {
    if (key in next) ui.setPref(key, next[key])
  }
}, { deep: true })

// Esc closes. If dirty, confirm. Closing reverts the preview (so
// the live ui reverts to the snapshot) and emits `close`.
function onKeydown(e) {
  if (!props.open) return
  if (e.key === 'Escape') {
    e.preventDefault()
    closeAndDiscard()
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

function revertAndClose() {
  if (snapshot.value) {
    for (const key of Object.keys(snapshot.value)) {
      ui.setPref(key, snapshot.value[key])
    }
    draft.value = JSON.parse(JSON.stringify(snapshot.value))
  }
  emit('close')
}

function closeAndDiscard() {
  if (!isDirty.value) {
    revertAndClose()
    return
  }
  ui.ask({
    title: 'Discard unsaved changes?',
    body: 'Your current edits will be lost. Saved settings are not affected.',
    confirmLabel: 'Discard',
    destructive: true,
  }).then((ok) => {
    if (ok) revertAndClose()
  })
}

function save() {
  if (!draft.value) return
  // Commit every key (previewable + map defaults). The ui watcher
  // persists to localStorage on the final write.
  for (const key of Object.keys(draft.value)) {
    ui.setPref(key, draft.value[key])
  }
  snapshot.value = JSON.parse(JSON.stringify(draft.value))
  emit('close')
}

function discard() {
  if (!isDirty.value) {
    emit('close')
    return
  }
  ui.ask({
    title: 'Discard unsaved changes?',
    body: 'Your current edits will be lost and the panel will close. Saved settings are not affected.',
    confirmLabel: 'Discard',
    destructive: true,
  }).then((ok) => {
    if (!ok) return
    if (snapshot.value) {
      for (const key of Object.keys(snapshot.value)) {
        ui.setPref(key, snapshot.value[key])
      }
      draft.value = JSON.parse(JSON.stringify(snapshot.value))
    }
    emit('close')
  })
}

function resetAll() {
  // Ask the user via the global confirm modal. Resolves true on
  // confirm — the .then() is OK to skip if the user cancels.
  ui.ask({
    title: 'Reset all settings?',
    body: 'Every preference on this page will be restored to its default. You can still discard to revert.',
    confirmLabel: 'Reset',
    destructive: true,
  }).then((ok) => {
    if (!ok) return
    draft.value = {
      defaultZoom: 3,
      defaultPitch: 0,
      tiltJoystickInverted: true,
      blurOnPanel: true,
      autoCloseOthers: true,
      showLegend: true,
      labelDensity: 'hover',
      popupAnchor: 'auto',
      clickBehavior: 'popup',
      showCompass: false,
      showScale: true,
      showMinimap: false,
      coordUnits: 'decimal',
      distanceUnits: 'km',
      cursor: 'crosshair',
      toolbarSize: 'm',
      chromeLayoutVersion: 3,
      chromeLayout: {
        map: { col: 0, row: 0, anchorX: 'start', anchorY: 'start' },
        toolsPrimary: { col: 0, row: 5, anchorX: 'start', anchorY: 'start' },
        toolsStyle: { col: 0, row: 24, anchorX: 'start', anchorY: 'start' },
        search: { col: 4, row: 0, anchorX: 'end', anchorY: 'start' },
        settings: { col: 0, row: 0, anchorX: 'end', anchorY: 'start' },
        fit: { col: 0, row: 10, anchorX: 'end', anchorY: 'end' },
        tilt: { col: 0, row: 5, anchorX: 'end', anchorY: 'end' },
        layout: { col: 8, row: 0, anchorX: 'end', anchorY: 'end' },
        visibility: { col: 4, row: 0, anchorX: 'end', anchorY: 'end' },
        basemap: { col: 0, row: 0, anchorX: 'end', anchorY: 'end' },
        coords: { col: 0, row: 0, anchorX: 'start', anchorY: 'end' },
        legend: { col: 0, row: 0, anchorX: 'center', anchorY: 'end' },
      },
      openRecentOnLaunch: true,
      showTemplatesOnEmpty: true,
    }
  })
}

function startLayoutCustomize() {
  emit('close')
  ui.setLayoutCustomizing(true)
}

function resetLayoutPreference() {
  ui.resetChromeLayout()
  if (draft.value) {
    draft.value = {
      ...draft.value,
      chromeLayout: JSON.parse(JSON.stringify(ui.prefs.chromeLayout)),
    }
  }
}

// Control handlers — write into the draft only. The watcher above
// mirrors previewable keys into ui.prefs.
function onSwitch(key, event) {
  if (!draft.value) return
  draft.value = { ...draft.value, [key]: event.target.checked }
}
function onSelect(key, value) {
  if (!draft.value) return
  draft.value = { ...draft.value, [key]: value }
}
function onSlider(key, event) {
  if (!draft.value) return
  draft.value = { ...draft.value, [key]: Number(event.target.value) }
}

// --- Custom dropdown (SettingsSelect) ----------------------------------
// Native <select> styling is OS-dependent and renders grey-on-white
// on most desktop browsers, which fights the dark surface of the
// modal. We render a button + popover list to keep the visual
// language consistent with the rest of the chrome (translucent
// dark, blue accent, soft borders, hover-state tints).

const openSelectKey = ref(null)   // which select is currently open
const selectRefs = ref({})        // map key -> { trigger, list }

function toggleSelect(key) {
  openSelectKey.value = openSelectKey.value === key ? null : key
}
function closeSelect() { openSelectKey.value = null }

function pickOption(key, value) {
  onSelect(key, value)
  closeSelect()
}

// Close any open select on outside click. Uses mousedown so the
// closing happens before focus changes, not after.
function onDocMouseDown(e) {
  if (openSelectKey.value == null) return
  const entry = selectRefs.value[openSelectKey.value]
  if (!entry) return
  if (entry.trigger?.contains(e.target)) return
  if (entry.list?.contains(e.target)) return
  closeSelect()
}
onMounted(() => document.addEventListener('mousedown', onDocMouseDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocMouseDown))

function registerRef(key, kind, el) {
  if (!selectRefs.value[key]) selectRefs.value[key] = {}
  selectRefs.value[key][kind] = el
}
</script>

<template>
  <!-- Full-viewport overlay. Backdrop is clickable so clicking outside
       dismisses (which discards). The card stops propagation so clicks
       on the card don't close it. -->
  <div v-if="open && draft" class="us-overlay" @mousedown.self="closeAndDiscard">
    <div class="us" role="dialog" aria-label="User settings" @mousedown.stop>
      <!-- Top header strip. Spans the full card width: title on the
           left, close (×) on the right. Following the quiet-canvas
           UX rule, the close sits where the user expects — top-right
           of the surface. -->
      <header class="us__header">
        <div class="us__header-title">
          <div class="us__title">Settings</div>
          <div v-if="isDirty" class="us__header-dirty" aria-live="polite">
            <span class="us__dirty-dot" aria-hidden="true" />
            <span>Unsaved changes</span>
          </div>
        </div>
        <button
          class="us__close"
          type="button"
          aria-label="Close settings"
          @click="closeAndDiscard"
        >×</button>
      </header>

      <!-- Body grid: rail | pane. The header above sits outside the
           grid so its borders don't fight the rail's right border. -->
      <nav class="us__rail" aria-label="Settings sections">
        <div class="us__rail-tabs">
          <button
            v-for="g in groups"
            :key="g.key"
            type="button"
            class="us__tab"
            :class="{ 'us__tab--active': activeGroup && activeGroup.key === g.key }"
            :aria-pressed="activeGroup && activeGroup.key === g.key"
            @click="activeKey = g.key"
          >
            {{ g.title }}
          </button>
        </div>
      </nav>

      <section v-if="activeGroup" class="us__pane">
        <header class="us__pane-head">
          <div class="us__pane-title">{{ activeGroup.title }}</div>
        </header>
        <div class="us__pane-body">
          <!-- Map section: zoom + pitch sliders. These are NOT
               previewable — they only apply on the next map load. -->
          <template v-if="activeGroup.key === 'map'">
            <div class="us__row">
              <span class="us__row-label">Default zoom</span>
              <input
                type="range" min="1" max="18" step="1"
                :value="draft.defaultZoom"
                @input="(e) => onSlider('defaultZoom', e)"
                class="us__slider"
                aria-label="Default zoom"
              />
              <span class="us__row-val">{{ draft.defaultZoom }}</span>
            </div>
            <p class="us__hint">Applies on the next map you open — your current view is kept.</p>
            <div class="us__row">
              <span class="us__row-label">Default pitch</span>
              <input
                type="range" min="0" max="75" step="5"
                :value="draft.defaultPitch"
                @input="(e) => onSlider('defaultPitch', e)"
                class="us__slider"
                aria-label="Default pitch"
              />
              <span class="us__row-val">{{ draft.defaultPitch }}°</span>
            </div>
            <p class="us__hint">Applies on the next map you open.</p>
            <div class="us__row">
              <label class="us__switch-label">
                <input
                  type="checkbox"
                  :checked="draft.tiltJoystickInverted"
                  @change="(e) => onSwitch('tiltJoystickInverted', e)"
                />
                <span class="us__row-title">Invert tilt joystick</span>
              </label>
              <span class="us__row-help">Reverse horizontal and vertical joystick movement.</span>
            </div>
          </template>

          <!-- Other sections: walk the items and render the right control. -->
          <template v-else-if="activeGroup.key === 'shortcuts'">
            <div class="us__shortcut-help">Hold Alt and hover a front button to reveal its shortcut. Hold Shift + Alt to reveal all visible front shortcuts.</div>
            <div v-for="group in shortcutGroups" :key="group.title" class="us__shortcut-group">
              <div class="us__shortcut-group-title">{{ group.title }}</div>
              <div v-for="shortcut in group.items" :key="shortcut.id" class="us__shortcut-row">
                <div class="us__shortcut-copy">
                  <span class="us__shortcut-name">{{ shortcut.label }}</span>
                  <span class="us__shortcut-desc">{{ shortcut.description }}</span>
                </div>
                <span class="us__shortcut-keys">{{ formatShortcut(shortcut) }}</span>
              </div>
            </div>
          </template>

          <template v-else>
            <div
              v-for="item in activeGroup.items"
              :key="item.key || item.kind"
              class="us__row"
              :class="{ 'us__row--disabled': item.disabled }"
            >
              <template v-if="item.kind === 'switch'">
                <label class="us__switch-label">
                  <input
                    type="checkbox"
                    :checked="draft[item.key]"
                    :disabled="item.disabled"
                    @change="(e) => onSwitch(item.key, e)"
                  />
                  <span class="us__row-title">{{ item.label }}</span>
                </label>
                <span v-if="item.help" class="us__row-help">{{ item.help }}</span>
              </template>
              <template v-else-if="item.kind === 'select'">
                <span class="us__row-title">{{ item.label }}</span>
                <div
                  class="us__select-wrap"
                  :class="{ 'us__select-wrap--open': openSelectKey === item.key }"
                >
                  <button
                    :ref="(el) => registerRef(item.key, 'trigger', el)"
                    type="button"
                    class="us__select"
                    :aria-haspopup="'listbox'"
                    :aria-expanded="openSelectKey === item.key"
                    @click="toggleSelect(item.key)"
                  >
                    <span class="us__select-value">
                      <span
                        v-if="item.key === 'cursor'"
                        class="us__cursor-mark"
                        :data-kind="draft[item.key]"
                        aria-hidden="true"
                      />
                      {{ (item.options.find((o) => o.value === draft[item.key]) || {}).label || '—' }}
                    </span>
                    <svg class="us__select-caret" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
                      <path d="M2 4l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                  </button>
                  <ul
                    v-if="openSelectKey === item.key"
                    :ref="(el) => registerRef(item.key, 'list', el)"
                    class="us__select-pop"
                    role="listbox"
                  >
                    <li
                      v-for="o in item.options"
                      :key="o.value"
                      role="option"
                      :aria-selected="draft[item.key] === o.value"
                    >
                      <button
                        type="button"
                        class="us__select-opt"
                        :class="{ 'us__select-opt--active': draft[item.key] === o.value }"
                        @click="pickOption(item.key, o.value)"
                      >
                        <span class="us__select-opt-label">
                          <span
                            v-if="item.key === 'cursor'"
                            class="us__cursor-mark"
                            :data-kind="o.value"
                            aria-hidden="true"
                          />
                          <span>{{ o.label }}</span>
                        </span>
                        <svg
                          v-if="draft[item.key] === o.value"
                          class="us__select-check"
                          viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"
                        >
                          <path d="M2 6.5l2.5 2.5L10 3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                      </button>
                    </li>
                  </ul>
                </div>
              </template>
              <template v-else-if="item.kind === 'chip'">
                <span class="us__row-title">{{ item.label }}</span>
                <div class="us__chip-row" role="radiogroup" :aria-label="item.label">
                  <button
                    v-for="o in item.options"
                    :key="o.value"
                    type="button"
                    class="us__chip"
                    :class="{ 'us__chip--active': draft[item.key] === o.value }"
                    role="radio"
                    :aria-checked="draft[item.key] === o.value"
                    @click="onSelect(item.key, o.value)"
                  >{{ o.label }}</button>
                </div>
                <span v-if="item.help" class="us__row-help">{{ item.help }}</span>
              </template>
              <template v-else-if="item.kind === 'layout'">
                <span class="us__row-title">Map layout</span>
                <div class="us__layout-actions">
                  <button type="button" class="us__mini-btn" @click="startLayoutCustomize">Customize</button>
                  <button type="button" class="us__mini-btn us__mini-btn--ghost" @click="resetLayoutPreference">Reset</button>
                </div>
                <span class="us__row-help">Move map controls on a snapped grid with fixed spacing.</span>
              </template>
            </div>
          </template>
        </div>
        <footer class="us__pane-foot">
          <button class="us__reset" type="button" @click="resetAll">Reset to defaults</button>
          <div class="us__pane-foot-actions">
            <button
              class="us__btn us__btn--ghost"
              type="button"
              :disabled="!isDirty"
              @click="discard"
            >Discard</button>
            <button
              class="us__btn us__btn--primary"
              type="button"
              @click="save"
            >Save</button>
          </div>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* Full-viewport overlay behind the card. Click backdrop to close
   (discards). Self-only listener so a click on the card doesn't
   bubble up. */
.us-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  /* Backdrop is partially transparent — that's the whole point of
     the preview-through-backdrop feature. The dim is enough to make
     the chrome readable, light enough to see the map underneath. */
  background: rgba(11, 14, 20, 0.35);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  animation: us-fade 140ms ease;
}
@keyframes us-fade {
  from { opacity: 0; }
  to   { opacity: 1; }
}

/* Card. Width / height clamp to the viewport so the panel never
   overflows — footer stays reachable on a 480px-tall window.
   Grid: header row spans both columns; below it the rail and pane
   sit side-by-side. */
.us {
  width: min(640px, calc(100vw - 48px));
  height: min(560px, calc(100vh - 48px));
  min-width: 420px;
  min-height: 380px;
  background: rgba(11, 14, 20, 0.92);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  color: #E6E8EC;
  display: grid;
  /* row 1: header (auto). row 2: rail | pane (1fr). */
  grid-template-columns: 180px 1fr;
  grid-template-rows: auto 1fr;
  grid-template-areas:
    "header header"
    "rail   pane";
  overflow: hidden;
  pointer-events: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  animation: us-pop 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes us-pop {
  from { transform: translateY(8px) scale(0.98); opacity: 0; }
  to   { transform: translateY(0) scale(1); opacity: 1; }
}

/* Header strip — title left, close (×) right. Sits across the top
   of the card, like a standard modal title bar. The rail begins
   below it, so the close is the only "chrome" element in the top-
   right corner of the card. */
.us__header {
  grid-area: header;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px 12px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.015);
  min-width: 0;
}
.us__header-title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.us__title { font-size: 13px; font-weight: 500; }
.us__header-dirty {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: rgba(245, 158, 11, 0.9);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.us__dirty-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #F59E0B;
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.18);
  display: inline-block;
  flex: none;
}
.us__close {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(230, 232, 236, 0.7);
  width: 28px; height: 28px;
  border-radius: 6px;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  flex: none;
  transition: background 100ms ease, color 100ms ease, border-color 100ms ease;
}
.us__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; border-color: rgba(255, 255, 255, 0.16); }

/* Rail (left column). Just the tab list now — the title moved up
   to the header strip above. */
.us__rail {
  grid-area: rail;
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(255, 255, 255, 0.02);
  min-width: 0;
}
.us__rail-tabs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 6px;
  flex: 1;
  overflow-y: auto;
}
.us__tab {
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 6px;
  color: rgba(230, 232, 236, 0.75);
  font-family: inherit;
  font-size: 12px;
  padding: 7px 10px;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
}
.us__tab:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #fff;
}
.us__tab--active {
  background: rgba(59, 130, 246, 0.16);
  color: #93C5FD;
}
.us__tab--active:hover {
  background: rgba(59, 130, 246, 0.22);
  color: #fff;
}

/* Pane (right column). Three rows: head (sticky title), body
   (scrollable controls), foot (Reset + Save/Discard). Body is the
   only scroller — that way the title and the footer stay pinned. */
.us__pane {
  grid-area: pane;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.us__pane-head {
  padding: 14px 16px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex: none;
}
.us__pane-title {
  font-size: 13px;
  font-weight: 500;
}
.us__pane-body {
  padding: 8px 6px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}
.us__pane-foot {
  padding: 10px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
}
.us__pane-foot-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

/* Inline hint under a control, e.g. "applies next time" for the
   non-previewable Map defaults. */
.us__hint {
  margin: -2px 10px 6px;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  line-height: 1.4;
}

/* Generic row (switch / select / slider). Each row reserves the
   same horizontal real estate: label | control | (optional help). */
.us__row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
}
.us__row:hover:not(.us__row--disabled) { background: rgba(255, 255, 255, 0.04); }
.us__row--disabled { opacity: 0.5; pointer-events: none; }

.us__row-label,
.us__row-title {
  font-size: 12px;
  color: rgba(230, 232, 236, 0.9);
  min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.us__row-help {
  grid-column: 1 / -1;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  margin-top: -2px;
  line-height: 1.4;
}
.us__shortcut-help {
  margin: 4px 10px 10px;
  color: rgba(230, 232, 236, 0.58);
  font-size: 11px;
  line-height: 1.45;
}
.us__shortcut-group {
  margin: 8px 4px 12px;
}
.us__shortcut-group-title {
  padding: 0 6px 5px;
  color: rgba(230, 232, 236, 0.48);
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.us__shortcut-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 6px;
}
.us__shortcut-row:hover {
  background: rgba(255, 255, 255, 0.04);
}
.us__shortcut-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.us__shortcut-name {
  color: rgba(230, 232, 236, 0.9);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.us__shortcut-desc {
  color: rgba(230, 232, 236, 0.48);
  font-size: 10px;
  line-height: 1.35;
}
.us__shortcut-keys {
  padding: 4px 7px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 10px;
  font-weight: 650;
  line-height: 1;
  white-space: nowrap;
}
.us__switch-label {
  display: flex; align-items: center; gap: 8px;
  cursor: pointer; min-width: 0;
}
.us__switch-label input { accent-color: #3B82F6; flex: none; }

/* Custom select. The trigger is a button that looks like a normal
   input; the popover is a translucent list anchored under it. The
   popover is rendered as a sibling inside .us__select-wrap so its
   position context is the wrap (which is `position: relative`). */
.us__select-wrap {
  position: relative;
  min-width: 0;
}
.us__select {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 140px;
  max-width: 200px;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 6px;
  color: #E6E8EC;
  padding: 5px 10px;
  font-family: inherit;
  font-size: 11px;
  font-weight: 400;
  cursor: pointer;
  transition: background 100ms ease, border-color 100ms ease;
  white-space: nowrap;
}
.us__select:hover { background: rgba(255, 255, 255, 0.04); border-color: rgba(255, 255, 255, 0.18); }
.us__select-wrap--open .us__select { border-color: #3B82F6; background: rgba(59, 130, 246, 0.10); }
.us__select-value {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  overflow: hidden; text-overflow: ellipsis;
  min-width: 0;
}
.us__select-caret {
  flex: none;
  color: rgba(230, 232, 236, 0.7);
  transition: transform 120ms ease;
}
.us__select-wrap--open .us__select-caret { transform: rotate(180deg); }

/* Segmented chip row — used for short enumerated options like
   toolbar size. Mirrors the visual language of le__size-row in
   LayerEditor.vue so the two surfaces feel related. The row
   aligns to the right edge of the setting row (where the select
   would normally live) and stays right-aligned in RTL. */
.us__chip-row {
  display: inline-flex;
  gap: 4px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 2px;
}
.us__chip {
  background: transparent;
  border: 0;
  color: rgba(230, 232, 236, 0.7);
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  padding: 5px 10px;
  border-radius: 4px;
  cursor: pointer;
  min-width: 30px;
  transition: background 100ms ease, color 100ms ease;
}
.us__chip:hover { color: #fff; }
.us__chip--active {
  background: rgba(59, 130, 246, 0.18);
  color: #fff;
}
.us__layout-actions {
  display: inline-flex;
  gap: 6px;
  justify-content: flex-end;
}
.us__mini-btn {
  border: 1px solid rgba(59, 130, 246, 0.35);
  border-radius: 6px;
  background: rgba(59, 130, 246, 0.20);
  color: #DBEAFE;
  font: inherit;
  font-size: 11px;
  font-weight: 650;
  padding: 5px 9px;
  cursor: pointer;
}
.us__mini-btn:hover {
  background: rgba(59, 130, 246, 0.30);
  color: #fff;
}
.us__mini-btn--ghost {
  border-color: rgba(255, 255, 255, 0.10);
  background: rgba(255, 255, 255, 0.06);
  color: rgba(230, 232, 236, 0.82);
}
.us__mini-btn--ghost:hover {
  background: rgba(255, 255, 255, 0.11);
}

.us__select-pop {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 5;
  min-width: 100%;
  max-width: 240px;
  max-height: 240px;
  overflow-y: auto;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: rgba(11, 14, 20, 0.96);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.5);
  animation: us-popover-in 120ms ease;
}
@keyframes us-popover-in {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.us__select-opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  background: transparent;
  border: 0;
  border-radius: 5px;
  color: rgba(230, 232, 236, 0.85);
  font-family: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
  white-space: nowrap;
}
.us__select-opt-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.us__cursor-mark {
  position: relative;
  flex: none;
  width: 18px;
  height: 18px;
  color: rgba(230, 232, 236, 0.9) !important;
}
.us__cursor-mark::before,
.us__cursor-mark::after {
  content: '';
  position: absolute;
  display: block;
}
.us__cursor-mark[data-kind="crosshair"]::before {
  left: 8px; top: 3px;
  width: 2px; height: 12px;
  background: currentColor;
}
.us__cursor-mark[data-kind="crosshair"]::after {
  left: 3px; top: 8px;
  width: 12px; height: 2px;
  background: currentColor;
}
.us__cursor-mark[data-kind="cross"]::before,
.us__cursor-mark[data-kind="cross"]::after {
  left: 3px; top: 8px;
  width: 12px; height: 2px;
  background: currentColor;
  border-radius: 2px;
}
.us__cursor-mark[data-kind="cross"]::before { transform: rotate(45deg); }
.us__cursor-mark[data-kind="cross"]::after { transform: rotate(-45deg); }
.us__cursor-mark[data-kind="circle"]::before {
  left: 4px; top: 4px;
  width: 8px; height: 8px;
  border: 2px solid currentColor;
  border-radius: 50%;
}
.us__cursor-mark[data-kind="dot"]::before {
  left: 6px; top: 6px;
  width: 6px; height: 6px;
  background: currentColor;
  border-radius: 50%;
}
.us__cursor-mark[data-kind="default"]::before {
  left: 4px; top: 2px;
  width: 13px; height: 15px;
  background: currentColor;
  clip-path: polygon(0 0, 0 15px, 4px 11px, 7px 17px, 9px 16px, 6px 10px, 12px 10px);
}
.us__cursor-mark[data-kind="pointer"]::before {
  left: 1px; top: 0;
  width: 17px; height: 18px;
  background: currentColor;
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' fill-rule='evenodd' d='M10.5 2C9.12 2 8 3.12 8 4.5v6.39l-.74-.74a2.38 2.38 0 0 0-3.36 3.36l4.03 4.03A7.03 7.03 0 0 0 12.9 19.6h1.35A5.75 5.75 0 0 0 20 13.85v-4.1c0-1.1-.9-2-2-2-.42 0-.82.13-1.14.36A2 2 0 0 0 15 6.9c-.39 0-.75.11-1.05.3A2 2 0 0 0 12 5.6V4.5C12 3.12 10.88 2 10.5 2Zm-.9 2.5a.9.9 0 1 1 1.8 0v7.1a.75.75 0 0 0 1.5 0v-4a.65.65 0 0 1 1.3 0v4a.75.75 0 0 0 1.5 0V8.9a.65.65 0 0 1 1.3 0v2.7a.75.75 0 0 0 1.5 0V9.75a.5.5 0 0 1 1 0v4.1a4.25 4.25 0 0 1-4.25 4.25H12.9a5.53 5.53 0 0 1-3.9-1.62l-4.03-4.03a.88.88 0 0 1 1.24-1.24l2.01 2.01a.82.82 0 0 0 1.38-.59V4.5Z'/%3E%3C/svg%3E") center / contain no-repeat;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' fill-rule='evenodd' d='M10.5 2C9.12 2 8 3.12 8 4.5v6.39l-.74-.74a2.38 2.38 0 0 0-3.36 3.36l4.03 4.03A7.03 7.03 0 0 0 12.9 19.6h1.35A5.75 5.75 0 0 0 20 13.85v-4.1c0-1.1-.9-2-2-2-.42 0-.82.13-1.14.36A2 2 0 0 0 15 6.9c-.39 0-.75.11-1.05.3A2 2 0 0 0 12 5.6V4.5C12 3.12 10.88 2 10.5 2Zm-.9 2.5a.9.9 0 1 1 1.8 0v7.1a.75.75 0 0 0 1.5 0v-4a.65.65 0 0 1 1.3 0v4a.75.75 0 0 0 1.5 0V8.9a.65.65 0 0 1 1.3 0v2.7a.75.75 0 0 0 1.5 0V9.75a.5.5 0 0 1 1 0v4.1a4.25 4.25 0 0 1-4.25 4.25H12.9a5.53 5.53 0 0 1-3.9-1.62l-4.03-4.03a.88.88 0 0 1 1.24-1.24l2.01 2.01a.82.82 0 0 0 1.38-.59V4.5Z'/%3E%3C/svg%3E") center / contain no-repeat;
}
.us__select-opt:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
.us__select-opt--active {
  background: rgba(59, 130, 246, 0.14);
  color: #93C5FD;
}
.us__select-opt--active:hover { background: rgba(59, 130, 246, 0.20); color: #fff; }
.us__select-check { flex: none; color: #3B82F6; }

.us__slider { width: 120px; accent-color: #3B82F6; }
.us__row-val {
  font-size: 11px; color: #fff;
  font-variant-numeric: tabular-nums;
  min-width: 32px; text-align: right;
}

/* The slider rows need to span all three conceptual columns (label,
   slider, value). Override the default 2-col grid for those. */
.us__row:has(.us__slider) {
  grid-template-columns: auto 1fr auto;
}
.us__row:has(.us__slider) .us__row-label { min-width: 110px; }

.us__reset {
  background: transparent;
  border: 1px solid rgba(239, 68, 68, 0.30);
  color: #FCA5A5;
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.us__reset:hover { background: rgba(239, 68, 68, 0.10); color: #fff; }

.us__btn {
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  padding: 7px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease, opacity 100ms ease;
}
.us__btn--ghost {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgba(230, 232, 236, 0.85);
}
.us__btn--ghost:hover:not(:disabled) { background: rgba(255, 255, 255, 0.06); color: #fff; }
.us__btn--ghost:disabled { opacity: 0.4; cursor: default; }
.us__btn--primary {
  background: #3B82F6;
  border: 1px solid #3B82F6;
  color: #fff;
}
.us__btn--primary:hover { background: #2563EB; border-color: #2563EB; }

/* Tight viewports: drop the min-width so a 360px-wide window can
   still show the panel without horizontal scroll. */
@media (max-width: 520px) {
  .us { min-width: 0; width: calc(100vw - 32px); grid-template-columns: 130px 1fr; }
}
@media (max-height: 480px) {
  .us { min-height: 0; height: calc(100vh - 32px); }
}
</style>
