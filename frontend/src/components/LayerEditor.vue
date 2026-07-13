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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { call } from '../api/client.js'
import { useUiStore } from '../state/ui.js'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useIconsStore } from '../state/icons.js'
import { ICON_PATHS } from '../api/icons.js'
import FilterBuilder from './FilterBuilder.vue'
import AdvancedGroupingModal from './AdvancedGroupingModal.vue'
import UiColorInput from './ui/UiColorInput.vue'
import UiNumberInput from './ui/UiNumberInput.vue'
import UiSelect from './ui/UiSelect.vue'
import { parseFilterRows, serializeFilterRows } from '../lib/filters.js'
import { RAMP_PRESETS, serializeRamp } from '../api/heatmap.js'

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
  location_source: 'Direct Fields',
  location_link_field: '',
  location_doctype: '',
  location_reverse_link_field: '',
  location_fields_json: '',
  latitude_field: 'latitude',
  longitude_field: 'longitude',
  label_field: '',
  color: '#3B82F6',
  size: 'm',
  pin_min_zoom: 0,
  cluster: 1,
  enabled: 1,
  icon: '',          // '' = no glyph (plain dot), otherwise id from icons.svg
  filter_rows: [], // [{ field, op, value }]
  popup_template: '',  // Jinja template rendered server-side per feature
  popup_fields: [],    // list of fieldnames for the default popup body
  linked_metrics_json: '',
  linked_metric_filters_json: '',
  group_by_field: '',  // '' = no grouping; otherwise a fieldname on source
  group_config: {},    // { "<value>": { color, icon, label } } — overrides default style
  click_action: 'popup', // 'popup' | 'redirect' | 'none'
  heatmap: 0,
  heatmap_mode: 'count',
  heatmap_weight_field: '',
  heatmap_weight_min: 0,
  heatmap_weight_max: 100,
  heatmap_weight_scale: 'linear',
  heatmap_radius_min: 10,
  heatmap_radius_max: 30,
  heatmap_intensity_min: 1,
  heatmap_intensity_max: 2.5,
  heatmap_opacity: 0.75,
  heatmap_ramp_json: '',
  territory_enabled: 0,
  territory_color: '',
  territory_opacity: 0.18,
  territory_padding_meters: 2500,
})
const saving = ref(false)
const error = ref('')
const sourceDts = ref([])
const sourceFields = ref([])          // local working list (filled from cache)
const sourceFieldsLoading = ref(false)
const moneyMetricSuggestions = ref([])
const moneyMetricSuggestionsLoading = ref(false)
const moneyMetricSuggestionsError = ref('')
const metricFieldCache = ref({})
const metricFieldLoading = ref({})
const metricFieldError = ref({})
const locationFields = ref([])
const locationFieldsLoading = ref(false)
const loadedLocationDoctype = ref('')
const sourcePickerOpen = ref(false)
const fieldPickerOpen = ref('')
const groupValues = ref([]) // distinct values for the group_by_field
const advancedGroupingOpen = ref(false)
const previewOriginalLayer = ref(null)
const previewOriginalUi = ref(null)
const previewCommitted = ref(false)
const uploadTitle = ref('')
const uploadScope = ref('Personal')
const iconEditTitle = ref('')
const iconBusy = ref(false)
const popupPreview = ref(null)
const popupPreviewLoading = ref(false)
const popupPreviewError = ref('')

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
const LOCATION_SOURCE_OPTIONS = [
  { v: 'Direct Fields', label: 'Direct fields', hint: 'Latitude and longitude live on the source row' },
  { v: 'Linked DocType', label: 'Linked DocType', hint: 'A Link field points to the location row' },
  { v: 'Reverse Linked DocType', label: 'Reverse linked DocType', hint: 'Location rows point back to the source row' },
  { v: 'Dynamic Link DocType', label: 'Dynamic Link DocType', hint: 'Location rows use Frappe Dynamic Link rows' },
]
const CLICK_ACTION_OPTIONS = [
  { v: 'popup', label: 'Show popup', hint: 'Open the map popup' },
  { v: 'open_form', label: 'Open DocType form', hint: 'Open the source row in Desk' },
  { v: 'none', label: 'None', hint: 'Ignore pin clicks' },
]
const HEATMAP_MODE_OPTIONS = [
  { v: 'count', label: 'Record concentration', hint: 'Where are records concentrated?' },
  { v: 'sum', label: 'Metric concentration', hint: 'Where is a numeric metric concentrated?' },
]
const HEATMAP_SCALE_OPTIONS = [
  { v: 'linear', label: 'Linear' },
  { v: 'log', label: 'Log' },
]
const UPLOAD_SCOPE_OPTIONS = [
  { v: 'Personal', label: 'Personal' },
  { v: 'Global', label: 'Global' },
]
const LINKED_METRIC_AGGREGATES = [
  { v: 'count', label: 'Count' },
  { v: 'sum', label: 'Sum' },
  { v: 'avg', label: 'Avg' },
  { v: 'min', label: 'Min' },
  { v: 'max', label: 'Max' },
]
const METRIC_FILTER_OPERATORS = ['=', '!=', '>', '>=', '<', '<=', 'between', 'in', 'not in', 'is']

const iconSections = computed(() => [
  { key: 'builtin', label: 'Built-in', icons: iconStore.builtin },
  { key: 'personal', label: 'Personal', icons: iconStore.personal },
  { key: 'global', label: 'Global', icons: iconStore.global },
].filter((section) => section.icons.length || section.key !== 'global' || iconStore.canManageGlobal))
const allIconOptions = computed(() => iconStore.all)
const selectedIcon = computed(() => iconStore.byKey.get(form.value.icon))
const colorInputInvalid = computed(() => !!form.value.color && !isValidCssColor(form.value.color))
const filteredSourceDts = computed(() => {
  const q = String(form.value.source_doctype || '').trim().toLowerCase()
  const items = q
    ? sourceDts.value.filter((dt) =>
      String(dt.name || '').toLowerCase().includes(q) ||
      String(dt.module || '').toLowerCase().includes(q)
    )
    : sourceDts.value
  return items.slice(0, 80)
})
const filteredLocationDts = computed(() => {
  const q = String(form.value.location_doctype || '').trim().toLowerCase()
  const items = q
    ? sourceDts.value.filter((dt) =>
      String(dt.name || '').toLowerCase().includes(q) ||
      String(dt.module || '').toLowerCase().includes(q)
    )
    : sourceDts.value
  return items.slice(0, 80)
})

async function loadSourceDoctypes() {
  if (sourceDts.value.length) return
  try {
    sourceDts.value = await call('expedition.api.layer.list_source_doctypes')
  } catch (e) {
    error.value = 'Could not load source DocTypes: ' + e.message
  }
}

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
    await loadSourceDoctypes()
    if (v.mode === 'create') {
      form.value = {
        name: null,
        title: '',
        source_doctype: '',
        location_source: 'Direct Fields',
        location_link_field: '',
        location_doctype: '',
        location_reverse_link_field: '',
        location_fields_json: '',
        latitude_field: 'latitude',
        longitude_field: 'longitude',
        label_field: '',
        color: '#3B82F6',
        size: 'm',
        pin_min_zoom: 0,
        cluster: 1,
        enabled: 1,
        icon: '',
        filter_rows: [],
        popup_template: '',
        popup_fields: [],
        linked_metrics_json: '',
        linked_metric_filters_json: '',
        group_by_field: '',
        group_config: {},
        click_action: 'popup',
        heatmap: 0,
        heatmap_mode: 'count',
        heatmap_weight_field: '',
        heatmap_weight_min: 0,
        heatmap_weight_max: 100,
        heatmap_weight_scale: 'linear',
        heatmap_weight_stops_json: '',
        heatmap_radius_min: 10,
        heatmap_radius_max: 30,
        heatmap_intensity_min: 1,
        heatmap_intensity_max: 2.5,
        heatmap_opacity: 0.75,
        heatmap_ramp_json: '',
        territory_enabled: 0,
        territory_color: '',
        territory_opacity: 0.18,
        territory_padding_meters: 2500,
        radius_enabled: 0,
        radius_field: '',
        radius_meters: 5000,
        radius_opacity: 0.18,
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
        location_source: l.location_source || 'Direct Fields',
        location_link_field: l.location_link_field || '',
        location_doctype: l.location_doctype || '',
        location_reverse_link_field: l.location_reverse_link_field || '',
        location_fields_json: l.location_fields_json || '',
        latitude_field: l.latitude_field,
        longitude_field: l.longitude_field,
        label_field: l.label_field || '',
        color: l.color || '#3B82F6',
        size: l.size || 'm',
        pin_min_zoom: l.pin_min_zoom ?? 0,
        cluster: l.cluster ? 1 : 0,
        enabled: l.enabled ? 1 : 0,
        icon: l.icon || '',
        filter_rows: parseFilterRows(l.filter_json),
        popup_template: l.popup_template || '',
        popup_fields: l.popup_fields || [],
        linked_metrics_json: l.linked_metrics_json || '',
        linked_metric_filters_json: l.linked_metric_filters_json || '',
        group_by_field: l.group_by_field || '',
        group_config: _parseGroupConfig(l.group_config_json || l.group_config),
        click_action: l.click_action || 'popup',
        heatmap: l.heatmap ? 1 : 0,
        heatmap_mode: l.heatmap_mode || 'count',
        heatmap_weight_field: l.heatmap_weight_field || '',
        heatmap_weight_min: l.heatmap_weight_min ?? 0,
        heatmap_weight_max: l.heatmap_weight_max ?? 100,
        heatmap_weight_scale: l.heatmap_weight_scale || 'linear',
        heatmap_weight_stops_json: l.heatmap_weight_stops_json || '',
        heatmap_radius_min: l.heatmap_radius_min ?? 10,
        heatmap_radius_max: l.heatmap_radius_max ?? 30,
        heatmap_intensity_min: l.heatmap_intensity_min ?? 1,
        heatmap_intensity_max: l.heatmap_intensity_max ?? 2.5,
        heatmap_opacity: l.heatmap_opacity ?? 0.75,
        heatmap_ramp_json: l.heatmap_ramp_json || '',
        territory_enabled: l.territory_enabled ? 1 : 0,
        territory_color: l.territory_color || '',
        territory_opacity: l.territory_opacity ?? 0.18,
        territory_padding_meters: l.territory_padding_meters ?? 2500,
        radius_enabled: l.radius_enabled ? 1 : 0,
        radius_field: l.radius_field || '',
        radius_meters: l.radius_meters ?? 5000,
        radius_opacity: l.radius_opacity ?? 0.18,
      }
      await _loadSourceFields(l.source_doctype)
      await _loadLocationFieldsFromForm()
      if (form.value.group_by_field && !isAdvancedGrouping.value) {
        if (groupMode.value === 'bands') {
          _syncBandStyles()
        } else {
          await loadExactGroupValues()
        }
      }
    }
  },
  { immediate: true }
)

watch(
  () => ({
    color: form.value.color,
    size: form.value.size,
    pin_min_zoom: form.value.pin_min_zoom,
    cluster: form.value.cluster,
    enabled: form.value.enabled,
    icon: form.value.icon,
    heatmap: form.value.heatmap,
    heatmap_mode: form.value.heatmap_mode,
    heatmap_weight_field: form.value.heatmap_weight_field,
    heatmap_weight_min: form.value.heatmap_weight_min,
    heatmap_weight_max: form.value.heatmap_weight_max,
    heatmap_weight_scale: form.value.heatmap_weight_scale,
    heatmap_radius_min: form.value.heatmap_radius_min,
    heatmap_radius_max: form.value.heatmap_radius_max,
    heatmap_intensity_min: form.value.heatmap_intensity_min,
    heatmap_intensity_max: form.value.heatmap_intensity_max,
    heatmap_opacity: form.value.heatmap_opacity,
    heatmap_ramp_json: form.value.heatmap_ramp_json,
    territory_enabled: form.value.territory_enabled,
    territory_color: form.value.territory_color,
    territory_opacity: form.value.territory_opacity,
    territory_padding_meters: form.value.territory_padding_meters,
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
      pin_min_zoom: Number(preview.pin_min_zoom) || 0,
      cluster: preview.cluster,
      enabled: preview.enabled,
      icon: preview.icon || '',
        heatmap: preview.heatmap,
        heatmap_mode: preview.heatmap_mode,
        heatmap_weight_field: preview.heatmap_weight_field,
        heatmap_weight_min: preview.heatmap_weight_min,
        heatmap_weight_max: preview.heatmap_weight_max,
        heatmap_weight_scale: preview.heatmap_weight_scale,
        heatmap_radius_min: preview.heatmap_radius_min,
        heatmap_radius_max: preview.heatmap_radius_max,
        heatmap_intensity_min: preview.heatmap_intensity_min,
        heatmap_intensity_max: preview.heatmap_intensity_max,
        heatmap_opacity: preview.heatmap_opacity,
        heatmap_ramp_json: preview.heatmap_ramp_json,
        territory_enabled: preview.territory_enabled,
        territory_color: preview.territory_color || '',
        territory_opacity: preview.territory_opacity,
        territory_padding_meters: preview.territory_padding_meters,
        radius_enabled: preview.radius_enabled,
      radius_field: preview.radius_field || '',
      radius_meters: preview.radius_meters,
      radius_opacity: preview.radius_opacity,
      group_by_field: preview.group_by_field || '',
      group_config_json: preview.group_config_json || '',
      group_config: form.value.group_config || {},
    })
    layerStore.previewGroupStyles(form.value.name, form.value.group_config || {})
  },
  { deep: true }
)

function cancelLayerPreview() {
  const original = previewOriginalLayer.value
  if (original && !previewCommitted.value) {
    layerStore.previewLayerFields(original.name, original)
    layerStore.previewGroupStyles(original.name, original.group_config || {})
    layerStore.refetchLayer(original.name).catch(() => {})
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

function closeGroupIconMenus(except = null) {
  document.querySelectorAll('.le__group-icon-menu[open]').forEach((menu) => {
    if (menu !== except) menu.removeAttribute('open')
  })
}

function closeGroupColorMenus(except = null) {
  document.querySelectorAll('.le__group-color-menu[open]').forEach((menu) => {
    if (menu !== except) menu.removeAttribute('open')
  })
}

function onGroupIconMenuToggle(e) {
  if (e.target?.open) closeGroupIconMenus(e.target)
}

function onDocumentClick(e) {
  if (!e.target?.closest?.('.le__link-field')) {
    sourcePickerOpen.value = false
    fieldPickerOpen.value = ''
  }
  if (!e.target?.closest?.('.le__group-icon-menu')) closeGroupIconMenus()
  if (!e.target?.closest?.('.le__group-color-menu')) closeGroupColorMenus()
  if (!e.target?.closest?.('.le__band-date-menu') && !e.target?.closest?.('.le__date-trigger')) closeBandEdgePicker()
  if (!e.target?.closest?.('.le__group-field-menu')) {
    document.querySelectorAll('.le__group-field-menu[open]').forEach((menu) => {
      menu.removeAttribute('open')
    })
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
})

function setGroupIcon(groupKey, iconKey, event = null) {
  const key = String(groupKey)
  const cfg = form.value.group_config[key] || { color: '', icon: '', label: '' }
  cfg.icon = iconKey || ''
  form.value.group_config[key] = cfg
  event?.target?.closest?.('.le__group-icon-menu')?.removeAttribute('open')
}

function setGroupColor(groupKey, color) {
  const key = String(groupKey)
  const cfg = form.value.group_config[key] || { color: '', icon: '', label: '' }
  cfg.color = color
  form.value.group_config[key] = cfg
}

function setGroupTerritoryColor(groupKey, color) {
  const key = String(groupKey)
  const cfg = form.value.group_config[key] || { color: '', icon: '', label: '' }
  cfg.territory_color = color
  form.value.group_config[key] = cfg
}

function groupIcon(groupKey) {
  const iconKey = form.value.group_config[String(groupKey)]?.icon
  if (iconKey === "__none") return null
  return iconKey ? iconStore.byKey.get(iconKey) : null
}

function layerIconOption() {
  return form.value.icon ? iconStore.byKey.get(form.value.icon) : null
}

function groupIconMode(groupKey) {
  const iconKey = form.value.group_config[String(groupKey)]?.icon
  if (iconKey === "__none") return "none"
  return iconKey ? "override" : "inherit"
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
  if (obj.__grouping?.version >= 2) {
    const levels = Array.isArray(obj.__grouping.levels)
      ? obj.__grouping.levels
          .filter((level) => level?.field)
          .map((level) => {
            const item = { field: level.field, mode: level.mode === 'bands' ? 'bands' : 'value' }
            if (item.mode === 'bands') {
              item.kind = level.kind || 'number'
              item.bands = Array.isArray(level.bands) ? level.bands : []
            }
            return item
          })
      : []
    if (!levels.length) return ''
    const groups = {}
    const rawGroups = obj.groups && typeof obj.groups === 'object' ? obj.groups : {}
    for (const [key, value] of Object.entries(rawGroups)) {
      if (!value || typeof value !== 'object') continue
      const item = {}
      if (value.color) item.color = value.color
      if (value.territory_color) item.territory_color = value.territory_color
      if (Object.prototype.hasOwnProperty.call(value, 'icon')) item.icon = value.icon || ''
      if (value.label) item.label = value.label
      if (Object.keys(item).length) groups[key] = item
    }
    return JSON.stringify({
      __grouping: { version: 2, levels },
      groups,
    })
  }
  const cleaned = {}
  for (const [k, v] of Object.entries(obj)) {
    if (!v || typeof v !== 'object') continue
    if (k === '__grouping') {
      if (v.mode === 'bands' && Array.isArray(v.bands) && v.bands.length) {
        cleaned.__grouping = {
          mode: 'bands',
          kind: v.kind || groupBandKind.value || 'number',
          bands: v.bands.map((band) => ({
            key: String(band.key),
            min: band.min === '' ? null : band.min,
            max: band.max === '' ? null : band.max,
            label: _bandLabel(band),
          })),
        }
      }
    } else if (v.color || v.territory_color || v.icon || v.label) {
      cleaned[k] = { ...v }
    }
  }
  return Object.keys(cleaned).length ? JSON.stringify(cleaned) : ''
}

async function _loadSourceFields(dt) {
  if (!dt) {
    sourceFields.value = []
    sourceFieldsLoading.value = false
    moneyMetricSuggestions.value = []
    moneyMetricSuggestionsLoading.value = false
    moneyMetricSuggestionsError.value = ''
    return
  }
  _loadMoneyMetricSuggestions(dt)
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

async function _loadMoneyMetricSuggestions(dt) {
  const requested = String(dt || '').trim()
  if (!requested) {
    moneyMetricSuggestions.value = []
    moneyMetricSuggestionsLoading.value = false
    moneyMetricSuggestionsError.value = ''
    return
  }
  moneyMetricSuggestionsLoading.value = true
  moneyMetricSuggestionsError.value = ''
  try {
    const result = await call('expedition.api.layer.suggest_money_metrics', {
      source_doctype: requested,
      limit: 16,
    })
    if (form.value.source_doctype !== requested) return
    moneyMetricSuggestions.value = Array.isArray(result?.suggestions)
      ? result.suggestions
      : []
  } catch (e) {
    if (form.value.source_doctype !== requested) return
    moneyMetricSuggestions.value = []
    moneyMetricSuggestionsError.value = e.message || String(e)
  } finally {
    if (form.value.source_doctype === requested) {
      moneyMetricSuggestionsLoading.value = false
    }
  }
}

async function _loadLocationFields(dt) {
  if (!dt) {
    locationFields.value = []
    locationFieldsLoading.value = false
    loadedLocationDoctype.value = ''
    return
  }
  locationFieldsLoading.value = true
  try {
    locationFields.value = await layerStore.getSourceFields(dt)
    loadedLocationDoctype.value = dt
  } catch (e) {
    error.value = 'Could not load linked location fields: ' + e.message
    locationFields.value = []
    loadedLocationDoctype.value = ''
  } finally {
    locationFieldsLoading.value = false
  }
}

async function _loadLocationFieldsFromForm() {
  if (form.value.location_source === 'Direct Fields') {
    locationFields.value = []
    return
  }
  await _loadLocationFields(form.value.location_doctype)
}

// Numeric fields only — used for the radius-field picker so the
// halo can't accidentally be sized by a Text field.
const linkedMetricFields = computed(() => {
  return linkedMetricRows.value
    .filter((metric) => metric?.key)
    .map((metric) => ({
      fieldname: `_metric_${metric.key}`,
      fieldtype: 'Float',
      label: metric.label || metric.key,
      options: '',
    }))
})
const groupFieldOptions = computed(() => [
  ...sourceFields.value,
  ...linkedMetricFields.value,
])
const numericFields = computed(() =>
  [
    ...sourceFields.value.filter((f) =>
      ['Int', 'Float', 'Currency', 'Percent'].includes(f.fieldtype)
    ),
    ...linkedMetricFields.value,
  ]
)
const coordinateFields = computed(() =>
  sourceFields.value.filter((f) => f.fieldtype === 'Float')
)
const linkFields = computed(() =>
  sourceFields.value.filter((f) => f.fieldtype === 'Link' && f.options)
)
const activeCoordinateFields = computed(() =>
  form.value.location_source !== 'Direct Fields'
    ? locationFields.value.filter((f) => f.fieldtype === 'Float')
    : coordinateFields.value
)
const activeCoordinateLoading = computed(() =>
  form.value.location_source !== 'Direct Fields'
    ? locationFieldsLoading.value
    : sourceFieldsLoading.value
)
const reverseLinkFields = computed(() =>
  locationFields.value.filter((f) =>
    f.fieldtype === 'Link' && f.options === form.value.source_doctype
  )
)
const locationFieldRows = computed(() =>
  _parseJsonArray(form.value.location_fields_json).filter((fieldname) => typeof fieldname === 'string')
)
const availableLocationExtraFields = computed(() =>
  (form.value.location_source !== 'Direct Fields' ? locationFields.value : sourceFields.value)
    .filter((f) =>
      f.fieldname &&
      !['Section Break', 'Column Break', 'Tab Break', 'HTML', 'Table', 'Table MultiSelect', 'Button', 'Image', 'Fold'].includes(f.fieldtype) &&
      ![form.value.latitude_field, form.value.longitude_field].includes(f.fieldname) &&
      !locationFieldRows.value.includes(f.fieldname)
    )
)
const linkedMetricRows = computed(() =>
  _parseJsonArray(form.value.linked_metrics_json).filter((row) => row && typeof row === 'object')
)
const linkedMetricFilterRows = computed(() =>
  _parseJsonArray(form.value.linked_metric_filters_json).filter((row) => row && typeof row === 'object')
)
const linkedMetricKeys = computed(() => linkedMetricRows.value.map((row) => row.key).filter(Boolean))
const linkedMetricByKey = computed(() => {
  const out = new Map()
  for (const metric of linkedMetricRows.value) {
    if (metric?.key) out.set(metric.key, metric)
  }
  return out
})
function metricDoctype(metric) {
  return String(metric?.source_doctype || '').trim()
}

function metricFields(metric) {
  return metricFieldCache.value[metricDoctype(metric)] || []
}

function metricFieldsBusy(metric) {
  return !!metricFieldLoading.value[metricDoctype(metric)]
}

function metricFieldsMessage(metric) {
  const dt = metricDoctype(metric)
  if (!dt) return 'Enter a DocType first.'
  if (metricFieldLoading.value[dt]) return 'Loading fields...'
  if (metricFieldError.value[dt]) return 'Could not load fields.'
  if (!metricFieldCache.value[dt]) return 'Open to load fields.'
  return ''
}

function metricLinkFields(metric) {
  const fields = metricFields(metric).filter((f) =>
    f.fieldname && (
      f.fieldtype === 'Dynamic Link' ||
      (f.fieldtype === 'Link' && f.options === form.value.source_doctype)
    )
  )
  return fields.sort((a, b) => {
    const aScore = a.fieldtype === 'Link' ? 0 : 1
    const bScore = b.fieldtype === 'Link' ? 0 : 1
    return aScore - bScore || String(a.label || a.fieldname).localeCompare(String(b.label || b.fieldname))
  })
}

function metricDynamicSelectorFields(metric) {
  const linkField = metricFields(metric).find((f) => f.fieldname === metric.link_field)
  const required = linkField?.fieldtype === 'Dynamic Link' ? linkField.options : ''
  if (!required) return []
  const fields = metricFields(metric).filter((f) => {
    if (!f.fieldname) return false
    if (required && f.fieldname === required) return true
    if (f.fieldtype === 'Link' && f.options === 'DocType') return true
    if (f.fieldtype === 'Select') return true
    return ['Data', 'Small Text', 'Read Only'].includes(f.fieldtype)
  })
  return fields.sort((a, b) => {
    const aScore = required && a.fieldname === required ? 0 : 1
    const bScore = required && b.fieldname === required ? 0 : 1
    return aScore - bScore || String(a.label || a.fieldname).localeCompare(String(b.label || b.fieldname))
  })
}

function metricAmountFields(metric) {
  return metricFields(metric).filter((f) =>
    f.fieldname && ['Currency', 'Duration', 'Float', 'Int', 'Percent', 'Rating'].includes(f.fieldtype)
  )
}
const visibleMoneyMetricSuggestions = computed(() => {
  const existing = new Set(linkedMetricKeys.value)
  return moneyMetricSuggestions.value
    .filter((suggestion) => suggestion?.key && !existing.has(suggestion.key))
    .slice(0, 8)
})
const popupFieldOptions = computed(() =>
  sourceFields.value.filter((field) => !form.value.popup_fields.includes(field.fieldname))
)
const popupTemplateSnippets = computed(() => {
  const snippets = [
    { label: 'Label', value: '{{ label }}' },
    { label: 'Name', value: '{{ doc.name }}' },
  ]
  for (const field of sourceFields.value.slice(0, 4)) {
    if (!field?.fieldname || field.fieldname === 'name') continue
    snippets.push({
      label: field.label || field.fieldname,
      value: `{{ doc.${field.fieldname} }}`,
    })
  }
  for (const metric of linkedMetricRows.value.slice(0, 3)) {
    if (!metric?.key) continue
    snippets.push({
      label: metric.label || metric.key,
      value: `{{ metrics.${metric.key} }}`,
    })
  }
  for (const fieldname of locationFieldRows.value.slice(0, 3)) {
    snippets.push({
      label: `Location ${fieldname}`,
      value: `{{ location.${fieldname} }}`,
    })
  }
  return snippets.slice(0, 10)
})
const heatmapRampOptions = computed(() =>
  Object.entries(RAMP_PRESETS).map(([key, preset]) => ({ key, label: preset.label }))
)
const selectedGroupField = computed(() =>
  groupFieldOptions.value.find((f) => f.fieldname === form.value.group_by_field) || null
)
const groupBandKind = computed(() => {
  const ft = selectedGroupField.value?.fieldtype
  if (['Int', 'Float', 'Currency', 'Percent', 'Duration', 'Rating'].includes(ft)) return 'number'
  if (ft === 'Date') return 'date'
  if (ft === 'Datetime') return 'datetime'
  if (ft === 'Time') return 'time'
  return ''
})
const groupBySupportsBands = computed(() =>
  !!groupBandKind.value
)

function applyHeatmapRamp(key) {
  const preset = RAMP_PRESETS[key]
  if (!preset) return
  const stops = preset.build ? preset.build(form.value.color) : preset.stops
  form.value.heatmap_ramp_json = serializeRamp(stops)
}
const groupMode = computed(() =>
  form.value.group_config?.__grouping?.mode === 'bands' ? 'bands' : 'value'
)
const isAdvancedGrouping = computed(() => form.value.group_config?.__grouping?.version >= 2)
const advancedGroupingLevels = computed(() =>
  isAdvancedGrouping.value && Array.isArray(form.value.group_config.__grouping.levels)
    ? form.value.group_config.__grouping.levels
    : []
)
const advancedGroupingSummary = computed(() => {
  if (!advancedGroupingLevels.value.length) return ''
  return advancedGroupingLevels.value
    .map((level) => {
      const field = sourceFields.value.find((f) => f.fieldname === level.field)
      return field?.label || level.field
    })
    .join(' > ')
})
const advancedGroupCount = computed(() => {
  const groups = form.value.group_config?.groups
  return groups && typeof groups === 'object' ? Object.keys(groups).length : 0
})
const groupBands = computed(() =>
  Array.isArray(form.value.group_config?.__grouping?.bands)
    ? form.value.group_config.__grouping.bands
    : []
)
const selectedGroupFieldLabel = computed(() =>
  selectedGroupField.value
    ? `${selectedGroupField.value.label} (${selectedGroupField.value.fieldname})`
    : 'None'
)
const bandEdgePicker = ref({ key: '', bandIndex: -1, edge: '', month: '' })
const bandEdgePickerOpen = computed(() => !!bandEdgePicker.value.key)
const bandEdgePickerTitle = computed(() =>
  bandEdgePicker.value.edge === 'min' ? 'From' : 'To'
)
const bandEdgeMonthLabel = computed(() => {
  const date = _monthDate(bandEdgePicker.value.month)
  return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
})
const bandEdgeCalendarDays = computed(() => {
  const base = _monthDate(bandEdgePicker.value.month)
  const first = new Date(base.getFullYear(), base.getMonth(), 1)
  const startOffset = first.getDay()
  const daysInMonth = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate()
  const out = []
  for (let i = 0; i < startOffset; i++) out.push(null)
  for (let day = 1; day <= daysInMonth; day++) {
    const value = _dateInputValue(new Date(base.getFullYear(), base.getMonth(), day))
    out.push({ day, value })
  }
  return out
})

// SVG <use href> against the icons sprite. Append a cache-bust so a
// stale sprite after a hard reload is avoided.
const _iconSpriteHref = (() => {
  const v = Date.now()
  return (id) => `/assets/expedition/icons.svg#${id}?v=${v}`
})()
function iconHref(id) { return _iconSpriteHref(id) }

async function onSourceChange() {
  const sourceDoctype = form.value.source_doctype?.trim?.() || ''
  form.value.source_doctype = sourceDoctype
  await _loadSourceFields(sourceDoctype)
  form.value.location_source = 'Direct Fields'
  form.value.location_link_field = ''
  form.value.location_doctype = ''
  form.value.location_reverse_link_field = ''
  locationFields.value = []
  chooseDefaultCoordinateFields(coordinateFields.value)
  form.value.label_field = ''
  form.value.filter_rows = []
  form.value.group_by_field = ''
  form.value.group_config = {}
  groupValues.value = []
}

async function chooseSourceDoctype(name) {
  form.value.source_doctype = name || ''
  sourcePickerOpen.value = false
  await onSourceChange()
}

function chooseDefaultCoordinateFields(fields) {
  const floatFields = fields || []
  const byName = new Map(floatFields.map((f) => [f.fieldname, f]))
  form.value.latitude_field = byName.has('latitude') ? 'latitude' : (floatFields[0]?.fieldname || '')
  form.value.longitude_field = byName.has('longitude') ? 'longitude' : (floatFields.find((f) => f.fieldname !== form.value.latitude_field)?.fieldname || '')
}

async function onLocationSourceChange() {
  form.value.location_link_field = ''
  form.value.location_doctype = ''
  form.value.location_reverse_link_field = ''
  locationFields.value = []
  loadedLocationDoctype.value = ''
  chooseDefaultCoordinateFields(
    form.value.location_source !== 'Direct Fields'
      ? []
      : coordinateFields.value
  )
}

async function chooseLocationLinkField(fieldname) {
  const field = linkFields.value.find((f) => f.fieldname === fieldname)
  form.value.location_link_field = fieldname || ''
  form.value.location_doctype = field?.options || ''
  fieldPickerOpen.value = ''
  await _loadLocationFields(form.value.location_doctype)
  chooseDefaultCoordinateFields(activeCoordinateFields.value)
}

async function onLocationDoctypeChange() {
  form.value.location_doctype = form.value.location_doctype?.trim?.() || ''
  const changed = form.value.location_doctype !== loadedLocationDoctype.value
  if (changed) form.value.location_reverse_link_field = ''
  await _loadLocationFields(form.value.location_doctype)
  chooseDefaultCoordinateFields(activeCoordinateFields.value)
}

async function chooseLocationDoctype(name) {
  form.value.location_doctype = name || ''
  fieldPickerOpen.value = ''
  await onLocationDoctypeChange()
}

function chooseReverseLinkField(fieldname) {
  form.value.location_reverse_link_field = fieldname || ''
  fieldPickerOpen.value = ''
}

function fieldChoiceLabel(fieldname, fields = sourceFields.value) {
  if (!fieldname) return 'None'
  const field = fields.find((f) => f.fieldname === fieldname)
  return field ? `${field.label} (${field.fieldname})` : fieldname
}

function chooseLayerField(kind, fieldname) {
  form.value[kind] = fieldname || ''
  fieldPickerOpen.value = ''
}

function optionLabel(options, value, fallback = 'None') {
  const option = options.find((item) => item.v === value)
  return option?.label || fallback
}

async function chooseLocationSource(value) {
  form.value.location_source = value
  fieldPickerOpen.value = ''
  await onLocationSourceChange()
}

function chooseHeatmapRamp(key) {
  applyHeatmapRamp(key)
  fieldPickerOpen.value = ''
}

async function onGroupByChange() {
  form.value.group_config = {}
  groupValues.value = []
  if (!form.value.source_doctype || !form.value.group_by_field) return
  if (groupBySupportsBands.value) {
    setGroupMode('bands')
    return
  }
  await loadExactGroupValues()
}

function openAdvancedGrouping() {
  advancedGroupingOpen.value = true
}

function applyAdvancedGroupConfig(config) {
  form.value.group_config = config || {}
  if (form.value.group_config?.__grouping?.version >= 2) {
    form.value.group_by_field = form.value.group_config.__grouping.levels?.[0]?.field || ''
    groupValues.value = []
  }
}

async function chooseGroupField(fieldname) {
  form.value.group_by_field = fieldname || ''
  document.querySelectorAll('.le__group-field-menu[open]').forEach((menu) => {
    menu.removeAttribute('open')
  })
  await onGroupByChange()
}

async function loadExactGroupValues() {
  if (isAdvancedGrouping.value) return
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

function _bandKey() {
  return `band_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
}

function _dateInputValue(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function _monthDate(value) {
  const source = value || _dateInputValue(new Date())
  const [year, month] = String(source).split('-').map((part) => Number(part))
  return new Date(year || new Date().getFullYear(), Math.max(0, (month || 1) - 1), 1)
}

function _addMonths(date, months) {
  const next = new Date(date)
  next.setMonth(next.getMonth() + months)
  return next
}

function _bandEdgeValue(index, edge) {
  return groupBands.value[index]?.[edge] ?? ''
}

function _bandEdgeDate(value) {
  return String(value || '').slice(0, 10)
}

function _bandEdgeTime(value) {
  const text = String(value || '')
  if (text.includes('T')) return text.slice(11, 16)
  if (text.includes(' ')) return text.slice(11, 16)
  if (/^\d{2}:\d{2}/.test(text)) return text.slice(0, 5)
  return ''
}

function formatBandEdgeValue(value, placeholder) {
  return value === '' || value == null ? placeholder : String(value).replace('T', ' ')
}

function openBandEdgePicker(index, edge) {
  const value = _bandEdgeValue(index, edge)
  const dateText = _bandEdgeDate(value) || _dateInputValue(new Date())
  bandEdgePicker.value = {
    key: `${index}:${edge}`,
    bandIndex: index,
    edge,
    month: dateText,
  }
}

function closeBandEdgePicker() {
  bandEdgePicker.value = { key: '', bandIndex: -1, edge: '', month: '' }
}

function shiftBandEdgeMonth(delta) {
  bandEdgePicker.value = {
    ...bandEdgePicker.value,
    month: _dateInputValue(_addMonths(_monthDate(bandEdgePicker.value.month), delta)),
  }
}

function selectBandDate(dateText) {
  const { bandIndex, edge } = bandEdgePicker.value
  if (bandIndex < 0 || !edge) return
  const current = _bandEdgeValue(bandIndex, edge)
  const time = groupBandKind.value === 'datetime'
    ? (_bandEdgeTime(current) || '00:00')
    : ''
  updateBand(bandIndex, { [edge]: groupBandKind.value === 'datetime' ? `${dateText}T${time}` : dateText })
  if (groupBandKind.value === 'date') closeBandEdgePicker()
}

function updateBandEdgeTime(value) {
  const { bandIndex, edge } = bandEdgePicker.value
  if (bandIndex < 0 || !edge) return
  if (groupBandKind.value === 'time') {
    updateBand(bandIndex, { [edge]: value })
    return
  }
  const dateText = _bandEdgeDate(_bandEdgeValue(bandIndex, edge)) || _dateInputValue(new Date())
  updateBand(bandIndex, { [edge]: `${dateText}T${value || '00:00'}` })
}

function _defaultBands() {
  if (groupBandKind.value === 'date') {
    const today = new Date()
    const nextMonth = _dateInputValue(_addMonths(today, 1))
    const afterNextMonth = _dateInputValue(_addMonths(today, 2))
    return [
      { key: _bandKey(), min: '', max: nextMonth },
      { key: _bandKey(), min: nextMonth, max: afterNextMonth },
      { key: _bandKey(), min: afterNextMonth, max: '' },
    ]
  }
  if (groupBandKind.value === 'datetime') {
    const today = new Date()
    const nextMonth = `${_dateInputValue(_addMonths(today, 1))}T00:00`
    const afterNextMonth = `${_dateInputValue(_addMonths(today, 2))}T00:00`
    return [
      { key: _bandKey(), min: '', max: nextMonth },
      { key: _bandKey(), min: nextMonth, max: afterNextMonth },
      { key: _bandKey(), min: afterNextMonth, max: '' },
    ]
  }
  if (groupBandKind.value === 'time') {
    return [
      { key: _bandKey(), min: '', max: '09:00' },
      { key: _bandKey(), min: '09:00', max: '17:00' },
      { key: _bandKey(), min: '17:00', max: '' },
    ]
  }
  return [
    { key: _bandKey(), min: '', max: 1000 },
    { key: _bandKey(), min: 1000, max: 2000 },
    { key: _bandKey(), min: 2000, max: '' },
  ]
}

function _bandLabel(band) {
  if (band.min !== '' && band.min != null && band.max !== '' && band.max != null) return `${band.min} - ${band.max}`
  if (band.min !== '' && band.min != null) return `>= ${band.min}`
  if (band.max !== '' && band.max != null) return `< ${band.max}`
  return 'Band'
}

function _syncBandStyles() {
  const cfg = { ...(form.value.group_config || {}) }
  const bands = Array.isArray(cfg.__grouping?.bands) ? cfg.__grouping.bands : []
  const keys = bands.map((band) => String(band.key))
  for (let i = 0; i < bands.length; i++) {
    const band = bands[i]
    const key = String(band.key)
    cfg[key] = {
      ...(cfg[key] || {}),
      color: cfg[key]?.color || GROUP_PALETTE[i % GROUP_PALETTE.length],
      icon: cfg[key]?.icon || '',
      label: _bandLabel(band),
    }
  }
  for (const key of Object.keys(cfg)) {
    if (!key.startsWith('__') && groupMode.value === 'bands' && !keys.includes(key)) delete cfg[key]
  }
  form.value.group_config = cfg
  groupValues.value = keys
}

function setGroupMode(mode) {
  if (!form.value.group_by_field) return
  if (mode === 'bands') {
    const cfg = { ...(form.value.group_config || {}) }
    const existing = cfg.__grouping && Array.isArray(cfg.__grouping.bands)
      ? cfg.__grouping.bands
      : []
    cfg.__grouping = { mode: 'bands', kind: groupBandKind.value || 'number', bands: existing.length ? existing : _defaultBands() }
    form.value.group_config = cfg
    _syncBandStyles()
  } else {
    const cfg = { ...(form.value.group_config || {}) }
    delete cfg.__grouping
    form.value.group_config = cfg
    loadExactGroupValues()
  }
}

function addBand() {
  const cfg = { ...(form.value.group_config || {}) }
  const bands = [...(cfg.__grouping?.bands || [])]
  const previous = bands[bands.length - 1]
  bands.push({ key: _bandKey(), min: previous?.max ?? '', max: '' })
  cfg.__grouping = { mode: 'bands', kind: groupBandKind.value || 'number', bands }
  form.value.group_config = cfg
  _syncBandStyles()
}

function _sameBandEdge(a, b) {
  return String(a ?? '') === String(b ?? '')
}

function updateBand(index, patch) {
  const cfg = { ...(form.value.group_config || {}) }
  const bands = [...(cfg.__grouping?.bands || [])]
  if (!bands[index]) return
  const current = bands[index]
  if (Object.prototype.hasOwnProperty.call(patch, 'max') && bands[index + 1] && _sameBandEdge(bands[index + 1].min, current.max)) {
    bands[index + 1] = { ...bands[index + 1], min: patch.max }
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'min') && bands[index - 1] && _sameBandEdge(bands[index - 1].max, current.min)) {
    bands[index - 1] = { ...bands[index - 1], max: patch.min }
  }
  bands[index] = { ...bands[index], ...patch }
  cfg.__grouping = { mode: 'bands', kind: groupBandKind.value || 'number', bands }
  form.value.group_config = cfg
  _syncBandStyles()
}

function removeBand(index) {
  const cfg = { ...(form.value.group_config || {}) }
  const bands = [...(cfg.__grouping?.bands || [])]
  const [removed] = bands.splice(index, 1)
  if (removed?.key) delete cfg[String(removed.key)]
  cfg.__grouping = { mode: 'bands', kind: groupBandKind.value || 'number', bands }
  form.value.group_config = cfg
  _syncBandStyles()
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

function insertPopupSnippet(snippet) {
  if (!snippet) return
  const current = String(form.value.popup_template || '')
  const spacer = current && !current.endsWith('\n') ? '\n' : ''
  form.value.popup_template = `${current}${spacer}${snippet}`
}

async function previewPopupTemplate() {
  popupPreview.value = null
  popupPreviewError.value = ''
  const template = String(form.value.popup_template || '').trim()
  if (!template) {
    popupPreviewError.value = 'Add a popup template before previewing.'
    return
  }
  if (!form.value.name) {
    popupPreviewError.value = 'Save the layer once before previewing this template.'
    return
  }
  popupPreviewLoading.value = true
  try {
    popupPreview.value = await call('expedition.api.layer.preview_popup_template', {
      layer: form.value.name,
      popup_template: form.value.popup_template,
    })
  } catch (e) {
    popupPreviewError.value = e.message || String(e)
  } finally {
    popupPreviewLoading.value = false
  }
}

function _parseJsonArray(raw) {
  if (!raw) return []
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function _writeJsonArray(field, rows) {
  form.value[field] = rows && rows.length ? JSON.stringify(rows) : ''
}

function _metricKeyFromLabel(text) {
  return String(text || '')
    .trim()
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/^([^A-Za-z_])/, '_$1')
    .replace(/_+/g, '_')
    .replace(/^_+$/, '')
    .slice(0, 48)
}

function addLocationField(fieldname) {
  if (!fieldname) return
  const rows = [...locationFieldRows.value]
  if (!rows.includes(fieldname)) rows.push(fieldname)
  _writeJsonArray('location_fields_json', rows)
  fieldPickerOpen.value = ''
}

function removeLocationField(index) {
  const rows = [...locationFieldRows.value]
  rows.splice(index, 1)
  _writeJsonArray('location_fields_json', rows)
}

function addLinkedMetric() {
  const rows = [...linkedMetricRows.value]
  rows.push({
    key: `metric_${rows.length + 1}`,
    label: `Metric ${rows.length + 1}`,
    source_doctype: '',
    link_field: '',
    dynamic_link_doctype_field: '',
    aggregate: 'sum',
    field: '',
    filters: [],
  })
  _writeJsonArray('linked_metrics_json', rows)
}

function addMoneyMetricSuggestion(suggestion) {
  if (!suggestion?.key) return
  const rows = [...linkedMetricRows.value]
  if (rows.some((row) => row.key === suggestion.key)) return
  rows.push({
    key: suggestion.key,
    label: suggestion.label || suggestion.key,
    source_doctype: suggestion.source_doctype || '',
    link_field: suggestion.link_field || '',
    dynamic_link_doctype_field: suggestion.dynamic_link_doctype_field || '',
    aggregate: suggestion.aggregate || 'sum',
    field: suggestion.aggregate === 'count' ? '' : (suggestion.field || ''),
    filters: Array.isArray(suggestion.filters) ? suggestion.filters : [],
  })
  _writeJsonArray('linked_metrics_json', rows)
}

function addAllMoneyMetricSuggestions() {
  visibleMoneyMetricSuggestions.value.forEach((suggestion) => {
    addMoneyMetricSuggestion(suggestion)
  })
}

async function loadMetricFields(metric) {
  const dt = metricDoctype(metric)
  if (!dt || metricFieldCache.value[dt] || metricFieldLoading.value[dt]) return
  metricFieldLoading.value = { ...metricFieldLoading.value, [dt]: true }
  metricFieldError.value = { ...metricFieldError.value, [dt]: '' }
  try {
    const fields = await layerStore.getSourceFields(dt)
    metricFieldCache.value = { ...metricFieldCache.value, [dt]: Array.isArray(fields) ? fields : [] }
  } catch (e) {
    metricFieldError.value = { ...metricFieldError.value, [dt]: e.message || String(e) }
  } finally {
    metricFieldLoading.value = { ...metricFieldLoading.value, [dt]: false }
  }
}

function metricFieldChoiceLabel(metric, fieldname, fallback) {
  if (metricFieldsBusy(metric)) return 'Loading fields...'
  const field = metricFields(metric).find((f) => f.fieldname === fieldname)
  if (field) return `${field.label || field.fieldname} (${field.fieldname})`
  return fieldname || fallback
}

function chooseMetricLinkField(index, metric, fieldname) {
  const field = metricFields(metric).find((f) => f.fieldname === fieldname)
  const patch = { link_field: fieldname || '' }
  if (field?.fieldtype === 'Dynamic Link') {
    patch.dynamic_link_doctype_field = field.options || metric.dynamic_link_doctype_field || ''
  } else if (field?.fieldtype === 'Link') {
    patch.dynamic_link_doctype_field = ''
  }
  updateLinkedMetric(index, patch)
  fieldPickerOpen.value = ''
}

function chooseMetricField(index, fieldname, patchKey) {
  updateLinkedMetric(index, { [patchKey]: fieldname || '' })
  fieldPickerOpen.value = ''
}

function updateLinkedMetric(index, patch) {
  const rows = [...linkedMetricRows.value]
  if (!rows[index]) return
  const next = { ...rows[index], ...patch }
  if (Object.prototype.hasOwnProperty.call(patch, 'label') && !rows[index].key) {
    next.key = _metricKeyFromLabel(patch.label)
  }
  if (next.aggregate === 'count') next.field = ''
  rows[index] = next
  _writeJsonArray('linked_metrics_json', rows)
}

function removeLinkedMetric(index) {
  const rows = [...linkedMetricRows.value]
  const [removed] = rows.splice(index, 1)
  _writeJsonArray('linked_metrics_json', rows)
  if (removed?.key) {
    const filters = linkedMetricFilterRows.value.filter((row) => row.metric !== removed.key)
    _writeJsonArray('linked_metric_filters_json', filters)
  }
}

function addLinkedMetricFilter() {
  const rows = [...linkedMetricFilterRows.value]
  rows.push({
    metric: linkedMetricKeys.value[0] || '',
    operator: '>',
    value: 0,
  })
  _writeJsonArray('linked_metric_filters_json', rows)
}

function updateLinkedMetricFilter(index, patch) {
  const rows = [...linkedMetricFilterRows.value]
  if (!rows[index]) return
  rows[index] = { ...rows[index], ...patch }
  _writeJsonArray('linked_metric_filters_json', rows)
}

function removeLinkedMetricFilter(index) {
  const rows = [...linkedMetricFilterRows.value]
  rows.splice(index, 1)
  _writeJsonArray('linked_metric_filters_json', rows)
}

function linkedMetricChoiceLabel(key) {
  const metric = linkedMetricByKey.value.get(key)
  if (!metric) return key || 'Choose metric'
  return metric.label || metric.key
}

function chooseLinkedMetricFilter(index, key) {
  updateLinkedMetricFilter(index, { metric: key || '' })
  fieldPickerOpen.value = ''
}

function moneySuggestionSourceLabel(suggestion) {
  const path = suggestion?.dynamic_link_doctype_field
    ? `${suggestion.dynamic_link_doctype_field}/${suggestion.link_field}`
    : suggestion?.link_field
  return `${suggestion?.source_doctype || 'DocType'} · ${path || 'link'} · ${suggestion?.aggregate || 'sum'} ${suggestion?.field || 'rows'}`
}

function normalizePinMinZoom(value) {
  const zoom = Number(value)
  if (!Number.isFinite(zoom) || zoom <= 0) return 0
  return Math.min(24, Math.max(0, zoom))
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
    form.value.territory_color = normalizeColorText(form.value.territory_color)
    if (form.value.territory_color && !isValidCssColor(form.value.territory_color)) {
      error.value = 'Enter a valid map color or leave it empty for automatic coloring.'
      saving.value = false
      return
    }
    if (form.value.heatmap && form.value.heatmap_mode === 'sum') {
      if (!form.value.heatmap_weight_field) {
        error.value = 'Choose a numeric metric field for weighted heatmap mode.'
        saving.value = false
        return
      }
      if (Number(form.value.heatmap_weight_min) === Number(form.value.heatmap_weight_max)) {
        error.value = 'Heatmap metric minimum and maximum must be different.'
        saving.value = false
        return
      }
    }
    form.value.pin_min_zoom = normalizePinMinZoom(form.value.pin_min_zoom)
    if (form.value.location_source === 'Linked DocType' && !form.value.location_link_field) {
      error.value = 'Choose the Link field that points to the location document.'
      saving.value = false
      return
    }
    if (form.value.location_source === 'Reverse Linked DocType') {
      if (!form.value.location_doctype) {
        error.value = 'Choose the location DocType.'
        saving.value = false
        return
      }
      if (!form.value.location_reverse_link_field) {
        error.value = 'Choose the field on the location DocType that links back to the source.'
        saving.value = false
        return
      }
    }
    if (form.value.location_source === 'Dynamic Link DocType' && !form.value.location_doctype) {
      error.value = 'Choose the location DocType.'
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
        location_source: form.value.location_source,
        location_link_field: form.value.location_link_field,
        location_doctype: form.value.location_doctype,
        location_reverse_link_field: form.value.location_reverse_link_field,
        location_fields_json: form.value.location_fields_json || '',
        latitude_field: form.value.latitude_field,
        longitude_field: form.value.longitude_field,
        label_field: form.value.label_field,
        color: form.value.color,
        size: form.value.size,
        pin_min_zoom: Number(form.value.pin_min_zoom) || 0,
        cluster: form.value.cluster,
        enabled: form.value.enabled,
        icon: form.value.icon,
        popup_template: form.value.popup_template || '',
        popup_fields_json: _serializePopupFields(form.value.popup_fields),
        linked_metrics_json: form.value.linked_metrics_json || '',
        linked_metric_filters_json: form.value.linked_metric_filters_json || '',
        group_by_field: form.value.group_by_field,
        group_config_json: _serializeGroupConfig(form.value.group_config),
        click_action: form.value.click_action,
        heatmap: form.value.heatmap,
        heatmap_mode: form.value.heatmap_mode,
        heatmap_weight_field: form.value.heatmap_mode === 'sum' ? form.value.heatmap_weight_field : '',
        heatmap_weight_min: form.value.heatmap_weight_min,
        heatmap_weight_max: form.value.heatmap_weight_max,
        heatmap_weight_scale: form.value.heatmap_weight_scale,
        heatmap_weight_stops_json: form.value.heatmap_weight_stops_json || '',
        heatmap_radius_min: form.value.heatmap_radius_min,
        heatmap_radius_max: form.value.heatmap_radius_max,
        heatmap_intensity_min: form.value.heatmap_intensity_min,
        heatmap_intensity_max: form.value.heatmap_intensity_max,
        heatmap_opacity: form.value.heatmap_opacity,
        heatmap_ramp_json: form.value.heatmap_ramp_json || '',
        territory_enabled: form.value.territory_enabled,
        territory_color: form.value.territory_color || '',
        territory_opacity: form.value.territory_opacity,
        territory_padding_meters: form.value.territory_padding_meters,
        radius_enabled: form.value.radius_enabled,
        radius_field: form.value.radius_field,
        radius_meters: form.value.radius_meters,
        radius_opacity: form.value.radius_opacity,
        filter_json: serializeFilterRows(form.value.filter_rows),
      }
      if (asMaster.value) {
        const dto = await layerStore.addMaster({ ...basePayload })
        void dto
      } else {
        if (!mapStore.activeMap?.map?.name) {
          error.value = 'No active map — open a map first.'
          saving.value = false
          return
        }
        await layerStore.addLayer({
          ...basePayload,
          map_name: mapStore.activeMap.map.name,
        })
      }
    } else {
      const editFields = {
        title: form.value.title,
        color: form.value.color,
        size: form.value.size,
        pin_min_zoom: Number(form.value.pin_min_zoom) || 0,
        cluster: form.value.cluster,
        enabled: form.value.enabled,
        icon: form.value.icon,
        location_source: form.value.location_source,
        location_link_field: form.value.location_link_field,
        location_doctype: form.value.location_doctype,
        location_reverse_link_field: form.value.location_reverse_link_field,
        location_fields_json: form.value.location_fields_json || '',
        label_field: form.value.label_field,
        filter_json: serializeFilterRows(form.value.filter_rows),
        popup_template: form.value.popup_template || '',
        popup_fields_json: _serializePopupFields(form.value.popup_fields),
        linked_metrics_json: form.value.linked_metrics_json || '',
        linked_metric_filters_json: form.value.linked_metric_filters_json || '',
        group_by_field: form.value.group_by_field,
        group_config_json: _serializeGroupConfig(form.value.group_config),
        click_action: form.value.click_action,
        heatmap: form.value.heatmap,
        heatmap_mode: form.value.heatmap_mode,
        heatmap_weight_field: form.value.heatmap_mode === 'sum' ? form.value.heatmap_weight_field : '',
        heatmap_weight_min: form.value.heatmap_weight_min,
        heatmap_weight_max: form.value.heatmap_weight_max,
        heatmap_weight_scale: form.value.heatmap_weight_scale,
        heatmap_weight_stops_json: form.value.heatmap_weight_stops_json || '',
        heatmap_radius_min: form.value.heatmap_radius_min,
        heatmap_radius_max: form.value.heatmap_radius_max,
        heatmap_intensity_min: form.value.heatmap_intensity_min,
        heatmap_intensity_max: form.value.heatmap_intensity_max,
        heatmap_opacity: form.value.heatmap_opacity,
        heatmap_ramp_json: form.value.heatmap_ramp_json || '',
        territory_enabled: form.value.territory_enabled,
        territory_color: form.value.territory_color || '',
        territory_opacity: form.value.territory_opacity,
        territory_padding_meters: form.value.territory_padding_meters,
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
            {{ isCreate ? (asMaster ? 'New reusable layer' : 'Add layer') : (isMaster ? 'Edit reusable layer' : 'Edit layer') }}
          </h3>
          <p class="le__subtitle">
            {{ isCreate
              ? (asMaster ? 'Save a reusable layer template that can be attached to maps.' : 'Add a layer to the current map.')
              : (isMaster ? 'Map layers created from this template are independent copies.' : 'Style, label, filter — all live.') }}
          </p>
        </div>
        <button class="le__close" type="button" @click="close" aria-label="Close">×</button>
      </header>

      <div class="le__body">
        <!-- Save-as-master toggle (create mode only) -->
        <button
          v-if="isCreate"
          type="button"
          class="le__toggle-row le__toggle-row--master"
          :class="{ 'le__toggle-row--on': asMaster }"
          :aria-pressed="asMaster ? 'true' : 'false'"
          @click="asMaster = !asMaster"
        >
          <span class="le__toggle-dot" aria-hidden="true" />
          <span>Save as reusable layer template</span>
        </button>

        <!-- Master banner (edit master only) -->
        <div v-else-if="isMaster" class="le__master-banner">
          This is a reusable layer template. Map layers created from it are independent copies.
        </div>

        <!-- Title -->
        <label class="le__field">
          <span class="le__label">Title</span>
          <input v-model="form.title" class="le__input" type="text" placeholder="e.g. Customers — APAC" />
        </label>

        <!-- Source DocType (create only) -->
        <label v-if="isCreate" class="le__field le__link-field">
          <span class="le__label">Source DocType</span>
          <input
            v-model="form.source_doctype"
            class="le__input"
            type="text"
            placeholder="Select a DocType…"
            autocomplete="off"
            @focus="sourcePickerOpen = true"
            @input="sourcePickerOpen = true"
            @keydown.enter.prevent="filteredSourceDts[0] && chooseSourceDoctype(filteredSourceDts[0].name)"
            @blur="onSourceChange"
          />
          <div v-if="sourcePickerOpen" class="le__option-pop">
            <button
              v-for="dt in filteredSourceDts"
              :key="dt.name"
              type="button"
              class="le__option"
              @mousedown.prevent="chooseSourceDoctype(dt.name)"
            >
              <span>{{ dt.name }}</span>
              <small>{{ dt.module }}</small>
            </button>
            <p v-if="!filteredSourceDts.length" class="le__option-empty">No matching DocTypes.</p>
          </div>
        </label>

        <div class="le__field le__link-field">
          <span class="le__label">Location source</span>
          <UiSelect
            :model-value="form.location_source"
            :options="LOCATION_SOURCE_OPTIONS"
            value-key="v"
            label-key="label"
            meta-key="hint"
            placeholder="Direct fields"
            :selected-label="optionLabel(LOCATION_SOURCE_OPTIONS, form.location_source, 'Direct fields')"
            @select="option => chooseLocationSource(option.v)"
          />
        </div>

        <div v-if="form.location_source === 'Linked DocType'" class="le__field le__link-field">
          <span class="le__label">Location link field</span>
          <UiSelect
            :model-value="form.location_link_field"
            :options="linkFields"
            value-key="fieldname"
            label-key="label"
            meta-key="fieldname"
            :disabled="!form.source_doctype || sourceFieldsLoading"
            :placeholder="sourceFieldsLoading ? 'Loading fields...' : 'Choose link field'"
            :selected-label="sourceFieldsLoading ? 'Loading fields...' : fieldChoiceLabel(form.location_link_field, linkFields)"
            empty-text="No Link fields found."
            @select="field => chooseLocationLinkField(field.fieldname)"
          />
        </div>

        <div v-if="form.location_source === 'Reverse Linked DocType' || form.location_source === 'Dynamic Link DocType'" class="le__field le__link-field">
          <span class="le__label">Location DocType</span>
          <input
            v-model="form.location_doctype"
            class="le__input"
            type="text"
            placeholder="DocType with latitude / longitude…"
            autocomplete="off"
            @focus="fieldPickerOpen = 'location_doctype'"
            @input="fieldPickerOpen = 'location_doctype'"
            @blur="onLocationDoctypeChange"
            @keydown.enter.prevent="filteredLocationDts[0] ? chooseLocationDoctype(filteredLocationDts[0].name) : onLocationDoctypeChange()"
          />
          <div v-if="fieldPickerOpen === 'location_doctype'" class="le__option-pop">
            <button
              v-for="dt in filteredLocationDts"
              :key="dt.name"
              type="button"
              class="le__option"
              :data-active="form.location_doctype === dt.name"
              @mousedown.prevent="chooseLocationDoctype(dt.name)"
            >
              <span>{{ dt.name }}</span>
              <small>{{ dt.module }}</small>
            </button>
            <p v-if="!filteredLocationDts.length" class="le__option-empty">No matching DocTypes.</p>
          </div>
        </div>

        <div v-if="form.location_source === 'Reverse Linked DocType'" class="le__field le__link-field">
          <span class="le__label">Reverse link field</span>
          <UiSelect
            :model-value="form.location_reverse_link_field"
            :options="reverseLinkFields"
            value-key="fieldname"
            label-key="label"
            meta-key="fieldname"
            :disabled="!form.location_doctype || locationFieldsLoading"
            :placeholder="locationFieldsLoading ? 'Loading fields...' : 'Choose reverse link'"
            :selected-label="locationFieldsLoading ? 'Loading fields...' : fieldChoiceLabel(form.location_reverse_link_field, reverseLinkFields)"
            empty-text="No Link fields point to the source DocType."
            @select="field => chooseReverseLinkField(field.fieldname)"
          />
        </div>

        <div v-if="isCreate" class="le__row">
          <div class="le__field le__field--half le__link-field">
            <span class="le__label">Lat field</span>
            <UiSelect
              :model-value="form.latitude_field"
              :options="activeCoordinateFields"
              value-key="fieldname"
              label-key="label"
              meta-key="fieldname"
              compact
              :disabled="!form.source_doctype || activeCoordinateLoading"
              :placeholder="activeCoordinateLoading ? 'Loading fields...' : 'Choose lat field'"
              :selected-label="activeCoordinateLoading ? 'Loading fields...' : fieldChoiceLabel(form.latitude_field, activeCoordinateFields)"
              empty-text="No Float fields found."
              @select="field => chooseLayerField('latitude_field', field.fieldname)"
            />
          </div>
          <div class="le__field le__field--half le__link-field">
            <span class="le__label">Lng field</span>
            <UiSelect
              :model-value="form.longitude_field"
              :options="activeCoordinateFields"
              value-key="fieldname"
              label-key="label"
              meta-key="fieldname"
              compact
              :disabled="!form.source_doctype || activeCoordinateLoading"
              :placeholder="activeCoordinateLoading ? 'Loading fields...' : 'Choose lng field'"
              :selected-label="activeCoordinateLoading ? 'Loading fields...' : fieldChoiceLabel(form.longitude_field, activeCoordinateFields)"
              empty-text="No Float fields found."
              @select="field => chooseLayerField('longitude_field', field.fieldname)"
            />
          </div>
        </div>

        <div class="le__field le__link-field">
          <span class="le__label">Location popup fields</span>
          <div class="le__chips le__chips--inline">
            <span v-for="(fieldname, index) in locationFieldRows" :key="fieldname" class="le__token">
              {{ fieldChoiceLabel(fieldname, form.location_source !== 'Direct Fields' ? locationFields : sourceFields) }}
              <button type="button" class="le__token-x" @click="removeLocationField(index)">×</button>
            </span>
            <button
              type="button"
              class="le__mini-btn"
              :disabled="!availableLocationExtraFields.length"
              @click="fieldPickerOpen = fieldPickerOpen === 'location_extra_fields' ? '' : 'location_extra_fields'"
            >
              Add field
            </button>
          </div>
          <div v-if="fieldPickerOpen === 'location_extra_fields'" class="le__option-pop le__option-pop--static">
            <button v-for="f in availableLocationExtraFields" :key="f.fieldname" type="button" class="le__option" @mousedown.prevent="addLocationField(f.fieldname)">
              <span>{{ f.label }}</span>
              <small>{{ f.fieldname }} · {{ f.fieldtype }}</small>
            </button>
            <p v-if="!availableLocationExtraFields.length" class="le__option-empty">No extra fields available.</p>
          </div>
        </div>

        <!-- Label field (uses source row's text) -->
        <div class="le__field le__link-field">
          <span class="le__label">Label field <span class="le__hint">(used as the pin popup title)</span></span>
          <UiSelect
            :model-value="form.label_field"
            :options="[{ fieldname: '', label: 'None', meta: 'Use default title' }, ...sourceFields]"
            value-key="fieldname"
            label-key="label"
            meta-key="fieldname"
            :disabled="!form.source_doctype || sourceFieldsLoading"
            :placeholder="sourceFieldsLoading ? 'Loading fields...' : 'Choose label field'"
            :selected-label="sourceFieldsLoading ? 'Loading fields...' : fieldChoiceLabel(form.label_field)"
            @select="field => chooseLayerField('label_field', field.fieldname)"
          />
        </div>

        <!-- Color + Size -->
        <div class="le__row">
          <div class="le__field le__field--half">
            <span class="le__label">Color</span>
            <UiColorInput
              v-model="form.color"
              :presets="COLOR_PRESETS"
              :invalid="colorInputInvalid"
              placeholder="#3B82F6 or rgba(59,130,246,0.8)"
              @blur="form.color = normalizeColorText(form.color)"
            />
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
        <div class="le__row">
          <label class="le__field le__field--half">
            <span class="le__label">Show pins from zoom</span>
            <UiNumberInput v-model="form.pin_min_zoom" min="0" max="24" step="0.5" compact />
          </label>
          <p class="le__field-note">Use 0 to keep pins visible while zoomed out.</p>
        </div>

        <div class="le__filter">
          <div class="le__filter-header">
            <button
              type="button"
              class="le__toggle-row le__toggle-row--inline"
              :class="{ 'le__toggle-row--on': !!form.territory_enabled }"
              :aria-pressed="form.territory_enabled ? 'true' : 'false'"
              @click="form.territory_enabled = form.territory_enabled ? 0 : 1"
            >
              <span class="le__toggle-dot" aria-hidden="true" />
              <span>Map coloring <span class="le__hint">(territory background)</span></span>
            </button>
          </div>
          <template v-if="form.territory_enabled">
            <div class="le__row">
              <label class="le__field le__field--half">
                <span class="le__label">Map color</span>
                <UiColorInput
                  v-model="form.territory_color"
                  :presets="COLOR_PRESETS"
                  compact
                  placeholder="Auto from pin color"
                  @blur="form.territory_color = normalizeColorText(form.territory_color)"
                />
              </label>
              <label class="le__field le__field--half">
                <span class="le__label">Spread meters</span>
                <UiNumberInput v-model="form.territory_padding_meters" min="100" max="50000" step="100" compact />
              </label>
            </div>
            <label class="le__field">
              <span class="le__label">Map color opacity</span>
              <UiNumberInput v-model="form.territory_opacity" min="0" max="1" step="0.05" compact />
            </label>
          </template>
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
              <div v-if="iconStore.canManageGlobal" class="le__field le__field--scope le__link-field">
                <UiSelect
                  v-model="uploadScope"
                  :options="UPLOAD_SCOPE_OPTIONS"
                  value-key="v"
                  label-key="label"
                  :searchable="false"
                  compact
                />
              </div>
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
        <button
          type="button"
          class="le__toggle-row"
          :class="{ 'le__toggle-row--on': !!form.cluster }"
          :aria-pressed="form.cluster ? 'true' : 'false'"
          @click="form.cluster = form.cluster ? 0 : 1"
        >
          <span class="le__toggle-dot" aria-hidden="true" />
          <span>Cluster pins at low zoom</span>
        </button>

        <!-- Enabled toggle -->
        <button
          type="button"
          class="le__toggle-row"
          :class="{ 'le__toggle-row--on': !!form.enabled }"
          :aria-pressed="form.enabled ? 'true' : 'false'"
          @click="form.enabled = form.enabled ? 0 : 1"
        >
          <span class="le__toggle-dot" aria-hidden="true" />
          <span>Visible on map</span>
        </button>

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
            <button type="button" class="le__btn le__btn--ghost le__btn--sm" @click="openAdvancedGrouping">
              Advanced
            </button>
          </div>
          <div v-if="isAdvancedGrouping" class="le__advanced-group-summary">
            <div>
              <strong>{{ advancedGroupingSummary }}</strong>
              <span>{{ advancedGroupCount }} custom override{{ advancedGroupCount === 1 ? '' : 's' }}</span>
            </div>
            <button type="button" class="le__btn le__btn--ghost le__btn--sm" @click="openAdvancedGrouping">Edit groups</button>
          </div>
          <UiSelect
            v-if="!isAdvancedGrouping"
            v-model="form.group_by_field"
            :options="[{ fieldname: '', label: 'None', meta: 'No grouping' }, ...groupFieldOptions]"
            value-key="fieldname"
            label-key="label"
            meta-key="fieldname"
            :selected-label="selectedGroupFieldLabel"
            @update:model-value="chooseGroupField"
          />

          <div v-if="!isAdvancedGrouping && form.group_by_field && groupBySupportsBands" class="le__group-mode">
            <button
              type="button"
              class="le__seg"
              :class="{ 'le__seg--active': groupMode === 'bands' }"
              @click="setGroupMode('bands')"
            >Bands</button>
            <button
              type="button"
              class="le__seg"
              :class="{ 'le__seg--active': groupMode === 'value' }"
              @click="setGroupMode('value')"
            >Exact values</button>
          </div>

          <div v-if="!isAdvancedGrouping && form.group_by_field && groupMode === 'bands'" class="le__group-list">
            <p class="le__group-note">Each band matches From inclusive and To exclusive. Leave either side blank for open-ended ranges.</p>
            <div v-for="(band, i) in groupBands" :key="band.key" class="le__group-row le__band-row">
              <details class="le__group-color-menu">
                <summary
                  class="le__group-color-trigger"
                  :style="{ background: form.group_config[String(band.key)]?.color || GROUP_PALETTE[i % GROUP_PALETTE.length] }"
                  :title="'Color for ' + (form.group_config[String(band.key)]?.label || band.label || band.key)"
                />
                <div class="le__group-color-popover">
                  <UiColorInput
                    :model-value="form.group_config[String(band.key)]?.color || GROUP_PALETTE[i % GROUP_PALETTE.length]"
                    :presets="GROUP_PALETTE"
                    compact
                    placeholder="#RRGGBB"
                    @update:model-value="value => setGroupColor(band.key, value)"
                  />
                </div>
              </details>
              <details class="le__group-color-menu">
                <summary
                  class="le__group-color-trigger le__group-color-trigger--territory"
                  :style="{ background: form.group_config[String(band.key)]?.territory_color || form.group_config[String(band.key)]?.color || GROUP_PALETTE[i % GROUP_PALETTE.length] }"
                  :title="'Map color for ' + (form.group_config[String(band.key)]?.label || band.label || band.key)"
                />
                <div class="le__group-color-popover">
                  <button
                    type="button"
                    class="le__option le__option--compact"
                    @click="setGroupTerritoryColor(band.key, '')"
                  >
                    <span>Auto from pin color</span>
                  </button>
                  <UiColorInput
                    :model-value="form.group_config[String(band.key)]?.territory_color || ''"
                    :presets="GROUP_PALETTE"
                    compact
                    placeholder="Auto"
                    @update:model-value="value => setGroupTerritoryColor(band.key, value)"
                  />
                </div>
              </details>
              <UiNumberInput
                v-if="groupBandKind === 'number'"
                placeholder="From"
                :model-value="band.min"
                compact
                @update:model-value="value => updateBand(i, { min: value })"
              />
              <button
                v-else
                type="button"
                class="le__date-trigger"
                @click="openBandEdgePicker(i, 'min')"
              >{{ formatBandEdgeValue(band.min, 'From') }}</button>
              <UiNumberInput
                v-if="groupBandKind === 'number'"
                placeholder="To"
                :model-value="band.max"
                compact
                @update:model-value="value => updateBand(i, { max: value })"
              />
              <button
                v-else
                type="button"
                class="le__date-trigger"
                @click="openBandEdgePicker(i, 'max')"
              >{{ formatBandEdgeValue(band.max, 'To') }}</button>
              <span class="le__band-label">{{ form.group_config[String(band.key)]?.label || _bandLabel(band) }}</span>
              <details class="le__group-icon-menu" @toggle="onGroupIconMenuToggle">
                <summary class="le__group-icon-trigger" :title="groupIconMode(band.key) === 'inherit' ? 'Layer icon' : groupIconMode(band.key) === 'none' ? 'No icon' : iconLabel(groupIcon(band.key))">
                  <template v-if="groupIconMode(band.key) === 'inherit' && layerIconOption()">
                    <svg v-if="isBuiltinIcon(layerIconOption())" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                      <path :d="ICON_PATHS[layerIconOption().key] || ''" fill="currentColor" />
                    </svg>
                    <span v-else-if="layerIconOption().icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="layerIconOption().svg_content" />
                    <img v-else class="le__icon-preview le__icon-preview--image" :src="layerIconOption().image_data_url" alt="" />
                  </template>
                  <template v-else-if="groupIcon(band.key)">
                    <svg v-if="isBuiltinIcon(groupIcon(band.key))" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                      <path :d="ICON_PATHS[groupIcon(band.key).key] || ''" fill="currentColor" />
                    </svg>
                    <span v-else-if="groupIcon(band.key).icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="groupIcon(band.key).svg_content" />
                    <img v-else class="le__icon-preview le__icon-preview--image" :src="groupIcon(band.key).image_data_url" alt="" />
                  </template>
                  <span v-else class="le__icon-preview le__icon-preview--none">∅</span>
                </summary>
                <div class="le__group-icons" role="radiogroup" :aria-label="'Icon for ' + _bandLabel(band)">
                  <button
                    type="button"
                    class="le__group-icon"
                    :data-active="groupIconMode(band.key) === 'inherit'"
                    @click="e => setGroupIcon(band.key, '', e)"
                    title="Layer icon"
                    aria-label="Layer icon"
                  >
                    <template v-if="layerIconOption()">
                      <svg v-if="isBuiltinIcon(layerIconOption())" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                        <path :d="ICON_PATHS[layerIconOption().key] || ''" fill="currentColor" />
                      </svg>
                      <span v-else-if="layerIconOption().icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="layerIconOption().svg_content" />
                      <img v-else class="le__icon-preview le__icon-preview--image" :src="layerIconOption().image_data_url" alt="" />
                    </template>
                    <span v-else class="le__icon-preview le__icon-preview--none">∅</span>
                  </button>
                  <button
                    type="button"
                    class="le__group-icon"
                    :data-active="groupIconMode(band.key) === 'none'"
                    @click="e => setGroupIcon(band.key, '__none', e)"
                    title="No icon"
                    aria-label="No icon"
                  >
                    <span class="le__icon-preview le__icon-preview--none">∅</span>
                  </button>
                  <button
                    v-for="icon in allIconOptions"
                    :key="icon.key"
                    type="button"
                    class="le__group-icon"
                    :data-active="form.group_config[String(band.key)]?.icon === icon.key"
                    @click="e => setGroupIcon(band.key, icon.key, e)"
                    :title="iconLabel(icon)"
                    :aria-label="iconLabel(icon)"
                  >
                    <svg v-if="isBuiltinIcon(icon)" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                      <path :d="ICON_PATHS[icon.key] || ''" fill="currentColor" />
                    </svg>
                    <span v-else-if="icon.icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="icon.svg_content" />
                    <img v-else class="le__icon-preview le__icon-preview--image" :src="icon.image_data_url" alt="" />
                  </button>
                </div>
              </details>
              <button type="button" class="le__btn le__btn--icon" @click="removeBand(i)" aria-label="Remove band">×</button>
            </div>
            <div v-if="bandEdgePickerOpen" class="le__band-date-menu">
              <div class="le__date-head">
                <button type="button" class="le__date-nav" @click="shiftBandEdgeMonth(-1)" aria-label="Previous month">‹</button>
                <span>{{ groupBandKind === 'time' ? bandEdgePickerTitle : bandEdgeMonthLabel }}</span>
                <button v-if="groupBandKind !== 'time'" type="button" class="le__date-nav" @click="shiftBandEdgeMonth(1)" aria-label="Next month">›</button>
              </div>
              <template v-if="groupBandKind !== 'time'">
                <div class="le__date-week">
                  <span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span>
                </div>
                <div class="le__date-grid">
                  <span v-for="(day, idx) in bandEdgeCalendarDays" :key="idx" class="le__date-empty">
                    <button
                      v-if="day"
                      type="button"
                      class="le__date-day"
                      :data-active="_bandEdgeDate(_bandEdgeValue(bandEdgePicker.bandIndex, bandEdgePicker.edge)) === day.value"
                      @click="selectBandDate(day.value)"
                    >{{ day.day }}</button>
                  </span>
                </div>
              </template>
              <label v-if="groupBandKind !== 'date'" class="le__time-row">
                <span>Time</span>
                <input
                  class="le__input le__input--xs"
                  type="text"
                  inputmode="numeric"
                  placeholder="HH:MM"
                  :value="_bandEdgeTime(_bandEdgeValue(bandEdgePicker.bandIndex, bandEdgePicker.edge))"
                  @input="e => updateBandEdgeTime(e.target.value)"
                />
              </label>
              <div class="le__date-actions">
                <button type="button" class="le__btn le__btn--ghost" @click="updateBand(bandEdgePicker.bandIndex, { [bandEdgePicker.edge]: '' }); closeBandEdgePicker()">Clear</button>
                <button type="button" class="le__btn le__btn--ghost" @click="closeBandEdgePicker">Done</button>
              </div>
            </div>
            <button type="button" class="le__btn le__btn--ghost" @click="addBand">Add band</button>
          </div>

          <!-- Group-specific overrides -->
          <div v-if="!isAdvancedGrouping && form.group_by_field && groupMode === 'value' && groupValues.length" class="le__group-list">
            <p class="le__group-note">Colors auto-assigned. Icons use the layer icon unless overridden.</p>
            <div v-for="val in groupValues" :key="String(val)" class="le__group-row">
              <div class="le__group-val">
                <details class="le__group-color-menu">
                  <summary
                    class="le__group-color-trigger"
                    :style="{ background: form.group_config[String(val)]?.color || GROUP_PALETTE[0] }"
                    :title="'Color for ' + val"
                  />
                  <div class="le__group-color-popover">
                    <UiColorInput
                      :model-value="form.group_config[String(val)]?.color || GROUP_PALETTE[0]"
                      :presets="GROUP_PALETTE"
                      compact
                      placeholder="#RRGGBB"
                      @update:model-value="value => setGroupColor(val, value)"
                    />
                  </div>
                </details>
                <details class="le__group-color-menu">
                  <summary
                    class="le__group-color-trigger le__group-color-trigger--territory"
                    :style="{ background: form.group_config[String(val)]?.territory_color || form.group_config[String(val)]?.color || GROUP_PALETTE[0] }"
                    :title="'Map color for ' + val"
                  />
                  <div class="le__group-color-popover">
                    <button
                      type="button"
                      class="le__option le__option--compact"
                      @click="setGroupTerritoryColor(val, '')"
                    >
                      <span>Auto from pin color</span>
                    </button>
                    <UiColorInput
                      :model-value="form.group_config[String(val)]?.territory_color || ''"
                      :presets="GROUP_PALETTE"
                      compact
                      placeholder="Auto"
                      @update:model-value="value => setGroupTerritoryColor(val, value)"
                    />
                  </div>
                </details>
                <span class="le__group-val-text">{{ form.group_config[String(val)]?.label || val }}</span>
              </div>
              <div class="le__group-override">
                <details class="le__group-icon-menu" @toggle="onGroupIconMenuToggle">
                <summary class="le__group-icon-trigger" :title="groupIconMode(val) === 'inherit' ? 'Layer icon' : groupIconMode(val) === 'none' ? 'No icon' : iconLabel(groupIcon(val))">
                    <template v-if="groupIconMode(val) === 'inherit' && layerIconOption()">
                      <svg v-if="isBuiltinIcon(layerIconOption())" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                        <path :d="ICON_PATHS[layerIconOption().key] || ''" fill="currentColor" />
                      </svg>
                      <span v-else-if="layerIconOption().icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="layerIconOption().svg_content" />
                      <img v-else class="le__icon-preview le__icon-preview--image" :src="layerIconOption().image_data_url" alt="" />
                    </template>
                    <template v-else-if="groupIcon(val)">
                      <svg v-if="isBuiltinIcon(groupIcon(val))" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                        <path :d="ICON_PATHS[groupIcon(val).key] || ''" fill="currentColor" />
                      </svg>
                      <span v-else-if="groupIcon(val).icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="groupIcon(val).svg_content" />
                      <img v-else class="le__icon-preview le__icon-preview--image" :src="groupIcon(val).image_data_url" alt="" />
                    </template>
                    <span v-else class="le__icon-preview le__icon-preview--none">∅</span>
                  </summary>
                  <div class="le__group-icons" role="radiogroup" :aria-label="'Icon for ' + val">
                    <button
                      type="button"
                      class="le__group-icon"
                      :data-active="groupIconMode(val) === 'inherit'"
                      @click="e => setGroupIcon(val, '', e)"
                      title="Layer icon"
                      aria-label="Layer icon"
                    >
                      <template v-if="layerIconOption()">
                        <svg v-if="isBuiltinIcon(layerIconOption())" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                          <path :d="ICON_PATHS[layerIconOption().key] || ''" fill="currentColor" />
                        </svg>
                        <span v-else-if="layerIconOption().icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="layerIconOption().svg_content" />
                        <img v-else class="le__icon-preview le__icon-preview--image" :src="layerIconOption().image_data_url" alt="" />
                      </template>
                      <span v-else class="le__icon-preview le__icon-preview--none">∅</span>
                    </button>
                    <button
                      type="button"
                      class="le__group-icon"
                      :data-active="groupIconMode(val) === 'none'"
                      @click="e => setGroupIcon(val, '__none', e)"
                      title="No icon"
                      aria-label="No icon"
                    >
                      <span class="le__icon-preview le__icon-preview--none">∅</span>
                    </button>
                    <button
                      v-for="icon in allIconOptions"
                      :key="icon.key"
                      type="button"
                      class="le__group-icon"
                      :data-active="form.group_config[String(val)]?.icon === icon.key"
                      @click="e => setGroupIcon(val, icon.key, e)"
                      :title="iconLabel(icon)"
                      :aria-label="iconLabel(icon)"
                    >
                      <svg v-if="isBuiltinIcon(icon)" class="le__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                        <path :d="ICON_PATHS[icon.key] || ''" fill="currentColor" />
                      </svg>
                      <span v-else-if="icon.icon_format !== 'Image'" class="le__icon-preview le__icon-preview--custom" v-html="icon.svg_content" />
                      <img v-else class="le__icon-preview le__icon-preview--image" :src="icon.image_data_url" alt="" />
                    </button>
                  </div>
                </details>
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
          <p v-if="!form.group_by_field && !isAdvancedGrouping" class="le__filter-empty">No grouping — all pins use the same style.</p>
        </div>

        <!-- Popup Fields (default popup body) -->
        <div class="le__filter">
          <div class="le__filter-header">
            <span class="le__label">Popup fields <span class="le__hint">(default popup)</span></span>
            <div class="le__link-field le__add-field-menu">
              <UiSelect
                class="le__inline-select"
                model-value=""
                :options="popupFieldOptions"
                value-key="fieldname"
                label-key="label"
                meta-key="fieldname"
                placeholder="Add field"
                compact
                :disabled="!popupFieldOptions.length"
                empty-text="All fields already added."
                @select="field => addPopupField(field.fieldname)"
              />
            </div>
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
        <div class="le__field le__link-field">
          <span class="le__label">Click action</span>
          <UiSelect
            v-model="form.click_action"
            :options="CLICK_ACTION_OPTIONS"
            value-key="v"
            label-key="label"
            meta-key="hint"
            :selected-label="optionLabel(CLICK_ACTION_OPTIONS, form.click_action, 'Show popup')"
          />
        </div>

        <div class="le__filter">
          <div class="le__filter-header">
            <button
              type="button"
              class="le__toggle-row le__toggle-row--inline"
              :class="{ 'le__toggle-row--on': !!form.heatmap }"
              :aria-pressed="form.heatmap ? 'true' : 'false'"
              @click="form.heatmap = form.heatmap ? 0 : 1"
            >
              <span class="le__toggle-dot" aria-hidden="true" />
              <span>Heatmap analysis <span class="le__hint">(density or weighted metric)</span></span>
            </button>
          </div>
          <template v-if="form.heatmap">
            <div class="le__field">
              <span class="le__label">Question</span>
              <div class="le__seg-row">
                <button
                  v-for="option in HEATMAP_MODE_OPTIONS"
                  :key="option.v"
                  type="button"
                  class="le__seg le__seg--wide"
                  :class="{ 'le__seg--active': form.heatmap_mode === option.v }"
                  :title="option.hint"
                  @click="form.heatmap_mode = option.v"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>
            <template v-if="form.heatmap_mode === 'sum'">
              <div class="le__row">
                <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Metric field</span>
                  <UiSelect
                    v-model="form.heatmap_weight_field"
                    :options="[{ fieldname: '', label: 'Choose metric', meta: 'No weighted metric' }, ...numericFields]"
                    value-key="fieldname"
                    label-key="label"
                    meta-key="fieldname"
                    compact
                    :selected-label="form.heatmap_weight_field ? fieldChoiceLabel(form.heatmap_weight_field, numericFields) : 'Choose metric'"
                  />
                </div>
                <div class="le__field le__field--half">
                  <span class="le__label">Scale</span>
                  <div class="le__seg-row">
                    <button
                      v-for="option in HEATMAP_SCALE_OPTIONS"
                      :key="option.v"
                      type="button"
                      class="le__seg"
                      :class="{ 'le__seg--active': form.heatmap_weight_scale === option.v }"
                      @click="form.heatmap_weight_scale = option.v"
                    >
                      {{ option.label }}
                    </button>
                  </div>
                </div>
              </div>
              <div class="le__row">
                <label class="le__field le__field--half">
                  <span class="le__label">Metric min</span>
                  <UiNumberInput v-model="form.heatmap_weight_min" compact />
                </label>
                <label class="le__field le__field--half">
                  <span class="le__label">Metric max</span>
                  <UiNumberInput v-model="form.heatmap_weight_max" compact />
                </label>
              </div>
            </template>
            <div class="le__row">
              <label class="le__field le__field--half">
                <span class="le__label">Radius</span>
                <UiNumberInput v-model="form.heatmap_radius_max" min="8" max="80" step="1" compact />
              </label>
              <label class="le__field le__field--half">
                <span class="le__label">Intensity</span>
                <UiNumberInput v-model="form.heatmap_intensity_max" min="0.5" max="8" step="0.25" compact />
              </label>
            </div>
            <div class="le__row">
              <label class="le__field le__field--half">
                <span class="le__label">Opacity</span>
                <UiNumberInput v-model="form.heatmap_opacity" min="0.1" max="1" step="0.05" compact />
              </label>
              <div class="le__field le__field--half le__link-field">
                <span class="le__label">Ramp</span>
                <UiSelect
                  model-value=""
                  :options="[{ key: '', label: 'Layer color', meta: 'Use the current layer color' }, ...heatmapRampOptions]"
                  value-key="key"
                  label-key="label"
                  meta-key="meta"
                  placeholder="Choose ramp"
                  compact
                  @select="ramp => chooseHeatmapRamp(ramp.key)"
                />
              </div>
            </div>
          </template>
        </div>

        <!-- Halo radius controls (PR-8). When enabled, each pin gets a
             translucent circle around it representing a service area.
             radius_field picks a numeric field from the source to vary per
             feature; radius_meters is the fallback. -->
        <div class="le__filter">
          <div class="le__filter-header">
            <button
              type="button"
              class="le__toggle-row le__toggle-row--inline"
              :class="{ 'le__toggle-row--on': !!form.radius_enabled }"
              :aria-pressed="form.radius_enabled ? 'true' : 'false'"
              @click="form.radius_enabled = form.radius_enabled ? 0 : 1"
            >
              <span class="le__toggle-dot" aria-hidden="true" />
              <span>Radius halo <span class="le__hint">(service area around pin)</span></span>
            </button>
          </div>
          <template v-if="form.radius_enabled">
            <div class="le__row">
              <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Radius field (optional)</span>
                  <UiSelect
                    v-model="form.radius_field"
                    :options="[{ fieldname: '', label: 'Use default meters', meta: 'No per-feature radius field' }, ...numericFields]"
                    value-key="fieldname"
                    label-key="label"
                    meta-key="fieldname"
                    compact
                    :selected-label="form.radius_field ? fieldChoiceLabel(form.radius_field, numericFields) : 'Use default meters'"
                  />
                </div>
                <label class="le__field le__field--half">
                  <span class="le__label">Default meters</span>
                  <UiNumberInput v-model="form.radius_meters" min="100" max="50000" step="100" compact />
                </label>
            </div>
            <label class="le__field">
              <span class="le__label">Halo opacity</span>
              <UiNumberInput v-model="form.radius_opacity" min="0" max="1" step="0.05" compact />
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
          <div v-if="popupTemplateSnippets.length" class="le__template-snippets">
            <button
              v-for="snippet in popupTemplateSnippets"
              :key="`${snippet.label}:${snippet.value}`"
              type="button"
              class="le__snippet-chip"
              @click="insertPopupSnippet(snippet.value)"
            >
              {{ snippet.label }}
            </button>
          </div>
          <div class="le__popup-preview-actions">
            <button
              type="button"
              class="le__mini-btn"
              :disabled="popupPreviewLoading || !form.popup_template"
              @click="previewPopupTemplate"
            >
              {{ popupPreviewLoading ? 'Previewing' : 'Preview template' }}
            </button>
            <span v-if="popupPreview?.source_label" class="le__preview-source">
              {{ popupPreview.source_label }}
            </span>
          </div>
          <p v-if="popupPreviewError" class="le__preview-error">{{ popupPreviewError }}</p>
          <div v-if="popupPreview?.html" class="le__popup-preview" v-html="popupPreview.html" />
          <p class="le__hint le__hint--block">
            Rendered server-side per row. Fields of the source row are in scope (e.g.
            <code>&#123;&#123; doc.name &#125;&#125;</code>,
            <code>&#123;&#123; title &#125;&#125;</code>), plus
            <code>metrics</code>, <code>location</code>, and <code>layer</code>.
            Leave empty to fall back to the default popup.
          </p>
        </details>

        <details class="le__advanced" open>
          <summary>Field metrics <span class="le__hint">(linked records)</span></summary>
          <div v-if="form.source_doctype" class="le__metric-suggestions">
            <div class="le__suggestion-head">
              <span>Suggested field metrics</span>
              <button
                type="button"
                class="le__mini-btn"
                :disabled="!visibleMoneyMetricSuggestions.length"
                @click="addAllMoneyMetricSuggestions"
              >
                Add all
              </button>
            </div>
            <p v-if="moneyMetricSuggestionsLoading" class="le__suggestion-empty">Scanning linked DocTypes...</p>
            <p v-else-if="moneyMetricSuggestionsError" class="le__suggestion-empty">Could not load suggestions.</p>
            <div v-else-if="visibleMoneyMetricSuggestions.length" class="le__suggestion-grid">
              <button
                v-for="suggestion in visibleMoneyMetricSuggestions"
                :key="suggestion.key"
                type="button"
                class="le__suggestion-chip"
                @click="addMoneyMetricSuggestion(suggestion)"
              >
                <span>{{ suggestion.label }}</span>
                <small>{{ moneySuggestionSourceLabel(suggestion) }}</small>
              </button>
            </div>
            <p v-else class="le__suggestion-empty">No unused suggestions found.</p>
          </div>
          <div class="le__metric-list">
            <article v-for="(metric, index) in linkedMetricRows" :key="`${metric.key || index}:${index}`" class="le__metric-card">
              <div class="le__metric-head">
                <input
                  class="le__input le__input--sm"
                  :value="metric.label || ''"
                  placeholder="Outstanding amount"
                  @input="updateLinkedMetric(index, { label: $event.target.value })"
                />
                <button type="button" class="le__icon-btn" title="Remove metric" @click="removeLinkedMetric(index)">×</button>
              </div>
              <div class="le__row">
                <label class="le__field le__field--half">
                  <span class="le__label">Key</span>
                  <input class="le__input le__input--sm" :value="metric.key || ''" placeholder="outstanding" @input="updateLinkedMetric(index, { key: _metricKeyFromLabel($event.target.value) })" />
                </label>
                <label class="le__field le__field--half">
                  <span class="le__label">DocType</span>
                  <input class="le__input le__input--sm" :value="metric.source_doctype || ''" placeholder="Sales Invoice" @input="updateLinkedMetric(index, { source_doctype: $event.target.value })" />
                </label>
              </div>
              <div class="le__row">
                <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Link field</span>
                  <UiSelect
                    :model-value="metric.link_field"
                    :options="metricLinkFields(metric)"
                    value-key="fieldname"
                    label-key="label"
                    meta-key="fieldname"
                    compact
                    :disabled="!metric.source_doctype"
                    :placeholder="metricFieldsMessage(metric) || 'Choose link field'"
                    :selected-label="metricFieldChoiceLabel(metric, metric.link_field, 'Choose link field')"
                    :empty-text="metricFieldsMessage(metric) || 'No Link or Dynamic Link fields found.'"
                    @open="loadMetricFields(metric)"
                    @select="field => chooseMetricLinkField(index, metric, field.fieldname)"
                  />
                </div>
                <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Dynamic selector</span>
                  <UiSelect
                    :model-value="metric.dynamic_link_doctype_field"
                    :options="[{ fieldname: '', label: 'No selector', meta: 'Use this for normal Link fields' }, ...metricDynamicSelectorFields(metric)]"
                    value-key="fieldname"
                    label-key="label"
                    meta-key="fieldname"
                    compact
                    :disabled="!metric.source_doctype"
                    :placeholder="metricFieldsMessage(metric) || 'Optional selector'"
                    :selected-label="metricFieldChoiceLabel(metric, metric.dynamic_link_doctype_field, 'Optional selector')"
                    :empty-text="metricFieldsMessage(metric) || 'No selector-like fields found.'"
                    @open="loadMetricFields(metric)"
                    @select="field => chooseMetricField(index, field.fieldname, 'dynamic_link_doctype_field')"
                  />
                </div>
              </div>
              <div class="le__row">
                <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Value field</span>
                  <UiSelect
                    :model-value="metric.field"
                    :options="metricAmountFields(metric)"
                    value-key="fieldname"
                    label-key="label"
                    meta-key="fieldname"
                    compact
                    :disabled="metric.aggregate === 'count' || !metric.source_doctype"
                    :placeholder="metric.aggregate === 'count' ? 'Rows' : (metricFieldsMessage(metric) || 'Choose value field')"
                    :selected-label="metric.aggregate === 'count' ? 'Rows' : metricFieldChoiceLabel(metric, metric.field, 'Choose value field')"
                    :empty-text="metricFieldsMessage(metric) || 'No numeric value fields found.'"
                    @open="loadMetricFields(metric)"
                    @select="field => chooseMetricField(index, field.fieldname, 'field')"
                  />
                </div>
              </div>
              <div class="le__seg-row">
                <button
                  v-for="option in LINKED_METRIC_AGGREGATES"
                  :key="option.v"
                  type="button"
                  class="le__seg"
                  :class="{ 'le__seg--active': (metric.aggregate || 'count') === option.v }"
                  @click="updateLinkedMetric(index, { aggregate: option.v })"
                >
                  {{ option.label }}
                </button>
              </div>
            </article>
            <p v-if="!linkedMetricRows.length" class="le__empty-state">No field metrics configured.</p>
          </div>
          <button type="button" class="le__btn le__btn--ghost le__btn--wide" @click="addLinkedMetric">Add metric</button>
          <p class="le__hint le__hint--block">
            Use any DocType with a Link or Dynamic Link back to this source. Currency and numeric fields can drive value intelligence.
          </p>
        </details>

        <details class="le__advanced">
          <summary>Metric filters <span class="le__hint">(optional)</span></summary>
          <div class="le__metric-list">
            <article v-for="(filter, index) in linkedMetricFilterRows" :key="index" class="le__metric-card le__metric-card--compact">
              <div class="le__row">
                <div class="le__field le__field--half le__link-field">
                  <span class="le__label">Metric</span>
                  <UiSelect
                    :model-value="filter.metric"
                    :options="linkedMetricRows"
                    value-key="key"
                    label-key="label"
                    meta-key="key"
                    compact
                    :disabled="!linkedMetricRows.length"
                    :selected-label="linkedMetricChoiceLabel(filter.metric)"
                    empty-text="Add a field metric first."
                    @select="metric => chooseLinkedMetricFilter(index, metric.key)"
                  />
                </div>
                <label class="le__field le__field--half">
                  <span class="le__label">Value</span>
                  <input class="le__input le__input--sm" :value="filter.value ?? ''" placeholder="0" @input="updateLinkedMetricFilter(index, { value: $event.target.value })" />
                </label>
              </div>
              <div class="le__seg-row le__seg-row--wrap">
                <button
                  v-for="operator in METRIC_FILTER_OPERATORS"
                  :key="operator"
                  type="button"
                  class="le__seg"
                  :class="{ 'le__seg--active': (filter.operator || '=') === operator }"
                  @click="updateLinkedMetricFilter(index, { operator })"
                >
                  {{ operator }}
                </button>
                <button type="button" class="le__seg le__seg--danger" @click="removeLinkedMetricFilter(index)">Remove</button>
              </div>
            </article>
            <p v-if="!linkedMetricFilterRows.length" class="le__empty-state">No metric filters configured.</p>
          </div>
          <button type="button" class="le__btn le__btn--ghost le__btn--wide" :disabled="!linkedMetricRows.length" @click="addLinkedMetricFilter">Add filter</button>
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
    <AdvancedGroupingModal
      :open="advancedGroupingOpen"
      :model-value="form.group_config"
      :group-by-field="form.group_by_field"
      :source-doctype="form.source_doctype"
      :source-fields="groupFieldOptions"
      :linked-metrics-json="form.linked_metrics_json"
      :filter-json="serializeFilterRows(form.filter_rows)"
      :layer-color="safeLayerColor(form.color)"
      :layer-icon="form.icon"
      :color-palette="GROUP_PALETTE"
      @close="advancedGroupingOpen = false"
      @update:model-value="applyAdvancedGroupConfig"
      @update:group-by-field="form.group_by_field = $event"
    />
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
  overflow: hidden;
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
  flex: 1; overflow-x: hidden; overflow-y: auto; padding: 16px 20px;
  display: flex; flex-direction: column; gap: 14px;
}
.le__field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.le__field--half { flex: 1 1 0; min-width: 0; }
.le__row { display: flex; gap: 12px; min-width: 0; }
.le__field-note {
  flex: 1 1 0;
  align-self: end;
  margin: 0 0 3px;
  color: rgba(230, 232, 236, 0.5);
  font-size: 11px;
  line-height: 1.35;
}
.le__label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.6); font-weight: 500;
}
.le__hint { color: rgba(230, 232, 236, 0.4); text-transform: none; letter-spacing: 0; font-weight: 400; margin-left: 6px; }
.le__input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
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
.le__link-field { position: relative; }
.le__field-select {
  width: 100%;
  min-height: 36px;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  color: #E6E8EC;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.le__field-select span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.le__field-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.le__field-select--sm {
  min-height: 31px;
  padding: 6px 8px;
  font-size: 11px;
}
.le__field-select--xs {
  min-height: 28px;
  padding: 5px 8px;
  font-size: 11px;
}
.le__field--scope {
  width: 108px;
  flex: 0 0 auto;
}
.le__option-pop {
  position: absolute;
  z-index: 80;
  left: 0;
  right: 0;
  top: calc(100% + 6px);
  max-height: 238px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.38);
  box-sizing: border-box;
}
.le__option-pop--static {
  position: static;
  max-height: 190px;
  margin-top: 8px;
}
.le__option-pop--right {
  left: auto;
  min-width: 220px;
}
.le__option {
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
  padding: 7px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.9);
  text-align: left;
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.le__option span,
.le__option small {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.le__option small {
  color: rgba(230, 232, 236, 0.48);
  font-size: 10px;
}
.le__option:hover {
  background: rgba(59, 130, 246, 0.15);
}
.le__option[data-active="true"] {
  background: rgba(59, 130, 246, 0.2);
  color: #fff;
}
.le__option-empty {
  margin: 0;
  padding: 8px;
  color: rgba(230, 232, 236, 0.55);
  font-size: 11px;
}
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
.le__color-row--compact .le__color {
  width: 30px;
  height: 30px;
  border-radius: 7px;
}
.le__color {
  width: 36px; height: 36px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.28);
  flex: none;
}
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
.le__chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.le__token {
  max-width: 100%;
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 7px 4px 9px;
  border-radius: 7px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  color: rgba(230, 232, 236, 0.88);
  font-size: 11px;
  box-sizing: border-box;
}
.le__token-x,
.le__icon-btn {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(230, 232, 236, 0.7);
  cursor: pointer;
  font: inherit;
  line-height: 1;
}
.le__token-x:hover,
.le__icon-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #fff;
}
.le__mini-btn {
  min-height: 28px;
  border: 1px solid rgba(59, 130, 246, 0.34);
  border-radius: 7px;
  background: rgba(59, 130, 246, 0.12);
  color: rgba(230, 232, 236, 0.92);
  padding: 5px 9px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.le__mini-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.le__metric-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.le__metric-suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 9px;
  border: 1px solid rgba(59, 130, 246, 0.18);
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.06);
}
.le__suggestion-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: rgba(230, 232, 236, 0.86);
  font-size: 11px;
  font-weight: 650;
}
.le__suggestion-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 6px;
}
.le__suggestion-chip {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.2);
  color: rgba(230, 232, 236, 0.9);
  padding: 7px 8px;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.le__suggestion-chip:hover {
  border-color: rgba(59, 130, 246, 0.42);
  background: rgba(59, 130, 246, 0.12);
}
.le__suggestion-chip span,
.le__suggestion-chip small {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.le__suggestion-chip span {
  font-size: 11px;
  font-weight: 650;
}
.le__suggestion-chip small {
  color: rgba(230, 232, 236, 0.58);
  font-size: 10px;
}
.le__suggestion-empty {
  margin: 0;
  color: rgba(230, 232, 236, 0.58);
  font-size: 11px;
}
.le__metric-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.035);
}
.le__metric-card--compact {
  gap: 7px;
}
.le__metric-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.le__seg-row {
  display: flex;
  gap: 4px;
  min-width: 0;
}
.le__seg-row--wrap {
  flex-wrap: wrap;
}
.le__seg {
  min-height: 26px;
  flex: 1 1 0;
  min-width: 42px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.24);
  color: rgba(230, 232, 236, 0.68);
  padding: 4px 7px;
  font-size: 10px;
  font-family: inherit;
  cursor: pointer;
}
.le__seg--wide {
  min-width: 0;
}
.le__seg--active {
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.76);
  color: #fff;
}
.le__seg--danger {
  flex: 0 0 auto;
  border-color: rgba(239, 68, 68, 0.24);
  color: rgba(252, 165, 165, 0.92);
}
.le__empty-state {
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.11);
  color: rgba(230, 232, 236, 0.48);
  font-size: 11px;
}
.le__btn--wide {
  width: 100%;
  justify-content: center;
  margin-top: 8px;
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
.le__toggle-row--master {
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
.le__toggle-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  width: 100%;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(230, 232, 236, 0.84);
  padding: 8px 10px;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.le__toggle-row--inline {
  width: auto;
  min-height: 30px;
  padding: 6px 9px;
}
.le__toggle-row--on {
  border-color: rgba(59, 130, 246, 0.62);
  background: rgba(59, 130, 246, 0.14);
  color: #fff;
}
.le__toggle-dot {
  width: 9px;
  height: 9px;
  flex: none;
  border-radius: 50%;
  background: rgba(230, 232, 236, 0.3);
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.08);
}
.le__toggle-row--on .le__toggle-dot {
  background: #3B82F6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.18);
}
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
.le__template-snippets {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  min-width: 0;
  margin-top: 8px;
}
.le__snippet-chip {
  max-width: 100%;
  min-height: 25px;
  border: 1px solid rgba(59, 130, 246, 0.22);
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.08);
  color: rgba(191, 219, 254, 0.88);
  padding: 4px 8px;
  font: inherit;
  font-size: 10px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.le__snippet-chip:hover {
  border-color: rgba(59, 130, 246, 0.48);
  background: rgba(59, 130, 246, 0.16);
  color: #fff;
}
.le__popup-preview-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 8px;
}
.le__preview-source {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(230, 232, 236, 0.56);
  font-size: 11px;
}
.le__preview-error {
  margin: 8px 0 0;
  color: #FCA5A5;
  font-size: 11px;
  line-height: 1.4;
}
.le__popup-preview {
  max-height: 190px;
  overflow: auto;
  margin-top: 8px;
  padding: 10px;
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.24);
  color: rgba(230, 232, 236, 0.92);
  font-size: 12px;
  line-height: 1.4;
}
.le__popup-preview :deep(*) {
  max-width: 100%;
}
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
.le__btn--sm { padding: 4px 8px; font-size: 11px; border-radius: 6px; }
.le__advanced-group-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid rgba(59, 130, 246, 0.22);
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.08);
}
.le__advanced-group-summary > div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.le__advanced-group-summary strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #DBEAFE;
  font-size: 12px;
}
.le__advanced-group-summary span {
  color: rgba(226, 232, 240, 0.62);
  font-size: 11px;
}
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
.le__group-field-menu {
  position: relative;
}
.le__group-field-menu > summary {
  list-style: none;
}
.le__group-field-menu > summary::-webkit-details-marker {
  display: none;
}
.le__field-picker-trigger {
  min-height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0px 10px;
  border-radius: 7px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.24);
  color: #E6E8EC;
  font-size: 12px;
  cursor: pointer;
}
.le__chevron {
  color: rgba(230, 232, 236, 0.5);
}
.le__field-picker {
  position: absolute;
  z-index: 25;
  left: 0;
  right: 0;
  top: 40px;
  max-height: 238px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.38);
}
.le__field-option {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.86);
  cursor: pointer;
  text-align: left;
}
.le__field-option small {
  color: rgba(230, 232, 236, 0.48);
  font-size: 10px;
}
.le__field-option:hover,
.le__field-option[data-active="true"] {
  background: rgba(59, 130, 246, 0.14);
  border-color: rgba(59, 130, 246, 0.35);
}
.le__group-mode {
  display: inline-flex;
  gap: 2px;
  margin-top: 8px;
  padding: 2px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.16);
}
.le__seg {
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: rgba(230, 232, 236, 0.68);
  font-size: 11px;
  padding: 5px 8px;
  cursor: pointer;
}
.le__seg--active {
  background: rgba(59, 130, 246, 0.22);
  color: #EFF6FF;
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
.le__group-color-menu {
  position: relative;
  flex: none;
}
.le__group-color-menu > summary {
  list-style: none;
}
.le__group-color-menu > summary::-webkit-details-marker {
  display: none;
}
.le__group-color-trigger {
  width: 28px;
  height: 28px;
  display: block;
  border-radius: 6px;
  border: 2px solid rgba(255, 255, 255, 0.82);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
  cursor: pointer;
}
.le__group-color-trigger--territory {
  border-style: dashed;
  opacity: 0.82;
}
.le__option--compact {
  grid-column: 1 / -1;
  min-height: 28px;
  padding: 5px 7px;
}
.le__group-color-popover {
  position: absolute;
  z-index: 22;
  left: 0;
  top: 34px;
  width: 207px;
  display: grid;
  grid-template-columns: repeat(6, 20px) 40px;
  gap: 6px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.35);
}
.le__group-color-code {
  grid-column: 1 / 7;
  width: 100%;
  margin-top: 2px;
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
.le__group-icon-menu {
  position: relative;
}
.le__group-icon-menu > summary {
  list-style: none;
}
.le__group-icon-menu > summary::-webkit-details-marker {
  display: none;
}
.le__group-icon-trigger {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.22);
  color: #E6E8EC;
  cursor: pointer;
}
.le__group-icon-trigger:hover {
  background: rgba(255, 255, 255, 0.06);
}
.le__group-icons {
  position: absolute;
  z-index: 20;
  right: 0;
  top: 34px;
  width: 188px;
  max-height: 96px;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(6, 26px);
  gap: 3px;
  padding: 6px;
  border-radius: 7px;
  background: rgba(18, 20, 24, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.35);
}
.le__group-icon {
  width: 26px;
  height: 26px;
  flex: 0 0 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: #E6E8EC;
  cursor: pointer;
}
.le__group-icon:hover {
  background: rgba(255, 255, 255, 0.06);
}
.le__group-icon[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
  border-color: #3B82F6;
}
.le__group-icon .le__icon-preview {
  width: 15px;
  height: 15px;
}
.le__band-row {
  align-items: flex-start;
}
.le__band-number {
  width: 74px;
}
.le__band-label {
  min-width: 110px;
  flex: 1 1 110px;
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  color: rgba(230, 232, 236, 0.78);
  font-size: 12px;
}
.le__date-trigger {
  width: 112px;
  min-height: 28px;
  padding: 4px 7px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.22);
  color: rgba(230, 232, 236, 0.86);
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}
.le__date-trigger:hover {
  background: rgba(255, 255, 255, 0.06);
}
.le__band-date-menu {
  width: 236px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.38);
}
.le__date-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #E6E8EC;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
}
.le__date-nav {
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.06);
  color: #E6E8EC;
  cursor: pointer;
}
.le__date-week,
.le__date-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}
.le__date-week {
  margin-bottom: 4px;
  color: rgba(230, 232, 236, 0.42);
  font-size: 10px;
  text-align: center;
}
.le__date-empty {
  min-height: 28px;
}
.le__date-day {
  width: 100%;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: rgba(230, 232, 236, 0.84);
  cursor: pointer;
}
.le__date-day:hover,
.le__date-day[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
  border-color: rgba(59, 130, 246, 0.55);
}
.le__time-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  color: rgba(230, 232, 236, 0.68);
  font-size: 11px;
}
.le__date-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 8px;
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
