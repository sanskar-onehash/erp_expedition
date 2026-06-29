<script setup>
/**
 * LayerPanel — left-edge slide-in panel. PR-2 of the quiet-canvas plan.
 *
 * Single section: Layers. Contains the active layers + an inline
 * search/select picker at the top to attach more layers from the
 * user's master mappings. Zones live in ToolsPanel (PR-3). Skin picker
 * lives in BasemapPanel (PR-1).
 *
 * Header: map title + layer count + close. No save button — layer
 * config and zones are auto-saved on every edit; the "map card" save
 * flow (title/camera/skin) was removed because it duplicated live
 * persistence and confused users.
 *
 * Width: 300px. Max height: 100vh minus 24px top/bottom. Translucent,
 * same glass as the rest of the chrome.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { ICON_PATHS } from '../api/icons.js'
import FilterBuilder from './FilterBuilder.vue'
import { filterCount, parseFilterRows, serializeFilterRows, summarizeFilterRows } from '../lib/filters.js'

const mapStore = useMapStore()
const layerStore = useLayersStore()
const ui = useUiStore()

const activeMapName = computed(() => mapStore.activeMap?.map?.name)
const templates = computed(() => mapStore.templates || [])
const activeLayers = computed(() => layerStore.layers || [])
const masters = computed(() => layerStore.masters || [])

const layersOpen = ref(true)
const templatesOpen = ref(true)

// Inline add-layer picker state.
const pickerQuery = ref('')
const pickerOpen = ref(false)
const pickerRoot = ref(null) // template ref for document-click-outside check

const filteredMasters = computed(() => {
  if (!activeMapName.value) return []
  const attached = new Set(
    activeLayers.value
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
  const owner = mapStore.activeMap?.map?.owner
  const me = window.expeditionSession?.user
  return !owner || owner === me || me === 'Administrator'
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
function toggleLayer(layerName, event) {
  layerStore.updateLayer(layerName, { enabled: event.target.checked ? 1 : 0 })
}
function editLayer(layer) { ui.openLayerEditor(layer) }
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
  if (event.target?.closest?.('.fb__floating-menu')) return
  const root = pickerRoot.value
  if (root && !root.contains(event.target)) closePicker()
  if (quickFilterFor.value && !containsTarget(quickFilterPopupRoot.value, event.target)) {
    quickFilterFor.value = null
  }
}
watch(
  () => pickerOpen.value || !!quickFilterFor.value,
  (open) => {
    if (open) {
      document.addEventListener('mousedown', onDocumentMouseDown, true)
    } else {
      document.removeEventListener('mousedown', onDocumentMouseDown, true)
    }
  }
)
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
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
})
watch(activeMapName, () => {
  if (layerStore.masters.length === 0) layerStore.loadMasters()
})

defineExpose({})
</script>

<template>
  <aside class="lp" role="region" aria-label="Layers">
    <header class="lp__header">
      <div class="lp__title-wrap">
        <div class="lp__title">{{ mapStore.activeMap?.map?.title || 'Expedition' }}</div>
        <div class="lp__subtitle">{{ activeLayers.length }} layers</div>
      </div>
      <button class="lp__close" type="button" aria-label="Close layers" @click="$emit('close')">×</button>
    </header>

    <div class="lp__body">
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
              <label class="lp__row lp__row--layer">
                <input
                  type="checkbox"
                  :checked="layer.enabled !== false && layer.enabled !== 0"
                  @change="(e) => toggleLayer(layer.name, e)"
                />
                <span class="lp__swatch" :style="{ background: layer.color || '#3B82F6' }">
                  <svg v-if="layer.icon" class="lp__swatch-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path :d="iconPath(layer.icon)" fill="currentColor" />
                  </svg>
                </span>
                <span class="lp__label">{{ layer.title || layer.name }}</span>
              </label>
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
.lp__section { padding: 6px 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }

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
.lp__row--layer { cursor: default; }
.lp__row--layer input[type="checkbox"] {
  width: 14px; height: 14px; margin: 0; flex: none; accent-color: #3B82F6;
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

.lp__swatch {
  width: 12px; height: 12px; border-radius: 3px; flex: none;
  border: 1px solid rgba(0, 0, 0, 0.2);
  display: inline-flex; align-items: center; justify-content: center;
}
.lp__swatch-icon { width: 10px; height: 10px; fill: rgba(255, 255, 255, 0.92); }
.lp__label { font-size: 12px; color: #E6E8EC; flex: 1; min-width: 0; display: flex; flex-direction: column; }
.lp__empty { padding: 10px; font-size: 11px; color: rgba(230, 232, 236, 0.6); margin: 0; }
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
</style>
