<script setup>
/**
 * LayerPanel — left-edge map workspace panel.
 *
 * Saved map controls live above the existing active layer controls.
 *
 * Width: 300px. Max height: 100vh minus 24px top/bottom. Translucent,
 * same glass as the rest of the chrome.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { ICON_PATHS } from '../api/icons.js'
import { call } from '../api/client.js'
import FilterBuilder from './FilterBuilder.vue'
import { filterCount, parseFilterRows, serializeFilterRows, summarizeFilterRows } from '../lib/filters.js'

const mapStore = useMapStore()
const layerStore = useLayersStore()
const ui = useUiStore()
const emit = defineEmits(['close'])

const activeMapName = computed(() => mapStore.activeMap?.map?.name)
const templates = computed(() => mapStore.templates || [])
const maps = computed(() => mapStore.recent || [])
const activeLayers = computed(() => layerStore.layers || [])
const masters = computed(() => layerStore.masters || [])
const panelRoot = ref(null)

const layersOpen = ref(true)
const mapsOpen = ref(true)
const templatesOpen = ref(true)
const shareOpen = ref(false)
const createOpen = ref(false)
const mapQuery = ref('')
const mapTitleDraft = ref('')
const newMapTitle = ref('')
const newMapPublic = ref(false)
const shareUsers = ref([])
const userQuery = ref('')
const shareDraftUser = ref(null)
const shareDraftCanRead = ref(true)
const shareDraftCanWrite = ref(false)
const shareDraftCanShare = ref(false)
const userOptions = ref([])
const userSearchOpen = ref(false)
const userSearchLoading = ref(false)
const userPickerRoot = ref(null)
const mapSaving = ref(false)
const mapError = ref('')
let userSearchTimer = null

// Inline add-layer picker state.
const pickerQuery = ref('')
const pickerOpen = ref(false)
const pickerRoot = ref(null) // template ref for document-click-outside check

const filteredMasters = computed(() => {
  if (!activeMapName.value) return []
  // Only exclude enabled layers; disabled layers can be "re-added" via the
  // picker which idempotently re-enables them on the server.
  const attached = new Set(
    activeLayers.value
      .filter((l) => l.enabled !== false && l.enabled !== 0)
      .map((l) => `${l.source_doctype}::${l.title}`)
  )
  const q = pickerQuery.value.trim().toLowerCase()
  return masters.value.filter((m) => {
    if (attached.has(`${m.source_doctype}::${m.title}`)) return false
    if (!q) return true
    return (
      (m.title || '').toLowerCase().includes(q) ||
      (m.source_doctype || '').toLowerCase().includes(q)
    )
  })
})
// Grouped + sorted for the dropdown. Doctype keys are sorted
// alphabetically; within a group, masters are sorted by title.
const pickerGroups = computed(() => {
  const out = new Map()
  for (const m of filteredMasters.value) {
    const key = m.source_doctype || '(no source)'
    if (!out.has(key)) out.set(key, [])
    out.get(key).push(m)
  }
  const grouped = Array.from(out.entries()).sort((a, b) => a[0].localeCompare(b[0]))
  for (const [, items] of grouped) items.sort((a, b) => (a.title || '').localeCompare(b.title || ''))
  return grouped
})

const canEdit = computed(() => {
  if (mapStore.activeMap?.permissions?.write) return true
  const owner = mapStore.activeMap?.map?.owner_user || mapStore.activeMap?.map?.owner
  const me = window.expeditionSession?.user
  return !owner || owner === me || me === 'Administrator'
})
const canShare = computed(() => {
  if (mapStore.activeMap?.permissions?.share) return true
  const owner = mapStore.activeMap?.map?.owner_user || mapStore.activeMap?.map?.owner
  const me = window.expeditionSession?.user
  return !owner || owner === me || me === 'Administrator'
})
const activeMap = computed(() => mapStore.activeMap?.map || null)
const activeAccess = computed(() => {
  if (!activeMap.value) return 'private'
  if (activeMap.value.is_public) return 'public'
  const owner = activeMap.value.owner_user || activeMap.value.owner
  const me = window.expeditionSession?.user
  return owner && owner !== me && me !== 'Administrator' ? 'shared' : 'private'
})
const activeSubtitle = computed(() => {
  const layerLabel = activeLayers.value.length === 1 ? '1 layer' : `${activeLayers.value.length} layers`
  return `${activeAccess.value} · ${layerLabel}`
})
const filteredMaps = computed(() => {
  const q = mapQuery.value.trim().toLowerCase()
  return maps.value.filter((m) => {
    if (!q) return true
    return (m.title || '').toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q)
  })
})

const _iconSpriteHref = (() => {
  const v = Date.now()
  return (id) => `/assets/expedition/icons.svg#${id}?v=${v}`
})()
function iconHref(id) { return _iconSpriteHref(id) }
function iconPath(id) { return ICON_PATHS[id] || '' }

function openMap(name) {
  if (name && name !== activeMapName.value) mapStore.switchMap(name)
}
function accessLabel(row) {
  if (row?.is_public || row?.access === 'public') {
    return row?.public_access === 'Writable' ? 'Public writable' : 'Public'
  }
  if (row?.access === 'shared') return 'Shared'
  return 'Private'
}
function accessKind(row) {
  if (row?.is_public || row?.access === 'public') return 'public'
  if (row?.access === 'shared') return 'shared'
  return 'private'
}
async function createBlankMap() {
  const title = newMapTitle.value.trim()
  if (!title) {
    mapError.value = 'Name the map first.'
    return
  }
  mapSaving.value = true
  mapError.value = ''
  try {
    await mapStore.createBlankMap({
      title,
      basemap_style: ui.currentSkinId,
      is_public: newMapPublic.value ? 1 : 0,
      public_access: 'Read Only',
    })
    newMapTitle.value = ''
    newMapPublic.value = false
    createOpen.value = false
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
async function saveActiveMap() {
  if (!activeMap.value) return
  mapSaving.value = true
  mapError.value = ''
  try {
    await mapStore.updateActiveMap({
      title: mapTitleDraft.value.trim() || activeMap.value.title,
      basemap_style: ui.currentSkinId,
      is_public: activeMap.value.is_public ? 1 : 0,
      public_access: activeMap.value.public_access || 'Read Only',
    })
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
async function setPublic(on) {
  if (!activeMap.value || !canShare.value) return
  mapSaving.value = true
  mapError.value = ''
  try {
    const dto = await mapStore.updateActiveMap({
      is_public: on ? 1 : 0,
      public_access: 'Read Only',
    })
    mapStore.activeMap.map.is_public = dto.is_public
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
async function setEveryoneRead(on) {
  if (!activeMap.value || !canShare.value) return
  mapSaving.value = true
  mapError.value = ''
  try {
    const dto = await mapStore.updateActiveMap({
      is_public: on ? 1 : 0,
      public_access: on ? (activeMap.value.public_access || 'Read Only') : 'Read Only',
    })
    mapStore.activeMap.map.is_public = dto.is_public
    mapStore.activeMap.map.public_access = dto.public_access || 'Read Only'
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
async function setEveryoneWrite(on) {
  if (!activeMap.value || !canShare.value) return
  mapSaving.value = true
  mapError.value = ''
  try {
    const dto = await mapStore.updateActiveMap({
      is_public: 1,
      public_access: on ? 'Writable' : 'Read Only',
    })
    mapStore.activeMap.map.is_public = dto.is_public
    mapStore.activeMap.map.public_access = dto.public_access || 'Read Only'
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
async function saveShares() {
  mapSaving.value = true
  mapError.value = ''
  try {
    await mapStore.shareActiveMap(shareUsers.value.map((row) => ({
      user: row.user,
      access: row.canWrite ? 'write' : 'read',
      read: row.canRead ? 1 : 0,
      share: row.canShare ? 1 : 0,
    })))
  } catch (e) {
    mapError.value = String(e.message || e)
  } finally {
    mapSaving.value = false
  }
}
function normalizeSharedUsers(rows = []) {
  shareUsers.value = (rows || []).map((row) => ({
    user: row.user || row.value,
    label: row.label || row.user || row.value,
    canRead: row.read == null ? true : !!Number(row.read),
    canWrite: !!Number(row.write || 0) || row.access === 'write',
    canShare: !!Number(row.share || 0),
  })).filter((row) => row.user)
}
function scheduleUserSearch() {
  userSearchOpen.value = true
  if (userSearchTimer) window.clearTimeout(userSearchTimer)
  userSearchTimer = window.setTimeout(() => searchUsers(userQuery.value), 160)
}
async function searchUsers(txt = '') {
  userSearchLoading.value = true
  try {
    const selected = shareDraftUser.value?.value
    const existing = new Set(shareUsers.value.map((row) => row.user))
    userOptions.value = (await call('expedition.api.action.search_users', {
      txt,
      limit: 8,
    }) || []).filter((row) => row.value && (!existing.has(row.value) || row.value === selected))
    userSearchOpen.value = true
  } catch (e) {
    console.warn('[expedition] user search failed', e)
    userOptions.value = []
  } finally {
    userSearchLoading.value = false
  }
}
function addSharedUser(user) {
  if (!user?.value) return
  shareDraftUser.value = user
  userQuery.value = user.label || user.value
  userOptions.value = []
  userSearchOpen.value = false
}
function upsertSharedUser() {
  const user = shareDraftUser.value
  if (!user?.value || !shareDraftCanRead.value) return
  const next = {
    user: user.value,
    label: user.label || user.value,
    canRead: true,
    canWrite: !!shareDraftCanWrite.value,
    canShare: !!shareDraftCanShare.value,
  }
  const exists = shareUsers.value.some((row) => row.user === next.user)
  shareUsers.value = exists
    ? shareUsers.value.map((row) => (row.user === next.user ? next : row))
    : [...shareUsers.value, next]
  shareDraftUser.value = null
  shareDraftCanRead.value = true
  shareDraftCanWrite.value = false
  shareDraftCanShare.value = false
  userQuery.value = ''
}
function removeSharedUser(user) {
  shareUsers.value = shareUsers.value.filter((row) => row.user !== user)
}
function updateShareFlag(user, key, value) {
  shareUsers.value = shareUsers.value.map((row) =>
    row.user === user ? { ...row, [key]: !!value } : row
  ).filter((row) => row.canRead)
}
function editShareRow(row) {
  shareDraftUser.value = { value: row.user, label: row.label || row.user }
  userQuery.value = row.label || row.user
  shareDraftCanRead.value = !!row.canRead
  shareDraftCanWrite.value = !!row.canWrite
  shareDraftCanShare.value = !!row.canShare
}
function toggleLayer(layerName, enabled) {
  layerStore.updateLayer(layerName, { enabled: enabled ? 1 : 0 })
}
function editLayer(layer) { ui.openLayerEditor(layer) }
function createLayer() {
  closePicker()
  ui.openLayerEditor(null, { asMaster: false })
}
function toggleHeatmap(layerName) { ui.toggleHeatmap(layerName) }
function toggleRadius(layerName) { ui.toggleRadius(layerName) }
function setRadiusField(layerName, field) { ui.setRadiusField(layerName, field) }

// Discover numeric properties on a layer's first feature. We don't
// have the source DocType's full schema cached; the feature
// properties are a usable proxy. The first feature wins; same source
// always has the same schema, so this is stable.
function numericFieldsFor(layerName) {
  const fc = layerStore.features[layerName]
  if (!fc || !fc.features || !fc.features.length) return []
  const sample = fc.features[0].properties || {}
  const out = []
  for (const [k, v] of Object.entries(sample)) {
    if (k.startsWith('_')) continue
    if (typeof v === 'number' && Number.isFinite(v)) out.push(k)
  }
  return out
}

const radiusPickerFor = ref(null) // layerName currently shown, or null
function toggleRadiusPicker(layerName) {
  radiusPickerFor.value = radiusPickerFor.value === layerName ? null : layerName
}

// Quick filter edit state: layerName -> open boolean, plus temporary edits
const quickFilterFor = ref(null) // layerName currently editing, or null
const quickFilterDraft = ref([])
const quickFilterSchemas = ref({}) // layerName -> filter schema
const quickFilterPopupRoot = ref(null)
const quickFilterPlacement = ref({
  top: 12,
  left: 120,
  width: 320,
  maxHeight: 320,
})
const quickFilterPopoverStyle = computed(() => ({
  top: `${quickFilterPlacement.value.top}px`,
  left: `${quickFilterPlacement.value.left}px`,
  width: `${quickFilterPlacement.value.width}px`,
  maxHeight: `${quickFilterPlacement.value.maxHeight}px`,
}))
function placeQuickFilterPopover(event) {
  const rect = event?.currentTarget?.getBoundingClientRect?.()
  if (!rect) return
  const margin = 12
  const width = Math.min(320, window.innerWidth - margin * 2)
  const left = Math.min(Math.max(margin, 120), window.innerWidth - width - margin)
  const below = window.innerHeight - rect.bottom - margin
  const above = rect.top - margin
  const preferredHeight = 280
  const openAbove = below < 220 && above > below
  const maxHeight = Math.max(180, Math.min(preferredHeight, openAbove ? above : below))
  const top = openAbove
    ? Math.max(margin, rect.top - maxHeight - 8)
    : Math.min(rect.bottom + 8, window.innerHeight - maxHeight - margin)
  quickFilterPlacement.value = { top, left, width, maxHeight }
}
function openQuickFilter(layer, event) {
  quickFilterDraft.value = parseFilterRows(layer.filter_json)
  quickFilterFor.value = layer.name
  placeQuickFilterPopover(event)
}
async function saveQuickFilter(layer) {
  if (!layer || !quickFilterFor.value) return
  const json = serializeFilterRows(quickFilterDraft.value)
  try {
    await layerStore.updateLayer(layer.name, { filter_json: json })
    quickFilterFor.value = null
  } catch (e) {
    console.error('[expedition] saveQuickFilter failed', e)
    await ui.ask({
      title: 'Could not update filter',
      body: String(e.message || e),
      confirmLabel: 'OK',
      cancelLabel: '',
      destructive: false,
    })
  }
}
function cancelQuickFilter() {
  quickFilterFor.value = null
}
function setQuickFilterSchema(layerName, schema) {
  quickFilterSchemas.value = { ...quickFilterSchemas.value, [layerName]: schema }
}
function filterBadge(layer) {
  return filterCount(layer.filter_json)
}
function filterSummaries(layer) {
  const fields = quickFilterSchemas.value[layer.name]?.fields || []
  return summarizeFilterRows(parseFilterRows(layer.filter_json), fields).slice(0, 3)
}

async function attachMaster(master) {
  if (!activeMapName.value || !master || !master.name) return
  return layerStore.attachToMap(master.name, activeMapName.value).catch(async (e) => {
    console.error('[expedition] attach master failed', e)
    await ui.ask({
      title: `Could not add "${master.title || master.name}"`,
      body: String(e.message || e),
      confirmLabel: 'OK',
      cancelLabel: '',
      destructive: false,
    })
  })
}

function closePicker() {
  pickerOpen.value = false
  pickerQuery.value = ''
}
function containsTarget(refValue, target) {
  const nodes = Array.isArray(refValue) ? refValue : [refValue]
  return nodes.some((node) => {
    const el = node?.$el || node
    return el?.contains?.(target)
  })
}
function onDocumentMouseDown(event) {
  const target = event.target
  if (event.target?.closest?.('.fb__floating-menu')) return
  if (target?.closest?.('.expedition__tl')) return
  if (userSearchOpen.value && userPickerRoot.value && !userPickerRoot.value.contains(target)) {
    userSearchOpen.value = false
  }
  if (containsTarget(panelRoot.value, target)) return
  if (quickFilterFor.value && containsTarget(quickFilterPopupRoot.value, target)) return

  emit('close')

  const root = pickerRoot.value
  if (root && !root.contains(target)) closePicker()
  if (quickFilterFor.value && !containsTarget(quickFilterPopupRoot.value, target)) {
    quickFilterFor.value = null
  }
}
onMounted(() => {
  document.addEventListener('mousedown', onDocumentMouseDown, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
  if (userSearchTimer) window.clearTimeout(userSearchTimer)
})
async function onPick(master) {
  await attachMaster(master)
}

function basemapSwatchFor(styleId) {
  switch (styleId) {
    case 'ofm-dark': return 'vector-dark'
    case 'carto-light': case 'carto-light-nolabels': return 'raster-light'
    case 'carto-dark': case 'carto-dark-nolabels': return 'raster-dark'
    case 'esri-imagery': return 'satellite'
    default: return 'vector'
  }
}

onMounted(() => {
  if (layerStore.masters.length === 0) {
    layerStore.loadMasters().catch((e) =>
      console.error('[expedition] loadMasters failed', e)
    )
  }
  if (mapStore.recent.length === 0) {
    mapStore.refreshMaps().catch((e) =>
      console.error('[expedition] refreshMaps failed', e)
    )
  }
})
watch(activeMapName, () => {
  if (layerStore.masters.length === 0) layerStore.loadMasters()
  mapTitleDraft.value = mapStore.activeMap?.map?.title || ''
  normalizeSharedUsers(mapStore.sharedUsers || [])
})
watch(
  () => mapStore.activeMap?.map?.title,
  (title) => { mapTitleDraft.value = title || '' },
  { immediate: true }
)
watch(
  () => mapStore.sharedUsers,
  (rows) => { normalizeSharedUsers(rows || []) },
  { deep: true, immediate: true }
)

// ---- Map Cards ----
// Cards snapshot the current layer set (enabled state + filters) and
// are stored in localStorage via the ui store. Applying a card calls
// updateLayer for each layer whose state differs from the snapshot.
const cardsOpen = ref(true)
const newCardTitle = ref('')
const saveCardOpen = ref(false)
const cardApplying = ref(null)  // card id currently being applied

function openSaveCard() {
  newCardTitle.value = ''
  saveCardOpen.value = true
}

function saveCard() {
  const title = newCardTitle.value.trim()
  if (!title) return
  const layerStates = activeLayers.value.map((l) => ({
    name: l.name,
    title: l.title || l.name,
    enabled: l.enabled,
    filter_json: l.filter_json || '',
  }))
  ui.saveMapCard(title, layerStates)
  newCardTitle.value = ''
  saveCardOpen.value = false
}

async function applyCard(card) {
  if (cardApplying.value) return
  cardApplying.value = card.id
  try {
    const ops = card.layers.map((state) => {
      const current = activeLayers.value.find((l) => l.name === state.name)
      if (!current) return null
      const updates = {}
      if (current.enabled !== state.enabled) updates.enabled = state.enabled
      if (state.filter_json !== undefined && current.filter_json !== state.filter_json) {
        updates.filter_json = state.filter_json
      }
      if (Object.keys(updates).length === 0) return null
      return layerStore.updateLayer(state.name, updates)
    }).filter(Boolean)
    await Promise.all(ops)
  } catch (e) {
    console.error('[expedition] applyCard failed', e)
  } finally {
    cardApplying.value = null
  }
}

function deleteCard(card) {
  ui.deleteMapCard(card.id)
}

// Cards filtered to the active map (cards have no map binding — show all).
const allCards = computed(() => ui.mapCards || [])

defineExpose({})
</script>

<template>
  <aside ref="panelRoot" class="lp" role="region" aria-label="Map workspace">
    <header class="lp__header">
      <div class="lp__title-wrap">
        <div class="lp__title">{{ activeMap?.title || 'Untitled map' }}</div>
        <div class="lp__subtitle">{{ activeSubtitle }}</div>
      </div>
      <button
        v-if="canEdit"
        class="lp__save"
        type="button"
        :disabled="mapSaving"
        title="Save map"
        @click="saveActiveMap"
      >
        {{ mapSaving ? 'Saving' : 'Save' }}
      </button>
      <button class="lp__close" type="button" aria-label="Close map workspace" @click="$emit('close')">×</button>
    </header>

    <div class="lp__body">
      <section class="lp__section lp__section--map">
        <div class="lp__map-card">
          <input
            v-model="mapTitleDraft"
            class="lp__map-title-input"
            type="text"
            :readonly="!canEdit"
            aria-label="Map name"
            @keydown.enter.prevent="saveActiveMap"
          />
          <div class="lp__map-actions">
            <button type="button" class="lp__action lp__action--primary" @click="createOpen = !createOpen">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
              <span>New</span>
            </button>
            <button v-if="canShare" type="button" class="lp__action" @click="shareOpen = !shareOpen">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 12h8M16 8l4 4-4 4M4 4h8v16H4z" /></svg>
              <span>Share</span>
            </button>
          </div>

          <div v-if="createOpen" class="lp__subpanel">
            <input
              v-model="newMapTitle"
              class="lp__picker-input"
              type="text"
              placeholder="Blank map name"
              aria-label="Blank map name"
              @keydown.enter.prevent="createBlankMap"
            />
            <button
              type="button"
              class="lp__toggle-row"
              :class="{ 'lp__toggle-row--on': newMapPublic }"
              :aria-pressed="newMapPublic ? 'true' : 'false'"
              @click="newMapPublic = !newMapPublic"
            >
              <span class="lp__toggle-knob" aria-hidden="true" />
              <span>Public</span>
            </button>
            <button type="button" class="lp__wide-primary" :disabled="mapSaving" @click="createBlankMap">
              Create blank map
            </button>
          </div>

          <div v-if="shareOpen && canShare" class="lp__subpanel">
            <div class="lp__share-title">Share {{ activeMap?.title || 'this map' }} with</div>
            <div class="lp__share-grid">
              <div class="lp__share-head">User</div>
              <div class="lp__share-head">Read</div>
              <div class="lp__share-head">Write</div>
              <div class="lp__share-head">Share</div>
              <div class="lp__share-head"></div>

              <div class="lp__share-person lp__share-person--everyone">Everyone</div>
              <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': !!activeMap?.is_public }" :aria-pressed="activeMap?.is_public ? 'true' : 'false'" @click="setEveryoneRead(!activeMap?.is_public)"><span /></button>
              <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': activeMap?.is_public && activeMap?.public_access === 'Writable' }" :aria-pressed="activeMap?.is_public && activeMap?.public_access === 'Writable' ? 'true' : 'false'" @click="setEveryoneWrite(!(activeMap?.is_public && activeMap?.public_access === 'Writable'))"><span /></button>
              <button type="button" class="lp__share-check" disabled aria-pressed="false"><span /></button>
              <div></div>

              <div ref="userPickerRoot" class="lp__user-picker">
                <input
                  v-model="userQuery"
                  class="lp__share-input"
                  type="text"
                  placeholder="Begin typing for results."
                  aria-label="Share with user"
                  autocomplete="off"
                  @focus="searchUsers(userQuery)"
                  @input="scheduleUserSearch"
                  @keydown.escape="userSearchOpen = false"
                />
                <div v-if="userSearchOpen" class="lp__user-pop">
                  <button v-for="user in userOptions" :key="user.value" type="button" class="lp__user-option" @mousedown.prevent="addSharedUser(user)">
                    <span class="lp__user-main">{{ user.label || user.value }}</span>
                    <span v-if="user.description" class="lp__user-meta">{{ user.description }}</span>
                  </button>
                  <p v-if="!userOptions.length" class="lp__empty lp__empty--tight">
                    {{ userSearchLoading ? 'Searching…' : 'No users found.' }}
                  </p>
                </div>
              </div>
              <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': shareDraftCanRead }" :aria-pressed="shareDraftCanRead ? 'true' : 'false'" @click="shareDraftCanRead = !shareDraftCanRead"><span /></button>
              <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': shareDraftCanWrite }" :aria-pressed="shareDraftCanWrite ? 'true' : 'false'" @click="shareDraftCanWrite = !shareDraftCanWrite"><span /></button>
              <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': shareDraftCanShare }" :aria-pressed="shareDraftCanShare ? 'true' : 'false'" @click="shareDraftCanShare = !shareDraftCanShare"><span /></button>
              <button type="button" class="lp__add-share" :disabled="!shareDraftUser || !shareDraftCanRead" @click="upsertSharedUser">Add</button>

              <template v-for="user in shareUsers" :key="user.user">
                <button type="button" class="lp__share-person" @click="editShareRow(user)">{{ user.label || user.user }}</button>
                <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': user.canRead }" :aria-pressed="user.canRead ? 'true' : 'false'" @click="updateShareFlag(user.user, 'canRead', !user.canRead)"><span /></button>
                <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': user.canWrite }" :aria-pressed="user.canWrite ? 'true' : 'false'" @click="updateShareFlag(user.user, 'canWrite', !user.canWrite)"><span /></button>
                <button type="button" class="lp__share-check" :class="{ 'lp__share-check--on': user.canShare }" :aria-pressed="user.canShare ? 'true' : 'false'" @click="updateShareFlag(user.user, 'canShare', !user.canShare)"><span /></button>
                <button type="button" class="lp__remove-user" aria-label="Remove user" @click="removeSharedUser(user.user)">×</button>
              </template>
            </div>
            <button type="button" class="lp__wide-primary" :disabled="mapSaving" @click="saveShares">
              Update shares
            </button>
          </div>
          <p v-if="mapError" class="lp__error">{{ mapError }}</p>
        </div>
      </section>

      <section class="lp__section">
        <button class="lp__section-toggle" type="button" @click="mapsOpen = !mapsOpen">
          <span>Maps</span>
          <span class="lp__chevron" :data-open="mapsOpen">▾</span>
        </button>
        <div v-if="mapsOpen" class="lp__maps-body">
          <input v-model="mapQuery" type="text" class="lp__picker-input" placeholder="Search maps…" aria-label="Search maps" />
          <ul v-if="filteredMaps.length" class="lp__list lp__map-list">
            <li v-for="m in filteredMaps" :key="'m-' + m.name" class="lp__item lp__item--map">
              <button type="button" class="lp__row lp__row--map" :class="{ 'lp__row--active': m.name === activeMapName }" @click="openMap(m.name)">
                <span class="lp__map-swatch" :data-kind="basemapSwatchFor(m.basemap_style)" />
                <span class="lp__row-main">
                  <span class="lp__row-title">{{ m.title || m.name }}</span>
                  <span class="lp__row-meta">{{ accessLabel(m) }}</span>
                </span>
                <span class="lp__access-dot" :data-kind="accessKind(m)" />
              </button>
            </li>
          </ul>
          <p v-else class="lp__empty">No maps found.</p>
        </div>
      </section>

      <!-- Templates — self-hides when there are no templates. Kept as
           a future-proof section: when public templates exist (or the
           user clones one), they surface here without panel rework. -->
      <section v-if="templates.length" class="lp__section">
        <button class="lp__section-toggle" type="button" @click="templatesOpen = !templatesOpen">
          <span>Templates</span>
          <span class="lp__chevron" :data-open="templatesOpen">▾</span>
        </button>
        <ul v-if="templatesOpen" class="lp__list">
          <li
            v-for="t in templates"
            :key="'t-' + t.name"
            class="lp__row lp__row--template"
          >
            <span class="lp__map-swatch" :data-kind="basemapSwatchFor(t.basemap_style)" />
            <span class="lp__row-main" @click="openMap(t.name)">
              <span class="lp__row-title">{{ t.title }}</span>
              <span v-if="t.template_category" class="lp__row-meta">{{ t.template_category }}</span>
            </span>
            <button type="button" class="lp__clone" title="Clone this template into your account"
                    @click="mapStore.cloneTemplate(t.name, t.title)">
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <path d="M9 4h11v11 M20 4l-9 9 M15 14v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"
                      fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </li>
        </ul>
      </section>

      <!-- Layers: inline add-picker at the top, then the active
           layers list. The picker dropdown is position:absolute
           under the input — the layer rows below stay put. -->
      <section class="lp__section">
        <div class="lp__section-bar">
          <button class="lp__section-toggle" type="button" @click="layersOpen = !layersOpen">
            <span>Layers</span>
            <span class="lp__chevron" :data-open="layersOpen">▾</span>
          </button>
          <button
            v-if="canEdit"
            type="button"
            class="lp__create"
            title="Create layer"
            aria-label="Create layer"
            @click="createLayer"
          >
            +
          </button>
        </div>

        <div v-if="layersOpen" class="lp__layers-body">
          <!-- Picker only renders for users who can edit the map.
               Read-only viewers don't get a disabled grey box; the
               picker just isn't there. -->
          <div v-if="canEdit" ref="pickerRoot" class="lp__picker">
            <input
              v-model="pickerQuery"
              type="text"
              class="lp__picker-input"
              placeholder="+ Add a layer…"
              aria-label="Add a layer to this map"
              @focus="pickerOpen = true"
              @keydown.escape="closePicker"
            />
            <div v-show="pickerOpen" class="lp__picker-pop">
                <div v-if="pickerGroups.length">
                  <div v-for="[doctype, items] in pickerGroups" :key="doctype" class="lp__group">
                    <div class="lp__group-label">{{ doctype }}</div>
                    <ul class="lp__list">
                      <li v-for="m in items" :key="m.name" class="lp__item">
                        <button
                          type="button"
                          class="lp__row lp__row--picker"
                          @mousedown.prevent="onPick(m)"
                        >
                          <span class="lp__swatch" :style="{ background: m.color || '#3B82F6' }">
                            <svg v-if="m.icon" class="lp__swatch-icon" viewBox="0 0 24 24" aria-hidden="true">
                              <path :d="iconPath(m.icon)" fill="currentColor" />
                            </svg>
                          </span>
                          <span class="lp__label">{{ m.title }}</span>
                        </button>
                      </li>
                    </ul>
                  </div>
                </div>
                <p v-else class="lp__empty">
                  {{ pickerQuery.trim() ? 'No matching layers.' : 'No more layers to add.' }}
                </p>
              </div>
          </div>

          <div v-if="activeLayers.length" class="lp__divider" />

          <ul v-if="activeLayers.length" class="lp__list">
            <li v-for="layer in activeLayers" :key="layer.name" class="lp__item" :style="{ '--exp-layer-color': layer.color || '#F59E0B' }">
              <div class="lp__row lp__row--layer">
                <button
                  type="button"
                  class="lp__visibility-toggle"
                  :class="{ 'lp__visibility-toggle--on': layer.enabled !== false && layer.enabled !== 0 }"
                  :aria-pressed="layer.enabled !== false && layer.enabled !== 0 ? 'true' : 'false'"
                  :aria-label="(layer.enabled !== false && layer.enabled !== 0 ? 'Hide' : 'Show') + ' ' + (layer.title || layer.name)"
                  @click="toggleLayer(layer.name, !(layer.enabled !== false && layer.enabled !== 0))"
                >
                  <span />
                </button>
                <span class="lp__swatch" :style="{ background: layer.color || '#3B82F6' }">
                  <svg v-if="layer.icon" class="lp__swatch-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path :d="iconPath(layer.icon)" fill="currentColor" />
                  </svg>
                </span>
                <span class="lp__label">{{ layer.title || layer.name }}</span>
              </div>
              <div class="lp__row-actions">
                <button
                  class="lp__row-icon"
                  type="button"
                  :class="{ 'lp__row-icon--on': ui.isHeatmapOn(layer.name) }"
                  :aria-pressed="ui.isHeatmapOn(layer.name) ? 'true' : 'false'"
                  :aria-label="(ui.isHeatmapOn(layer.name) ? 'Disable' : 'Enable') + ' heatmap for ' + (layer.title || layer.name)"
                  :title="ui.isHeatmapOn(layer.name) ? 'Heatmap on — click to show pins' : 'Show as heatmap'"
                  @click="toggleHeatmap(layer.name)"
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" fill="currentColor" opacity="0.25" />
                    <circle cx="12" cy="12" r="5" fill="currentColor" opacity="0.55" />
                    <circle cx="12" cy="12" r="2" fill="currentColor" opacity="1" />
                  </svg>
                </button>
                <div class="lp__radius-wrap">
                  <button
                    class="lp__row-icon"
                    type="button"
                    :class="{ 'lp__row-icon--on': ui.isRadiusOn(layer.name) }"
                    :aria-pressed="ui.isRadiusOn(layer.name) ? 'true' : 'false'"
                    :aria-label="(ui.isRadiusOn(layer.name) ? 'Disable' : 'Enable') + ' radius halo for ' + (layer.title || layer.name)"
                    :title="ui.isRadiusOn(layer.name) ? ('Radius halo on' + (ui.radiusField[layer.name] ? ' · ' + ui.radiusField[layer.name] : '')) : 'Show radius halo'"
                    @click="toggleRadius(layer.name); toggleRadiusPicker(layer.name)"
                  >
                    <!-- Radius glyph: solid disk with concentric outer ring. -->
                    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-dasharray="2 2" />
                      <circle cx="12" cy="12" r="3" fill="currentColor" />
                    </svg>
                  </button>
                  <div v-if="radiusPickerFor === layer.name && ui.isRadiusOn(layer.name)" class="lp__radius-pop">
                    <div class="lp__radius-head">Radius field</div>
                    <button
                      type="button"
                      class="lp__radius-opt"
                      :class="{ 'lp__radius-opt--on': !ui.radiusField[layer.name] }"
                      @click="setRadiusField(layer.name, '')"
                    >— fixed (use slider)</button>
                    <button
                      v-for="f in numericFieldsFor(layer.name)"
                      :key="f"
                      type="button"
                      class="lp__radius-opt"
                      :class="{ 'lp__radius-opt--on': ui.radiusField[layer.name] === f }"
                      @click="setRadiusField(layer.name, f)"
                    >{{ f }}</button>
                    <div v-if="!ui.radiusField[layer.name]" class="lp__radius-row">
                      <span class="lp__radius-row-label">Size</span>
                      <input
                        type="range" min="500" max="20000" step="500"
                        :value="ui.radiusMeters[layer.name] || 5000"
                        @input="(e) => ui.setRadiusMeters(layer.name, e.target.value)"
                        class="lp__radius-slider"
                        aria-label="Halo size"
                      />
                      <span class="lp__radius-row-val">{{ ui.radiusMeters[layer.name] || 5000 }}</span>
                    </div>
                  </div>
                </div>
                <div class="lp__qf-wrap">
                  <button
                    class="lp__row-icon"
                    type="button"
                    :class="{ 'lp__row-icon--on': !!layer.filter_json }"
                    :aria-pressed="!!layer.filter_json ? 'true' : 'false'"
                    :aria-label="'Quick filter ' + (layer.title || layer.name)"
                    :title="layer.filter_json ? 'Filter active - click to edit' : 'Add a quick filter'"
                    @click.stop="openQuickFilter(layer, $event)"
                  >
                    <!-- Filter glyph: funnel. -->
                    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                      <path d="M3 5h18l-7 9v6l-4-2v-4z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
                    </svg>
                    <span v-if="filterBadge(layer)" class="lp__filter-badge">{{ filterBadge(layer) }}</span>
                  </button>
                </div>
                <button class="lp__row-edit" type="button" @click="editLayer(layer)" aria-label="'Edit ' + (layer.title || layer.name)">edit</button>
              </div>
              <Teleport to="body">
                <div
                  v-if="quickFilterFor === layer.name"
                  ref="quickFilterPopupRoot"
                  class="lp__qf-pop"
                  :style="quickFilterPopoverStyle"
                  @click.stop
                >
                  <FilterBuilder
                    v-model="quickFilterDraft"
                    :source-doctype="layer.source_doctype"
                    compact
                    title="Layer filters"
                    @schema-loaded="(schema) => setQuickFilterSchema(layer.name, schema)"
                  />
                  <div v-if="filterSummaries(layer).length" class="lp__qf-chips">
                    <span v-for="summary in filterSummaries(layer)" :key="summary" class="lp__qf-chip">{{ summary }}</span>
                  </div>
                  <div class="lp__qf-actions">
                    <button type="button" class="lp__qf-btn lp__qf-btn--ghost" @click="cancelQuickFilter">Cancel</button>
                    <button type="button" class="lp__qf-btn lp__qf-btn--primary" @click="saveQuickFilter(layer)">Save</button>
                  </div>
                  <p v-if="!layer.source_doctype" class="lp__qf-note">No source DocType configured for this layer.</p>
                </div>
              </Teleport>
            </li>
          </ul>
          <p v-else class="lp__empty">No layers yet.</p>
        </div>
      </section>

      <!-- Map Cards — saved layer presets. -->
      <section class="lp__section">
        <div class="lp__section-bar">
          <button class="lp__section-toggle" type="button" @click="cardsOpen = !cardsOpen">
            <span>Cards</span>
            <span class="lp__chevron" :data-open="cardsOpen">▾</span>
          </button>
          <button class="lp__create" type="button" title="Save current layers as a card" @click="openSaveCard">+</button>
        </div>

        <div v-if="cardsOpen" class="lp__cards-body">
          <!-- Save-card inline form -->
          <div v-if="saveCardOpen" class="lp__subpanel">
            <input
              v-model="newCardTitle"
              class="lp__picker-input"
              type="text"
              placeholder="Card name (e.g. North Sales)"
              aria-label="Card name"
              autofocus
              @keydown.enter.prevent="saveCard"
              @keydown.escape.prevent="saveCardOpen = false"
            />
            <div style="display:flex;gap:6px;margin-top:6px">
              <button type="button" class="lp__qf-btn lp__qf-btn--ghost" @click="saveCardOpen = false">Cancel</button>
              <button type="button" class="lp__qf-btn lp__qf-btn--primary" :disabled="!newCardTitle.trim()" @click="saveCard">Save</button>
            </div>
          </div>

          <ul v-if="allCards.length" class="lp__list lp__cards-list">
            <li v-for="card in allCards" :key="card.id" class="lp__item lp__item--card">
              <button
                type="button"
                class="lp__card-row"
                :class="{ 'lp__card-row--applying': cardApplying === card.id }"
                :title="'Apply: ' + card.title"
                :disabled="!!cardApplying"
                @click="applyCard(card)"
              >
                <svg class="lp__card-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 6h16M4 10h16M4 14h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                </svg>
                <span class="lp__card-title">{{ card.title }}</span>
                <span class="lp__card-meta">{{ card.layers.length }} layer{{ card.layers.length !== 1 ? 's' : '' }}</span>
              </button>
              <button type="button" class="lp__remove-user lp__card-del" aria-label="Delete card" @click.stop="deleteCard(card)">×</button>
            </li>
          </ul>
          <p v-else class="lp__empty">No cards saved yet.</p>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.lp {
  width: 300px;
  /* Fit panel to content height. We use a max-height of viewport minus
     safe margins so very tall lists still scroll, but a short list
     leaves the panel sitting at its natural size (no full-page bar). */
  max-height: calc(100vh - 24px);
  background: rgba(11, 14, 20, 0.86);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  color: #E6E8EC;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  pointer-events: auto;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
}
.lp__header {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 12px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex: none;
}
.lp__title-wrap { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.lp__title { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lp__subtitle { font-size: 10px; color: rgba(230, 232, 236, 0.5); text-transform: uppercase; letter-spacing: 0.08em; }
.lp__save {
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(59, 130, 246, 0.36);
  background: rgba(59, 130, 246, 0.18);
  color: #DBEAFE;
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  flex: none;
}
.lp__save:hover { background: rgba(59, 130, 246, 0.28); color: #fff; }
.lp__save:disabled { opacity: 0.6; cursor: wait; }
.lp__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1; border-radius: 5px;
  flex: none;
}
.lp__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }

.lp__clone {
  background: transparent;
  border: 0;
  color: rgba(230, 232, 236, 0.5);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  flex: none;
}
.lp__clone:hover {
  background: rgba(59, 130, 246, 0.15);
  color: #93C5FD;
}
.lp__row--template { display: flex; align-items: center; gap: 8px; }

/* Quick filter popover (inline filter editor on each layer row). */
.lp__qf-wrap { position: relative; }
.lp__qf-pop {
  position: fixed;
  z-index: 80;
  box-sizing: border-box;
  overflow: hidden;
  --fb-compact-rows-max-height: 154px;
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  font-feature-settings: 'cv11', 'ss01', 'ss03';
  background: rgba(11, 14, 20, 0.95);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 10px;
  padding: 8px;
  display: flex; flex-direction: column; gap: 6px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.50);
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}
.lp__qf-pop:hover { scrollbar-color: rgba(255, 255, 255, 0.32) transparent; }
.lp__qf-pop::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.lp__qf-pop::-webkit-scrollbar-track { background: transparent; }
.lp__qf-pop::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}
.lp__qf-pop:hover::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.32); }
.lp__qf-pop::-webkit-scrollbar-thumb:vertical { min-height: 24px; }
.lp__qf-head {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.6); font-weight: 500;
}
.lp__qf-row { display: flex; gap: 4px; }
.lp__qf-input {
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px; color: #E6E8EC;
  padding: 5px 7px; font-size: 11px;
  font-family: inherit; outline: none;
  text-transform: none; letter-spacing: 0; font-weight: 400;
  min-width: 0;
}
.lp__qf-input:focus { border-color: #3B82F6; }
.lp__qf-input--field { flex: 1; }
.lp__qf-input--op { width: 64px; flex: none; }
.lp__qf-input--val { flex: 1; }
.lp__qf-actions { display: flex; gap: 4px; justify-content: flex-end; margin-top: 2px; }
.lp__qf-btn {
  padding: 4px 8px; border-radius: 5px;
  font-size: 11px; cursor: pointer; font-family: inherit; font-weight: 500;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #E6E8EC;
}
.lp__qf-btn--primary { background: #3B82F6; border-color: #3B82F6; color: #fff; }
.lp__qf-btn--ghost { background: transparent; }
.lp__qf-note {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  margin: 0;
}
.lp__qf-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding-top: 2px;
}
.lp__qf-chip {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 5px;
  padding: 3px 6px;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.22);
  color: rgba(219, 234, 254, 0.92);
  font-size: 10px;
}

.lp__body { padding: 6px 0; overflow-x: hidden; overflow-y: auto; flex: 1; min-width: 0; }
.lp__section { padding: 5px 8px 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.lp__section--map { padding-top: 6px; }
.lp__map-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  background: transparent;
  border: 0;
}
.lp__map-title-input {
  width: 100%;
  box-sizing: border-box;
  border: 0;
  border-radius: 6px;
  outline: none;
  background: rgba(0, 0, 0, 0.26);
  color: #fff;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 6px 8px;
}
.lp__map-title-input:focus { box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.62); }
.lp__map-title-input[readonly] { color: rgba(230, 232, 236, 0.82); }
.lp__map-actions {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
}
.lp__action {
  height: 26px;
  width: auto;
  min-width: 0;
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.075);
  background: rgba(0, 0, 0, 0.18);
  color: rgba(230, 232, 236, 0.88);
  font-family: inherit;
  font-size: 10.5px;
  font-weight: 600;
  cursor: pointer;
}
.lp__action:hover { background: rgba(255, 255, 255, 0.075); color: #fff; }
.lp__action--primary {
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.32);
  color: #DBEAFE;
}
.lp__action svg {
  width: 13px;
  height: 13px;
  flex: none;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.lp__subpanel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0 0;
  background: transparent;
  border: 0;
}
.lp__toggle-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 28px;
  width: fit-content;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(230, 232, 236, 0.82);
  padding: 5px 9px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.lp__toggle-row--on {
  border-color: rgba(59, 130, 246, 0.62);
  background: rgba(59, 130, 246, 0.14);
  color: #fff;
}
.lp__toggle-knob {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(230, 232, 236, 0.38);
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.08);
}
.lp__toggle-row--on .lp__toggle-knob {
  background: #3B82F6;
}
.lp__wide-primary {
  width: 100%;
  height: 26px;
  border-radius: 6px;
  border: 1px solid rgba(59, 130, 246, 0.38);
  background: #2563EB;
  color: #fff;
  font-family: inherit;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}
.lp__wide-primary:hover { background: #3B82F6; }
.lp__wide-primary:disabled { opacity: 0.62; cursor: wait; }
.lp__segment {
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 3px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.07);
}
.lp__segment-btn {
  height: 23px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.64);
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.lp__segment-btn--on {
  background: rgba(59, 130, 246, 0.22);
  color: #DBEAFE;
}
.lp__maps-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0 0;
}
.lp__map-list {
  max-height: 178px;
  overflow-y: auto;
  padding-right: 2px;
}
.lp__user-picker { position: relative; }
.lp__user-pop {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 35;
  max-height: 146px;
  overflow-y: auto;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(11, 14, 20, 0.97);
  box-shadow: 0 12px 34px rgba(0, 0, 0, 0.42);
}
.lp__user-option {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 1px;
  border: 0;
  background: transparent;
  color: #E6E8EC;
  padding: 6px 8px;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
}
.lp__user-option:hover { background: rgba(59, 130, 246, 0.14); }
.lp__user-main,
.lp__user-meta,
.lp__share-user {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lp__user-main { font-size: 11px; }
.lp__user-meta { font-size: 10px; color: rgba(230, 232, 236, 0.48); }
.lp__share-title {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.55);
  padding: 2px 0 0;
}
.lp__share-grid {
  display: grid;
  grid-template-columns: minmax(112px, 1fr) 34px 38px 38px 32px;
  align-items: center;
  gap: 4px;
}
.lp__share-head {
  color: rgba(230, 232, 236, 0.48);
  font-size: 9.5px;
  text-align: center;
  white-space: nowrap;
}
.lp__share-head:first-child { text-align: left; }
.lp__share-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  justify-self: center;
  width: 26px;
  height: 26px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(230, 232, 236, 0.82);
  padding: 0;
  cursor: pointer;
}
.lp__share-check span {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  background: rgba(230, 232, 236, 0.22);
}
.lp__share-check--on {
  border-color: rgba(59, 130, 246, 0.6);
  background: rgba(59, 130, 246, 0.14);
}
.lp__share-check--on span {
  background: #3B82F6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.18);
}
.lp__share-check:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.lp__share-input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 6px;
  color: #E6E8EC;
  padding: 6px 8px;
  font-size: 11px;
  font-family: inherit;
  outline: none;
}
.lp__share-input:focus { border-color: rgba(59, 130, 246, 0.6); }
.lp__add-share {
  height: 24px;
  padding: 0 7px;
  border-radius: 6px;
  border: 1px solid rgba(59, 130, 246, 0.34);
  background: rgba(59, 130, 246, 0.18);
  color: #DBEAFE;
  font-family: inherit;
  font-size: 10.5px;
  font-weight: 600;
  cursor: pointer;
}
.lp__add-share:disabled {
  opacity: 0.46;
  cursor: not-allowed;
}
.lp__share-person {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  height: 26px;
  border: 0;
  background: transparent;
  padding: 0;
  text-align: left;
  font-family: inherit;
  font-size: 11px;
  color: rgba(230, 232, 236, 0.88);
  cursor: pointer;
}
.lp__share-person--everyone {
  cursor: default;
  font-weight: 600;
  color: #E6E8EC;
}
.lp__share-person:hover:not(.lp__share-person--everyone) { color: #fff; }
.lp__remove-user {
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.46);
  cursor: pointer;
  font-size: 14px;
}
.lp__remove-user:hover { background: rgba(255, 255, 255, 0.07); color: #fff; }
.lp__error {
  margin: 0;
  padding: 7px 8px;
  border-radius: 7px;
  background: rgba(239, 68, 68, 0.12);
  color: #FCA5A5;
  font-size: 11px;
}

/* Inline add-layer picker. The popover is in-flow (not absolute)
   so it doesn't compete with the panel's own scrollbar. The
   max-height caps the dropdown at ~5 rows; anything beyond that
   scrolls inside the popover. The popover animates open/close via
   <Transition name="lp-picker"> so the layer-row shift reads as
   intentional instead of a layout jump. */
.lp__layers-body { display: flex; flex-direction: column; min-width: 0; }
.lp__picker { padding: 4px 0 6px; min-width: 0; }
.lp__picker-input {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  /* border-box so 7px/10px padding is included in width: 100%.
     Without this, the input spills 20px past the panel edge and
     triggers horizontal scroll on .lp__body. Vue's scoped styles
     don't reset box-sizing globally — set it where it matters. */
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 6px;
  color: #E6E8EC;
  padding: 7px 10px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
  transition: border-color 100ms ease;
  text-overflow: ellipsis;
}
.lp__picker-input:focus { border-color: rgba(59, 130, 246, 0.6); }
.lp__picker-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: rgba(0, 0, 0, 0.18);
}
.lp__picker-pop {
  margin-top: 6px;
  max-height: 148px;
  overflow-y: auto;
  background: rgba(11, 14, 20, 0.96);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  box-sizing: border-box;
}
.lp__row--picker {
  display: flex; align-items: center; gap: 8px;
  width: 100%;
  min-width: 0;
  padding: 6px 10px;
  background: transparent;
  border: 0;
  color: inherit;
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  border-radius: 0;
}
.lp__row--picker:hover { background: rgba(59, 130, 246, 0.12); }
.lp__row--picker:disabled { opacity: 0.5; cursor: not-allowed; }
.lp__divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.06);
  margin: 6px 4px 4px;
}
.lp__section:last-child { border-bottom: 0; }
.lp__section-bar { display: flex; align-items: center; justify-content: space-between; }
.lp__section-toggle {
  flex: 1; display: flex; align-items: center; justify-content: space-between;
  padding: 6px 8px; background: transparent; border: 0; color: rgba(230, 232, 236, 0.5);
  font-family: inherit; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  cursor: pointer; font-weight: 500; border-radius: 6px;
}
.lp__section-toggle:hover { background: rgba(255, 255, 255, 0.04); color: rgba(230, 232, 236, 0.8); }
.lp__create {
  width: 24px;
  height: 24px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(59, 130, 246, 0.14);
  border: 1px solid rgba(59, 130, 246, 0.26);
  border-radius: 6px;
  color: #DBEAFE;
  cursor: pointer;
  font-family: inherit;
  font-size: 17px;
  line-height: 1;
}
.lp__create:hover {
  background: rgba(59, 130, 246, 0.26);
  color: #fff;
}
.lp__chevron { font-size: 10px; transition: transform 150ms ease; opacity: 0.6; }
.lp__chevron[data-open="true"] { transform: rotate(180deg); }

.lp__list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 1px; }
.lp__row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 7px;
  cursor: pointer; font-size: 12px;
  transition: background 80ms ease;
  min-width: 0;
}
.lp__item:hover .lp__row { background: rgba(255, 255, 255, 0.05); }
.lp__row--active { background: rgba(59, 130, 246, 0.10); box-shadow: inset 2px 0 0 #3B82F6; }
.lp__row--map {
  width: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
}
.lp__row--layer { cursor: default; }
.lp__visibility-toggle {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  padding: 0;
  cursor: pointer;
}
.lp__visibility-toggle span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(230, 232, 236, 0.28);
}
.lp__visibility-toggle--on {
  border-color: rgba(59, 130, 246, 0.6);
  background: rgba(59, 130, 246, 0.14);
}
.lp__visibility-toggle--on span {
  background: #3B82F6;
}
.lp__row-main { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.lp__row-title { font-size: 12px; color: #E6E8EC; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lp__row-meta { font-size: 10px; color: rgba(230, 232, 236, 0.5); }
.lp__row-actions {
  display: inline-flex; align-items: center; gap: 2px;
  flex: none;
  /* Reserve a fixed track so the actions never get squeezed or
     cropped, regardless of label length. */
  min-width: 96px;            /* 4 buttons × 22px + 3 × 2px gap */
  justify-content: flex-end;
}
.lp__row-actions:empty { display: none; }
.lp__row-edit {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.4);
  font-size: 10px; cursor: pointer; padding: 4px 6px; border-radius: 4px;
  font-family: inherit; letter-spacing: 0.04em;
}
.lp__row-edit:hover { color: #fff; background: rgba(255, 255, 255, 0.08); }

/* Heatmap toggle button (PR-7). Sits between the row and the edit
   button. Color matches the layer's pin color when on, dim when off. */
.lp__row-icon {
  background: transparent; border: 0;
  width: 22px; height: 22px;
  position: relative;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 5px; cursor: pointer;
  color: rgba(230, 232, 236, 0.4);
  font-family: inherit;
  flex: none;
  transition: background 100ms ease, color 100ms ease;
}
.lp__row-icon:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.lp__row-icon--on { color: var(--exp-layer-color, #F59E0B); }
.lp__row-icon--on:hover { color: #fff; }
.lp__filter-badge {
  position: absolute;
  top: -3px;
  right: -4px;
  min-width: 13px;
  height: 13px;
  padding: 0 3px;
  box-sizing: border-box;
  border-radius: 999px;
  background: #3B82F6;
  color: #fff;
  font-size: 9px;
  line-height: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* Radius popover (PR-8). Anchored under the radius button. Wraps the
   toggle so its menu is part of the same hit area. */
.lp__radius-wrap { position: relative; }
.lp__radius-pop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 25;
  width: 200px;
  background: rgba(11, 14, 20, 0.96);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 10px;
  padding: 6px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
}
.lp__radius-head {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.5);
  padding: 6px 8px 4px;
}
.lp__radius-opt {
  display: block; width: 100%; text-align: left;
  padding: 6px 8px; border-radius: 6px;
  background: transparent; border: 0; cursor: pointer;
  color: rgba(230, 232, 236, 0.9); font-size: 11px;
  font-family: inherit;
}
.lp__radius-opt:hover { background: rgba(255, 255, 255, 0.06); }
.lp__radius-opt--on { color: #93C5FD; background: rgba(59, 130, 246, 0.10); }
.lp__radius-row {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 8px 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  margin-top: 4px;
}
.lp__radius-row-label {
  font-size: 10px; color: rgba(230, 232, 236, 0.5);
  text-transform: uppercase; letter-spacing: 0.06em;
  flex: none;
}
.lp__radius-slider { flex: 1; accent-color: #3B82F6; }
.lp__radius-row-val { font-size: 11px; color: #fff; font-variant-numeric: tabular-nums; flex: none; min-width: 36px; text-align: right; }

.lp__map-swatch {
  width: 16px; height: 16px; border-radius: 4px; flex: none;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background:
    linear-gradient(135deg, rgba(255,255,255,0.20) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #6B7A99 0%, #2E3A55 100%);
}
.lp__map-swatch[data-kind="vector-dark"] {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #1E2A3A 0%, #0B0E14 100%);
}
.lp__map-swatch[data-kind="raster-light"] {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.20) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #E0E5EC 0%, #B8C0CC 100%);
}
.lp__map-swatch[data-kind="raster-dark"] {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #2A2E3A 0%, #0E0F12 100%);
}
.lp__map-swatch[data-kind="satellite"] {
  background:
    linear-gradient(135deg, rgba(255,255,255,0.20) 0%, rgba(255,255,255,0) 60%),
    linear-gradient(135deg, #3B5530 0%, #1E2A20 100%);
}
.lp__access-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  flex: none;
  background: rgba(230, 232, 236, 0.42);
  box-shadow: 0 0 0 3px rgba(230, 232, 236, 0.05);
}
.lp__access-dot[data-kind="public"] {
  background: #22C55E;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.12);
}
.lp__access-dot[data-kind="shared"] {
  background: #F59E0B;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.12);
}

.lp__swatch {
  width: 12px; height: 12px; border-radius: 3px; flex: none;
  border: 1px solid rgba(0, 0, 0, 0.2);
  display: inline-flex; align-items: center; justify-content: center;
}
.lp__swatch-icon { width: 10px; height: 10px; fill: rgba(255, 255, 255, 0.92); }
.lp__label { font-size: 12px; color: #E6E8EC; flex: 1; min-width: 0; display: flex; flex-direction: column; }
.lp__empty { padding: 10px; font-size: 11px; color: rgba(230, 232, 236, 0.6); margin: 0; }
.lp__empty--tight { padding: 7px 8px; }
.lp__group { margin-bottom: 8px; }
.lp__group:last-child { margin-bottom: 0; }
.lp__group-label { font-size: 10px; color: rgba(230, 232, 236, 0.4); padding: 0 8px 4px; text-transform: lowercase; letter-spacing: 0.02em; }
.lp__item {
  display: grid;
  /* Two equal-sized columns: (1) label cell, (2) actions cell.
     The actions track is fixed-width so the 4 trailing buttons always
     fit and don't crop, regardless of label length. */
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 4px;
  padding: 1px 0;
}
.lp__item--map {
  display: block;
  padding: 1px 0;
}

/* ---- Map Cards ---- */
.lp__cards-body { display: flex; flex-direction: column; gap: 2px; padding: 4px 0 6px; }
.lp__cards-list { gap: 2px; }
.lp__item--card {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 1px 0;
}
.lp__card-row {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border-radius: 7px;
  background: transparent;
  border: 0;
  color: rgba(230, 232, 236, 0.88);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: background 80ms ease;
}
.lp__card-row:hover { background: rgba(59, 130, 246, 0.09); color: #fff; }
.lp__card-row:disabled { opacity: 0.54; cursor: not-allowed; }
.lp__card-row--applying { opacity: 0.72; cursor: wait; }
.lp__card-icon {
  width: 13px;
  height: 13px;
  flex: none;
  color: rgba(148, 163, 184, 0.7);
}
.lp__card-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lp__card-meta {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.36);
  white-space: nowrap;
  flex: none;
}
.lp__card-del {
  flex: none;
  margin-right: 4px;
}
</style>
