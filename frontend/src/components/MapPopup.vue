<script setup>
/**
 * MapPopup — pin-anchored popup card. PR-5 of the quiet-canvas plan.
 *
 * Replaces CommandCard.vue. Behavior:
 *   - Anchored to the clicked pin's screen position via map.project()
 *   - Edge-flip: when the popup would clip the viewport, it flips to
 *     the other side of the pin and re-anchors to the new edge.
 *   - Camera follow: re-positions on map.move / map.zoom.
 *   - Falls back to a top-right docked position if projection fails.
 *   - Esc / × / click outside the popup closes it.
 *
 * Sections:
 *   - Header: title + close
 *   - Quick actions: open form, schedule visit, log activity
 *   - Custom popup_template (if any) OR property table
 *   - Visit history (Expedition Activity rows linked to the source doc)
 *
 * Width: 320px. Same glass as the panels.
 */
import { onMounted, onBeforeUnmount, ref, computed, watch, nextTick } from 'vue'
import { call } from '../api/client.js'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'

const ui = useUiStore()
const mapStore = useMapStore()

const cardEl = ref(null)
// screen position of the popup's anchor point in CSS pixels (relative
// to the .expedition container, which is the viewport). edge:
// 'left' | 'right' — which side of the pin the card is on.
const screen = ref(null)
const edge = ref('left')

// Visit history (Expedition Activity rows linked to the same source doc).
const history = ref([])
const historyLoading = ref(false)
const historyError = ref('')
// Aggregated stats: counts by year (active vs passive).
const aggregate = ref(null)
const aggregateLoading = ref(false)
const actionError = ref('')
const todoBusy = ref(false)
const assignBusy = ref(false)
const assignField = ref('')
const assignUser = ref('')
const userOptions = ref([])
const userSearchLoading = ref(false)
const userSearchOpen = ref(false)
const activeTab = ref('details')
const fieldSearch = ref('')
const showMoreFields = ref(false)
const showAssignPanel = ref(false)
let userSearchTimer = null

const feature = computed(() => ui.selectedFeature)
const layer = computed(() => {
  const f = feature.value
  return f ? f.layer || {} : {}
})
const clickAction = computed(() => layer.value.click_action || 'popup')
const sourceDoctype = computed(() => {
  const f = feature.value
  return f && f.properties && f.properties._doctype
})
const sourceName = computed(() => {
  const f = feature.value
  return f && f.properties && f.properties._name
})

function close() { ui.selectedFeature = null }
function onKey(e) { if (e.key === 'Escape' && feature.value) close() }

function recompute() {
  const sel = ui.selectedFeature
  if (!sel || !sel._lngLat) { screen.value = null; return }
  const m = window.expeditionMap?.getMap?.()
  if (!m) return
  const p = m.project([sel._lngLat.lng, sel._lngLat.lat])
  // p is in CSS pixels relative to the map container, which is the
  // viewport (.expedition has fixed inset: 0).
  const w = 360
  const margin = 12
  const cardH = 220 // estimated; recomputed after mount
  const vw = window.innerWidth
  const vh = window.innerHeight
  // Default: card opens to the right of the pin.
  let openRight = true
  let left = p.x + 16
  if (left + w + margin > vw) {
    // Flip to the left side of the pin.
    openRight = false
    left = p.x - w - 16
    if (left < margin) {
      // Pin is too close to both edges — dock to the side that has
      // more room.
      if (p.x < vw / 2) {
        openRight = true
        left = Math.max(margin, p.x + 16)
      } else {
        openRight = false
        left = Math.min(vw - w - margin, p.x - w - 16)
      }
    }
  }
  let top = p.y - 24
  // Clamp vertically so the card never clips top/bottom. Allow the
  // user to scroll inside the card if its content is taller.
  top = Math.max(margin, Math.min(top, vh - cardH - margin))
  edge.value = openRight ? 'left' : 'right'
  screen.value = { top, left }
}

// After the card mounts, measure its real height and clamp again.
const measuredH = ref(0)
function measure() {
  if (!cardEl.value) return
  const h = cardEl.value.offsetHeight
  if (h !== measuredH.value) {
    measuredH.value = h
    recompute()
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await nextTick()
  measure()
  recompute()
  const m = window.expeditionMap?.getMap?.()
  if (m) {
    m.on('move', recompute)
    m.on('moveend', recompute)
    m.on('zoom', recompute)
    m.on('zoomend', recompute)
  }
})
onBeforeUnmount(() => {
  if (userSearchTimer) window.clearTimeout(userSearchTimer)
  window.removeEventListener('keydown', onKey)
  const m = window.expeditionMap?.getMap?.()
  if (m) {
    m.off('move', recompute)
    m.off('moveend', recompute)
    m.off('zoom', recompute)
    m.off('zoomend', recompute)
  }
})

watch(feature, async (v) => {
  if (v) {
    await nextTick()
    measure()
    recompute()
    const firstAssignable = assignmentOptionsForLayer(v.layer)[0]
    assignField.value = firstAssignable?.fieldname || ''
    assignUser.value = assignField.value && assignField.value !== '__frappe_assign'
      ? v.properties?.[assignField.value] || defaultAssignUser()
      : defaultAssignUser()
    userOptions.value = []
    userSearchOpen.value = false
    actionError.value = ''
    activeTab.value = 'details'
    fieldSearch.value = ''
    showMoreFields.value = false
    showAssignPanel.value = false
    loadHistory()
  } else {
    screen.value = null
    history.value = []
    actionError.value = ''
  }
})

watch(assignField, () => {
  const current = feature.value?.properties?.[assignField.value]
  assignUser.value = assignField.value && assignField.value !== '__frappe_assign'
    ? current || defaultAssignUser()
    : defaultAssignUser()
  userOptions.value = []
  userSearchOpen.value = false
})

const title = computed(() => {
  const f = feature.value
  if (!f) return ''
  if (f.properties._group_label) return f.properties._group_label
  // Per-group override label takes precedence
  const gv = f.properties._group_value
  const cfg = (f.layer && f.layer.group_config) || {}
  const override = (gv != null && cfg[String(gv)]) || null
  if (override && override.label) return override.label
  return f.properties._label || f.properties.name || f.properties._name || '(untitled)'
})
const subtitle = computed(() => {
  const f = feature.value
  if (!f) return ''
  const layer = f.layer || {}
  const src = f.properties._doctype
  const lname = layer.title || layer.name
  if (src && lname) return `${lname} · ${src}`
  return lname || src || ''
})
const propRows = computed(() => {
  const f = feature.value
  if (!f) return []
  const skip = new Set(['idx', 'lft', 'rgt', 'old_parent', 'docstatus'])
  const rows = []
  for (const [k, v] of Object.entries(f.properties || {})) {
    if (skip.has(k)) continue
    if (String(k).startsWith('_')) continue
    if (v === null || v === undefined || v === '') continue
    rows.push([k, v])
  }
  // If the layer configured explicit popup_fields, use those first.
  const explicit = f.properties._popup_fields
  if (Array.isArray(explicit) && explicit.length) {
    const out = []
    for (const fn of explicit) {
      if (f.properties[fn] != null && f.properties[fn] !== '') {
        out.push([fn, f.properties[fn]])
      }
    }
    return out
  }
  return rows
})
const fieldLabels = computed(() => layer.value.field_labels || {})
const SYSTEM_ASSIGNMENT_FIELDS = new Set(['owner', 'modified_by', 'creation', 'modified'])

function assignmentOptionsForLayer(layerMeta) {
  const userLinkFields = (layerMeta?.assignment_fields || []).filter((field) =>
    field?.fieldname && !SYSTEM_ASSIGNMENT_FIELDS.has(field.fieldname)
  )
  return [
    {
      fieldname: '__frappe_assign',
      label: 'Assign To',
      fieldtype: 'Assignment',
      options: 'User',
      standard: true,
    },
    ...userLinkFields,
  ]
}

const assignmentFields = computed(() => assignmentOptionsForLayer(layer.value))
const selectedAssignment = computed(() =>
  assignmentFields.value.find((f) => f.fieldname === assignField.value) || assignmentFields.value[0] || null
)
const currentAssignmentValue = computed(() => {
  const fieldname = selectedAssignment.value?.fieldname
  if (!fieldname || fieldname === '__frappe_assign') return ''
  return feature.value?.properties?.[fieldname] || ''
})

function labelFor(fieldname) {
  return fieldLabels.value[fieldname] || fieldname
}

function defaultAssignUser() {
  const user = (window.expeditionSession?.user || '').trim()
  return user && user !== 'Administrator' ? user : ''
}

function rowObject([fieldname, value]) {
  return {
    fieldname,
    label: labelFor(fieldname),
    value,
    formatted: formatValue(value),
  }
}

function classifyField(row) {
  const key = String(row.fieldname || '').toLowerCase()
  const label = String(row.label || '').toLowerCase()
  const haystack = `${key} ${label}`
  if (haystack.match(/phone|mobile|email|contact|whatsapp/)) return 'Contact'
  if (haystack.match(/address|city|state|country|territory|pincode|postal|zip/)) return 'Location'
  if (haystack.match(/date|time|created|modified|last|next|due/)) return 'Dates'
  if (typeof row.value === 'number' || haystack.match(/amount|total|limit|balance|outstanding|qty|quantity|count|rate|price|value/)) return 'Business'
  return 'Other'
}

const allRows = computed(() => propRows.value.map(rowObject))
const primaryRows = computed(() => allRows.value.slice(0, 4))
const secondaryRows = computed(() => allRows.value.slice(4))
const filteredSecondaryRows = computed(() => {
  const q = fieldSearch.value.trim().toLowerCase()
  if (!q) return secondaryRows.value
  return secondaryRows.value.filter((row) =>
    `${row.fieldname} ${row.label} ${row.formatted}`.toLowerCase().includes(q)
  )
})
const groupedRows = computed(() => {
  const groups = []
  const byName = new Map()
  for (const row of filteredSecondaryRows.value) {
    const groupName = classifyField(row)
    if (!byName.has(groupName)) {
      const group = { name: groupName, rows: [] }
      byName.set(groupName, group)
      groups.push(group)
    }
    byName.get(groupName).rows.push(row)
  }
  return groups
})
const visibleGroups = computed(() => showMoreFields.value || fieldSearch.value.trim()
  ? groupedRows.value
  : groupedRows.value.slice(0, 2)
)
const hiddenFieldCount = computed(() => {
  const visible = visibleGroups.value.reduce((sum, group) => sum + group.rows.length, 0)
  return Math.max(0, filteredSecondaryRows.value.length - visible)
})
const statusRow = computed(() =>
  allRows.value.find((row) => String(row.fieldname).toLowerCase().includes('status'))
)

function formatValue(value) {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString() : String(value)
  return String(value)
}

// Quick action: open the source DocType's form for this row.
function openForm() {
  if (!sourceDoctype.value || !sourceName.value) return
  // Use Frappe's form route. If we're inside the Frappe Desk, the
  // standard route works; on the public /expedition page, this opens
  // a new tab to the standard form view.
  const route = `/app/${encodeURIComponent(sourceDoctype.value.toLowerCase().replace(/ /g, '-'))}/${encodeURIComponent(sourceName.value)}`
  if (window.frappe?.set_route) {
    try { window.frappe.set_route(route); return } catch {}
  }
  window.open(route, '_blank')
}

// Quick action: schedule a visit (Expedition Activity row, type=visit).
async function scheduleVisit() {
  if (!sourceDoctype.value || !sourceName.value) return
  const f = feature.value
  const lat = f && f._lngLat ? f._lngLat.lat : null
  const lng = f && f._lngLat ? f._lngLat.lng : null
  try {
    await call('expedition.api.activity.log_activity', {
      activity_type: 'visit',
      title: `Visit to ${sourceDoctype.value} ${sourceName.value}`,
      related_doctype: sourceDoctype.value,
      related_name: sourceName.value,
      map_name: mapStore.activeMap?.map?.name || null,
      latitude: lat,
      longitude: lng,
    })
    // Reload history so the new row appears at the top
    await loadHistory()
  } catch (e) {
    console.error('[expedition] scheduleVisit failed', e)
    await ui.ask({
      title: 'Could not log visit',
      body: String(e.message || e),
      confirmLabel: 'OK',
      cancelLabel: '',
      destructive: false,
    })
  }
}

async function createTodo() {
  if (!sourceDoctype.value || !sourceName.value) return
  todoBusy.value = true
  actionError.value = ''
  try {
    await call('expedition.api.action.create_todo', {
      source_doctype: sourceDoctype.value,
      source_name: sourceName.value,
      description: `Follow up on ${title.value}`,
      allocated_to: window.expeditionSession?.user || '',
    })
  } catch (e) {
    actionError.value = e.message || String(e)
  } finally {
    todoBusy.value = false
  }
}

function toggleAssignPanel() {
  showAssignPanel.value = !showAssignPanel.value
  if (showAssignPanel.value) {
    activeTab.value = 'details'
    searchUsers(assignUser.value)
  } else {
    userSearchOpen.value = false
  }
}

function scheduleUserSearch() {
  userSearchOpen.value = true
  if (userSearchTimer) window.clearTimeout(userSearchTimer)
  userSearchTimer = window.setTimeout(() => searchUsers(assignUser.value), 180)
}

async function searchUsers(txt = '') {
  userSearchLoading.value = true
  try {
    userOptions.value = await call('expedition.api.action.search_users', {
      txt,
      limit: 8,
    }) || []
    userSearchOpen.value = true
  } catch (e) {
    console.warn('[expedition] user search failed', e)
    userOptions.value = []
  } finally {
    userSearchLoading.value = false
  }
}

function selectUser(user) {
  assignUser.value = user.value
  userSearchOpen.value = false
}

async function assignRecord() {
  if (!sourceDoctype.value || !sourceName.value || !assignField.value || !assignUser.value) return
  assignBusy.value = true
  actionError.value = ''
  try {
    if (assignField.value === '__frappe_assign') {
      await call('expedition.api.action.assign_to', {
        source_doctype: sourceDoctype.value,
        source_name: sourceName.value,
        user: assignUser.value,
        description: `Assignment for ${title.value}`,
      })
      showAssignPanel.value = false
      return
    }
    await call('expedition.api.action.assign', {
      source_doctype: sourceDoctype.value,
      source_name: sourceName.value,
      fieldname: assignField.value,
      user: assignUser.value,
    })
    if (feature.value?.properties) feature.value.properties[assignField.value] = assignUser.value
  } catch (e) {
    actionError.value = e.message || String(e)
  } finally {
    assignBusy.value = false
  }
}

async function unassignRecord() {
  if (!sourceDoctype.value || !sourceName.value || !assignField.value) return
  assignBusy.value = true
  actionError.value = ''
  try {
    await call('expedition.api.action.unassign', {
      source_doctype: sourceDoctype.value,
      source_name: sourceName.value,
      fieldname: assignField.value,
    })
    if (feature.value?.properties) feature.value.properties[assignField.value] = ''
  } catch (e) {
    actionError.value = e.message || String(e)
  } finally {
    assignBusy.value = false
  }
}

async function loadHistory() {
  if (!sourceDoctype.value || !sourceName.value) {
    history.value = []
    aggregate.value = null
    return
  }
  historyLoading.value = true
  aggregateLoading.value = true
  historyError.value = ''
  try {
    const [rows, agg] = await Promise.all([
      call('expedition.api.activity.list_for_related', {
        related_doctype: sourceDoctype.value,
        related_name: sourceName.value,
        limit: 10,
      }),
      call('expedition.api.activity.aggregate_for_related', {
        related_doctype: sourceDoctype.value,
        related_name: sourceName.value,
        bucket: 'year',
      }),
    ])
    history.value = rows || []
    aggregate.value = agg
  } catch (e) {
    historyError.value = e.message || String(e)
    history.value = []
    aggregate.value = null
  } finally {
    historyLoading.value = false
    aggregateLoading.value = false
  }
}

// "Active vs passive" signal: how long since the last interaction?
const activityStatus = computed(() => {
  const a = aggregate.value
  if (!a || !a.last_activity_at) return null
  const last = new Date(a.last_activity_at)
  if (isNaN(last.getTime())) return null
  const days = Math.floor((Date.now() - last.getTime()) / (1000 * 60 * 60 * 24))
  if (days < 60) return { label: 'Active', kind: 'active', days }
  if (days < 365) return { label: 'Cooling', kind: 'cooling', days }
  return { label: 'Passive', kind: 'passive', days }
})

function formatDate(s) {
  if (!s) return ''
  try {
    const d = new Date(s)
    if (isNaN(d.getTime())) return s
    return d.toLocaleString()
  } catch { return s }
}
</script>

<template>
  <div
    v-if="feature && screen"
    ref="cardEl"
    class="mp"
    :class="['mp--' + edge]"
    :style="{ top: screen.top + 'px', left: screen.left + 'px' }"
    role="dialog"
    :aria-label="title"
  >
    <header class="mp__header">
      <div class="mp__titles">
        <div class="mp__title-row">
          <h3 class="mp__title">{{ title }}</h3>
          <span v-if="statusRow" class="mp__status">{{ statusRow.formatted }}</span>
        </div>
        <p class="mp__subtitle">{{ subtitle }}</p>
      </div>
      <button class="mp__close" type="button" @click="close" aria-label="Close">×</button>
    </header>

    <div v-if="sourceDoctype && sourceName" class="mp__actions" aria-label="Record actions">
      <button v-if="clickAction !== 'none'" type="button" class="mp__action" @click="openForm" title="Open the source document">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>Open</span>
      </button>
      <button type="button" class="mp__action" :disabled="todoBusy" @click="createTodo" title="Create a ToDo linked to this record">
        <span>{{ todoBusy ? 'Adding...' : 'ToDo' }}</span>
      </button>
      <button
        v-if="assignmentFields.length"
        type="button"
        class="mp__action"
        :class="{ 'mp__action--active': showAssignPanel }"
        @click="toggleAssignPanel"
        title="Assign this record"
      >
        <span>Assign</span>
      </button>
      <button type="button" class="mp__action mp__action--ghost" title="More actions">
        <span>...</span>
      </button>
    </div>

    <nav class="mp__tabs" aria-label="Popup sections">
      <button type="button" class="mp__tab" :class="{ 'mp__tab--active': activeTab === 'details' }" @click="activeTab = 'details'">
        Details
      </button>
      <button
        v-if="sourceDoctype && sourceName"
        type="button"
        class="mp__tab"
        :class="{ 'mp__tab--active': activeTab === 'activity' }"
        @click="activeTab = 'activity'"
      >
        Activity
        <span v-if="aggregate && aggregate.total" class="mp__tab-count">{{ aggregate.total }}</span>
      </button>
    </nav>

    <div v-if="sourceDoctype && sourceName && assignmentFields.length && showAssignPanel" class="mp__assign-panel">
      <div class="mp__assign-row">
        <select v-model="assignField" class="mp__assign-input" aria-label="Assignment field">
          <option v-for="field in assignmentFields" :key="field.fieldname" :value="field.fieldname">
            {{ field.label || field.fieldname }}
          </option>
        </select>
        <div class="mp__user-picker">
          <input
            v-model="assignUser"
            class="mp__assign-input"
            type="search"
            placeholder="Search user"
            autocomplete="off"
            aria-label="Assignee"
            @focus="searchUsers(assignUser)"
            @input="scheduleUserSearch"
            @keydown.escape.stop="userSearchOpen = false"
          />
          <div v-if="userSearchOpen" class="mp__user-menu">
            <button
              v-for="user in userOptions"
              :key="user.value"
              type="button"
              class="mp__user-option"
              @mousedown.prevent="selectUser(user)"
            >
              <span class="mp__user-label">{{ user.label }}</span>
              <span class="mp__user-value">{{ user.value }}</span>
            </button>
            <div v-if="userSearchLoading" class="mp__user-empty">Searching...</div>
            <div v-else-if="!userOptions.length" class="mp__user-empty">No users found</div>
          </div>
        </div>
      </div>
      <div class="mp__assign-row">
        <span class="mp__assign-current">{{ currentAssignmentValue || 'Unassigned' }}</span>
        <button type="button" class="mp__assign-btn" :disabled="assignBusy || !assignUser" @click="assignRecord">
          {{ assignBusy ? 'Saving...' : 'Assign' }}
        </button>
        <button type="button" class="mp__assign-btn" :disabled="assignBusy || !currentAssignmentValue || assignField === 'owner'" @click="unassignRecord">
          Clear
        </button>
      </div>
    </div>

    <div class="mp__body">
      <section v-if="activeTab === 'details'" class="mp__section">
        <div v-if="feature.properties._popup_html" class="mp__custom" v-html="feature.properties._popup_html" />
        <template v-else>
          <div v-if="primaryRows.length" class="mp__primary">
            <div v-for="row in primaryRows" :key="row.fieldname" class="mp__primary-row">
              <span class="mp__primary-label">{{ row.label }}</span>
              <span class="mp__primary-value">{{ row.formatted }}</span>
            </div>
          </div>
          <p v-else class="mp__empty">No additional properties.</p>

          <div v-if="secondaryRows.length" class="mp__more">
            <div class="mp__more-head">
              <span>More Details</span>
              <span>{{ secondaryRows.length }} fields</span>
            </div>
            <input
              v-if="secondaryRows.length > 8"
              v-model="fieldSearch"
              class="mp__field-search"
              type="search"
              placeholder="Search fields"
            />
            <div class="mp__groups">
              <section v-for="group in visibleGroups" :key="group.name" class="mp__group">
                <div class="mp__group-head">
                  <span>{{ group.name }}</span>
                  <span>{{ group.rows.length }}</span>
                </div>
                <div class="mp__group-rows">
                  <div v-for="row in group.rows" :key="row.fieldname" class="mp__field-row">
                    <span class="mp__field-label">{{ row.label }}</span>
                    <span class="mp__field-value">{{ row.formatted }}</span>
                  </div>
                </div>
              </section>
            </div>
            <button v-if="hiddenFieldCount" type="button" class="mp__show-more" @click="showMoreFields = true">
              Show {{ hiddenFieldCount }} more fields
            </button>
          </div>
        </template>
        <p v-if="actionError" class="mp__action-error">{{ actionError }}</p>
      </section>

      <section v-else-if="activeTab === 'activity' && sourceDoctype && sourceName" class="mp__section">
        <div class="mp__activity-head">
          <div>
            <div class="mp__activity-title">Activity</div>
            <div v-if="activityStatus" class="mp__activity-subtitle">{{ activityStatus.label }} for {{ activityStatus.days }}d</div>
          </div>
          <button type="button" class="mp__visit-btn" @click="scheduleVisit">Log Visit</button>
        </div>
        <div class="mp__history-list">
          <div v-if="aggregate && !aggregateLoading" class="mp__history-summary">
            <div class="mp__summary-row">
              <span class="mp__summary-label">Total</span>
              <span class="mp__summary-val">{{ aggregate.total }}</span>
            </div>
            <div v-if="aggregate.last_activity_at" class="mp__summary-row">
              <span class="mp__summary-label">Last</span>
              <span class="mp__summary-val">{{ formatDate(aggregate.last_activity_at) }}</span>
            </div>
            <div v-if="activityStatus" class="mp__summary-row">
              <span class="mp__summary-label">Status</span>
              <span class="mp__summary-status" :data-kind="activityStatus.kind">
                {{ activityStatus.label }} ({{ activityStatus.days }}d)
              </span>
            </div>
          </div>

          <div v-if="aggregate && aggregate.buckets.length" class="mp__history-years">
            <div v-for="b in aggregate.buckets" :key="b.period" class="mp__history-year">
              <div class="mp__history-year-head">
                <span class="mp__history-year-period">{{ b.period }}</span>
                <span class="mp__history-year-count">{{ b.total }}</span>
              </div>
              <div v-if="b.by_type" class="mp__history-year-types">
                <span v-for="(cnt, typ) in b.by_type" :key="typ" class="mp__history-year-type">
                  {{ typ }}: {{ cnt }}
                </span>
              </div>
            </div>
          </div>

          <p v-if="historyLoading" class="mp__history-empty">Loading...</p>
          <p v-else-if="historyError" class="mp__history-empty mp__history-err">Could not load history.</p>
          <p v-else-if="!history.length" class="mp__history-empty">No visits logged yet.</p>
          <ul v-else class="mp__history-items">
            <li v-for="h in history" :key="h.name" class="mp__history-item">
              <div class="mp__history-item-head">
                <span class="mp__history-type">{{ h.activity_type || h.title }}</span>
                <span class="mp__history-date">{{ formatDate(h.occurred_at) }}</span>
              </div>
              <div v-if="h.user" class="mp__history-user">by {{ h.user }}</div>
              <div v-if="h.outcome" class="mp__history-outcome" :data-outcome="h.outcome">{{ h.outcome }}</div>
              <div v-if="h.notes" class="mp__history-notes">{{ h.notes }}</div>
            </li>
          </ul>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.mp {
  position: absolute;
  z-index: 200;
  width: 360px;
  background: rgba(11, 14, 20, 0.86);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: #E6E8EC;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  pointer-events: auto;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
  animation: mp-in 180ms ease-out;
  max-height: min(560px, 72vh);
}
@keyframes mp-in {
  from { transform: translateY(-6px); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}
.mp__header {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 12px 12px 9px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.mp__titles { flex: 1; min-width: 0; }
.mp__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.mp__title {
  margin: 0;
  font-size: 14px; font-weight: 500; line-height: 1.3;
  letter-spacing: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0;
  flex: 1;
}
.mp__subtitle { margin: 2px 0 0; font-size: 10px; color: rgba(230, 232, 236, 0.55); }
.mp__status {
  flex: none;
  max-width: 118px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid rgba(34, 197, 94, 0.26);
  background: rgba(34, 197, 94, 0.12);
  color: #A7F3D0;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 10px;
  font-weight: 600;
}
.mp__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 20px; line-height: 1; cursor: pointer; padding: 0 4px; border-radius: 5px;
}
.mp__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }

.mp__body { padding: 10px 12px 12px; overflow-y: auto; flex: 1; }
.mp__section { min-height: 0; }
.mp__empty { font-size: 12px; color: rgba(230, 232, 236, 0.5); padding: 12px 0; margin: 0; text-align: center; }
.mp__custom { padding: 4px 8px 4px 0; }
.mp__custom :deep(a) { color: #93C5FD; }
.mp__custom :deep(.pin) { font-size: 13px; line-height: 1.4; }

.mp__actions {
  display: flex; gap: 6px;
  padding: 7px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
.mp__action {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgba(230, 232, 236, 0.86);
  padding: 5px 9px;
  border-radius: 6px;
  font-size: 11px; font-family: inherit; font-weight: 500;
  cursor: pointer;
}
.mp__action:hover,
.mp__action--active { background: rgba(59, 130, 246, 0.18); border-color: rgba(59, 130, 246, 0.34); color: #BFDBFE; }
.mp__action--ghost { margin-left: auto; padding-left: 8px; padding-right: 8px; }
.mp__action:disabled { opacity: 0.55; cursor: default; }
.mp__action svg { flex: none; }

.mp__tabs {
  display: flex;
  gap: 4px;
  padding: 8px 12px 0;
}
.mp__tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: rgba(230, 232, 236, 0.58);
  cursor: pointer;
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  padding: 6px 8px 7px;
}
.mp__tab:hover { color: rgba(230, 232, 236, 0.86); }
.mp__tab--active {
  color: #E6E8EC;
  border-bottom-color: #60A5FA;
}
.mp__tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 17px;
  height: 17px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.22);
  color: #BFDBFE;
  padding: 0 5px;
  font-size: 10px;
}

.mp__assign-panel {
  margin: 8px 12px 0;
  padding: 8px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
}
.mp__assign-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.mp__assign-row + .mp__assign-row { margin-top: 6px; }
.mp__assign-input {
  min-width: 0;
  flex: 1;
  background: rgba(0, 0, 0, 0.20);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: #E6E8EC;
  border-radius: 5px;
  padding: 5px 7px;
  font-size: 11px;
  font-family: inherit;
}
.mp__user-picker {
  position: relative;
  flex: 1;
  min-width: 0;
}
.mp__user-picker .mp__assign-input {
  width: 100%;
  box-sizing: border-box;
}
.mp__user-menu {
  position: absolute;
  z-index: 260;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 178px;
  overflow-y: auto;
  padding: 4px;
  border-radius: 7px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(11, 14, 20, 0.98);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.42);
}
.mp__user-option {
  width: 100%;
  display: grid;
  gap: 2px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #E6E8EC;
  cursor: pointer;
  font-family: inherit;
  padding: 6px 7px;
  text-align: left;
}
.mp__user-option:hover {
  background: rgba(59, 130, 246, 0.18);
}
.mp__user-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  font-weight: 600;
}
.mp__user-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(230, 232, 236, 0.55);
  font-size: 10px;
}
.mp__user-empty {
  padding: 8px 7px;
  color: rgba(230, 232, 236, 0.55);
  font-size: 11px;
}
.mp__assign-current {
  flex: 1;
  min-width: 0;
  color: rgba(230, 232, 236, 0.65);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mp__assign-btn {
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.30);
  color: #A7F3D0;
  border-radius: 5px;
  padding: 5px 8px;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.mp__assign-btn:disabled {
  opacity: 0.45;
  cursor: default;
}
.mp__action-error {
  margin: 6px 0 0;
  color: #FCA5A5;
  font-size: 11px;
}

.mp__primary {
  display: grid;
  gap: 6px;
}
.mp__primary-row,
.mp__field-row {
  display: grid;
  grid-template-columns: minmax(92px, 0.46fr) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}
.mp__primary-row {
  padding: 7px 8px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid rgba(255, 255, 255, 0.055);
}
.mp__primary-label,
.mp__field-label {
  color: rgba(230, 232, 236, 0.54);
  font-size: 11px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.mp__primary-value,
.mp__field-value {
  color: #E6E8EC;
  font-size: 12px;
  line-height: 1.35;
  text-align: left;
  overflow-wrap: anywhere;
}
.mp__primary-value { font-weight: 600; }
.mp__more {
  margin-top: 10px;
}
.mp__more-head,
.mp__group-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.mp__more-head {
  color: rgba(230, 232, 236, 0.66);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 7px;
}
.mp__field-search {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 8px;
  background: rgba(0, 0, 0, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: #E6E8EC;
  border-radius: 7px;
  padding: 7px 9px;
  font-size: 11px;
  font-family: inherit;
  outline: none;
}
.mp__field-search:focus { border-color: rgba(96, 165, 250, 0.72); }
.mp__groups {
  display: grid;
  gap: 7px;
}
.mp__group {
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.05);
  overflow: hidden;
}
.mp__group-head {
  padding: 6px 8px;
  color: rgba(230, 232, 236, 0.72);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  background: rgba(255, 255, 255, 0.035);
}
.mp__group-rows {
  display: grid;
}
.mp__field-row {
  padding: 6px 8px;
}
.mp__field-row + .mp__field-row {
  border-top: 1px solid rgba(255, 255, 255, 0.045);
}
.mp__show-more {
  width: 100%;
  margin-top: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.045);
  color: rgba(230, 232, 236, 0.78);
  border-radius: 7px;
  padding: 7px 8px;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.mp__show-more:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.075);
}

.mp__activity-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.mp__activity-title {
  color: #E6E8EC;
  font-size: 13px;
  font-weight: 600;
}
.mp__activity-subtitle {
  margin-top: 2px;
  color: rgba(230, 232, 236, 0.55);
  font-size: 11px;
}
.mp__visit-btn {
  flex: none;
  border: 1px solid rgba(16, 185, 129, 0.30);
  background: rgba(16, 185, 129, 0.12);
  color: #A7F3D0;
  border-radius: 6px;
  padding: 6px 9px;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.mp__visit-btn:hover { background: rgba(16, 185, 129, 0.2); }

/* Visit history section. */
.mp__history { margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.06); }
.mp__history-toggle {
  display: flex; align-items: center; gap: 6px;
  background: transparent; border: 0; padding: 0;
  color: rgba(230, 232, 236, 0.7);
  font-family: inherit; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.08em;
  cursor: pointer; font-weight: 500;
}
.mp__history-toggle:hover { color: #E6E8EC; }
.mp__chevron {
  display: inline-block; font-size: 10px;
  transition: transform 150ms ease;
  opacity: 0.7;
}
.mp__chevron[data-open="true"] { transform: rotate(180deg); }
.mp__history-count {
  background: rgba(59, 130, 246, 0.18);
  color: #93C5FD;
  border-radius: 8px;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 600;
}
.mp__history-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.mp__history-status[data-kind="active"] {
  background: rgba(34, 197, 94, 0.18);
  color: #86efac;
}
.mp__history-status[data-kind="cooling"] {
  background: rgba(245, 158, 11, 0.18);
  color: #fcd34d;
}
.mp__history-status[data-kind="passive"] {
  background: rgba(239, 68, 68, 0.18);
  color: #fca5a5;
}
.mp__history-list { margin-top: 6px; }
.mp__history-empty {
  font-size: 11px;
  color: rgba(230, 232, 236, 0.5);
  margin: 4px 0 0;
}
.mp__history-err { color: #FCA5A5; }

/* Activity summary row (total / last / status). */
.mp__history-summary {
  display: flex; gap: 8px;
  padding: 6px 8px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  margin-bottom: 6px;
}
.mp__summary-row {
  display: flex; flex-direction: column; gap: 1px;
  font-size: 10px;
  flex: 1;
}
.mp__summary-label {
  color: rgba(230, 232, 236, 0.5);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 500;
}
.mp__summary-val {
  color: #E6E8EC;
  font-weight: 600;
  font-size: 13px;
}
.mp__summary-status {
  font-weight: 600;
  font-size: 11px;
}
.mp__summary-status[data-kind="active"] { color: #86efac; }
.mp__summary-status[data-kind="cooling"] { color: #fcd34d; }
.mp__summary-status[data-kind="passive"] { color: #fca5a5; }

/* Year breakdown. */
.mp__history-years {
  display: flex; flex-direction: column; gap: 4px;
  margin-bottom: 6px;
}
.mp__history-year {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 5px;
  padding: 4px 8px;
}
.mp__history-year-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 10px; font-weight: 600;
  color: rgba(230, 232, 236, 0.85);
}
.mp__history-year-count {
  color: #93C5FD;
}
.mp__history-year-types {
  display: flex; gap: 6px;
  margin-top: 3px;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.65);
}
.mp__history-items {
  list-style: none; padding: 0; margin: 6px 0 0;
  display: flex; flex-direction: column; gap: 6px;
  max-height: 160px; overflow-y: auto;
}
.mp__history-item {
  background: rgba(0, 0, 0, 0.20);
  border-radius: 6px;
  padding: 6px 8px;
}
.mp__history-item-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  font-size: 11px;
}
.mp__history-type { font-weight: 500; color: #E6E8EC; }
.mp__history-date {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  font-variant-numeric: tabular-nums;
}
.mp__history-user {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.55);
  margin-top: 2px;
}
.mp__history-outcome {
  display: inline-block;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
  background: rgba(230, 232, 236, 0.06);
  color: rgba(230, 232, 236, 0.75);
}
.mp__history-outcome[data-outcome="successful"] {
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}
.mp__history-outcome[data-outcome="failed"] {
  background: rgba(239, 68, 68, 0.15);
  color: #fca5a5;
}
.mp__history-outcome[data-outcome="rescheduled"] {
  background: rgba(245, 158, 11, 0.15);
  color: #fcd34d;
}
.mp__history-notes {
  font-size: 11px;
  color: rgba(230, 232, 236, 0.85);
  margin-top: 4px;
  line-height: 1.4;
}
</style>
