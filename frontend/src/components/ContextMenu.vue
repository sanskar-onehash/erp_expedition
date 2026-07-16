<script setup>
/**
 * ContextMenu — right-click quick actions on the map. Translucent
 * overlay anchored at the mouse position with actions:
 *   - Save current view as map card
 *   - Drop a manual pin
 *   - Copy coordinates to clipboard
 *   - Start polygon draw from here (seeds the first vertex)
 *   - Cancel / dismiss
 *
 * Listens to ui.contextMenu (set by Basemap's `contextmenu` handler).
 * Auto-dismisses on click outside, Esc, or any item action.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { usePinsStore } from '../state/pins.js'
import { useIconsStore } from '../state/icons.js'
import { call } from '../api/client.js'
import { wrapLng } from '../lib/geo.js'
import { ICON_PATHS } from '../api/icons.js'
import UiColorInput from './ui/UiColorInput.vue'
import UiSelect from './ui/UiSelect.vue'

const ui = useUiStore()
const mapStore = useMapStore()
const pinsStore = usePinsStore()
const iconStore = useIconsStore()

const rootEl = ref(null)
const titleInputEl = ref(null)
const actionError = ref('')
const mode = ref('menu')
const draftTitle = ref('')
const draftDescription = ref('')
const draftPinType = ref('note')
const draftPinColor = ref('#F59E0B')
const draftPinIcon = ref('pin-marker')
const draftPinVisibility = ref('map')
const draftCreateTodo = ref(false)
const draftTodoText = ref('')
const draftTodoDate = ref('')
const draftTodoPriority = ref('Medium')
const draftAssign = ref(false)
const draftAssignee = ref('')
const userOptions = ref([])
const userSearchOpen = ref(false)
const iconBusy = ref(false)
const uploadTitle = ref('')
const uploadScope = ref('Personal')
const activePinTab = ref('details')
const saving = ref(false)

const PIN_TYPE_OPTIONS = [
  { value: 'note', label: 'Note' },
  { value: 'site', label: 'Site' },
  { value: 'follow_up', label: 'Follow up' },
  { value: 'risk', label: 'Risk' },
]
const COLOR_PRESETS = ['#F59E0B', '#22C55E', '#3B82F6', '#EF4444', '#A855F7', '#14B8A6', '#F97316']
const UPLOAD_SCOPE_OPTIONS = [
  { v: 'Personal', label: 'Personal' },
  { v: 'Global', label: 'Global' },
]
const TODO_PRIORITY_OPTIONS = ['Low', 'Medium', 'High']

const menu = computed(() => ui.contextMenu)
const isOpen = computed(() => !!menu.value)
const actionContext = computed(() => {
  const m = menu.value
  if (!m) return null
  return {
    lat: m.lat,
    lng: wrapLng(m.lng),
    latlng: { lat: m.lat, lng: wrapLng(m.lng) },
    point: { x: m.x, y: m.y },
    map: window.expeditionMap?.getMap?.() || null,
    mapDoc: mapStore.activeMap?.map || null,
    activeMap: mapStore.activeMap || null,
    feature: m.feature || null,
    closeMenu: dismiss,
    toast: (message) => {
      if (message) actionError.value = String(message)
    },
  }
})

const customActions = computed(() => {
  const ctx = actionContext.value
  if (!ctx || !window.Expedition?.getMapContextActions) return []
  return window.Expedition.getMapContextActions(ctx)
})

const hasCustomActions = computed(() => customActions.value.length > 0)

// Keep the menu on-screen: if the click was near the right or
// bottom edge, shift the panel back into the viewport.
const positionStyle = computed(() => {
  const m = menu.value
  if (!m) return {}
  const w = mode.value === 'menu' ? 220 : 330
  const h = mode.value === 'menu' ? 230 : 560
  let x = m.x, y = m.y
  if (typeof window !== 'undefined') {
    if (x + w + 8 > window.innerWidth) x = Math.max(8, window.innerWidth - w - 8)
    if (y + h + 8 > window.innerHeight) y = Math.max(8, window.innerHeight - h - 8)
  }
  return { top: y + 'px', left: x + 'px' }
})

function dismiss() {
  actionError.value = ''
  resetDraft()
  ui.closeContextMenu()
}

function resetDraft() {
  mode.value = 'menu'
  draftTitle.value = ''
  draftDescription.value = ''
  draftPinType.value = 'note'
  draftPinColor.value = '#F59E0B'
  draftPinIcon.value = 'pin-marker'
  draftPinVisibility.value = 'map'
  draftCreateTodo.value = false
  draftTodoText.value = ''
  draftTodoDate.value = ''
  draftTodoPriority.value = 'Medium'
  draftAssign.value = false
  draftAssignee.value = ''
  userOptions.value = []
  userSearchOpen.value = false
  uploadTitle.value = ''
  uploadScope.value = 'Personal'
  activePinTab.value = 'details'
  saving.value = false
}

function focusTitleInput() {
  nextTick(() => titleInputEl.value?.focus?.())
}

function onSaveView() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) { actionError.value = 'Map not ready.'; return }
  draftTitle.value = 'Untitled view'
  mode.value = 'save-view'
  focusTitleInput()
}

async function submitSaveView() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) { actionError.value = 'Map not ready.'; return }
  const title = draftTitle.value.trim()
  if (!title) { actionError.value = 'Title is required.'; return }
  const center = m.getCenter()
  saving.value = true
  actionError.value = ''
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
  } finally {
    saving.value = false
  }
}

function onDropPin() {
  const m = menu.value
  if (!m) return
  const mapName = mapStore.activeMap?.map?.name
  if (!mapName) { actionError.value = 'Open a map before dropping a pin.'; return }
  draftTitle.value = `Pin at ${m.lat.toFixed(4)}, ${wrapLng(m.lng).toFixed(4)}`
  draftDescription.value = ''
  draftTodoText.value = 'Follow up on ' + draftTitle.value
  draftTodoDate.value = getTodayDateString()
  draftTodoPriority.value = 'Medium'
  draftAssignee.value = window.expeditionSession?.user || ''
  activePinTab.value = 'details'
  mode.value = 'drop-pin'
  iconStore.loadIcons().catch(() => {})
  searchUsers(draftAssignee.value)
  focusTitleInput()
}

const iconSections = computed(() => {
  const builtin = iconStore.builtin || []
  const personal = iconStore.personal || []
  const global = iconStore.global || []
  return [
    { key: 'builtin', label: 'Built-in', icons: builtin },
    { key: 'personal', label: 'Personal', icons: personal },
    { key: 'global', label: 'Global', icons: global },
  ].filter((section) => section.icons.length || section.key !== 'global' || iconStore.canManageGlobal)
})

function getTodayDateString() {
  const d = new Date()
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function isBuiltinIcon(icon) {
  return icon?.source === 'builtin'
}

function iconLabel(icon) {
  return icon?.title || icon?.key || 'Icon'
}

async function uploadIconFromEvent(event) {
  const file = event.target?.files?.[0]
  if (!file) return
  iconBusy.value = true
  actionError.value = ''
  try {
    const imageDataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    const icon = await iconStore.uploadIcon({
      title: uploadTitle.value || file.name.replace(/\.[^.]+$/, ''),
      scope: uploadScope.value,
      imageDataUrl,
    })
    draftPinIcon.value = icon.key
    uploadTitle.value = ''
  } catch (e) {
    actionError.value = e.message || String(e)
  } finally {
    iconBusy.value = false
    if (event.target) event.target.value = ''
  }
}

async function searchUsers(txt = '') {
  try {
    userOptions.value = await call('expedition.api.action.search_users', { txt, limit: 8 }) || []
    userSearchOpen.value = true
  } catch (_) {
    userOptions.value = []
  }
}

function selectUser(user) {
  draftAssignee.value = user.value
  userSearchOpen.value = false
}

async function submitPin() {
  const m = menu.value
  if (!m) return
  const mapName = mapStore.activeMap?.map?.name
  if (!mapName) { actionError.value = 'Open a map before dropping a pin.'; return }
  const title = draftTitle.value.trim()
  if (!title) { actionError.value = 'Title is required.'; return }
  saving.value = true
  actionError.value = ''
  try {
    await pinsStore.createPin(mapName, {
      title,
      pin_type: draftPinType.value,
      description: draftDescription.value.trim(),
      latitude: m.lat,
      longitude: wrapLng(m.lng),
      color: draftPinColor.value,
      icon: draftPinIcon.value,
      visibility: draftPinVisibility.value,
    }).then(async (created) => {
      const postCreate = []
      if (draftCreateTodo.value) {
        postCreate.push(call('expedition.api.action.create_todo', {
          source_doctype: 'Expedition Map Pin',
          source_name: created.name,
          description: draftTodoText.value || `Follow up on ${title}`,
          allocated_to: draftAssignee.value || '',
          priority: draftTodoPriority.value || 'Medium',
          date: draftTodoDate.value || null,
        }))
      }
      if (draftAssign.value && draftAssignee.value) {
        postCreate.push(call('expedition.api.action.assign_to', {
          source_doctype: 'Expedition Map Pin',
          source_name: created.name,
          user: draftAssignee.value,
          description: `Assignment for ${title}`,
          priority: draftTodoPriority.value || 'Medium',
          date: draftTodoDate.value || null,
        }))
      }
      if (postCreate.length) await Promise.all(postCreate)
    })
    dismiss()
  } catch (e) {
    actionError.value = e.message || String(e)
  } finally {
    saving.value = false
  }
}

async function runCustomAction(action) {
  if (!action || typeof action.handler !== 'function') return
  try {
    await action.handler(actionContext.value)
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
  if (e.key === 'Escape' && isOpen.value) {
    if (mode.value !== 'menu') {
      actionError.value = ''
      resetDraft()
    } else {
      dismiss()
    }
  }
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
      :class="{ 'ctx--form': mode !== 'menu' }"
      :style="positionStyle"
      role="menu"
      aria-label="Map quick actions"
      @contextmenu.prevent
    >
      <div class="ctx__head">
        <span class="ctx__title">{{ mode === 'save-view' ? 'Save view' : mode === 'drop-pin' ? 'New pin' : 'Quick actions' }}</span>
        <span class="ctx__coords">{{ menu.lat.toFixed(4) }}, {{ menu.lng.toFixed(4) }}</span>
      </div>

      <template v-if="mode === 'menu'">
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
          <span>Drop pin here</span>
        </button>
        <div v-if="hasCustomActions" class="ctx__group" role="presentation">
          <button
            v-for="action in customActions"
            :key="action.id"
            class="ctx__item"
            type="button"
            role="menuitem"
            @click="runCustomAction(action)"
          >
            <span class="ctx__glyph" aria-hidden="true">{{ action.glyph || '+' }}</span>
            <span>{{ action.label || action.id }}</span>
          </button>
        </div>
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
      </template>

      <form v-else class="ctx__form" @submit.prevent="mode === 'save-view' ? submitSaveView() : submitPin()">
        <label class="ctx__field">
          <span>Title</span>
          <input
            ref="titleInputEl"
            v-model="draftTitle"
            class="ctx__input"
            type="text"
            autocomplete="off"
            spellcheck="false"
          >
        </label>

        <template v-if="mode === 'drop-pin'">
          <nav class="ctx__tabs" aria-label="Pin sections">
            <button type="button" class="ctx__tab" :class="{ 'ctx__tab--active': activePinTab === 'details' }" @click="activePinTab = 'details'">Details</button>
            <button type="button" class="ctx__tab" :class="{ 'ctx__tab--active': activePinTab === 'style' }" @click="activePinTab = 'style'">Style</button>
            <button type="button" class="ctx__tab" :class="{ 'ctx__tab--active': activePinTab === 'todo' }" @click="activePinTab = 'todo'">ToDo</button>
            <button type="button" class="ctx__tab" :class="{ 'ctx__tab--active': activePinTab === 'assign' }" @click="activePinTab = 'assign'">Assign</button>
          </nav>

          <section v-if="activePinTab === 'details'" class="ctx__panel">
            <div class="ctx__row">
              <label class="ctx__field">
                <span>Type</span>
                <select v-model="draftPinType" class="ctx__input">
                  <option v-for="option in PIN_TYPE_OPTIONS" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <label class="ctx__field">
                <span>Visibility</span>
                <select v-model="draftPinVisibility" class="ctx__input">
                  <option value="map">Map</option>
                  <option value="private">Private</option>
                </select>
              </label>
            </div>
            <label class="ctx__field">
              <span>Note</span>
              <textarea
                v-model="draftDescription"
                class="ctx__input ctx__textarea"
                rows="3"
                placeholder="Add context for this pin"
              />
            </label>
          </section>

          <section v-else-if="activePinTab === 'style'" class="ctx__panel">
            <label class="ctx__field">
              <span>Color</span>
              <UiColorInput
                v-model="draftPinColor"
                :presets="COLOR_PRESETS"
                placeholder="#F59E0B or rgba(245,158,11,0.8)"
                @blur="draftPinColor = draftPinColor.trim()"
              />
            </label>

            <div class="ctx__field">
              <span>Icon</span>
              <div v-for="section in iconSections" :key="section.key" class="ctx__icon-section">
                <div class="ctx__icon-section-title">{{ section.label }}</div>
                <div class="ctx__icon-grid">
                  <button
                    v-for="icon in section.icons"
                    :key="icon.key"
                    class="ctx__icon-cell"
                    type="button"
                    :class="{ 'ctx__icon-cell--active': draftPinIcon === icon.key }"
                    :title="iconLabel(icon)"
                    @click="draftPinIcon = icon.key"
                  >
                    <svg v-if="isBuiltinIcon(icon)" class="ctx__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                      <path :d="ICON_PATHS[icon.key] || ''" fill="currentColor" />
                    </svg>
                    <span v-else-if="icon.icon_format !== 'Image'" class="ctx__icon-preview ctx__icon-preview--custom" v-html="icon.svg_content" />
                    <img v-else class="ctx__icon-preview ctx__icon-preview--image" :src="icon.image_data_url" alt="">
                  </button>
                </div>
              </div>
              <div v-if="iconStore.canCreate" class="ctx__upload">
                <input v-model="uploadTitle" class="ctx__input ctx__input--sm" type="text" placeholder="Icon name">
                <UiSelect
                  v-if="iconStore.canManageGlobal"
                  v-model="uploadScope"
                  :options="UPLOAD_SCOPE_OPTIONS"
                  value-key="v"
                  label-key="label"
                  :searchable="false"
                  compact
                />
                <label class="ctx__btn ctx__btn--ghost" :class="{ 'ctx__btn--disabled': iconBusy }">
                  Upload Image
                  <input class="ctx__file" type="file" accept="image/*" :disabled="iconBusy" @change="uploadIconFromEvent">
                </label>
              </div>
            </div>
          </section>

          <section v-else-if="activePinTab === 'todo'" class="ctx__panel ctx__task-panel">
            <label class="ctx__toggle">
              <input v-model="draftCreateTodo" type="checkbox">
              <span>Create ToDo with this pin</span>
            </label>
            <label class="ctx__task-field">
              <span>Description</span>
              <textarea v-model="draftTodoText" class="ctx__input ctx__textarea" rows="2" placeholder="Description..." />
            </label>
            <div class="ctx__row">
              <label class="ctx__task-field ctx__field--relative">
                <span>Allocated To</span>
                <input
                  v-model="draftAssignee"
                  class="ctx__input"
                  type="search"
                  placeholder="Search user"
                  autocomplete="off"
                  @focus="searchUsers(draftAssignee)"
                  @input="searchUsers(draftAssignee)"
                  @keydown.escape.stop="userSearchOpen = false"
                >
                <div v-if="userSearchOpen && userOptions.length" class="ctx__options">
                  <button v-for="user in userOptions" :key="user.value" type="button" class="ctx__option" @mousedown.prevent="selectUser(user)">
                    <span>{{ user.label }}</span>
                    <small>{{ user.value }}</small>
                  </button>
                </div>
              </label>
              <label class="ctx__task-field">
                <span>Due Date</span>
                <input v-model="draftTodoDate" class="ctx__input" type="date">
              </label>
            </div>
            <label class="ctx__task-field">
              <span>Priority</span>
              <UiSelect v-model="draftTodoPriority" :options="TODO_PRIORITY_OPTIONS" :searchable="false" compact />
            </label>
          </section>

          <section v-else-if="activePinTab === 'assign'" class="ctx__panel ctx__task-panel">
            <label class="ctx__toggle">
              <input v-model="draftAssign" type="checkbox">
              <span>Assign this pin</span>
            </label>
            <label class="ctx__task-field ctx__field--relative">
              <span>Assign To</span>
              <input
                v-model="draftAssignee"
                class="ctx__input"
                type="search"
                placeholder="Search user"
                autocomplete="off"
                @focus="searchUsers(draftAssignee)"
                @input="searchUsers(draftAssignee)"
                @keydown.escape.stop="userSearchOpen = false"
              >
              <div v-if="userSearchOpen && userOptions.length" class="ctx__options">
                <button v-for="user in userOptions" :key="user.value" type="button" class="ctx__option" @mousedown.prevent="selectUser(user)">
                  <span>{{ user.label }}</span>
                  <small>{{ user.value }}</small>
                </button>
              </div>
            </label>
            <div class="ctx__assign-current">
              <span>Current</span>
              <strong>Unassigned</strong>
            </div>
          </section>
        </template>

        <div class="ctx__actions">
          <button class="ctx__btn" type="button" :disabled="saving" @click="resetDraft">Back</button>
          <button class="ctx__btn ctx__btn--primary" type="submit" :disabled="saving || !draftTitle.trim()">
            {{ saving ? 'Saving...' : mode === 'save-view' ? 'Save view' : 'Drop pin' }}
          </button>
        </div>
      </form>
      <p v-if="actionError" class="ctx__err">{{ actionError }}</p>
    </div>
  </Transition>
</template>

<style scoped>
.ctx {
  position: fixed;
  z-index: 9500;
  width: 220px;
  max-height: calc(100vh - 16px);
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
  box-sizing: border-box;
  overflow: hidden;
}
.ctx--form {
  width: 330px;
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
.ctx__form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 3px 2px 2px;
  overflow-y: auto;
  min-height: 0;
  scrollbar-width: thin;
}
.ctx__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
}
.ctx__tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 3px;
  padding: 2px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.045);
}
.ctx__tab {
  min-width: 0;
  height: 26px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.56);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.ctx__tab--active {
  background: rgba(255, 255, 255, 0.11);
  color: #E6E8EC;
}
.ctx__panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ctx__task-panel {
  padding: 9px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.045);
}
.ctx__field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  font-size: 10px;
  font-weight: 600;
  color: rgba(230, 232, 236, 0.58);
  text-transform: uppercase;
}
.ctx__field--relative { position: relative; }
.ctx__input {
  width: 100%;
  min-width: 0;
  height: 30px;
  box-sizing: border-box;
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.07);
  color: #E6E8EC;
  font: inherit;
  font-size: 12px;
  padding: 0 9px;
  outline: none;
  text-transform: none;
}
.ctx__input--sm {
  height: 28px;
}
.ctx__textarea {
  min-height: 58px;
  max-height: 110px;
  padding-top: 8px;
  padding-bottom: 8px;
  resize: vertical;
  line-height: 1.35;
}
.ctx__input:focus {
  border-color: rgba(96, 165, 250, 0.62);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.22);
}
.ctx__input option {
  background: #111827;
  color: #E6E8EC;
}
:deep(.ui-color) { min-width: 0; }
:deep(.ui-color__input) {
  background: rgba(255, 255, 255, 0.07);
  height: 36px;
}
:deep(.ui-color__presets) { gap: 5px; }
.ctx__icon-section {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.ctx__icon-section + .ctx__icon-section {
  margin-top: 4px;
}
.ctx__icon-section-title {
  color: rgba(230, 232, 236, 0.44);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}
.ctx__icon-grid {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 5px;
}
.ctx__icon-cell {
  height: 28px;
  min-width: 0;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.055);
  color: #E6E8EC;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.ctx__icon-cell--active {
  background: rgba(245, 158, 11, 0.18);
  border-color: rgba(245, 158, 11, 0.72);
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.22);
}
.ctx__icon-preview {
  width: 15px;
  height: 15px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.ctx__icon-preview--custom :deep(svg) {
  width: 15px;
  height: 15px;
}
.ctx__icon-preview--image {
  object-fit: contain;
}
.ctx__upload {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(76px, auto) auto;
  gap: 6px;
  align-items: center;
  margin-top: 2px;
}
.ctx__file {
  display: none;
}
.ctx__toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(230, 232, 236, 0.78);
  font-size: 12px;
}
.ctx__toggle input {
  accent-color: #60A5FA;
}
.ctx__task-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  color: rgba(230, 232, 236, 0.62);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}
.ctx__assign-current {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 28px;
  padding: 0 8px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.045);
  color: rgba(230, 232, 236, 0.54);
  font-size: 11px;
}
.ctx__assign-current strong {
  color: rgba(230, 232, 236, 0.86);
  font-weight: 600;
}
.ctx__actions {
  display: flex;
  justify-content: flex-end;
  gap: 7px;
  padding-top: 4px;
  flex: 0 0 auto;
}
.ctx__btn {
  height: 28px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(230, 232, 236, 0.78);
  padding: 0 10px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.ctx__btn--primary {
  background: rgba(255, 255, 255, 0.92);
  border-color: rgba(255, 255, 255, 0.72);
  color: #111827;
  font-weight: 650;
}
.ctx__btn--ghost { white-space: nowrap; }
.ctx__btn--disabled { opacity: 0.45; pointer-events: none; }
.ctx__btn:disabled {
  opacity: 0.48;
  cursor: default;
}
.ctx__options {
  position: absolute;
  z-index: 2;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  max-height: 150px;
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  background: rgba(17, 24, 39, 0.98);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
  padding: 4px;
}
.ctx__option {
  width: 100%;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #E6E8EC;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  padding: 6px 7px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.ctx__option:hover { background: rgba(255, 255, 255, 0.07); }
.ctx__option small { color: rgba(230, 232, 236, 0.48); }
.ctx__group {
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  margin-top: 4px;
  padding-top: 4px;
}
.ctx__glyph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  color: rgba(230, 232, 236, 0.78);
}
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
