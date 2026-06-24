<script setup>
/**
 * LayerPanel — left-edge slide-in panel. PR-2 of the quiet-canvas plan.
 *
 * Absorbs the layer / recent / templates / masters sections from the
 * old LeftRail. Zones live in ToolsPanel (PR-3). Skin picker lives in
 * BasemapPanel (PR-1). Header is compact: map title + count + close.
 *
 * Sections (each collapsible):
 *   1. Recent maps        — last 5 opened
 *   2. Templates          — public templates
 *   3. Layers on this map — toggles + edit pencil
 *   4. Available mappings — masters not yet attached, grouped by doctype
 *   5. Master mappings    — collapsed by default
 *
 * Width: 300px. Max height: 100vh minus 24px top/bottom. Translucent,
 * same glass as the rest of the chrome.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { call } from '../api/client.js'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { ICON_PATHS } from '../api/icons.js'

const mapStore = useMapStore()
const layerStore = useLayersStore()
const ui = useUiStore()

// Map card save UI state.
const savePanelOpen = ref(false)
const saveForm = ref({
  title: '',
  isPublic: false,
  campaign: '',
  summary: null,
})
const saving = ref(false)
const saveError = ref('')

const activeMapName = computed(() => mapStore.activeMap?.map?.name)
const recent = computed(() => ui.recent || [])
const templates = computed(() => mapStore.templates || [])
const activeLayers = computed(() => layerStore.layers || [])
const masters = computed(() => layerStore.masters || [])

const openSections = ref({
  recent: true,
  templates: true,
  layers: true,
  available: true,
  masters: false,
})

const availableMappings = computed(() => {
  if (!activeMapName.value) return []
  const attached = new Set(
    activeLayers.value
      .filter((l) => l.map === activeMapName.value)
      .map((l) => `${l.source_doctype}::${l.title}`)
  )
  return masters.value.filter(
    (m) => !attached.has(`${m.source_doctype}::${m.title}`)
  )
})
const availableByDoctype = computed(() => {
  const out = new Map()
  for (const m of availableMappings.value) {
    const key = m.source_doctype || '(no source)'
    if (!out.has(key)) out.set(key, [])
    out.get(key).push(m)
  }
  return Array.from(out.entries()).sort((a, b) => a[0].localeCompare(b[0]))
})

const canEdit = computed(() => {
  const owner = mapStore.activeMap?.map?.owner
  return !owner || owner === window.frappe?.session?.user || (window.frappe?.session?.user === 'Administrator')
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
function addLayer() { ui.openLayerEditor(null, { asMaster: false }) }
function addMasterMapping() { ui.openLayerEditor(null, { asMaster: true }) }
function editLayer(layer) { ui.openLayerEditor(layer) }
function editMaster(master) { ui.openLayerEditor(master) }
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
const quickFilterDraft = ref({ field: '', op: '=', value: '' })
const quickFilterFields = ref({}) // layerName -> [{fieldname, label, fieldtype}]
async function openQuickFilter(layer) {
  // Parse the first filter row as the draft (if any)
  const rows = _parseFilter(layer.filter_json)
  quickFilterDraft.value = (rows && rows[0]) ? { ...rows[0] } : { field: '', op: '=', value: '' }
  quickFilterFor.value = layer.name
  // Lazily load the source DocType's filterable fields.
  if (!quickFilterFields.value[layer.name] && layer.source_doctype) {
    try {
      const fields = await call('expedition.api.layer.list_source_fields', { source_doctype: layer.source_doctype })
      quickFilterFields.value[layer.name] = fields
    } catch (e) {
      console.error('[expedition] list_source_fields failed', e)
      quickFilterFields.value[layer.name] = []
    }
  }
}
async function saveQuickFilter(layer) {
  if (!layer || !quickFilterFor.value) return
  // Serialize the single draft row back to JSON. Note: this overwrites any
  // existing multi-row filters; for v1 we keep it simple.
  const json = quickFilterDraft.value.field && quickFilterDraft.value.op
    ? JSON.stringify([[quickFilterDraft.value.field, quickFilterDraft.value.op, quickFilterDraft.value.value]])
    : ''
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

function _parseFilter(json) {
  if (!json) return []
  try {
    const parsed = typeof json === 'string' ? JSON.parse(json) : json
    if (!Array.isArray(parsed)) return []
    return parsed.map(([field, op, value]) => ({ field, op: op || '=', value: value ?? '' }))
  } catch { return [] }
}

async function attachMaster(masterName) {
  if (!activeMapName.value || !masterName) return
  try { await layerStore.attachToMap(masterName, activeMapName.value) }
  catch (e) {
    console.error('[expedition] attach master failed', e)
    await ui.ask({
      title: 'Could not attach master',
      body: String(e.message || e),
      confirmLabel: 'OK',
      cancelLabel: '',
      destructive: false,
    })
  }
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

function formatOpenedAgo(ts) {
  if (!ts) return ''
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
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

function openSavePanel() {
  saveError.value = ''
  // Default title suggestion: the active map's title or today's date
  const cur = mapStore.activeMap?.map?.title
  saveForm.value = {
    title: cur && cur !== 'Untitled' ? cur : `Map ${new Date().toLocaleDateString()}`,
    isPublic: false,
    campaign: '',
    summary: null,
  }
  savePanelOpen.value = true
}

async function saveCurrentCard() {
  if (!saveForm.value.title.trim()) {
    saveError.value = 'Title is required'
    return
  }
  saving.value = true
  saveError.value = ''
  try {
    // Capture the current canvas viewport (center, zoom, bearing, pitch).
    // MapLibre's `getCenter()` returns a LngLat which exposes `.lng` and
    // `.lat`. MapLibre APIs (flyTo / easeTo / jumpTo) expect [lng, lat].
    const m = window.expeditionMap?.getMap?.()
    let viewport = null
    if (m) {
      const c = m.getCenter()
      viewport = { center: [c.lng, c.lat], zoom: m.getZoom(), bearing: m.getBearing(), pitch: m.getPitch() }
    }
    // Build a summary so the user can see at a glance what the card
    // contains when it shows up in the recent list.
    const layerSummary = (layerStore.layers || []).map((l) => ({
      name: l.name,
      title: l.title,
      source_doctype: l.source_doctype,
      enabled: l.enabled !== 0 && l.enabled !== false,
    }))
    const summary = {
      layers: layerSummary,
      filters: ui.filterSelections || {},
      saved_at: new Date().toISOString(),
    }
    const basemap = mapStore.activeMap?.map?.basemap_style || 'light'
    const dto = await mapStore.saveMapCard({
      title: saveForm.value.title.trim(),
      is_public: saveForm.value.isPublic ? 1 : 0,
      campaign: saveForm.value.campaign || null,
      basemap_style: basemap,
      viewport: viewport ? JSON.stringify(viewport) : null,
      filters_json: '{}',
      summary_json: JSON.stringify(summary),
    })
    savePanelOpen.value = false
    // Switch to the saved card so the user sees their saved view.
    if (dto && dto.name && dto.name !== activeMapName.value) {
      await mapStore.switchMap(dto.name)
    }
  } catch (e) {
    saveError.value = e.message || String(e)
  } finally {
    saving.value = false
  }
}

function cancelSave() {
  savePanelOpen.value = false
}

defineExpose({})
</script>

<template>
  <aside class="lp" role="region" aria-label="Layers">
    <header class="lp__header">
      <div class="lp__title-wrap">
        <div class="lp__title">{{ mapStore.activeMap?.map?.title || 'Expedition' }}</div>
        <div class="lp__subtitle">{{ activeLayers.length }} layers</div>
      </div>
      <div class="lp__header-actions">
        <button class="lp__save" type="button" aria-label="Save map card" title="Save current view as a map card"
                @click="openSavePanel">
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path d="M5 4h11l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z M7 4v5h9V4 M7 14h10v7H7z"
                  fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <button class="lp__close" type="button" aria-label="Close layers" @click="$emit('close')">×</button>
      </div>
    </header>

    <!-- Save map card form. Slides down under the header. -->
    <div v-if="savePanelOpen" class="lp__save-panel">
      <div class="lp__save-row">
        <label class="lp__save-label">
          <span>Title</span>
          <input v-model="saveForm.title" type="text" class="lp__save-input" placeholder="e.g. APAC Distributors" />
        </label>
      </div>
      <label class="lp__save-check">
        <input v-model="saveForm.isPublic" type="checkbox" :true-value="true" :false-value="false" />
        <span>Share with everyone</span>
      </label>
      <div class="lp__save-row">
        <label class="lp__save-label lp__save-label--half">
          <span>Campaign</span>
          <input v-model="saveForm.campaign" type="text" class="lp__save-input" placeholder="optional" />
        </label>
      </div>
      <p v-if="saveError" class="lp__save-error">{{ saveError }}</p>
      <div class="lp__save-actions">
        <button type="button" class="lp__save-btn lp__save-btn--ghost" @click="cancelSave">Cancel</button>
        <button type="button" class="lp__save-btn lp__save-btn--primary" :disabled="saving" @click="saveCurrentCard">
          {{ saving ? 'Saving…' : 'Save card' }}
        </button>
      </div>
    </div>

    <div class="lp__body">
      <!-- Recent -->
      <section v-if="recent.length" class="lp__section">
        <button class="lp__section-toggle" type="button" @click="openSections.recent = !openSections.recent">
          <span>Recent</span>
          <span class="lp__chevron" :data-open="openSections.recent">▾</span>
        </button>
        <ul v-if="openSections.recent" class="lp__list">
          <li
            v-for="m in recent"
            :key="'r-' + m.name"
            class="lp__row"
            :class="{ 'lp__row--active': m.name === activeMapName }"
            @click="openMap(m.name)"
          >
            <span class="lp__map-swatch" :data-kind="basemapSwatchFor(m.basemap_style)" />
            <span class="lp__row-main">
              <span class="lp__row-title">{{ m.title }}</span>
              <span class="lp__row-meta">{{ formatOpenedAgo(m.openedAt) }}</span>
            </span>
          </li>
        </ul>
      </section>

      <!-- Templates -->
      <section v-if="templates.length" class="lp__section">
        <button class="lp__section-toggle" type="button" @click="openSections.templates = !openSections.templates">
          <span>Templates</span>
          <span class="lp__chevron" :data-open="openSections.templates">▾</span>
        </button>
        <ul v-if="openSections.templates" class="lp__list">
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

      <!-- Layers on this map -->
      <section class="lp__section">
        <div class="lp__section-bar">
          <button class="lp__section-toggle" type="button" @click="openSections.layers = !openSections.layers">
            <span>Layers on this map</span>
            <span class="lp__chevron" :data-open="openSections.layers">▾</span>
          </button>
          <button v-if="canEdit" class="lp__add" type="button" @click="addLayer" title="Add layer">+</button>
        </div>
        <ul v-if="openSections.layers && activeLayers.length" class="lp__list">
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
                  :title="layer.filter_json ? 'Filter active — click to edit' : 'Add a quick filter'"
                  @click.stop="openQuickFilter(layer)"
                >
                  <!-- Filter glyph: funnel. -->
                  <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                    <path d="M3 5h18l-7 9v6l-4-2v-4z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
                  </svg>
                </button>
                <div v-if="quickFilterFor === layer.name" class="lp__qf-pop" @click.stop>
                  <div class="lp__qf-head">Quick filter</div>
                  <div class="lp__qf-row">
                    <select v-model="quickFilterDraft.field" class="lp__qf-input lp__qf-input--field">
                      <option value="" disabled>field</option>
                      <option v-for="f in (quickFilterFields[layer.name] || [])" :key="f.fieldname" :value="f.fieldname">
                        {{ f.label }} ({{ f.fieldname }})
                      </option>
                    </select>
                  </div>
                  <div class="lp__qf-row">
                    <select v-model="quickFilterDraft.op" class="lp__qf-input lp__qf-input--op">
                      <option value="=">=</option>
                      <option value="!=">≠</option>
                      <option value="like">contains</option>
                      <option value="not like">not contains</option>
                      <option value="in">in</option>
                      <option value="not in">not in</option>
                      <option value="&gt;">&gt;</option>
                      <option value="&lt;">&lt;</option>
                    </select>
                    <input v-model="quickFilterDraft.value" class="lp__qf-input lp__qf-input--val" type="text" placeholder="value" />
                  </div>
                  <div class="lp__qf-actions">
                    <button type="button" class="lp__qf-btn lp__qf-btn--ghost" @click="cancelQuickFilter">Cancel</button>
                    <button type="button" class="lp__qf-btn lp__qf-btn--primary" @click="saveQuickFilter(layer)">Save</button>
                  </div>
                  <p v-if="!layer.source_doctype" class="lp__qf-note">No source DocType configured for this layer.</p>
                  <p v-else-if="!quickFilterFields[layer.name]" class="lp__qf-note">Loading fields…</p>
                </div>
              </div>
              <button class="lp__row-edit" type="button" @click="editLayer(layer)" aria-label="'Edit ' + (layer.title || layer.name)">edit</button>
            </div>
          </li>
        </ul>
        <p v-else-if="openSections.layers" class="lp__empty">No layers yet.</p>
      </section>

      <!-- Available mappings -->
      <section v-if="activeMapName && availableByDoctype.length" class="lp__section">
        <button class="lp__section-toggle" type="button" @click="openSections.available = !openSections.available">
          <span>Available mappings</span>
          <span class="lp__chevron" :data-open="openSections.available">▾</span>
        </button>
        <div v-if="openSections.available">
          <div
            v-for="[doctype, items] in availableByDoctype"
            :key="doctype"
            class="lp__group"
          >
            <div class="lp__group-label">{{ doctype }}</div>
            <ul class="lp__list">
              <li v-for="m in items" :key="m.name" class="lp__item">
                <div class="lp__row lp__row--layer">
                  <span class="lp__swatch" :style="{ background: m.color || '#3B82F6' }">
                    <svg v-if="m.icon" class="lp__swatch-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path :d="iconPath(m.icon)" fill="currentColor" />
                    </svg>
                  </span>
                  <span class="lp__label">{{ m.title }}</span>
                </div>
                <button class="lp__row-edit lp__row-edit--add" type="button" @click="attachMaster(m.name)" aria-label="'Add ' + m.title">+ add</button>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <!-- Master mappings -->
      <section v-if="masters.length" class="lp__section">
        <div class="lp__section-bar">
          <button class="lp__section-toggle" type="button" @click="openSections.masters = !openSections.masters">
            <span>Master mappings</span>
            <span class="lp__chevron" :data-open="openSections.masters">▾</span>
          </button>
          <button v-if="canEdit" class="lp__add" type="button" @click="addMasterMapping" title="New master mapping">+</button>
        </div>
        <ul v-if="openSections.masters" class="lp__list">
          <li v-for="m in masters" :key="'m-' + m.name" class="lp__item">
            <div class="lp__row lp__row--layer">
              <span class="lp__swatch" :style="{ background: m.color || '#3B82F6' }">
                <svg v-if="m.icon" class="lp__swatch-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path :d="iconPath(m.icon)" fill="currentColor" />
                </svg>
              </span>
              <span class="lp__label">
                <span>{{ m.title }}</span>
                <span class="lp__row-meta">{{ m.source_doctype }}</span>
              </span>
            </div>
            <button class="lp__row-edit" type="button" @click="editMaster(m)">edit</button>
          </li>
        </ul>
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
.lp__header-actions { display: flex; gap: 2px; align-items: center; }
.lp__save {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  cursor: pointer; padding: 4px 6px; border-radius: 5px; display: flex; align-items: center; justify-content: center;
}
.lp__save:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.lp__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1; border-radius: 5px;
}
.lp__close:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }

/* Save card panel. Slides under the header. */
.lp__save-panel {
  background: rgba(0, 0, 0, 0.18);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding: 12px;
  display: flex; flex-direction: column; gap: 10px;
}
.lp__save-row { display: flex; gap: 8px; }
.lp__save-label {
  display: flex; flex-direction: column; gap: 4px; flex: 1;
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.6); font-weight: 500;
}
.lp__save-label--half { flex: 1; }
.lp__save-input {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px; color: #E6E8EC;
  padding: 6px 8px; font-size: 12px;
  font-family: inherit; outline: none;
  text-transform: none; letter-spacing: 0; font-weight: 400;
}
.lp__save-input:focus { border-color: #3B82F6; }
.lp__save-check {
  display: flex; align-items: center; gap: 8px; font-size: 11px; cursor: pointer;
}
.lp__save-check input { accent-color: #3B82F6; }
.lp__save-error {
  margin: 0; padding: 6px 8px; font-size: 10px; color: #FCA5A5;
  background: rgba(239, 68, 68, 0.10);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 5px;
}
.lp__save-actions { display: flex; gap: 6px; justify-content: flex-end; }
.lp__save-btn {
  padding: 6px 10px; border-radius: 6px;
  font-size: 11px; cursor: pointer; font-family: inherit; font-weight: 500;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #E6E8EC;
}
.lp__save-btn--primary { background: #3B82F6; border-color: #3B82F6; color: #fff; }
.lp__save-btn--primary:disabled { opacity: 0.5; cursor: not-allowed; }
.lp__save-btn--ghost { background: transparent; }

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
  position: absolute;
  top: 100%;
  right: 0;
  z-index: 30;
  margin-top: 4px;
  width: 240px;
  background: rgba(11, 14, 20, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  padding: 8px;
  display: flex; flex-direction: column; gap: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
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

.lp__body { padding: 6px 0; overflow-y: auto; flex: 1; }
.lp__section { padding: 6px 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
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

.lp__add {
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.4);
  color: #93C5FD;
  width: 22px; height: 22px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 14px; line-height: 1;
  font-family: inherit;
  margin-right: 6px;
}
.lp__add:hover { background: rgba(59, 130, 246, 0.22); color: #fff; }

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
.lp__row-edit--add { color: #93C5FD; }
.lp__row-edit--add:hover { background: rgba(59, 130, 246, 0.18); color: #fff; }

/* Heatmap toggle button (PR-7). Sits between the row and the edit
   button. Color matches the layer's pin color when on, dim when off. */
.lp__row-icon {
  background: transparent; border: 0;
  width: 22px; height: 22px;
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