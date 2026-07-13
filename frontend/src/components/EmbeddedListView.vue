<script setup>
import { computed, ref } from 'vue'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { openDeskDoc } from '../lib/desk.js'

defineEmits(['close'])

const layers = useLayersStore()
const ui = useUiStore()
const query = ref('')
const activeLayer = ref('')
const metricOnly = ref(false)
const sortKey = ref('record')
const sortDir = ref('asc')

const visibleLayerOptions = computed(() =>
  (layers.visibleLayers || []).map((layer) => ({
    name: layer.name,
    title: layer.title || layer.name,
  }))
)

const layerByName = computed(() => {
  const out = new Map()
  for (const layer of layers.layers || []) out.set(layer.name, layer)
  return out
})

const filteredRows = computed(() => {
  const q = query.value.trim().toLowerCase()
  const out = []
  for (const layer of layers.visibleLayers || []) {
    if (activeLayer.value && activeLayer.value !== layer.name) continue
    const fc = layers.features?.[layer.name]
    for (const feature of fc?.features || []) {
      const props = feature.properties || {}
      const metrics = props._metrics && typeof props._metrics === 'object' ? props._metrics : {}
      if (metricOnly.value && !Object.keys(metrics).length) continue
      const label = props._label || props.title || props.name || props._name || feature.id || ''
      const city = props.city || props._location_city || props.location || ''
      const haystack = [
        label,
        props._name,
        props._doctype,
        layer.title,
        layer.source_doctype,
        city,
        ...Object.values(metrics),
      ].join(' ').toLowerCase()
      if (q && !haystack.includes(q)) continue
      out.push({
        id: `${layer.name}:${props._id || props._name || feature.id || out.length}`,
        layer,
        feature,
        props,
        label,
        city,
        metrics,
      })
    }
  }
  return out
})

const rows = computed(() => {
  const key = sortKey.value || 'record'
  const dir = sortDir.value === 'desc' ? -1 : 1
  return [...filteredRows.value].sort((a, b) => compareRows(a, b, key) * dir)
})

const totalRendered = computed(() => {
  let total = 0
  for (const layer of layers.visibleLayers || []) {
    total += layers.features?.[layer.name]?.features?.length || 0
  }
  return total
})

const metricKeys = computed(() => {
  const keys = new Set()
  for (const row of filteredRows.value) {
    for (const key of Object.keys(row.metrics || {})) keys.add(key)
  }
  return [...keys].sort((a, b) => metricLabel(a).localeCompare(metricLabel(b)))
})

const tableMinWidth = computed(() => `${560 + metricKeys.value.length * 116}px`)

const metricRowCount = computed(() =>
  filteredRows.value.filter((row) => Object.keys(row.metrics || {}).length).length
)

const metricTotals = computed(() =>
  metricKeys.value.map((key) => {
    let total = 0
    let count = 0
    for (const row of filteredRows.value) {
      const value = metricNumber(row.metrics?.[key])
      if (value == null) continue
      total += value
      count += 1
    }
    return { key, label: metricLabel(key), total, count }
  }).filter((item) => item.count)
)

const topMetricKey = computed(() => {
  const [top] = [...metricTotals.value].sort((a, b) => Math.abs(b.total) - Math.abs(a.total))
  return top?.key || metricKeys.value[0] || ''
})

function layerColor(layer) {
  return layers.getLayerStyle(layer.name)?.color || layer.color || '#3B82F6'
}

function formatMetric(value) {
  if (value == null || value === '') return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(n)
}

function metricLabel(key) {
  return String(key || '')
    .replace(/^_metric_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function sortLabel(key) {
  if (key === 'record') return 'Record'
  if (key === 'layer') return 'Layer'
  if (key === 'location') return 'Location'
  if (key.startsWith('metric:')) return metricLabel(key.slice(7))
  return key
}

function metricNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function compareText(a, b) {
  return String(a || '').localeCompare(String(b || ''), undefined, { numeric: true, sensitivity: 'base' })
}

function compareRows(a, b, key) {
  if (key === 'layer') return compareText(a.layer.title || a.layer.name, b.layer.title || b.layer.name)
  if (key === 'location') return compareText(a.city, b.city)
  if (key.startsWith('metric:')) {
    const metric = key.slice(7)
    const av = metricNumber(a.metrics?.[metric])
    const bv = metricNumber(b.metrics?.[metric])
    if (av == null && bv == null) return compareText(a.label, b.label)
    if (av == null) return 1
    if (bv == null) return -1
    if (av !== bv) return av - bv
    return compareText(a.label, b.label)
  }
  return compareText(a.label || a.props._name || a.feature.id, b.label || b.props._name || b.feature.id)
}

function setSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    return
  }
  sortKey.value = key
  sortDir.value = key.startsWith('metric:') ? 'desc' : 'asc'
}

function focusTopMetric() {
  if (topMetricKey.value) {
    sortKey.value = `metric:${topMetricKey.value}`
    sortDir.value = 'desc'
  }
}

function rowCoords(row) {
  const coords = row.feature?.geometry?.coordinates
  if (!Array.isArray(coords) || coords.length < 2) return null
  const [lng, lat] = coords
  if (typeof lng !== 'number' || typeof lat !== 'number') return null
  return { lng, lat }
}

function selectRow(row) {
  const coords = rowCoords(row)
  ui.clearSelectedZone()
  ui.selectedFeature = {
    layer: layerByName.value.get(row.layer.name) || row.layer,
    properties: row.props,
    _id: row.feature.id || row.props._id || row.props._name,
    _lngLat: coords,
  }
  const map = window.expeditionMap?.getMap?.()
  if (map && coords) {
    map.easeTo({
      center: [coords.lng, coords.lat],
      zoom: Math.max(map.getZoom(), 13),
      duration: 500,
    })
  }
}

function openRow(row) {
  openDeskDoc(row.props?._doctype || row.layer?.source_doctype, row.props?._name || row.feature?.id)
}

function onRowKeydown(event, row) {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    openRow(row)
    return
  }
  if (event.key?.toLowerCase?.() === 'o') {
    event.preventDefault()
    openRow(row)
  }
}
</script>

<template>
  <section class="elv" role="region" aria-label="Embedded record list">
    <header class="elv__header">
      <div class="elv__title">
        <span>Records</span>
        <small>{{ rows.length }} of {{ totalRendered }}</small>
      </div>
      <button type="button" class="elv__icon-btn" aria-label="Close list" @click="$emit('close')">×</button>
    </header>

    <div class="elv__tools">
      <label class="elv__search">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M11 4a7 7 0 1 0 4.95 11.95L21 21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
        </svg>
        <input v-model="query" type="search" placeholder="Search rendered records" />
      </label>
      <button
        type="button"
        class="elv__toggle"
        :class="{ 'elv__toggle--on': metricOnly }"
        :aria-pressed="metricOnly ? 'true' : 'false'"
        @click="metricOnly = !metricOnly"
      >
        Metrics
      </button>
      <button
        type="button"
        class="elv__toggle"
        :class="{ 'elv__toggle--on': sortKey === `metric:${topMetricKey}` && sortDir === 'desc' }"
        :disabled="!metricKeys.length"
        @click="focusTopMetric"
      >
        Top value
      </button>
      <span class="elv__sort-status">
        {{ sortLabel(sortKey) }} {{ sortDir === 'asc' ? '↑' : '↓' }}
      </span>
    </div>

    <div v-if="metricTotals.length" class="elv__metric-strip" aria-label="Metric summary">
      <span class="elv__metric-pill">
        Metric rows <strong>{{ metricRowCount.toLocaleString() }}</strong>
      </span>
      <button
        v-for="item in metricTotals"
        :key="item.key"
        type="button"
        class="elv__metric-pill elv__metric-pill--button"
        @click="setSort(`metric:${item.key}`)"
      >
        {{ item.label }} <strong>{{ formatMetric(item.total) }}</strong>
      </button>
    </div>

    <div class="elv__layers" role="tablist" aria-label="Layer filter">
      <button
        type="button"
        class="elv__chip"
        :class="{ 'elv__chip--active': !activeLayer }"
        @click="activeLayer = ''"
      >
        All
      </button>
      <button
        v-for="layer in visibleLayerOptions"
        :key="layer.name"
        type="button"
        class="elv__chip"
        :class="{ 'elv__chip--active': activeLayer === layer.name }"
        @click="activeLayer = layer.name"
      >
        {{ layer.title }}
      </button>
    </div>

    <div class="elv__table-wrap">
      <table class="elv__table" :style="{ minWidth: tableMinWidth }">
        <thead>
          <tr>
            <th>
              <button type="button" class="elv__sort" @click="setSort('record')">
                <span>Record</span>
                <small v-if="sortKey === 'record'">{{ sortDir === 'asc' ? '↑' : '↓' }}</small>
              </button>
            </th>
            <th>
              <button type="button" class="elv__sort" @click="setSort('layer')">
                <span>Layer</span>
                <small v-if="sortKey === 'layer'">{{ sortDir === 'asc' ? '↑' : '↓' }}</small>
              </button>
            </th>
            <th>
              <button type="button" class="elv__sort" @click="setSort('location')">
                <span>Location</span>
                <small v-if="sortKey === 'location'">{{ sortDir === 'asc' ? '↑' : '↓' }}</small>
              </button>
            </th>
            <th v-for="key in metricKeys" :key="key">
              <button type="button" class="elv__sort elv__sort--number" @click="setSort(`metric:${key}`)">
                <span>{{ metricLabel(key) }}</span>
                <small v-if="sortKey === `metric:${key}`">{{ sortDir === 'asc' ? '↑' : '↓' }}</small>
              </button>
            </th>
            <th class="elv__action-head">Open</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            tabindex="0"
            @click="selectRow(row)"
            @keydown.enter.prevent="selectRow(row)"
            @keydown="onRowKeydown($event, row)"
          >
            <td>
              <strong>{{ row.label || row.props._name || 'Untitled' }}</strong>
              <small>{{ row.props._doctype || row.layer.source_doctype || 'Source' }} · {{ row.props._name || row.feature.id || '-' }}</small>
            </td>
            <td class="elv__layer-cell">
              <span class="elv__layer-dot" :style="{ background: layerColor(row.layer) }" />
              <span>{{ row.layer.title || row.layer.name }}</span>
            </td>
            <td>{{ row.city || '-' }}</td>
            <td v-for="key in metricKeys" :key="key" class="elv__number">{{ formatMetric(row.metrics?.[key]) }}</td>
            <td class="elv__action-cell">
              <button
                type="button"
                class="elv__open-btn"
                :disabled="!(row.props._doctype || row.layer.source_doctype) || !(row.props._name || row.feature.id)"
                title="Open in Desk"
                :aria-label="'Open ' + (row.label || row.props._name || 'record') + ' in Desk'"
                @click.stop="openRow(row)"
              >
                Open
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="elv__empty">No rendered records match the current filters.</div>
    </div>
  </section>
</template>

<style scoped>
.elv {
  width: min(646px, calc(100vw - 38px));
  height: 100%;
  max-height: none;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-right: 0;
  border-radius: 8px 0 0 8px;
  background: rgba(14, 18, 24, 0.88);
  color: rgba(244, 247, 251, 0.94);
  box-shadow: -18px 0 60px rgba(0, 0, 0, 0.42);
  backdrop-filter: blur(18px) saturate(1.2);
  pointer-events: auto;
}
.elv__header,
.elv__tools,
.elv__layers {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
}
.elv__header {
  justify-content: space-between;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.elv__title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.elv__title span {
  font-size: 13px;
  font-weight: 720;
}
.elv__title small,
.elv__table small {
  color: rgba(244, 247, 251, 0.54);
  font-size: 10px;
}
.elv__icon-btn {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(244, 247, 251, 0.74);
  cursor: pointer;
}
.elv__icon-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #fff;
}
.elv__tools {
  flex-wrap: wrap;
  padding-bottom: 0;
}
.elv__search {
  min-width: 0;
  flex: 1 1 auto;
  min-width: 220px;
  display: flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 9px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.22);
}
.elv__search svg {
  width: 15px;
  height: 15px;
  color: rgba(244, 247, 251, 0.52);
  flex: none;
}
.elv__search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
}
.elv__search input::placeholder {
  color: rgba(244, 247, 251, 0.42);
}
.elv__toggle,
.elv__chip {
  flex: none;
  min-height: 28px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.05);
  color: rgba(244, 247, 251, 0.72);
  padding: 5px 9px;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.elv__toggle--on,
.elv__chip--active {
  border-color: rgba(59, 130, 246, 0.7);
  background: rgba(59, 130, 246, 0.18);
  color: #fff;
}
.elv__toggle:disabled {
  opacity: 0.48;
  cursor: default;
}
.elv__sort-status {
  flex: none;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(244, 247, 251, 0.48);
  font-size: 10px;
}
.elv__metric-strip {
  flex: none;
  display: flex;
  gap: 6px;
  min-width: 0;
  overflow-x: auto;
  padding: 8px 10px 0;
  scrollbar-width: none;
}
.elv__metric-strip::-webkit-scrollbar {
  display: none;
}
.elv__metric-pill {
  flex: none;
  max-width: 220px;
  min-height: 27px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(34, 197, 94, 0.18);
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.08);
  color: rgba(187, 247, 208, 0.78);
  padding: 4px 8px;
  font: inherit;
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.elv__metric-pill strong {
  color: #DCFCE7;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
}
.elv__metric-pill--button {
  cursor: pointer;
}
.elv__metric-pill--button:hover {
  border-color: rgba(34, 197, 94, 0.42);
  background: rgba(34, 197, 94, 0.14);
}
.elv__layers {
  overflow-x: auto;
  padding-top: 8px;
  scrollbar-width: none;
}
.elv__layers::-webkit-scrollbar {
  display: none;
}
.elv__table-wrap {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  margin: 8px 10px 10px;
}
.elv__table {
  width: 100%;
  min-width: 680px;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
}

@media (max-width: 720px) {
  .elv {
    width: calc(100vw - 38px);
  }
  .elv__search {
    flex-basis: 100%;
  }
  .elv__sort-status {
    display: none;
  }
}
.elv__table th,
.elv__table td {
  padding: 8px 9px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  text-align: left;
  vertical-align: middle;
  font-size: 11px;
}
.elv__table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: rgba(14, 18, 24, 0.94);
  color: rgba(244, 247, 251, 0.54);
  font-size: 10px;
  font-weight: 680;
  text-transform: uppercase;
}
.elv__sort {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 5px;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
  font: inherit;
  text-align: left;
  text-transform: inherit;
  cursor: pointer;
}
.elv__sort--number {
  justify-content: flex-end;
}
.elv__sort span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.elv__sort small {
  flex: none;
  color: #93C5FD;
  font-size: 11px;
}
.elv__sort:hover {
  color: #fff;
}
.elv__table tbody tr {
  cursor: pointer;
}
.elv__table tbody tr:hover,
.elv__table tbody tr:focus {
  outline: 0;
  background: rgba(59, 130, 246, 0.11);
}
.elv__table strong,
.elv__table small {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.elv__table strong {
  color: rgba(244, 247, 251, 0.92);
  font-size: 12px;
}
.elv__layer-cell span:last-child {
  color: rgba(244, 247, 251, 0.76);
  font-size: 11px;
}
.elv__layer-dot {
  width: 8px;
  height: 8px;
  display: inline-block;
  margin-right: 6px;
  border-radius: 50%;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.12);
  vertical-align: middle;
}
.elv__number {
  color: rgba(244, 247, 251, 0.86);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.elv__action-head,
.elv__action-cell {
  width: 74px;
  text-align: right;
}
.elv__open-btn {
  min-width: 54px;
  height: 26px;
  border: 1px solid rgba(147, 197, 253, 0.22);
  border-radius: 7px;
  background: rgba(59, 130, 246, 0.13);
  color: rgba(219, 234, 254, 0.92);
  font: inherit;
  font-size: 10px;
  font-weight: 680;
  cursor: pointer;
}
.elv__open-btn:hover {
  border-color: rgba(147, 197, 253, 0.5);
  background: rgba(59, 130, 246, 0.22);
  color: #fff;
}
.elv__open-btn:disabled {
  opacity: 0.44;
  cursor: default;
}
.elv__empty {
  padding: 24px 8px 28px;
  color: rgba(244, 247, 251, 0.52);
  font-size: 12px;
  text-align: center;
}
@media (max-width: 720px) {
  .elv {
    width: calc(100vw - 16px);
    max-height: 56vh;
  }
  .elv__header,
  .elv__tools,
  .elv__layers {
    padding-left: 8px;
    padding-right: 8px;
  }
  .elv__table {
    min-width: 560px;
  }
}
</style>
