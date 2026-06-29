<script setup>
/**
 * LayerEditor — the create/edit modal for Expedition Layer docs.
 *
 * Slides in as a translucent right-rail card. Stays out of the way of
 * the map (no full-page modals). Two modes:
 *   - create: pick a source DocType + lat/lng fields, set color
 *   - edit: edit any subset of color, size, cluster, label_field,
 *     filter_json (one or more [field, op, value] rows), enabled
 *
 * Persists to server via the layer store.
 */
import { ref, computed, watch, onMounted } from 'vue'
import { call } from '../api/client.js'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useIconsStore } from '../state/icons.js'
import { ICON_PATHS } from '../api/icons.js'
import FilterBuilder from './FilterBuilder.vue'
import { parseFilterRows, serializeFilterRows } from '../lib/filters.js'

const ui = useUiStore()
const mapStore = useMapStore()
const layerStore = useLayersStore()
const iconStore = useIconsStore()

const mode = computed(() => ui.editorOpen?.mode || 'edit')
const isCreate = computed(() => mode.value === 'create')
// In create mode the user can choose to save as a master (map=NULL) or
// as a per-map instance (map=active map). In edit mode, `isMaster` is
// derived from the layer doc's `map` field — masters have no map.
const asMaster = ref(false)
const isMaster = computed(() => {
  if (isCreate.value) return asMaster.value
  return !ui.editorOpen?.layer?.map
})

// Form state. Initialized from the editorOpen target on open.
const form = ref({
  name: null,
  title: '',
  source_doctype: '',
  latitude_field: 'latitude',
  longitude_field: 'longitude',
  label_field: '',
  color: '#3B82F6',
  size: 'm',
  cluster: 1,
  enabled: 1,
  icon: '',          // '' = no glyph (plain dot), otherwise id from icons.svg
  filter_rows: [], // [{ field, op, value }]
  popup_template: '',  // Jinja template rendered server-side per feature
  popup_fields: [],    // list of fieldnames for the default popup body
  group_by_field: '',  // '' = no grouping; otherwise a fieldname on source
  group_config: {},    // { "<value>": { color, icon, label } } — overrides default style
  click_action: 'popup', // 'popup' | 'redirect' | 'none'
})
const saving = ref(false)
const error = ref('')
const sourceDts = ref([])
const sourceFields = ref([])          // local working list (filled from cache)
const sourceFieldsLoading = ref(false)
const groupValues = ref([]) // distinct values for the group_by_field
const previewOriginalLayer = ref(null)
const previewOriginalUi = ref(null)
const previewCommitted = ref(false)
const uploadTitle = ref('')
const uploadScope = ref('Personal')
const iconEditTitle = ref('')
const iconBusy = ref(false)

const COLOR_PRESETS = [
  '#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6',
  '#06B6D4', '#F97316', '#84CC16', '#EF4444', '#6366F1',
  '#14B8A6', '#A855F7', '#0EA5E9', '#F43F5E', '#22C55E',
]

const HEX_COLOR_RE = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i
const RGB_COLOR_RE = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(0|1|0?\.\d+))?\s*\)$/i

const SIZE_OPTIONS = [
  { v: 'xs', label: 'XS' },
  { v: 's', label: 'S' },
  { v: 'm', label: 'M' },
  { v: 'l', label: 'L' },
  { v: 'xl', label: 'XL' },
]

const iconSections = computed(() => [
  { key: 'builtin', label: 'Built-in', icons: iconStore.builtin },
  { key: 'personal', label: 'Personal', icons: iconStore.personal },
  { key: 'global', label: 'Global', icons: iconStore.global },
].filter((section) => section.icons.length || section.key !== 'global' || iconStore.canManageGlobal))
const allIconOptions = computed(() => iconStore.all)
const selectedIcon = computed(() => iconStore.byKey.get(form.value.icon))
const colorInputInvalid = computed(() => !!form.value.color && !isValidCssColor(form.value.color))

watch(
  () => ui.editorOpen,
  async (v) => {
    if (!v) {
      cancelLayerPreview()
      return
    }
    cancelLayerPreview()
    error.value = ''
    asMaster.value = !!v.asMaster
    iconStore.loadIcons().catch((e) => { error.value = 'Could not load icons: ' + e.message })
    if (v.mode === 'create') {
      form.value = {
        name: null,
        title: '',
        source_doctype: '',
        latitude_field: 'latitude',
        longitude_field: 'longitude',
        label_field: '',
        color: '#3B82F6',
        size: 'm',
        cluster: 1,
        enabled: 1,
        icon: '',
        filter_rows: [],
        popup_template: '',
        popup_fields: [],
        group_by_field: '',
        group_config: {},
        click_action: 'popup',
        heatmap: 0,
        radius_enabled: 0,
        radius_field: '',
        radius_meters: 5000,
        radius_opacity: 0.18,
      }
      if (sourceDts.value.length === 0) {
        try {
          sourceDts.value = await call('expedition.api.layer.list_source_doctypes')
        } catch (e) {
          error.value = 'Could not load source DocTypes: ' + e.message
        }
      }
    } else if (v.mode === 'edit' && v.layer) {
      const requestedLayer = v.layer
      const liveLayer = layerStore.layers.find((layer) => layer.name === requestedLayer.name) || requestedLayer
      if (liveLayer && liveLayer.map) {
        previewOriginalLayer.value = {
          ...liveLayer,
          style: { ...(liveLayer.style || {}) },
        }
        previewOriginalUi.value = {
          heatmap: ui.isHeatmapOn(liveLayer.name),
          radius_enabled: ui.isRadiusOn(liveLayer.name),
          radius_field: ui.radiusField[liveLayer.name] || '',
          radius_meters: ui.radiusMeters[liveLayer.name] || 5000,
        }
        previewCommitted.value = false
      }
      const l = liveLayer
      form.value = {
        name: l.name,
        title: l.title,
        source_doctype: l.source_doctype,
        latitude_field: l.latitude_field,
        longitude_field: l.longitude_field,
        label_field: l.label_field || '',
        color: l.color || '#3B82F6',
        size: l.size || 'm',
        cluster: l.cluster ? 1 : 0,
        enabled: l.enabled ? 1 : 0,
        icon: l.icon || '',
        filter_rows: parseFilterRows(l.filter_json),
        popup_template: l.popup_template || '',
        popup_fields: l.popup_fields || [],
        group_by_field: l.group_by_field || '',
        group_config: l.group_config || {},
        click_action: l.click_action || 'popup',
        heatmap: l.heatmap ? 1 : 0,
        radius_enabled: l.radius_enabled ? 1 : 0,
        radius_field: l.radius_field || '',
        radius_meters: l.radius_meters ?? 5000,
        radius_opacity: l.radius_opacity ?? 0.18,
      }
      await _loadSourceFields(l.source_doctype)
    }
  },
  { immediate: true }
)

watch(
  () => ({
    color: form.value.color,
    size: form.value.size,
    cluster: form.value.cluster,
    enabled: form.value.enabled,
    icon: form.value.icon,
    heatmap: form.value.heatmap,
    radius_enabled: form.value.radius_enabled,
    radius_field: form.value.radius_field,
    radius_meters: form.value.radius_meters,
    radius_opacity: form.value.radius_opacity,
    group_by_field: form.value.group_by_field,
    group_config_json: _serializeGroupConfig(form.value.group_config),
  }),
  (preview) => {
    if (!previewOriginalLayer.value || previewCommitted.value) return
    if (!form.value.name || isCreate.value || isMaster.value) return
    ui.setHeatmap(form.value.name, !!preview.heatmap)
    ui.setRadius(form.value.name, !!preview.radius_enabled)
    ui.setRadiusField(form.value.name, preview.radius_field || '')
    ui.setRadiusMeters(form.value.name, preview.radius_meters)
    layerStore.previewLayerFields(form.value.name, {
      color: safeLayerColor(preview.color, previewOriginalLayer.value?.color || '#3B82F6'),
      size: preview.size || 'm',
      cluster: preview.cluster,
      enabled: preview.enabled,
      icon: preview.icon || '',
      heatmap: preview.heatmap,
      radius_enabled: preview.radius_enabled,
      radius_field: preview.radius_field || '',
      radius_meters: preview.radius_meters,
      radius_opacity: preview.radius_opacity,
      group_by_field: preview.group_by_field || '',
      group_config_json: preview.group_config_json || '',
      group_config: form.value.group_config || {},
    })
  },
  { deep: true }
)

function cancelLayerPreview() {
  const original = previewOriginalLayer.value
  if (original && !previewCommitted.value) {
    layerStore.previewLayerFields(original.name, original)
    if (previewOriginalUi.value) {
      ui.setHeatmap(original.name, previewOriginalUi.value.heatmap)
      ui.setRadius(original.name, previewOriginalUi.value.radius_enabled)
      ui.setRadiusField(original.name, previewOriginalUi.value.radius_field)
      ui.setRadiusMeters(original.name, previewOriginalUi.value.radius_meters)
    }
  }
  previewOriginalLayer.value = null
  previewOriginalUi.value = null
  previewCommitted.value = false
}

watch(selectedIcon, (icon) => {
  iconEditTitle.value = icon?.source === 'custom' ? icon.title : ''
}, { immediate: true })

function iconLabel(icon) {
  return icon?.title || icon?.key || ''
}

function isBuiltinIcon(icon) {
  return icon?.source === 'builtin'
}

function isValidCssColor(value) {
  const color = String(value || '').trim()
  if (!color) return false
  if (HEX_COLOR_RE.test(color)) return true
  const rgb = color.match(RGB_COLOR_RE)
  if (!rgb) return false
  const [, r, g, b, a] = rgb
  const channels = [r, g, b].map(Number)
  if (channels.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false
  if (a != null) {
    const alpha = Number(a)
    if (!Number.isFinite(alpha) || alpha < 0 || alpha > 1) return false
  }
  return true
}

function normalizeColorText(value) {
  return String(value || '').trim()
}

function safeLayerColor(value, fallback = '#3B82F6') {
  const color = normalizeColorText(value)
  return isValidCssColor(color) ? color : fallback
}

function expandHexColor(value) {
  const color = normalizeColorText(value)
  if (!HEX_COLOR_RE.test(color)) return ''
  const hex = color.slice(1)
  if (hex.length === 3 || hex.length === 4) {
    return '#' + hex.slice(0, 3).split('').map((c) => c + c).join('')
  }
  return '#' + hex.slice(0, 6)
}

function componentToHex(value) {
  return Number(value).toString(16).padStart(2, '0')
}

function colorPickerValue(value) {
  const hex = expandHexColor(value)
  if (hex) return hex
  const rgb = normalizeColorText(value).match(RGB_COLOR_RE)
  if (rgb) {
    return `#${componentToHex(rgb[1])}${componentToHex(rgb[2])}${componentToHex(rgb[3])}`
  }
  return '#3B82F6'
}

function setPickedColor(value) {
  form.value.color = String(value || '#3B82F6').toUpperCase()
}

function setPresetColor(value) {
  form.value.color = value
}

function isActivePreset(value) {
  return colorPickerValue(form.value.color).toLowerCase() === value.toLowerCase()
}

function isSvgFile(file) {
  return file?.type === 'image/svg+xml' || file?.name?.toLowerCase().endsWith('.svg')
}

function mimeForFile(file) {
  if (file?.type?.startsWith('image/')) return file.type
  const name = file?.name?.toLowerCase() || ''
  if (name.endsWith('.png')) return 'image/png'
  if (name.endsWith('.jpg') || name.endsWith('.jpeg')) return 'image/jpeg'
  if (name.endsWith('.webp')) return 'image/webp'
  if (name.endsWith('.gif')) return 'image/gif'
  if (name.endsWith('.bmp')) return 'image/bmp'
  if (name.endsWith('.avif')) return 'image/avif'
  if (name.endsWith('.ico')) return 'image/x-icon'
  if (name.endsWith('.tif') || name.endsWith('.tiff')) return 'image/tiff'
  return ''
}

function isImageFile(file) {
  return !!mimeForFile(file) || /\.(svg|png|jpe?g|webp|gif|bmp|avif|ico|tiff?)$/i.test(file?.name || '')
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('Could not read image file.'))
    reader.readAsDataURL(file)
  })
}

async function readIconFile(file) {
  if (!file || !isImageFile(file)) {
    throw new Error('Please choose an image file.')
  }
  if (isSvgFile(file)) {
    return { svgText: await file.text(), imageDataUrl: null }
  }
  const dataUrl = await readFileAsDataUrl(file)
  const mime = mimeForFile(file)
  const normalizedDataUrl = mime && !dataUrl.startsWith('data:image/')
    ? dataUrl.replace(/^data:[^;]*;base64,/i, `data:${mime};base64,`)
    : dataUrl
  return { svgText: null, imageDataUrl: normalizedDataUrl }
}

async function uploadIconFromEvent(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !iconStore.canCreate) return
  iconBusy.value = true
  error.value = ''
  try {
    const { svgText, imageDataUrl } = await readIconFile(file)
    const icon = await iconStore.uploadIcon({
      title: uploadTitle.value.trim() || file.name.replace(/\.[^.]+$/i, ''),
      scope: iconStore.canManageGlobal ? uploadScope.value : 'Personal',
      svgText,
      imageDataUrl,
    })
    form.value.icon = icon.key
    uploadTitle.value = ''
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    iconBusy.value = false
  }
}

async function replaceSelectedIcon(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  const icon = selectedIcon.value
  if (!file || !icon?.can_edit) return
  iconBusy.value = true
  error.value = ''
  try {
    const { svgText, imageDataUrl } = await readIconFile(file)
    await iconStore.updateIcon(icon.name, { title: iconEditTitle.value || icon.title, svgText, imageDataUrl })
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    iconBusy.value = false
  }
}

async function renameSelectedIcon() {
  const icon = selectedIcon.value
  if (!icon?.can_edit || !iconEditTitle.value.trim()) return
  iconBusy.value = true
  error.value = ''
  try {
    await iconStore.updateIcon(icon.name, { title: iconEditTitle.value.trim() })
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    iconBusy.value = false
  }
}

async function deleteSelectedIcon() {
  const icon = selectedIcon.value
  if (!icon?.can_delete) return
  const ok = await ui.ask({
    title: 'Delete this icon?',
    body: 'If it is already used by layers, it will be disabled instead of permanently deleted.',
    confirmLabel: 'Delete',
    destructive: true,
  })
  if (!ok) return
  iconBusy.value = true
  error.value = ''
  try {
    await iconStore.deleteIcon(icon.name)
    if (form.value.icon === icon.key) form.value.icon = ''
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    iconBusy.value = false
  }
}

function commitLayerPreview() {
  previewCommitted.value = true
  previewOriginalLayer.value = null
  previewOriginalUi.value = null
}

function _parsePopupFields(json) {
  if (!json) return []
  try {
    const parsed = typeof json === 'string' ? JSON.parse(json) : json
    if (!Array.isArray(parsed)) return []
    return parsed
  } catch {
    return []
  }
}

function _serializePopupFields(arr) {
  return Array.isArray(arr) && arr.length ? JSON.stringify(arr) : ''
}

function _parseGroupConfig(json) {
  if (!json) return {}
  try {
    const parsed = typeof json === 'string' ? JSON.parse(json) : json
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}

function _serializeGroupConfig(obj) {
  if (!obj || typeof obj !== 'object') return ''
  const cleaned = Object.fromEntries(
    Object.entries(obj).filter(([k, v]) => v && typeof v === 'object' && (v.color || v.icon || v.label))
  )
  return Object.keys(cleaned).length ? JSON.stringify(cleaned) : ''
}

async function _loadSourceFields(dt) {
  if (!dt) {
    sourceFields.value = []
    sourceFieldsLoading.value = false
    return
  }
  sourceFieldsLoading.value = true
  try {
    sourceFields.value = await layerStore.getSourceFields(dt)
  } catch (e) {
    error.value = 'Could not load fields: ' + e.message
    sourceFields.value = []
  } finally {
    sourceFieldsLoading.value = false
  }
}

// Numeric fields only — used for the radius-field picker so the
// halo can't accidentally be sized by a Text field.
const numericFields = computed(() =>
  sourceFields.value.filter((f) =>
    ['Int', 'Float', 'Currency', 'Percent'].includes(f.fieldtype)
  )
)

// SVG <use href> against the icons sprite. Append a cache-bust so a
// stale sprite after a hard reload is avoided.
const _iconSpriteHref = (() => {
  const v = Date.now()
  return (id) => `/assets/expedition/icons.svg#${id}?v=${v}`
})()
function iconHref(id) { return _iconSpriteHref(id) }

async function onSourceChange() {
  form.value.latitude_field = 'latitude'
  form.value.longitude_field = 'longitude'
  form.value.label_field = ''
  form.value.filter_rows = []
  form.value.group_by_field = ''
  form.value.group_config = {}
  groupValues.value = []
  await _loadSourceFields(form.value.source_doctype)
}

async function onGroupByChange() {
  form.value.group_config = {}
  groupValues.value = []
  if (!form.value.source_doctype || !form.value.group_by_field) return
  try {
    const resp = await call('expedition.api.layer.list_group_values', {
      source_doctype: form.value.source_doctype,
      field: form.value.group_by_field,
    })
    groupValues.value = resp.values || []
    // Seed group_config with auto-assigned colors so the user sees
    // immediate differentiation on the map (instead of an unwieldy
    // "configure every value" form). Existing user overrides on the
    // same value are preserved.
    const cfg = {}
    const values = groupValues.value.map(String)
    for (let i = 0; i < values.length; i++) {
      const v = values[i]
      const existing = form.value.group_config[v]
      if (existing && (existing.color || existing.icon || existing.label)) {
        cfg[v] = existing
      } else {
        // Auto-assign a color from a stable, perceptually-distinct
        // palette. Cycle if we run out (groups with >12 values get
        // repeated hues, which is fine).
        cfg[v] = {
          color: GROUP_PALETTE[i % GROUP_PALETTE.length],
          icon: '',
          label: '',
        }
      }
    }
    form.value.group_config = cfg
  } catch (e) {
    error.value = 'Could not load group values: ' + e.message
  }
}

// Perceptually distinct 12-color palette for group-by values. The user
// can override any value's color via the per-row color picker.
const GROUP_PALETTE = [
  '#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6',
  '#06B6D4', '#F97316', '#84CC16', '#EF4444', '#6366F1',
  '#14B8A6', '#A855F7',
]

function addPopupField(fieldname) {
  if (!fieldname) return
  if (!form.value.popup_fields.includes(fieldname)) {
    form.value.popup_fields.push(fieldname)
  }
}
function removePopupField(i) {
  form.value.popup_fields.splice(i, 1)
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    form.value.color = normalizeColorText(form.value.color)
    if (!isValidCssColor(form.value.color)) {
      error.value = 'Enter a valid color: #RGB, #RRGGBB, rgb(...), or rgba(...).'
      saving.value = false
      return
    }
    if (isCreate.value) {
      if (!form.value.title.trim()) {
        error.value = 'Title is required'
        saving.value = false
        return
      }
      if (!form.value.source_doctype) {
        error.value = 'Source DocType is required'
        saving.value = false
        return
      }
      const basePayload = {
        title: form.value.title,
        source_doctype: form.value.source_doctype,
        latitude_field: form.value.latitude_field,
        longitude_field: form.value.longitude_field,
        label_field: form.value.label_field,
        color: form.value.color,
        size: form.value.size,
        cluster: form.value.cluster,
        enabled: form.value.enabled,
        icon: form.value.icon,
        popup_template: form.value.popup_template || '',
        popup_fields_json: _serializePopupFields(form.value.popup_fields),
        group_by_field: form.value.group_by_field,
        group_config_json: _serializeGroupConfig(form.value.group_config),
        click_action: form.value.click_action,
        heatmap: form.value.heatmap,
        radius_enabled: form.value.radius_enabled,
        radius_field: form.value.radius_field,
        radius_meters: form.value.radius_meters,
        radius_opacity: form.value.radius_opacity,
      }
      const filterJson = serializeFilterRows(form.value.filter_rows)
      if (asMaster.value) {
        // Save as a master mapping (map=NULL). Master is reusable across
        // maps; per-map instances are created from it later.
        const dto = await layerStore.addMaster({ ...basePayload, filter_json: filterJson })
        // No need for a follow-up update — addMaster already passes filter_json.
        // (If filter rows were added, they're already in the payload.)
        void dto
      } else {
        // Save as a per-map instance. Requires an active map.
        if (!mapStore.activeMap?.map?.name) {
          error.value = 'No active map — open a map first.'
          saving.value = false
          return
        }
        const dto = await layerStore.addLayer({
          ...basePayload,
          map_name: mapStore.activeMap.map.name,
        })
        if (filterJson) {
          await layerStore.updateLayer(dto.name, { filter_json: filterJson })
        }
      }
    } else {
      const editFields = {
        title: form.value.title,
        color: form.value.color,
        size: form.value.size,
        cluster: form.value.cluster,
        enabled: form.value.enabled,
        icon: form.value.icon,
        label_field: form.value.label_field,
        filter_json: serializeFilterRows(form.value.filter_rows),
        popup_template: form.value.popup_template || '',
        popup_fields_json: _serializePopupFields(form.value.popup_fields),
        group_by_field: form.value.group_by_field,
        group_config_json: _serializeGroupConfig(form.value.group_config),
        click_action: form.value.click_action,
        heatmap: form.value.heatmap,
        radius_enabled: form.value.radius_enabled,
        radius_field: form.value.radius_field,
        radius_meters: form.value.radius_meters,
        radius_opacity: form.value.radius_opacity,
      }
      if (isMaster.value) {
        await layerStore.updateMaster(form.value.name, editFields)
      } else {
        await layerStore.updateLayer(form.value.name, editFields)
      }
    }
    commitLayerPreview()
    ui.closeLayerEditor()
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!form.value.name) return
  const ok = await ui.ask({
    title: 'Delete this layer?',
    body: 'This cannot be undone. The layer will be removed from the map and the underlying DocType rows will be unaffected.',
    confirmLabel: 'Delete',
    destructive: true,
  })
  if (!ok) return
  saving.value = true
  error.value = ''
  try {
    if (isMaster.value) {
      await layerStore.removeMaster(form.value.name)
    } else {
      await layerStore.removeLayer(form.value.name)
    }
    commitLayerPreview()
    ui.closeLayerEditor()
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    saving.value = false
  }
}

function close() {
  ui.closeLayerEditor()
}
</script>

<template>
  <div v-if="ui.editorOpen" class="le" @keydown.esc="close">
    <div class="le__backdrop" :class="{ 'le__backdrop--blur': ui.blurOnPanel }" @click="close" />
    <aside class="le__panel" role="dialog" aria-label="Layer editor">
      <header class="le__header">
        <div>
          <h3 class="le__title">
            {{ isCreate ? (asMaster ? 'New master mapping' : 'Add layer') : (isMaster ? 'Edit master mapping' : 'Edit layer') }}
          </h3>
          <p class="le__subtitle">
            {{ isCreate
              ? (asMaster ? 'Reusable across maps. Attach to a map to use.' : 'Add a layer to the current map.')
              : (isMaster ? 'Attached instances do not auto-update when this master changes.' : 'Style, label, filter — all live.') }}
          </p>
        </div>
        <button class="le__close" type="button" @click="close" aria-label="Close">×</button>
      </header>

      <div class="le__body">
        <!-- Save-as-master toggle (create mode only) -->
        <label v-if="isCreate" class="le__check le__check--master">
          <input v-model="asMaster" type="checkbox" :true-value="true" :false-value="false" />
          <span>Save as master mapping (reusable across maps)</span>
        </label>

        <!-- Master banner (edit master only) -->
        <div v-else-if="isMaster" class="le__master-banner">
          This is a master mapping. Attached instances inherit display properties but are independent copies.
        </div>

        <!-- Title -->
        <label class="le__field">
          <span class="le__label">Title</span>
          <input v-model="form.title" class="le__input" type="text" placeholder="e.g. Customers — APAC" />
        </label>

        <!-- Source DocType (create only) -->
        <label v-if="isCreate" class="le__field">
          <span class="le__label">Source DocType</span>
          <select v-model="form.source_doctype" class="le__input" @change="onSourceChange">
            <option value="" disabled>Select a DocType…</option>
            <option v-for="dt in sourceDts" :key="dt.name" :value="dt.name">
              {{ dt.name }} <template v-if="dt.module">— {{ dt.module }}</template>
            </option>
          </select>
        </label>

        <div v-if="isCreate" class="le__row">
          <label class="le__field le__field--half">
            <span class="le__label">Lat field</span>
            <input v-model="form.latitude_field" class="le__input" type="text" />
          </label>
          <label class="le__field le__field--half">
            <span class="le__label">Lng field</span>
            <input v-model="form.longitude_field" class="le__input" type="text" />
          </label>
        </div>

        <!-- Label field (uses source row's text) -->
        <label class="le__field">
          <span class="le__label">Label field <span class="le__hint">(used as the pin popup title)</span></span>
          <select v-model="form.label_field" class="le__input">
            <option value="">— none —</option>
            <option v-for="f in sourceFields" :key="f.fieldname" :value="f.fieldname">
              {{ f.label }} ({{ f.fieldname }})
            </option>
          </select>
        </label>

        <!-- Color + Size -->
        <div class="le__row">
          <div class="le__field le__field--half">
            <span class="le__label">Color</span>
            <div class="le__color-row le__color-row--custom">
              <input
                :value="colorPickerValue(form.color)"
                class="le__color"
                type="color"
                title="Pick color"
                @input="setPickedColor($event.target.value)"
              />
              <input
                v-model="form.color"
                class="le__input le__color-code"
                :class="{ 'le__input--invalid': colorInputInvalid }"
                type="text"
                spellcheck="false"
                placeholder="#3B82F6 or rgba(59,130,246,0.8)"
                @blur="form.color = normalizeColorText(form.color)"
              />
              <div class="le__color-presets">
                <button v-for="c in COLOR_PRESETS" :key="c" type="button"
                        class="le__color-chip" :class="{ 'le__color-chip--active': isActivePreset(c) }"
                        :style="{ background: c }" @click="setPresetColor(c)" />
              </div>
            </div>
          </div>
          <div class="le__field le__field--half">
            <span class="le__label">Size</span>
            <div class="le__size-row">
              <button v-for="s in SIZE_OPTIONS" :key="s.v" type="button"
                      class="le__size-chip" :class="{ 'le__size-chip--active': form.size === s.v }"
                      @click="form.size = s.v">{{ s.label }}</button>
            </div>
          </div>
        </div>

        <!-- Icon glyph (optional). None = plain dot. Custom SVG icons
             are scoped personal/global by the server. -->
        <div class="le__row le__row--col">
          <div class="le__field">
            <span class="le__label">Icon</span>
            <div class="le__icon-grid">
              <button type="button" class="le__icon-cell"
                      :class="{ 'le__icon-cell--active': !form.icon }"
                      :data-active="!form.icon"
                      @click="form.icon = ''"
                      title="No icon"
                      aria-label="No icon">
                <span class="le__icon-preview le__icon-preview--none">∅</span>
              </button>
            </div>
            <div v-for="section in iconSections" :key="section.key" class="le__icon-section">
              <div class="le__icon-section-title">{{ section.label }}</div>
              <div v-if="section.icons.length" class="le__icon-grid">
                <button v-for="icon in section.icons" :key="icon.key" type="button"
                        class="le__icon-cell"
                        :class="{ 'le__icon-cell--active': form.icon === icon.key }"
                        :data-active="form.icon === icon.key"
                        @click="form.icon = icon.key"
                        :title="iconLabel(icon)">
                  <svg v-if="isBuiltinIcon(icon)" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                    <path :d="ICON_PATHS[icon.key] || ''" fill="currentColor" />
                  </svg>
                  <span v-else-if="icon.icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="icon.svg_content" />
                  <img v-else class="le__icon-preview le__icon-preview--image" :src="icon.image_data_url" alt="" />
                </button>
              </div>
              <p v-else class="le__filter-empty">No {{ section.label.toLowerCase() }} icons.</p>
            </div>
            <div v-if="iconStore.canCreate" class="le__icon-manager">
              <input v-model="uploadTitle" class="le__input le__input--sm" type="text" placeholder="Icon name" />
              <select v-if="iconStore.canManageGlobal" v-model="uploadScope" class="le__input le__input--xs">
                <option value="Personal">Personal</option>
                <option value="Global">Global</option>
              </select>
              <label class="le__btn le__btn--ghost" :class="{ 'le__btn--disabled': iconBusy }">
                Upload Image
                <input type="file" accept="image/*" class="le__file-input" :disabled="iconBusy" @change="uploadIconFromEvent" />
              </label>
            </div>
            <div v-if="selectedIcon?.can_edit" class="le__icon-manager">
              <input v-model="iconEditTitle" class="le__input le__input--sm" type="text" />
              <button type="button" class="le__btn le__btn--ghost" :disabled="iconBusy" @click="renameSelectedIcon">Rename</button>
              <label class="le__btn le__btn--ghost" :class="{ 'le__btn--disabled': iconBusy }">
                Replace Image
                <input type="file" accept="image/*" class="le__file-input" :disabled="iconBusy" @change="replaceSelectedIcon" />
              </label>
              <button v-if="selectedIcon?.can_delete" type="button" class="le__btn le__btn--danger" :disabled="iconBusy" @click="deleteSelectedIcon">Delete</button>
            </div>
          </div>
        </div>

        <!-- Cluster toggle -->
        <label class="le__check">
          <input v-model="form.cluster" type="checkbox" :true-value="1" :false-value="0" />
          <span>Cluster pins at low zoom</span>
        </label>

        <!-- Enabled toggle -->
        <label class="le__check">
          <input v-model="form.enabled" type="checkbox" :true-value="1" :false-value="0" />
          <span>Visible on map</span>
        </label>

        <!-- Filter editor -->
        <div class="le__filter">
          <FilterBuilder
            v-model="form.filter_rows"
            :source-doctype="form.source_doctype"
            title="Filter rows"
          />
        </div>

        <!-- Group By / Segmentation -->
        <div class="le__filter">
          <div class="le__filter-header">
            <span class="le__label">Group By <span class="le__hint">(color/icon by value)</span></span>
          </div>
          <select v-model="form.group_by_field" class="le__input" @change="onGroupByChange">
            <option value="">— none —</option>
            <option v-for="f in sourceFields" :key="f.fieldname" :value="f.fieldname">
              {{ f.label }} ({{ f.fieldname }})
            </option>
          </select>

          <!-- Group-specific overrides -->
          <div v-if="form.group_by_field && groupValues.length" class="le__group-list">
            <p class="le__group-note">Colors auto-assigned. Click a swatch to override; click the label to rename.</p>
            <div v-for="val in groupValues" :key="String(val)" class="le__group-row">
              <div class="le__group-val">
                <input
                  type="color"
                  class="le__color le__color--sm"
                  :value="form.group_config[String(val)]?.color || GROUP_PALETTE[0]"
                  @input="e => {
                    const cfg = form.group_config[String(val)] || { color: '', icon: '', label: '' }
                    cfg.color = e.target.value
                    form.group_config[String(val)] = cfg
                  }"
                  :title="'Color for ' + val"
                />
                <span class="le__group-val-text">{{ form.group_config[String(val)]?.label || val }}</span>
              </div>
              <div class="le__group-override">
                <select
                  :value="form.group_config[String(val)]?.icon || ''"
                  @change="e => {
                    const cfg = form.group_config[String(val)] || { color: '', icon: '', label: '' }
                    cfg.icon = e.target.value
                    form.group_config[String(val)] = cfg
                  }"
                  class="le__input le__input--xs"
                >
                  <option value="">No icon</option>
                  <option v-for="icon in allIconOptions" :key="icon.key" :value="icon.key">{{ iconLabel(icon) }}</option>
                </select>
                <details class="le__group-label-edit">
                  <summary class="le__group-label-edit-toggle">Label…</summary>
                  <input
                    :value="form.group_config[String(val)]?.label || ''"
                    @input="e => {
                      const cfg = form.group_config[String(val)] || { color: '', icon: '', label: '' }
                      cfg.label = e.target.value
                      form.group_config[String(val)] = cfg
                    }"
                    type="text"
                    class="le__input le__input--sm"
                    placeholder="Override label"
                  />
                </details>
              </div>
            </div>
          </div>
          <p v-if="!form.group_by_field" class="le__filter-empty">No grouping — all pins use the same style.</p>
        </div>

        <!-- Popup Fields (default popup body) -->
        <div class="le__filter">
          <div class="le__filter-header">
            <span class="le__label">Popup fields <span class="le__hint">(default popup)</span></span>
            <select :value="''" @change="e => addPopupField(e.target.value)" class="le__input le__input--xs">
              <option value="" disabled>+ Add field</option>
              <option v-for="f in sourceFields" :key="f.fieldname" :value="f.fieldname">
                {{ f.label }}
              </option>
            </select>
          </div>
          <div v-if="form.popup_fields.length" class="le__popup-fields">
            <div v-for="(fn, i) in form.popup_fields" :key="fn" class="le__popup-field-row">
              <span class="le__popup-field-name">{{ fn }}</span>
              <button type="button" class="le__btn le__btn--icon" @click="removePopupField(i)" aria-label="Remove">×</button>
            </div>
          </div>
          <p v-if="!form.popup_fields.length" class="le__filter-empty">No fields — default popup shows all fields.</p>
        </div>

        <!-- Click Action -->
        <label class="le__field">
          <span class="le__label">Click action</span>
          <select v-model="form.click_action" class="le__input">
            <option value="popup">Show popup</option>
            <option value="redirect">Open DocType form</option>
            <option value="none">None (no click action)</option>
          </select>
        </label>

        <!-- Heatmap mode (client-only visual toggle; the backend
             heatmap field controls whether the server emits a separate
             GeoJSON heatmap source). -->
        <label class="le__check">
          <input v-model="form.heatmap" type="checkbox" :true-value="1" :false-value="0" />
          <span>Heatmap mode <span class="le__hint">(blur pins into intensity)</span></span>
        </label>

        <!-- Halo radius controls (PR-8). When enabled, each pin gets a
             translucent circle around it representing a service area.
             radius_field picks a numeric field from the source to vary per
             feature; radius_meters is the fallback. -->
        <div class="le__filter">
          <div class="le__filter-header">
            <label class="le__check le__check--inline">
              <input v-model="form.radius_enabled" type="checkbox" :true-value="1" :false-value="0" />
              <span>Radius halo <span class="le__hint">(service area around pin)</span></span>
            </label>
          </div>
          <template v-if="form.radius_enabled">
            <div class="le__row">
              <label class="le__field le__field--half">
                <span class="le__label">Radius field (optional)</span>
                <select v-model="form.radius_field" class="le__input le__input--sm">
                  <option value="">Use default meters</option>
                  <option v-for="f in numericFields" :key="f.fieldname" :value="f.fieldname">
                    {{ f.label }} ({{ f.fieldname }})
                  </option>
                </select>
              </label>
              <label class="le__field le__field--half">
                <span class="le__label">Default meters</span>
                <input v-model.number="form.radius_meters" class="le__input le__input--sm" type="number" min="100" max="50000" step="100" />
              </label>
            </div>
            <label class="le__field">
              <span class="le__label">Halo opacity</span>
              <input v-model.number="form.radius_opacity" class="le__input le__input--sm" type="number" min="0" max="1" step="0.05" />
            </label>
          </template>
        </div>

        <details class="le__advanced">
          <summary>Popup template <span class="le__hint">(Jinja — optional)</span></summary>
          <textarea
            v-model="form.popup_template"
            class="le__textarea"
            rows="5"
            spellcheck="false"
            placeholder="<div class='pin'>{{ label }} — {{ doc.name }}</div>"
          />
          <p class="le__hint le__hint--block">
            Rendered server-side per row. Fields of the source row are in scope (e.g.
            <code>&#123;&#123; doc.name &#125;&#125;</code>,
            <code>&#123;&#123; title &#125;&#125;</code>), plus
            <code>layer</code>. Leave empty to fall back to the default popup.
          </p>
        </details>

        <p v-if="error" class="le__error">{{ error }}</p>
      </div>

      <footer class="le__footer">
        <button v-if="!isCreate" type="button" class="le__btn le__btn--danger" @click="remove">Delete layer</button>
        <div class="le__footer-right">
          <button type="button" class="le__btn le__btn--ghost" @click="close">Cancel</button>
          <button type="button" class="le__btn le__btn--primary" :disabled="saving" @click="save">
            {{ saving ? 'Saving…' : isCreate ? 'Add layer' : 'Save changes' }}
          </button>
        </div>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.le {
  position: fixed; inset: 0; z-index: 1000;
  display: flex; justify-content: flex-end;
  font-family: 'Inter', system-ui, sans-serif;
}
.le__backdrop {
  position: absolute; inset: 0;
  background: rgba(11, 14, 20, 0.45);
  /* Blur is opt-in via the user's `blurOnPanel` setting; default off
     so the map stays visible behind the editor. */
}
.le__backdrop--blur {
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}
.le__panel {
  position: relative;
  width: 420px;
  max-width: 100vw;
  height: 100%;
  background: rgba(15, 19, 28, 0.92);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  color: #E6E8EC;
  display: flex;
  flex-direction: column;
  animation: le-slide 220ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes le-slide {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
.le__header {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 18px 20px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.le__title { margin: 0 0 4px; font-size: 16px; font-weight: 600; }
.le__subtitle { margin: 0; font-size: 11px; color: rgba(230, 232, 236, 0.6); }
.le__close {
  background: transparent; border: 0; color: rgba(230, 232, 236, 0.7);
  font-size: 22px; cursor: pointer; padding: 0 4px; line-height: 1;
}
.le__close:hover { color: #E6E8EC; }
.le__body {
  flex: 1; overflow-y: auto; padding: 16px 20px;
  display: flex; flex-direction: column; gap: 14px;
}
.le__field { display: flex; flex-direction: column; gap: 6px; }
.le__field--half { flex: 1; }
.le__row { display: flex; gap: 12px; }
.le__label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.6); font-weight: 500;
}
.le__hint { color: rgba(230, 232, 236, 0.4); text-transform: none; letter-spacing: 0; font-weight: 400; margin-left: 6px; }
.le__input {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  color: #E6E8EC;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
  transition: border-color 120ms ease;
}
.le__input:focus { border-color: #3B82F6; }
.le__input--sm { padding: 6px 8px; font-size: 11px; }
.le__input--value { flex: 1; min-width: 0; }
.le__input--invalid {
  border-color: rgba(239, 68, 68, 0.85);
  box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.22);
}
.le__color-row { display: flex; gap: 8px; align-items: center; }
.le__color-row--custom {
  align-items: flex-start;
  flex-wrap: wrap;
}
.le__color {
  width: 36px; height: 36px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  padding: 0;
  flex: none;
}
.le__color::-webkit-color-swatch-wrapper { padding: 4px; }
.le__color::-webkit-color-swatch { border: 0; border-radius: 4px; }
.le__color-code {
  flex: 1 1 126px;
  min-width: 0;
  height: 36px;
  box-sizing: border-box;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.le__color-presets {
  display: flex;
  flex: 1 0 100%;
  flex-wrap: wrap;
  gap: 4px;
}
.le__color-chip {
  width: 18px; height: 18px; border-radius: 5px; border: 1px solid rgba(0,0,0,0.3);
  cursor: pointer; padding: 0; outline: none;
}
.le__color-chip--active { box-shadow: 0 0 0 2px #fff; }
.le__size-row { display: flex; gap: 4px; }
.le__size-chip {
  background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255,255,255,0.1);
  color: rgba(230, 232, 236, 0.7);
  padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 11px;
  font-weight: 500; min-width: 32px;
}
.le__size-chip--active {
  background: rgba(59, 130, 246, 0.18); border-color: #3B82F6; color: #fff;
}

/* Icon glyph picker */
.le__row--col { flex-direction: column; }
.le__icon-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 4px;
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  padding: 6px;
}
.le__icon-cell {
  display: flex; align-items: center; justify-content: center;
  width: 100%; aspect-ratio: 1 / 1;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  color: #E6E8EC;
  transition: background 100ms ease, border-color 100ms ease;
}
.le__icon-cell:hover {
  background: rgba(255, 255, 255, 0.05);
}
.le__icon-cell[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
  border-color: #3B82F6;
}
.le__icon-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}
.le__icon-section-title {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.55);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.le__check--master {
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-radius: 7px;
  padding: 10px 12px;
}
.le__master-banner {
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 7px;
  padding: 10px 12px;
  font-size: 11px;
  color: rgba(245, 158, 11, 0.95);
  line-height: 1.4;
}
.le__icon-preview {
  width: 18px; height: 18px; fill: currentColor;
}
.le__icon-preview--custom :deep(svg) {
  width: 18px;
  height: 18px;
  display: block;
  color: currentColor;
}
.le__icon-preview--image {
  object-fit: contain;
}
.le__icon-preview--none {
  font-size: 14px; color: rgba(230, 232, 236, 0.5);
  display: flex; align-items: center; justify-content: center;
}
.le__icon-manager {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
  flex-wrap: wrap;
}
.le__file-input {
  display: none;
}
.le__check { display: flex; align-items: center; gap: 8px; font-size: 12px; cursor: pointer; }
.le__check input { accent-color: #3B82F6; }
.le__filter {
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.le__filter-header { display: flex; align-items: center; justify-content: space-between; }
.le__filter-row { display: flex; gap: 6px; align-items: center; }
.le__filter-empty { margin: 0; font-size: 11px; color: rgba(230, 232, 236, 0.5); }
.le__advanced {
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  padding-top: 8px;
}
.le__advanced > summary {
  cursor: pointer; font-size: 12px; color: rgba(230, 232, 236, 0.75);
  padding: 4px 0; user-select: none;
}
.le__textarea {
  width: 100%;
  background: rgba(0, 0, 0, 0.25);
  color: #E6E8EC;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  resize: vertical;
  min-height: 80px;
  box-sizing: border-box;
}
.le__textarea:focus { outline: none; border-color: #3B82F6; }
.le__hint--block {
  display: block; margin: 6px 0 0; line-height: 1.4;
}
.le__hint--block code {
  background: rgba(255, 255, 255, 0.05);
  padding: 0 4px; border-radius: 3px; font-size: 11px;
}
.le__footer {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(0, 0, 0, 0.2);
}
.le__footer-right { display: flex; gap: 8px; margin-left: auto; }
.le__btn {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #E6E8EC;
  padding: 7px 14px; border-radius: 7px; font-size: 12px; cursor: pointer;
  font-family: inherit; font-weight: 500;
}
.le__btn:hover { background: rgba(255, 255, 255, 0.10); }
.le__btn--ghost { background: transparent; border-color: rgba(255, 255, 255, 0.1); }
.le__btn--disabled,
.le__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.le__btn--primary {
  background: #3B82F6; border-color: #3B82F6; color: #fff;
}
.le__btn--primary:hover { background: #2563EB; }
.le__btn--primary:disabled { opacity: 0.5; cursor: not-allowed; }
.le__btn--danger { color: #EF4444; }
.le__btn--danger:hover { background: rgba(239, 68, 68, 0.10); }
.le__btn--icon { padding: 4px 8px; font-size: 14px; }
.le__error {
  margin: 0; padding: 8px 10px; font-size: 11px; color: #FCA5A5;
  background: rgba(239, 68, 68, 0.10);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 6px;
}

/* Group By / Segmentation UI */
.le__group-list {
  margin-top: 10px;
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.le__group-note {
  margin: 0 0 8px;
  font-size: 11px;
  color: rgba(230, 232, 236, 0.5);
  line-height: 1.4;
}
.le__group-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 6px;
  padding: 6px 8px;
}
.le__group-val {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  color: rgba(230, 232, 236, 0.85);
  min-width: 0;
}
.le__group-val-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}
.le__group-override {
  display: flex;
  gap: 6px;
  align-items: center;
}
.le__group-label-edit {
  margin-top: 4px;
}
.le__group-label-edit-toggle {
  cursor: pointer;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.5);
  text-decoration: underline dotted;
  user-select: none;
}
.le__group-color {
  display: flex;
  align-items: center;
  flex: none;
}
.le__color--sm {
  width: 24px;
  height: 24px;
  padding: 0;
}
.le__color--sm::-webkit-color-swatch-wrapper { padding: 2px; }
.le__color--sm::-webkit-color-swatch { border-radius: 3px; border: 0; }
.le__input--xs {
  padding: 4px 6px;
  font-size: 11px;
  min-width: 80px;
}

/* Popup Fields UI */
.le__popup-fields {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.le__popup-field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 5px;
  padding: 4px 8px;
}
.le__popup-field-name {
  font-size: 11px;
  color: rgba(230, 232, 236, 0.85);
}
</style>
