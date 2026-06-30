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
const showHistory = ref(true)
// Aggregated stats: counts by year (active vs passive).
const aggregate = ref(null)
const aggregateLoading = ref(false)

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
  const w = 320
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
    loadHistory()
  } else {
    screen.value = null
    history.value = []
  }
})

const title = computed(() => {
  const f = feature.value
  if (!f) return ''
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
  const skip = new Set(['_doctype', '_name', '_label', 'name', '_popup_html', '_layer_name', '_group_value', '_color', '_icon', '_icon_disabled', '_popup_fields'])
  const rows = []
  for (const [k, v] of Object.entries(f.properties || {})) {
    if (skip.has(k)) continue
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
        <h3 class="mp__title">{{ title }}</h3>
        <p class="mp__subtitle">{{ subtitle }}</p>
      </div>
      <button class="mp__close" type="button" @click="close" aria-label="Close">×</button>
    </header>
    <!-- Quick actions row. Always visible when a row has a source. -->
    <div v-if="sourceDoctype && sourceName" class="mp__actions">
      <button v-if="clickAction !== 'none'" type="button" class="mp__action"
              @click="openForm" title="Open the source document">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>Open</span>
      </button>
      <button type="button" class="mp__action" @click="scheduleVisit"
              title="Log a visit at this location">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <circle cx="12" cy="10" r="3" fill="none" stroke="currentColor" stroke-width="1.5"/>
          <path d="M12 21s-7-7-7-12a7 7 0 0 1 14 0c0 5-7 12-7 12z"
                fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
        <span>Visit</span>
      </button>
    </div>
    <div class="mp__body">
      <!--
        When the layer has a popup_template, the server renders it once per
        feature and ships the result in _popup_html. Render it raw: the
        author is the layer-editor user (privileged), and the content is
        scoped to that author's row data.
      -->
      <div v-if="feature.properties._popup_html" class="mp__custom" v-html="feature.properties._popup_html" />
      <template v-else>
        <table v-if="propRows.length" class="mp__props">
          <tbody>
            <tr v-for="[k, v] in propRows" :key="k">
              <th>{{ k }}</th>
              <td>{{ v }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="mp__empty">No additional properties.</p>
      </template>

      <!-- Visit history (Expedition Activity linked to the source doc). -->
      <div v-if="sourceDoctype && sourceName" class="mp__history">
        <button type="button" class="mp__history-toggle" @click="showHistory = !showHistory">
          <span class="mp__chevron" :data-open="showHistory">▾</span>
          <span>Activity</span>
          <span v-if="aggregate && aggregate.total" class="mp__history-count">{{ aggregate.total }}</span>
          <span v-if="activityStatus" class="mp__history-status" :data-kind="activityStatus.kind">
            {{ activityStatus.label }}
          </span>
        </button>
        <div v-if="showHistory" class="mp__history-list">
          <!-- Summary header: total activities, last interaction, active/passive signal. -->
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

          <!-- Year-by-year breakdown. -->
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

          <p v-if="historyLoading" class="mp__history-empty">Loading…</p>
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
      </div>
    </div>
  </div>
</template>

<style scoped>
.mp {
  position: absolute;
  z-index: 200;
  width: 320px;
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
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
  animation: mp-in 180ms ease-out;
  max-height: 70vh;
}
@keyframes mp-in {
  from { transform: translateY(-6px); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}
.mp__header {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 12px 12px 8px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.mp__titles { flex: 1; min-width: 0; }
.mp__title {
  margin: 0 0 2px;
  font-size: 14px; font-weight: 500; line-height: 1.3;
  letter-spacing: -0.01em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mp__subtitle { margin: 0; font-size: 10px; color: rgba(230, 232, 236, 0.55); }
.mp__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 20px; line-height: 1; cursor: pointer; padding: 0 4px; border-radius: 5px;
}
.mp__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }

.mp__body { padding: 8px 6px 12px 12px; overflow-y: auto; flex: 1; }
.mp__props { width: 100%; border-collapse: collapse; font-size: 12px; }
.mp__props th {
  text-align: left; font-weight: 500; color: rgba(230, 232, 236, 0.5);
  padding: 4px 8px 4px 0; vertical-align: top; white-space: nowrap; width: 1%;
}
.mp__props td { padding: 4px 0 4px 8px; word-break: break-word; color: #E6E8EC; }
.mp__props tr:not(:last-child) th,
.mp__props tr:not(:last-child) td { border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.mp__empty { font-size: 12px; color: rgba(230, 232, 236, 0.5); padding: 12px 0; margin: 0; text-align: center; }
.mp__custom { padding: 4px 8px 4px 0; }
.mp__custom :deep(a) { color: #93C5FD; }
.mp__custom :deep(.pin) { font-size: 13px; line-height: 1.4; }

/* Quick actions row (open form / schedule visit). */
.mp__actions {
  display: flex; gap: 6px;
  padding: 6px 12px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
.mp__action {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.30);
  color: #93C5FD;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11px; font-family: inherit; font-weight: 500;
  cursor: pointer;
}
.mp__action:hover { background: rgba(59, 130, 246, 0.22); color: #fff; }
.mp__action svg { flex: none; }

/* Visit history section. */
.mp__history {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
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
