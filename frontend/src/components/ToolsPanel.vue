<script setup>
/**
 * ToolsPanel — right-edge slide-in panel. PR-3 of the quiet-canvas plan.
 *
 * Absorbs the Zones and Insights sections from the old LeftRail /
 * BottomDrawer. Phase 2 hooks live here too (Drawing extras, Measure,
 * Settings) — currently rendered as disabled stubs.
 *
 * Width: 320px. Same glass as LayerPanel.
 */
import { computed, ref } from 'vue'
import { useMapStore } from '../state/map.js'
import { useUiStore } from '../state/ui.js'
import { useZonesStore } from '../state/zones.js'
import { useInsightsStore } from '../state/insights.js'
import { call } from '../api/client.js'

const mapStore = useMapStore()
const ui = useUiStore()
const zoneStore = useZonesStore()
const insights = useInsightsStore()

const activeMapName = computed(() => mapStore.activeMap?.map?.name)
const zones = computed(() =>
  activeMapName.value ? zoneStore.byMap[activeMapName.value] || [] : []
)
// Zones list filtered by the selected tag filter (if any).
const filteredZones = computed(() => {
  const tags = ui.zoneTags || []
  if (!tags.length) return zones.value
  return zones.value.filter((z) => z.tag && tags.includes(z.tag))
})
// Distinct zone tags used on the active map. Drives the tag-filter chip row.
const zoneTagsList = computed(() => {
  const set = new Set()
  for (const z of zones.value) if (z.tag) set.add(z.tag)
  return Array.from(set).sort()
})
const insightList = computed(() => insights.active || [])

const canEdit = computed(() => {
  const owner = mapStore.activeMap?.map?.owner
  return !owner || owner === window.frappe?.session?.user || (window.frappe?.session?.user === 'Administrator')
})

const openSections = ref({
  zones: true,
  insights: true,
  drawing: false,
  measure: true,
  pitch: true,
  activity: true,
})

// Activity feed (PR-13-ish). Shows recent Expedition Activity rows
// in the current viewport bounds. Fetches on demand so the panel
// can stay cheap. Date range + user filters are server-side via
// list_in_bounds.
const activityRows = ref([])
const activityLoading = ref(false)
const activityError = ref('')
const activityRange = ref('30d') // '7d' | '30d' | '90d' | 'all'
const activityUser = ref('')
const activityType = ref('')

function rangeToDates(r) {
  const now = new Date()
  if (r === '7d') {
    const d = new Date(now.getTime() - 7 * 86400_000)
    return { start: d.toISOString().slice(0, 10) }
  }
  if (r === '30d') {
    const d = new Date(now.getTime() - 30 * 86400_000)
    return { start: d.toISOString().slice(0, 10) }
  }
  if (r === '90d') {
    const d = new Date(now.getTime() - 90 * 86400_000)
    return { start: d.toISOString().slice(0, 10) }
  }
  return {} // all
}

async function loadActivity() {
  const m = window.expeditionMap?.getMap?.()
  if (!m) {
    activityError.value = 'Map is not ready.'
    return
  }
  const b = m.getBounds()
  const { start } = rangeToDates(activityRange.value)
  activityLoading.value = true
  activityError.value = ''
  try {
    const params = {
      bounds: {
        south: b.getSouth(),
        west: b.getWest(),
        north: b.getNorth(),
        east: b.getEast(),
      },
      limit: 200,
    }
    if (start) params.start_date = start
    if (activityUser.value) params.user = activityUser.value
    if (activityType.value) params.activity_types = [activityType.value]
    activityRows.value = await call('expedition.api.activity.list_in_bounds', params)
  } catch (e) {
    activityError.value = e.message || String(e)
    activityRows.value = []
  } finally {
    activityLoading.value = false
  }
}

function focusActivity(row) {
  if (row.latitude == null || row.longitude == null) return
  if (!window.expeditionMap) return
  window.expeditionMap.flyTo({
    center: [row.longitude, row.latitude],
    zoom: Math.max(window.expeditionMap.getZoom?.() || 6, 10),
    duration: 700,
  })
}

function formatAgo(s) {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  const diff = Date.now() - d.getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const days = Math.floor(h / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  return `${months}mo ago`
}

const SEVERITY_RING = {
  high: 'rgba(239, 68, 68, 0.55)',
  medium: 'rgba(245, 158, 11, 0.55)',
  low: 'rgba(59, 130, 246, 0.55)',
  info: 'rgba(148, 163, 184, 0.55)',
}
function severityColor(s) { return SEVERITY_RING[s] || SEVERITY_RING.info }
function severityLabel(s) { return ({ high: 'High', medium: 'Med', low: 'Low', info: 'Info' })[s] || s }

function startDrawPolygon() { ui.startDrawPolygon() }
function cancelDraw() { ui.cancelDraw() }

function focusZone(zone) {
  if (zone && zone.centroid_lat != null && zone.centroid_lng != null && window.expeditionMap) {
    window.expeditionMap.flyTo({
      center: [zone.centroid_lng, zone.centroid_lat],
      zoom: Math.max(window.expeditionMap.getZoom?.() || 4, 8),
      duration: 700,
      essential: true,
    })
  }
}
async function removeZone(zone) {
  if (!zone || !zone.name) return
  const ok = await ui.ask({
    title: 'Delete zone "' + (zone.title || zone.name) + '"?',
    body: 'This cannot be undone.',
    confirmLabel: 'Delete',
    destructive: true,
  })
  if (!ok) return
  try { await zoneStore.deleteZone(activeMapName.value, zone.name) }
  catch (e) {
    console.error('[expedition] delete zone failed', e)
    await ui.ask({
      title: 'Could not delete zone',
      body: 'The server returned an error. Try again, or check the console for details.',
      confirmLabel: 'OK',
      cancelLabel: '',
      destructive: false,
    }).then(() => {})
  }
}

function onInsightClick(ins) {
  if (ins.centroid_lat != null && ins.centroid_lng != null && window.expeditionMap) {
    window.expeditionMap.flyTo({ center: [ins.centroid_lng, ins.centroid_lat], zoom: 6, duration: 900 })
  }
}
</script>

<template>
  <aside class="tp" role="region" aria-label="Tools">
    <header class="tp__header">
      <div class="tp__title-wrap">
        <div class="tp__title">Tools</div>
        <div class="tp__subtitle">{{ zones.length }} zones · {{ insightList.length }} insights</div>
      </div>
      <button class="tp__close" type="button" aria-label="Close tools" @click="$emit('close')">×</button>
    </header>

    <div class="tp__body">
      <!-- Zones -->
      <section v-if="mapStore.activeMap" class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.zones = !openSections.zones">
            <span>Zones</span>
            <span class="tp__chevron" :data-open="openSections.zones">▾</span>
          </button>
          <button
            v-if="ui.drawMode !== 'polygon' && canEdit"
            class="tp__add" type="button" @click="startDrawPolygon"
            title="Draw a polygon on the map"
            aria-label="Draw new zone"
          >+</button>
          <button
            v-else-if="ui.drawMode === 'polygon'"
            class="tp__add" type="button" @click="cancelDraw"
            title="Cancel (Esc)"
            aria-label="Cancel drawing"
          >×</button>
        </div>
        <!-- Tag filter chips. Only show if there are zones with tags. -->
        <div v-if="openSections.zones && zoneTagsList.length" class="tp__zone-tags">
          <span class="tp__zone-tags-label">Filter by tag:</span>
          <div class="tp__zone-tags-row">
            <button
              v-for="tag in zoneTagsList"
              :key="'tag-' + tag"
              type="button"
              class="tp__zone-tag"
              :class="{ 'tp__zone-tag--on': ui.zoneTags.includes(tag) }"
              :aria-pressed="ui.zoneTags.includes(tag) ? 'true' : 'false'"
              :aria-label="'Toggle ' + tag"
              @click="ui.toggleZoneTag(tag)"
            >{{ tag }}</button>
            <button
              v-if="ui.zoneTags.length"
              type="button"
              class="tp__zone-tag-clear"
              title="Clear tag filter"
              @click="ui.clearZoneTags"
            >×</button>
          </div>
        </div>
        <ul v-if="openSections.zones && filteredZones.length" class="tp__list">
          <li v-for="z in filteredZones" :key="z.name" class="tp__item">
            <button type="button" class="tp__row" @click="focusZone(z)" :title="'Fly to ' + z.title">
              <span class="tp__swatch" :style="{ background: z.color || '#3B82F6' }" />
              <span class="tp__label">{{ z.title || z.name }}</span>
            </button>
            <button v-if="canEdit" class="tp__row-edit" type="button" @click="removeZone(z)" aria-label="'Delete ' + (z.title || z.name)">del</button>
          </li>
        </ul>
        <p v-else-if="openSections.zones" class="tp__empty">No zones yet. Use + to draw a region.</p>

        <div v-if="ui.drawMode === 'polygon' && openSections.zones" class="tp__draw">
          <input
            v-model="ui.zoneDraftTitle"
            class="tp__input"
            type="text"
            placeholder="Zone name (e.g. Mumbai region)"
            aria-label="Zone name"
          />
          <p class="tp__hint">Click on the map to add vertices. Double-click to finish. Esc to cancel.</p>
        </div>
      </section>

      <!-- Activity feed (PR-activity). Recent visits/calls in the
           visible bounds, with a date-range filter. -->
      <section v-if="mapStore.activeMap" class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.activity = !openSections.activity">
            <span>Activity feed</span>
            <span v-if="activityRows.length" class="tp__count">{{ activityRows.length }}</span>
            <span class="tp__chevron" :data-open="openSections.activity">▾</span>
          </button>
        </div>
        <div v-if="openSections.activity" class="tp__activity">
          <div class="tp__activity-controls">
            <select v-model="activityRange" class="tp__activity-select" @change="loadActivity">
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="90d">Last 90 days</option>
              <option value="all">All time</option>
            </select>
            <select v-model="activityType" class="tp__activity-select" @change="loadActivity">
              <option value="">All types</option>
              <option value="visit">Visits</option>
              <option value="call">Calls</option>
              <option value="demo">Demos</option>
              <option value="follow_up">Follow-ups</option>
              <option value="scheduled">Scheduled</option>
              <option value="note">Notes</option>
            </select>
          </div>
          <button
            type="button" class="tp__activity-refresh" :disabled="activityLoading"
            @click="loadActivity"
          >{{ activityLoading ? 'Loading…' : 'Refresh' }}</button>
          <p v-if="activityError" class="tp__empty tp__empty--err">{{ activityError }}</p>
          <p v-else-if="!activityRows.length && !activityLoading" class="tp__empty">
            No activity in current bounds.
          </p>
          <ul v-else class="tp__activity-list">
            <li v-for="row in activityRows" :key="row.name" class="tp__activity-item" @click="focusActivity(row)">
              <span class="tp__activity-type" :data-type="row.activity_type">{{ row.activity_type }}</span>
              <span class="tp__activity-title">{{ row.title }}</span>
              <span v-if="row.related_doctype" class="tp__activity-relation">↳ {{ row.related_doctype }}</span>
              <span class="tp__activity-ago">{{ formatAgo(row.occurred_at) }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- Insights -->
      <section v-if="insightList.length || insights.loading" class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.insights = !openSections.insights">
            <span>Insights</span>
            <span class="tp__chevron" :data-open="openSections.insights">▾</span>
          </button>
        </div>
        <ul v-if="openSections.insights && insightList.length" class="tp__list">
          <li v-for="ins in insightList" :key="ins.name" class="tp__item">
            <button
              type="button"
              class="tp__chip"
              :style="{ '--ring': severityColor(ins.severity) }"
              :title="ins.summary"
              @click="onInsightClick(ins)"
            >
              <span class="tp__chip-sev">{{ severityLabel(ins.severity) }}</span>
              <span class="tp__chip-title">{{ ins.title }}</span>
            </button>
          </li>
        </ul>
        <p v-else-if="openSections.insights && !insights.loading" class="tp__empty">No active insights.</p>
      </section>

      <!-- Drawing extras (Phase 2) -->
      <section class="tp__section">
        <button class="tp__section-toggle" type="button" @click="openSections.drawing = !openSections.drawing" disabled>
          <span>Drawing</span>
          <span class="tp__badge">Phase 2</span>
        </button>
        <p v-if="openSections.drawing" class="tp__empty">Circle and rectangle drawing — coming with radius tools.</p>
      </section>

      <!-- Measure (PR-11). Two buttons: distance line, area polygon. -->
      <section class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.measure = !openSections.measure">
            <span>Measure</span>
            <span class="tp__chevron" :data-open="openSections.measure">▾</span>
          </button>
        </div>
        <div v-if="openSections.measure" class="tp__measure">
          <button
            type="button"
            class="tp__measure-btn"
            :class="{ 'tp__measure-btn--on': ui.measureMode === 'line' }"
            @click="ui.startMeasure('line')"
            :aria-pressed="ui.measureMode === 'line' ? 'true' : 'false'"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <path d="M3 17l6-10 6 8 6-6" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="3" cy="17" r="1.6" fill="currentColor"/>
              <circle cx="21" cy="9" r="1.6" fill="currentColor"/>
            </svg>
            Distance
          </button>
          <button
            type="button"
            class="tp__measure-btn"
            :class="{ 'tp__measure-btn--on': ui.measureMode === 'polygon' }"
            @click="ui.startMeasure('polygon')"
            :aria-pressed="ui.measureMode === 'polygon' ? 'true' : 'false'"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <path d="M4 6l16 0 0 14 -16 0z" fill="none" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="4" cy="6" r="1.5" fill="currentColor"/>
              <circle cx="20" cy="6" r="1.5" fill="currentColor"/>
              <circle cx="20" cy="20" r="1.5" fill="currentColor"/>
              <circle cx="4" cy="20" r="1.5" fill="currentColor"/>
            </svg>
            Area
          </button>
        </div>
      </section>

      <!-- 3D / pitch (PR-9) -->
      <section class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.pitch = !openSections.pitch">
            <span>3D / Pitch</span>
            <span class="tp__chevron" :data-open="openSections.pitch">▾</span>
          </button>
          <button
            class="tp__row-icon"
            type="button"
            :class="{ 'tp__row-icon--on': ui.pitchEnabled }"
            :aria-pressed="ui.pitchEnabled ? 'true' : 'false'"
            :aria-label="(ui.pitchEnabled ? 'Disable' : 'Enable') + ' 3D pitch'"
            :title="ui.pitchEnabled ? '3D pitch on' : 'Tilt the map'"
            @click="ui.togglePitch()"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <!-- 3D box glyph. -->
              <path d="M12 4l8 4v8l-8 4-8-4V8l8-4z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
              <path d="M4 8l8 4 8-4M12 12v8" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" fill="none"/>
            </svg>
          </button>
        </div>
        <div v-if="openSections.pitch" class="tp__pitch-body">
          <div class="tp__row">
            <span class="tp__row-label">Tilt</span>
            <input
              type="range" min="0" max="75" step="1"
              :value="ui.pitchDegrees"
              @input="(e) => ui.setPitch(e.target.value)"
              class="tp__slider"
              aria-label="Pitch degrees"
            />
            <span class="tp__row-val">{{ ui.pitchDegrees }}°</span>
          </div>
          <p class="tp__hint">Pins extrude as 3D columns. Each layer's height defaults to 200m; pick a numeric field in the layer row for per-feature heights.</p>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.tp {
  width: 320px;
  /* Fit content height — short lists don't make a full-page bar. */
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
.tp__header {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 12px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex: none;
}
.tp__title-wrap { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tp__title { font-size: 13px; font-weight: 500; }
.tp__subtitle { font-size: 10px; color: rgba(230, 232, 236, 0.5); text-transform: uppercase; letter-spacing: 0.08em; }
.tp__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1; border-radius: 5px;
}
.tp__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }

.tp__body { padding: 6px 0; overflow-y: auto; flex: 1; }
.tp__section { padding: 6px 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.tp__section:last-child { border-bottom: 0; }
.tp__section-bar { display: flex; align-items: center; justify-content: space-between; }
.tp__section-toggle {
  flex: 1; display: flex; align-items: center; justify-content: space-between;
  padding: 6px 8px; background: transparent; border: 0; color: rgba(230, 232, 236, 0.5);
  font-family: inherit; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  cursor: pointer; font-weight: 500; border-radius: 6px;
}
.tp__section-toggle:hover:not([disabled]) { background: rgba(255, 255, 255, 0.04); color: rgba(230, 232, 236, 0.8); }
.tp__section-toggle[disabled] { cursor: not-allowed; opacity: 0.6; }
.tp__chevron { font-size: 10px; transition: transform 150ms ease; opacity: 0.6; }
.tp__chevron[data-open="true"] { transform: rotate(180deg); }
.tp__badge {
  font-size: 9px; padding: 2px 6px; border-radius: 999px;
  background: rgba(245, 158, 11, 0.15); color: #F59E0B;
  text-transform: none; letter-spacing: 0.02em; font-weight: 500;
}

.tp__add {
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.4);
  color: #93C5FD;
  width: 22px; height: 22px; border-radius: 5px;
  cursor: pointer; font-size: 14px; line-height: 1; font-family: inherit;
  margin-right: 6px;
}
.tp__add:hover { background: rgba(59, 130, 246, 0.22); color: #fff; }

.tp__list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 1px; }
.tp__row {
  flex: 1; display: flex; align-items: center; gap: 10px;
  padding: 6px 8px; border-radius: 7px;
  cursor: pointer; font-size: 12px;
  background: transparent; border: 0; color: inherit; text-align: left;
  font-family: inherit;
}
.tp__row:hover { background: rgba(255, 255, 255, 0.05); }
.tp__label { font-size: 12px; color: #E6E8EC; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tp__swatch { width: 10px; height: 10px; border-radius: 50%; flex: none; border: 1.5px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,0.4); }
.tp__row-edit {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.4);
  font-size: 10px; cursor: pointer; padding: 4px 6px; border-radius: 4px;
  font-family: inherit;
}
.tp__row-edit:hover { color: #fff; background: rgba(255, 255, 255, 0.08); }

.tp__chip {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 5px 11px 5px 5px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--ring, rgba(148, 163, 184, 0.55));
  border-radius: 999px;
  font-size: 12px; color: #e6e8ec; cursor: pointer; flex-shrink: 0;
  font-family: inherit; width: 100%; text-align: left;
}
.tp__chip:hover { background: rgba(255, 255, 255, 0.09); }
.tp__chip-sev {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
  color: #0b0e14; background: var(--ring);
}
.tp__chip-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.tp__empty { padding: 10px; font-size: 11px; color: rgba(230, 232, 236, 0.6); margin: 0; }
.tp__item { display: flex; align-items: center; }
.tp__draw { padding: 8px 8px 0; }
.tp__input {
  width: 100%;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 6px;
  color: #E6E8EC;
  padding: 6px 8px;
  font-size: 12px;
  font-family: inherit;
}
.tp__input:focus { outline: none; border-color: rgba(59, 130, 246, 0.6); }
.tp__hint { font-size: 10px; color: rgba(230, 232, 236, 0.5); margin: 6px 0 0; line-height: 1.4; }

/* 3D / pitch controls (PR-9). */
.tp__row-icon {
  background: transparent; border: 0;
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 5px; cursor: pointer;
  color: rgba(230, 232, 236, 0.4);
  font-family: inherit;
  flex: none;
  margin-right: 6px;
  transition: background 100ms ease, color 100ms ease;
}
.tp__row-icon:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.tp__row-icon--on { color: #93C5FD; }
.tp__row-icon--on:hover { color: #fff; }

.tp__pitch-body { padding: 6px 8px 8px; display: flex; flex-direction: column; gap: 6px; }
.tp__row {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 0;
}
.tp__row-label {
  font-size: 10px; color: rgba(230, 232, 236, 0.5);
  text-transform: uppercase; letter-spacing: 0.06em;
  flex: none; min-width: 32px;
}
.tp__slider { flex: 1; accent-color: #3B82F6; }
.tp__row-val {
  font-size: 11px; color: #fff;
  font-variant-numeric: tabular-nums;
  flex: none; min-width: 32px; text-align: right;
}

/* Measure (PR-11). */
.tp__measure {
  display: flex;
  gap: 6px;
  padding: 6px 8px 8px;
}
.tp__measure-btn {
  flex: 1;
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 6px 8px;
  color: rgba(230, 232, 236, 0.85);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease;
}
.tp__measure-btn:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.tp__measure-btn--on {
  background: rgba(16, 185, 129, 0.18);
  border-color: rgba(16, 185, 129, 0.45);
  color: #10B981;
}

/* Activity feed (PR-activity). */
.tp__count {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.18);
  color: #93C5FD;
  font-weight: 600;
}
.tp__activity { padding: 6px 8px 8px; display: flex; flex-direction: column; gap: 6px; }
.tp__activity-controls { display: flex; gap: 4px; }
.tp__activity-select {
  flex: 1;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  color: #E6E8EC;
  padding: 4px 6px;
  font-size: 11px;
  font-family: inherit;
  outline: none;
}
.tp__activity-select:focus { border-color: rgba(59, 130, 246, 0.6); }
.tp__activity-refresh {
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.30);
  color: #93C5FD;
  padding: 5px 8px;
  border-radius: 5px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  align-self: flex-start;
}
.tp__activity-refresh:hover:not(:disabled) { background: rgba(59, 130, 246, 0.22); color: #fff; }
.tp__activity-refresh:disabled { opacity: 0.5; cursor: not-allowed; }
.tp__activity-list {
  list-style: none; padding: 0; margin: 0;
  display: flex; flex-direction: column; gap: 4px;
  max-height: 220px; overflow-y: auto;
}
.tp__activity-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-rows: auto auto;
  column-gap: 8px;
  align-items: center;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.18);
  border-radius: 5px;
  cursor: pointer;
  font-size: 11px;
}
.tp__activity-item:hover { background: rgba(59, 130, 246, 0.12); }
.tp__activity-type {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(59, 130, 246, 0.18);
  color: #93C5FD;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 600;
  grid-column: 1; grid-row: 1;
}
.tp__activity-type[data-type="visit"] { background: rgba(34, 197, 94, 0.18); color: #86efac; }
.tp__activity-type[data-type="call"]  { background: rgba(245, 158, 11, 0.18); color: #fcd34d; }
.tp__activity-type[data-type="demo"]  { background: rgba(168, 85, 247, 0.18); color: #d8b4fe; }
.tp__activity-title {
  color: #E6E8EC;
  font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  grid-column: 2 / 4; grid-row: 1;
}
.tp__activity-relation {
  color: rgba(230, 232, 236, 0.5);
  font-size: 10px;
  grid-column: 1 / 3; grid-row: 2;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tp__activity-ago {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  font-variant-numeric: tabular-nums;
  grid-column: 3; grid-row: 2;
}
.tp__empty--err { color: #FCA5A5; }

/* Zone tag filter (chip row above the zone list). */
.tp__zone-tags {
  padding: 4px 8px 6px;
  display: flex; flex-direction: column; gap: 4px;
  background: rgba(0, 0, 0, 0.18);
  border-radius: 6px;
  margin-bottom: 6px;
}
.tp__zone-tags-label {
  font-size: 9px;
  color: rgba(230, 232, 236, 0.5);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 500;
}
.tp__zone-tags-row { display: flex; flex-wrap: wrap; gap: 4px; }
.tp__zone-tag {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(230, 232, 236, 0.7);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-family: inherit;
  cursor: pointer;
  transition: background 100ms ease, color 100ms ease, border-color 100ms ease;
}
.tp__zone-tag:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.tp__zone-tag--on {
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.5);
  color: #93C5FD;
}
.tp__zone-tag--on:hover { background: rgba(59, 130, 246, 0.28); color: #fff; }
.tp__zone-tag-clear {
  background: transparent;
  border: 0;
  color: rgba(230, 232, 236, 0.5);
  font-size: 12px;
  width: 18px; height: 18px;
  border-radius: 50%;
  cursor: pointer;
  padding: 0;
  font-family: inherit;
}
.tp__zone-tag-clear:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
</style>