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
import { useLayersStore } from '../state/layers.js'
import { call } from '../api/client.js'
import { viewportBoundsForServer } from '../lib/geo.js'
import UiNumberInput from './ui/UiNumberInput.vue'

const mapStore = useMapStore()
const ui = useUiStore()
const zoneStore = useZonesStore()
const insights = useInsightsStore()
const layerStore = useLayersStore()

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
const layerOptions = computed(() => layerStore.layers || [])
const selectedZone = ref(null)
const zoneSummary = ref(null)
const zoneSummaryLoading = ref(false)
const zoneSummaryError = ref('')

const canEdit = computed(() => {
  const owner = mapStore.activeMap?.map?.owner
  const me = window.expeditionSession?.user
  return !owner || owner === me || me === 'Administrator'
})

const customZoneActions = computed(() => {
  const list = window.Expedition?.Actions?.list?.() || []
  return list.filter((act) => act.type === 'zone')
})

const zoneActionsBusy = ref(false)
const zoneActionsError = ref('')

async function runZoneAction(act) {
  if (zoneActionsBusy.value) return
  zoneActionsBusy.value = true
  zoneActionsError.value = ''
  try {
    const zoneDoc = selectedZone.value
    const features = window.Expedition?.getFeaturesInZone?.(zoneDoc) || []
    await act.action(zoneDoc, features)
  } catch (e) {
    zoneActionsError.value = e.message || String(e)
    console.error(`[expedition] Custom zone action ${act.id} failed`, e)
  } finally {
    zoneActionsBusy.value = false
  }
}

const openSections = ref({
  zones: true,
  insights: true,
  drawing: false,
  measure: true,
  pitch: true,
  activity: true,
  coverage: true,
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
const coverageRadiusLayer = ref('')
const coverageTargetLayer = ref('')
const coverageResult = ref(null)
const coverageLoading = ref(false)
const coverageError = ref('')
const insightActionError = ref('')
const menuOpen = ref('')

const ACTIVITY_RANGE_OPTIONS = [
  { v: '7d', label: 'Last 7 days' },
  { v: '30d', label: 'Last 30 days' },
  { v: '90d', label: 'Last 90 days' },
  { v: 'all', label: 'All time' },
]
const ACTIVITY_TYPE_OPTIONS = [
  { v: '', label: 'All types' },
  { v: 'visit', label: 'Visits' },
  { v: 'call', label: 'Calls' },
  { v: 'demo', label: 'Demos' },
  { v: 'follow_up', label: 'Follow-ups' },
  { v: 'scheduled', label: 'Scheduled' },
  { v: 'note', label: 'Notes' },
]

function optionLabel(options, value, fallback = 'Select') {
  return options.find((option) => option.v === value)?.label || fallback
}

function layerLabel(layerName, fallback) {
  const layer = layerOptions.value.find((item) => item.name === layerName)
  return layer ? (layer.title || layer.name) : fallback
}

function chooseActivityRange(value) {
  activityRange.value = value
  menuOpen.value = ''
  loadActivity()
}

function chooseActivityType(value) {
  activityType.value = value
  menuOpen.value = ''
  loadActivity()
}

function chooseCoverageLayer(kind, value) {
  if (kind === 'radius') coverageRadiusLayer.value = value
  else coverageTargetLayer.value = value
  menuOpen.value = ''
}

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
  const boundsList = viewportBoundsForServer(m.getBounds()) || []
  const { start } = rangeToDates(activityRange.value)
  activityLoading.value = true
  activityError.value = ''
  try {
    const responses = await Promise.all(boundsList.map((bounds) => {
      const params = { bounds, limit: 200 }
      if (start) params.start_date = start
      if (activityUser.value) params.user = activityUser.value
      if (activityType.value) params.activity_types = [activityType.value]
      return call('expedition.api.activity.list_in_bounds', params)
    }))
    const seen = new Set()
    activityRows.value = responses.flat().filter((row) => {
      if (!row?.name || seen.has(row.name)) return false
      seen.add(row.name)
      return true
    }).slice(0, 200)
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

async function runCoverage() {
  if (!coverageRadiusLayer.value || !coverageTargetLayer.value) return
  coverageLoading.value = true
  coverageError.value = ''
  coverageResult.value = null
  try {
    coverageResult.value = await call('expedition.api.metric.radius_coverage', {
      radius_layer: coverageRadiusLayer.value,
      target_layer: coverageTargetLayer.value,
    })
  } catch (e) {
    coverageError.value = e.message || String(e)
  } finally {
    coverageLoading.value = false
  }
}

async function recomputeInsights() {
  if (!activeMapName.value) return
  insightActionError.value = ''
  try {
    await insights.recomputeFor(activeMapName.value)
  } catch (e) {
    insightActionError.value = e.message || String(e)
  }
}

const SEVERITY_RING = {
  high: 'rgba(239, 68, 68, 0.55)',
  medium: 'rgba(245, 158, 11, 0.55)',
  low: 'rgba(59, 130, 246, 0.55)',
  info: 'rgba(148, 163, 184, 0.55)',
}
function severityColor(s) { return SEVERITY_RING[s] || SEVERITY_RING.info }
function severityLabel(s) { return ({ high: 'High', medium: 'Med', low: 'Low', info: 'Info' })[s] || s }
function parseInsightDetail(ins) {
  if (!ins?.detail_json) return null
  if (typeof ins.detail_json === 'object') return ins.detail_json
  try {
    const parsed = JSON.parse(ins.detail_json)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}
function moneyInsightMetrics(ins) {
  const detail = parseInsightDetail(ins)
  return Array.isArray(detail?.metrics) ? detail.metrics : []
}
function moneyInsightSourceCount(ins) {
  const detail = parseInsightDetail(ins)
  const count = Number(detail?.source_count)
  return Number.isFinite(count) ? count : 0
}
function moneyInsightLayerLabel(ins) {
  const detail = parseInsightDetail(ins)
  return layerLabel(detail?.layer, detail?.source_doctype || ins.linked_doctype || 'Source')
}
function formatMetricValue(value) {
  if (value == null || value === '') return '0'
  const number = Number(value)
  if (Number.isFinite(number)) return number.toLocaleString()
  return String(value)
}
function metricVerb(metric) {
  if (metric?.aggregate === 'count') return `${metric.row_count || 0} rows`
  return `${metric?.aggregate || 'value'} · ${metric?.row_count || 0} rows`
}
function metricTopSources(metric) {
  return Array.isArray(metric?.top_sources) ? metric.top_sources.slice(0, 3) : []
}
function formatPercent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0%'
  return `${Math.round(n * 100)}%`
}
function metricSignalChips(metric) {
  const chips = []
  if (metric?.top_share != null) chips.push({ label: 'Top', value: formatPercent(metric.top_share) })
  if (metric?.top_3_share != null) chips.push({ label: 'Top 3', value: formatPercent(metric.top_3_share) })
  if (metric?.active_source_count != null) chips.push({ label: 'Active', value: formatMetricValue(metric.active_source_count) })
  if (metric?.zero_source_count) chips.push({ label: 'Zero', value: formatMetricValue(metric.zero_source_count) })
  for (const item of (Array.isArray(metric?.statuses) ? metric.statuses.slice(0, 3) : [])) {
    chips.push({ label: item.label || 'Status', value: formatMetricValue(item.count) })
  }
  return chips
}

function focusMoneySource(ins, source) {
  const detail = parseInsightDetail(ins)
  const layerName = detail?.layer
  if (!layerName || !source?.name) return
  const fc = layerStore.features?.[layerName]
  const feature = (fc?.features || []).find((item) => {
    const props = item?.properties || {}
    return props._name === source.name || props.name === source.name || item.id === source.name
  })
  if (!feature) return
  const props = feature.properties || {}
  const coords = Array.isArray(feature.geometry?.coordinates)
    ? feature.geometry.coordinates
    : null
  const lng = coords?.[0]
  const lat = coords?.[1]
  const layer = layerStore.layers.find((item) => item.name === layerName) || fc?.layer || { name: layerName }
  ui.clearSelectedZone()
  ui.selectedFeature = {
    layer,
    properties: props,
    _id: feature.id || props._id || props._name,
    _lngLat: typeof lng === 'number' && typeof lat === 'number' ? { lng, lat } : null,
  }
  const map = window.expeditionMap?.getMap?.()
  if (map && typeof lng === 'number' && typeof lat === 'number') {
    map.easeTo({
      center: [lng, lat],
      zoom: Math.max(map.getZoom(), 13),
      duration: 600,
    })
  }
}

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
  loadZoneSummary(zone)
}

async function loadZoneSummary(zone) {
  selectedZone.value = zone || null
  zoneSummary.value = null
  zoneSummaryError.value = ''
  if (!zone?.name) return
  zoneSummaryLoading.value = true
  try {
    zoneSummary.value = await call('expedition.api.metric.summarize_zone', {
      zone_name: zone.name,
    })
  } catch (e) {
    zoneSummaryError.value = e.message || String(e)
  } finally {
    zoneSummaryLoading.value = false
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

        <div v-if="openSections.zones && selectedZone" class="tp__zone-summary">
          <div class="tp__zone-summary-head">
            <span class="tp__zone-summary-title">{{ selectedZone.title || selectedZone.name }}</span>
            <button type="button" class="tp__zone-summary-refresh" :disabled="zoneSummaryLoading" @click="loadZoneSummary(selectedZone)">
              {{ zoneSummaryLoading ? 'Loading…' : 'Refresh' }}
            </button>
          </div>
          <p v-if="zoneSummaryError" class="tp__empty tp__empty--err">{{ zoneSummaryError }}</p>
          <p v-else-if="zoneSummaryLoading" class="tp__empty">Calculating zone metrics…</p>
          <div v-else-if="zoneSummary" class="tp__zone-metrics">
            <div v-for="row in zoneSummary.layers" :key="row.layer" class="tp__zone-metric">
              <span class="tp__zone-metric-label">{{ row.title || row.source_doctype }}</span>
              <span class="tp__zone-metric-value">{{ row.count }}</span>
            </div>
            <div v-for="metric in zoneSummary.metrics" :key="metric.label" class="tp__zone-metric">
              <span class="tp__zone-metric-label">{{ metric.label }}</span>
              <span class="tp__zone-metric-value">{{ metric.value ?? '—' }}</span>
            </div>
          </div>
          <div v-if="customZoneActions.length && zoneSummary" class="tp__zone-actions">
            <button
              v-for="act in customZoneActions"
              :key="act.id"
              type="button"
              class="tp__zone-action"
              :disabled="zoneActionsBusy"
              @click="runZoneAction(act)"
              :title="act.label"
            >
              {{ act.label }}
            </button>
            <p v-if="zoneActionsError" class="tp__action-err">{{ zoneActionsError }}</p>
          </div>
        </div>

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
            <div class="tp__menu">
              <button type="button" class="tp__select" @click="menuOpen = menuOpen === 'activity_range' ? '' : 'activity_range'">
                <span>{{ optionLabel(ACTIVITY_RANGE_OPTIONS, activityRange) }}</span>
                <span class="tp__select-chevron">⌄</span>
              </button>
              <div v-if="menuOpen === 'activity_range'" class="tp__menu-pop">
                <button
                  v-for="option in ACTIVITY_RANGE_OPTIONS"
                  :key="option.v"
                  type="button"
                  class="tp__menu-option"
                  :data-active="activityRange === option.v"
                  @mousedown.prevent="chooseActivityRange(option.v)"
                >{{ option.label }}</button>
              </div>
            </div>
            <div class="tp__menu">
              <button type="button" class="tp__select" @click="menuOpen = menuOpen === 'activity_type' ? '' : 'activity_type'">
                <span>{{ optionLabel(ACTIVITY_TYPE_OPTIONS, activityType) }}</span>
                <span class="tp__select-chevron">⌄</span>
              </button>
              <div v-if="menuOpen === 'activity_type'" class="tp__menu-pop">
                <button
                  v-for="option in ACTIVITY_TYPE_OPTIONS"
                  :key="option.v || 'all'"
                  type="button"
                  class="tp__menu-option"
                  :data-active="activityType === option.v"
                  @mousedown.prevent="chooseActivityType(option.v)"
                >{{ option.label }}</button>
              </div>
            </div>
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

      <section v-if="mapStore.activeMap" class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.coverage = !openSections.coverage">
            <span>Coverage</span>
            <span class="tp__chevron" :data-open="openSections.coverage">▾</span>
          </button>
        </div>
        <div v-if="openSections.coverage" class="tp__coverage">
          <div class="tp__coverage-controls">
            <div class="tp__menu">
              <button type="button" class="tp__select" @click="menuOpen = menuOpen === 'coverage_radius' ? '' : 'coverage_radius'">
                <span>{{ layerLabel(coverageRadiusLayer, 'Radius layer') }}</span>
                <span class="tp__select-chevron">⌄</span>
              </button>
              <div v-if="menuOpen === 'coverage_radius'" class="tp__menu-pop">
                <button type="button" class="tp__menu-option" :data-active="!coverageRadiusLayer" @mousedown.prevent="chooseCoverageLayer('radius', '')">Radius layer</button>
                <button
                  v-for="layer in layerOptions"
                  :key="'radius-' + layer.name"
                  type="button"
                  class="tp__menu-option"
                  :data-active="coverageRadiusLayer === layer.name"
                  @mousedown.prevent="chooseCoverageLayer('radius', layer.name)"
                >{{ layer.title || layer.name }}</button>
              </div>
            </div>
            <div class="tp__menu">
              <button type="button" class="tp__select" @click="menuOpen = menuOpen === 'coverage_target' ? '' : 'coverage_target'">
                <span>{{ layerLabel(coverageTargetLayer, 'Target layer') }}</span>
                <span class="tp__select-chevron">⌄</span>
              </button>
              <div v-if="menuOpen === 'coverage_target'" class="tp__menu-pop tp__menu-pop--right">
                <button type="button" class="tp__menu-option" :data-active="!coverageTargetLayer" @mousedown.prevent="chooseCoverageLayer('target', '')">Target layer</button>
                <button
                  v-for="layer in layerOptions"
                  :key="'target-' + layer.name"
                  type="button"
                  class="tp__menu-option"
                  :data-active="coverageTargetLayer === layer.name"
                  @mousedown.prevent="chooseCoverageLayer('target', layer.name)"
                >{{ layer.title || layer.name }}</button>
              </div>
            </div>
          </div>
          <button
            type="button"
            class="tp__activity-refresh"
            :disabled="coverageLoading || !coverageRadiusLayer || !coverageTargetLayer"
            @click="runCoverage"
          >{{ coverageLoading ? 'Analyzing…' : 'Analyze' }}</button>
          <p v-if="coverageError" class="tp__empty tp__empty--err">{{ coverageError }}</p>
          <div v-if="coverageResult" class="tp__coverage-grid">
            <div class="tp__coverage-stat">
              <span>Covered</span>
              <strong>{{ coverageResult.covered }}</strong>
            </div>
            <div class="tp__coverage-stat">
              <span>Uncovered</span>
              <strong>{{ coverageResult.uncovered }}</strong>
            </div>
            <div class="tp__coverage-stat">
              <span>Overlap</span>
              <strong>{{ coverageResult.overlap }}</strong>
            </div>
            <div class="tp__coverage-stat">
              <span>Coverage</span>
              <strong>{{ Math.round(coverageResult.coverage_percent || 0) }}%</strong>
            </div>
          </div>
        </div>
      </section>

      <!-- Insights -->
      <section class="tp__section">
        <div class="tp__section-bar">
          <button class="tp__section-toggle" type="button" @click="openSections.insights = !openSections.insights">
            <span>Insights</span>
            <span class="tp__chevron" :data-open="openSections.insights">▾</span>
          </button>
          <button
            type="button"
            class="tp__mini-action"
            :disabled="insights.recomputing || !activeMapName"
            @click="recomputeInsights"
          >
            {{ insights.recomputing ? 'Working' : 'Refresh' }}
          </button>
        </div>
        <ul v-if="openSections.insights && insightList.length" class="tp__list tp__insight-list">
          <li v-for="ins in insightList" :key="ins.name" class="tp__item tp__insight-item">
            <button
              type="button"
              class="tp__chip"
              :class="{ 'tp__chip--money': ins.insight_type === 'linked_money' }"
              :style="{ '--ring': severityColor(ins.severity) }"
              :title="ins.summary"
              @click="onInsightClick(ins)"
            >
              <span class="tp__chip-sev">{{ severityLabel(ins.severity) }}</span>
              <span class="tp__chip-title">{{ ins.title }}</span>
            </button>
            <div v-if="ins.insight_type === 'linked_money'" class="tp__money-insight">
              <div class="tp__money-head">
                <span>{{ moneyInsightLayerLabel(ins) }}</span>
                <strong>{{ moneyInsightSourceCount(ins).toLocaleString() }} mapped</strong>
              </div>
              <p class="tp__money-summary">{{ ins.summary }}</p>
              <div v-if="moneyInsightMetrics(ins).length" class="tp__money-metrics">
                <div
                  v-for="metric in moneyInsightMetrics(ins)"
                  :key="metric.key"
                  class="tp__money-metric"
                >
                  <span class="tp__money-label">{{ metric.label || metric.key }}</span>
                  <strong class="tp__money-value">{{ formatMetricValue(metric.value) }}</strong>
                  <small>{{ metric.source_doctype }} · {{ metricVerb(metric) }}</small>
                  <div v-if="metricSignalChips(metric).length" class="tp__money-signals">
                    <span
                      v-for="chip in metricSignalChips(metric)"
                      :key="chip.label"
                    >
                      {{ chip.label }} <strong>{{ chip.value }}</strong>
                    </span>
                  </div>
                  <div v-if="metricTopSources(metric).length" class="tp__money-top">
                    <button
                      v-for="source in metricTopSources(metric)"
                      :key="source.name"
                      type="button"
                      class="tp__money-top-row"
                      :title="'Focus ' + (source.label || source.name)"
                      @click="focusMoneySource(ins, source)"
                    >
                      <span>{{ source.label || source.name }}</span>
                      <strong>{{ formatMetricValue(source.value) }}</strong>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </li>
        </ul>
        <p v-if="openSections.insights && insightActionError" class="tp__error">{{ insightActionError }}</p>
        <p v-else-if="openSections.insights && insights.loading" class="tp__empty">Loading insights.</p>
        <p v-else-if="openSections.insights && !insights.loading && !insightList.length" class="tp__empty">No active insights.</p>
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
        </div>
        <div v-if="openSections.pitch" class="tp__pitch-body">
          <div class="tp__row">
            <span class="tp__row-label">Tilt</span>
            <input
              type="range" min="0" max="80" step="1"
              :value="ui.pitchDegrees"
              @input="(e) => ui.setPitch(e.target.value)"
              class="tp__slider"
              aria-label="Pitch degrees"
            />
            <div class="tp__num-wrap">
              <UiNumberInput
                :model-value="ui.pitchDegrees"
                min="0"
                max="80"
                step="1"
                compact
                aria-label="Pitch degrees (type)"
                @update:model-value="value => ui.setPitch(value)"
              />
              <span class="tp__row-suffix">°</span>
            </div>
          </div>
          <p class="tp__hint">Pins extrude as 3D columns while tilt is above 0°. Each layer's height defaults to 200m; pick a numeric field in the layer row for per-feature heights.</p>
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
.tp__mini-action {
  flex: none;
  min-height: 24px;
  border: 1px solid rgba(59, 130, 246, 0.34);
  border-radius: 7px;
  background: rgba(59, 130, 246, 0.12);
  color: rgba(191, 219, 254, 0.95);
  padding: 4px 8px;
  font-family: inherit;
  font-size: 10px;
  font-weight: 650;
  cursor: pointer;
}
.tp__mini-action:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.2);
  color: #fff;
}
.tp__mini-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
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
.tp__error {
  margin: 6px 4px 0;
  color: #FCA5A5;
  font-size: 11px;
  line-height: 1.4;
}
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
.tp__chip--money {
  border-radius: 8px 8px 6px 6px;
  border-color: rgba(34, 197, 94, 0.38);
  background: rgba(34, 197, 94, 0.08);
}
.tp__chip-sev {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; padding: 2px 7px; border-radius: 999px;
  color: #0b0e14; background: var(--ring);
}
.tp__chip-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tp__insight-list {
  gap: 6px;
}
.tp__insight-item {
  min-width: 0;
  display: grid;
  gap: 0;
}
.tp__money-insight {
  min-width: 0;
  display: grid;
  gap: 7px;
  margin-top: -1px;
  padding: 8px;
  border: 1px solid rgba(34, 197, 94, 0.18);
  border-top: 0;
  border-radius: 0 0 8px 8px;
  background: rgba(34, 197, 94, 0.045);
}
.tp__money-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.tp__money-head span {
  min-width: 0;
  color: rgba(220, 252, 231, 0.82);
  font-size: 11px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__money-head strong {
  flex: none;
  color: #BBF7D0;
  font-size: 10px;
  font-weight: 700;
}
.tp__money-summary {
  margin: 0;
  color: rgba(230, 232, 236, 0.62);
  font-size: 11px;
  line-height: 1.35;
}
.tp__money-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}
.tp__money-metric {
  min-width: 0;
  display: grid;
  gap: 2px;
  padding: 7px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
.tp__money-label,
.tp__money-metric small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__money-label {
  color: rgba(187, 247, 208, 0.68);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.tp__money-value {
  color: #DCFCE7;
  font-size: 14px;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.tp__money-metric small {
  color: rgba(230, 232, 236, 0.46);
  font-size: 10px;
}
.tp__money-signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
  margin-top: 3px;
}
.tp__money-signals span {
  max-width: 100%;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.11);
  color: rgba(187, 247, 208, 0.72);
  padding: 2px 6px;
  font-size: 9px;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tp__money-signals strong {
  color: #DCFCE7;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
}
.tp__money-top {
  display: grid;
  gap: 2px;
  margin-top: 4px;
  padding-top: 5px;
  border-top: 1px solid rgba(255, 255, 255, 0.055);
}
.tp__money-top-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px;
  align-items: center;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: inherit;
  padding: 2px 3px;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.tp__money-top-row:hover,
.tp__money-top-row:focus {
  outline: 0;
  background: rgba(34, 197, 94, 0.1);
}
.tp__money-top-row span,
.tp__money-top-row strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__money-top-row span {
  color: rgba(230, 232, 236, 0.58);
  font-size: 10px;
}
.tp__money-top-row strong {
  color: #BBF7D0;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

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
.tp__pitch-body { padding: 6px 8px 8px; display: flex; flex-direction: column; gap: 6px; }
.tp__row {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 4px;
}
.tp__row-label {
  font-size: 10px; color: rgba(230, 232, 236, 0.5);
  text-transform: uppercase; letter-spacing: 0.06em;
  flex: none; min-width: 32px;
}
.tp__slider { flex: 1; accent-color: #3B82F6; }
/* Number-input + degree-suffix cluster: sit close together, separated
   from the slider by the row's gap. */
.tp__pitch-body .tp__row > div {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}
.tp__num {
  width: 44px;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  color: #E6E8EC;
  padding: 3px 5px;
  font-size: 11px;
  font-family: inherit;
  font-variant-numeric: tabular-nums;
  text-align: right;
  flex: none;
  -moz-appearance: textfield;
}
.tp__num::-webkit-outer-spin-button,
.tp__num::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.tp__num:focus { outline: none; border-color: rgba(59, 130, 246, 0.6); }
.tp__row-suffix {
  font-size: 11px;
  color: rgba(230, 232, 236, 0.5);
  font-variant-numeric: tabular-nums;
  flex: none;
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
.tp__menu {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
}
.tp__select {
  width: 100%;
  min-height: 29px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  flex: 1;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 5px;
  color: #E6E8EC;
  padding: 4px 6px;
  font-size: 11px;
  font-family: inherit;
  outline: none;
  cursor: pointer;
}
.tp__select span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__select:hover,
.tp__select:focus { border-color: rgba(59, 130, 246, 0.6); }
.tp__select-chevron {
  flex: none;
  color: rgba(230, 232, 236, 0.5);
}
.tp__menu-pop {
  position: absolute;
  z-index: 70;
  top: calc(100% + 5px);
  left: 0;
  min-width: 100%;
  max-width: 260px;
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.38);
}
.tp__menu-pop--right {
  left: auto;
  right: 0;
}
.tp__menu-option {
  width: 100%;
  min-width: 0;
  padding: 7px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.86);
  text-align: left;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__menu-option:hover,
.tp__menu-option[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
  color: #fff;
}
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

.tp__coverage {
  padding: 6px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tp__coverage-controls {
  display: flex;
  gap: 4px;
}
.tp__coverage-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px;
}
.tp__coverage-stat {
  background: rgba(0, 0, 0, 0.18);
  border-radius: 6px;
  padding: 7px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tp__coverage-stat span {
  color: rgba(230, 232, 236, 0.55);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.tp__coverage-stat strong {
  color: #E6E8EC;
  font-size: 16px;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

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

.tp__zone-summary {
  margin: 6px 8px 8px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 7px;
}
.tp__zone-summary-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.tp__zone-summary-title {
  min-width: 0;
  color: #E6E8EC;
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__zone-summary-refresh {
  flex: none;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.30);
  color: #93C5FD;
  padding: 3px 7px;
  border-radius: 5px;
  font-size: 10px;
  font-family: inherit;
  cursor: pointer;
}
.tp__zone-summary-refresh:disabled {
  opacity: 0.5;
  cursor: default;
}
.tp__zone-metrics {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tp__zone-metric {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 7px;
  background: rgba(0, 0, 0, 0.18);
  border-radius: 5px;
}
.tp__zone-metric-label {
  min-width: 0;
  color: rgba(230, 232, 236, 0.72);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp__zone-metric-value {
  flex: none;
  color: #E6E8EC;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tp__zone-actions {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 10px;
}
.tp__zone-action {
  width: 100%;
  padding: 6px 12px;
  background: rgba(59, 130, 246, 0.16);
  border: 1px solid rgba(59, 130, 246, 0.34);
  border-radius: 4px;
  color: #BFDBFE;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  text-align: center;
  transition: all 0.15s ease;
}
.tp__zone-action:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.28);
  border-color: rgba(59, 130, 246, 0.54);
}
.tp__zone-action:disabled {
  opacity: 0.55;
  cursor: default;
}
.tp__action-err {
  font-size: 10px;
  color: #FCA5A5;
  margin: 4px 0 0 0;
}
</style>
