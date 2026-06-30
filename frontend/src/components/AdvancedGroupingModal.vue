<script setup>
import { computed, ref, watch } from 'vue'
import { call } from '../api/client.js'
import { useIconsStore } from '../state/icons.js'
import { ICON_PATHS } from '../api/icons.js'

const GROUP_PATH_SEPARATOR = '\x1f'

const props = defineProps({
  open: { type: Boolean, default: false },
  modelValue: { type: Object, default: () => ({}) },
  groupByField: { type: String, default: '' },
  sourceDoctype: { type: String, default: '' },
  sourceFields: { type: Array, default: () => [] },
  layerColor: { type: String, default: '#3B82F6' },
  layerIcon: { type: String, default: '' },
  colorPalette: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'update:modelValue', 'update:groupByField'])

const iconStore = useIconsStore()
const localConfig = ref({})
const levels = ref([])
const paths = ref([])
const loading = ref(false)
const error = ref('')
const search = ref('')
const expanded = ref(new Set())
const treeTruncated = ref(false)

const availableFields = computed(() =>
  props.sourceFields.filter((field) => !levels.value.some((level) => level.field === field.fieldname))
)
const fieldByName = computed(() => {
  const out = new Map()
  for (const field of props.sourceFields) out.set(field.fieldname, field)
  return out
})
const groups = computed(() =>
  localConfig.value && typeof localConfig.value.groups === 'object' && localConfig.value.groups
    ? localConfig.value.groups
    : {}
)
const allIconOptions = computed(() => iconStore.all)
const hasAdvancedGrouping = computed(() => levels.value.length > 0)
const levelSummary = computed(() =>
  levels.value
    .map((level) => fieldByName.value.get(level.field)?.label || level.field)
    .join(' > ')
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    iconStore.loadIcons().catch(() => {})
    hydrate()
    loadTree()
  },
  { immediate: true }
)

watch(
  () => levels.value.map((level) => level.field).join('|'),
  () => {
    if (props.open) loadTree()
  }
)

function clone(value) {
  try {
    return JSON.parse(JSON.stringify(value || {}))
  } catch {
    return {}
  }
}

function hydrate() {
  const incoming = clone(props.modelValue)
  const grouping = incoming.__grouping
  if (grouping?.version >= 2 && Array.isArray(grouping.levels)) {
    levels.value = grouping.levels
      .filter((level) => level?.field)
      .map(normalizeLevel)
    localConfig.value = {
      __grouping: { version: 2, levels: levels.value },
      groups: incoming.groups && typeof incoming.groups === 'object' ? incoming.groups : {},
    }
  } else if (props.groupByField) {
    const legacyGrouping = incoming.__grouping
    const legacyBands = legacyGrouping?.mode === 'bands' && Array.isArray(legacyGrouping.bands)
    const legacyBandLevel = legacyBands
      ? normalizeLevel({
          field: props.groupByField,
          mode: 'bands',
          kind: legacyGrouping.kind || bandKindForField(props.groupByField),
          bands: legacyGrouping.bands,
        })
      : null
    levels.value = [legacyBandLevel || { field: props.groupByField, mode: 'value' }]
    const legacyBandKeyMap = new Map()
    for (const band of legacyBandLevel?.bands || []) {
      legacyBandKeyMap.set(String(band.key), band.label || bandLabel(band))
    }
    const legacyGroups = {}
    for (const [key, value] of Object.entries(incoming || {})) {
      if (key.startsWith('__') || !value || typeof value !== 'object') continue
      legacyGroups[legacyBandKeyMap.get(String(key)) || key] = { ...value }
    }
    localConfig.value = {
      __grouping: { version: 2, levels: levels.value },
      groups: legacyGroups,
    }
  } else {
    levels.value = []
    localConfig.value = { __grouping: { version: 2, levels: [] }, groups: {} }
  }
  expanded.value = new Set()
  search.value = ''
  error.value = ''
}

async function loadTree() {
  if (!props.sourceDoctype || !levels.value.length) {
    paths.value = []
    treeTruncated.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const resp = await call('expedition.api.layer.list_group_tree', {
      source_doctype: props.sourceDoctype,
      fields: levels.value.map(serializeLevel),
    })
    paths.value = resp.paths || []
    treeTruncated.value = !!resp.truncated
    const nextExpanded = new Set(expanded.value)
    for (const path of paths.value.slice(0, 24)) {
      const values = path.values || []
      for (let i = 1; i < values.length; i++) nextExpanded.add(pathKey(values.slice(0, i)))
    }
    expanded.value = nextExpanded
  } catch (e) {
    error.value = e.message || String(e)
    paths.value = []
    treeTruncated.value = false
  } finally {
    loading.value = false
  }
}

function pathKey(values) {
  return values.map((value) => String(value || '(blank)')).join(GROUP_PATH_SEPARATOR)
}

function pathLabel(values) {
  return values.join(' / ')
}

function levelValueRank(depth, label) {
  const level = levels.value[depth]
  if (level?.mode !== 'bands') return null
  const bands = level.bands || []
  const idx = bands.findIndex((band) => (band.label || bandLabel(band)) === label)
  return idx >= 0 ? idx : bands.length + 1
}

function compareTreeRows(a, b) {
  const ar = levelValueRank(a.depth, a.label)
  const br = levelValueRank(b.depth, b.label)
  if (ar != null || br != null) return (ar ?? 9999) - (br ?? 9999)
  return String(a.label).localeCompare(String(b.label), undefined, { numeric: true, sensitivity: 'base' })
}

const allTreeRows = computed(() => {
  const nodes = new Map()
  const roots = []

  function ensureNode(values, depth) {
    const key = pathKey(values)
    if (!nodes.has(key)) {
      const node = {
        key,
        values,
        depth,
        label: values[depth],
        fullLabel: pathLabel(values),
        children: [],
        childKeys: new Set(),
      }
      nodes.set(key, node)
      if (depth === 0) roots.push(node)
    }
    return nodes.get(key)
  }

  for (const path of paths.value) {
    const values = path.values || []
    for (let i = 0; i < values.length; i++) {
      const prefix = values.slice(0, i + 1)
      const node = ensureNode(prefix, i)
      if (i > 0) {
        const parent = ensureNode(values.slice(0, i), i - 1)
        if (!parent.childKeys.has(node.key)) {
          parent.childKeys.add(node.key)
          parent.children.push(node)
        }
      }
    }
  }

  const rows = []
  function visit(node) {
    node.children.sort(compareTreeRows)
    rows.push({
      key: node.key,
      values: node.values,
      depth: node.depth,
      label: node.label,
      fullLabel: node.fullLabel,
      hasChildren: node.children.length > 0,
    })
    for (const child of node.children) visit(child)
  }

  roots.sort(compareTreeRows)
  for (const root of roots) visit(root)
  return rows
})

const treeRows = computed(() => {
  const query = search.value.trim().toLowerCase()
  return allTreeRows.value.filter((row) => {
    if (!query && !ancestorsExpanded(row)) return false
    if (!query) return true
    return row.fullLabel.toLowerCase().includes(query)
  })
})

function ancestorsExpanded(row) {
  if (row.depth === 0) return true
  for (let i = 1; i <= row.depth; i++) {
    if (!expanded.value.has(pathKey(row.values.slice(0, i)))) return false
  }
  return true
}

function toggleExpanded(row) {
  const next = new Set(expanded.value)
  if (next.has(row.key)) next.delete(row.key)
  else next.add(row.key)
  expanded.value = next
}

function expandAll() {
  expanded.value = new Set(allTreeRows.value.filter((row) => row.hasChildren).map((row) => row.key))
}

function collapseAll() {
  expanded.value = new Set()
}

function addLevel() {
  const field = availableFields.value[0]?.fieldname
  if (!field) return
  levels.value = [...levels.value, { field, mode: 'value' }]
}

function updateLevel(index, field) {
  if (!field) return
  const next = [...levels.value]
  next[index] = { field, mode: 'value' }
  levels.value = next
  pruneGroupsToLevels()
}

function bandKindForField(fieldname) {
  const ft = fieldByName.value.get(fieldname)?.fieldtype
  if (['Int', 'Float', 'Currency', 'Percent', 'Duration', 'Rating'].includes(ft)) return 'number'
  if (ft === 'Date') return 'date'
  if (ft === 'Datetime') return 'datetime'
  if (ft === 'Time') return 'time'
  return ''
}

function fieldSupportsBands(fieldname) {
  return !!bandKindForField(fieldname)
}

function normalizeLevel(level) {
  const mode = level?.mode === 'bands' ? 'bands' : 'value'
  const out = { field: level.field, mode }
  if (mode === 'bands') {
    out.kind = level.kind || bandKindForField(level.field) || 'number'
    out.bands = Array.isArray(level.bands)
      ? level.bands.map((band, idx) => ({
          key: String(band.key || `band_${idx + 1}`),
          min: band.min ?? '',
          max: band.max ?? '',
          label: band.label || bandLabel(band),
        }))
      : defaultBands(out.kind)
  }
  return out
}

function serializeLevel(level) {
  const out = { field: level.field, mode: level.mode || 'value' }
  if (out.mode === 'bands') {
    out.kind = level.kind || bandKindForField(level.field) || 'number'
    out.bands = Array.isArray(level.bands) ? level.bands : []
  }
  return out
}

function setLevelMode(index, mode) {
  const current = levels.value[index]
  if (!current) return
  const next = [...levels.value]
  next[index] = normalizeLevel({
    ...current,
    mode,
    kind: current.kind || bandKindForField(current.field) || 'number',
    bands: current.bands,
  })
  levels.value = next
  pruneGroupsToLevels()
}

function defaultBands(kind) {
  if (kind === 'time') {
    return [
      { key: 'band_1', min: '', max: '09:00', label: '< 09:00' },
      { key: 'band_2', min: '09:00', max: '17:00', label: '09:00 - 17:00' },
      { key: 'band_3', min: '17:00', max: '', label: '>= 17:00' },
    ]
  }
  return [
    { key: 'band_1', min: '', max: 1000, label: '< 1000' },
    { key: 'band_2', min: 1000, max: 2000, label: '1000 - 2000' },
    { key: 'band_3', min: 2000, max: '', label: '>= 2000' },
  ]
}

function bandLabel(band) {
  if (band.label) return band.label
  if (band.min !== '' && band.min != null && band.max !== '' && band.max != null) return `${band.min} - ${band.max}`
  if (band.min !== '' && band.min != null) return `>= ${band.min}`
  if (band.max !== '' && band.max != null) return `< ${band.max}`
  return 'Band'
}

function removeLevel(index) {
  levels.value = levels.value.filter((_, i) => i !== index)
  pruneGroupsToLevels()
}

function pruneGroupsToLevels() {
  const maxDepth = levels.value.length
  const nextGroups = {}
  for (const [key, value] of Object.entries(groups.value)) {
    const depth = key.split(GROUP_PATH_SEPARATOR).length
    if (depth <= maxDepth) nextGroups[key] = value
  }
  localConfig.value = {
    __grouping: { version: 2, levels: levels.value },
    groups: nextGroups,
  }
}

function groupOverride(key) {
  return groups.value[key] || {}
}

function effectiveStyle(row) {
  const style = { color: props.layerColor || '#3B82F6', icon: props.layerIcon || '' }
  for (let i = 0; i < row.values.length; i++) {
    const override = groupOverride(pathKey(row.values.slice(0, i + 1)))
    if (override.color) style.color = override.color
    if (Object.prototype.hasOwnProperty.call(override, 'icon')) style.icon = override.icon || ''
  }
  return style
}

function inheritanceLabel(row, prop) {
  const override = groupOverride(row.key)
  if (override[prop]) return 'Custom'
  if (prop === 'icon' && Object.prototype.hasOwnProperty.call(override, 'icon')) return 'Custom'
  if (row.depth === 0) return 'Inherited from layer'
  return `Inherited from ${row.values[row.values.length - 2]}`
}

function iconSelectValue(row) {
  const override = groupOverride(row.key)
  return Object.prototype.hasOwnProperty.call(override, 'icon')
    ? override.icon
    : effectiveStyle(row).icon
}

function setGroupProp(key, prop, value) {
  const nextGroups = { ...groups.value }
  const next = { ...(nextGroups[key] || {}) }
  if (value === '' && prop !== 'icon') delete next[prop]
  else next[prop] = value
  if (!next.color && !next.icon && !next.label) delete nextGroups[key]
  else nextGroups[key] = next
  localConfig.value = {
    __grouping: { version: 2, levels: levels.value },
    groups: nextGroups,
  }
}

function resetGroupProp(key, prop) {
  const nextGroups = { ...groups.value }
  const next = { ...(nextGroups[key] || {}) }
  delete next[prop]
  if (!next.color && !next.icon && !next.label) delete nextGroups[key]
  else nextGroups[key] = next
  localConfig.value = {
    __grouping: { version: 2, levels: levels.value },
    groups: nextGroups,
  }
}

function iconLabel(icon) {
  return icon?.title || icon?.key || ''
}

function isBuiltinIcon(icon) {
  return icon?.source === 'builtin'
}

function iconOption(iconKey) {
  return iconKey ? iconStore.byKey.get(iconKey) : null
}

function triggerIcon(row) {
  const iconKey = iconSelectValue(row)
  return iconKey === '__none' ? '' : iconKey
}

function closeRowMenu(event) {
  event?.target?.closest?.('details')?.removeAttribute('open')
}

function save() {
  if (!levels.value.length) {
    emit('update:modelValue', {})
    emit('update:groupByField', '')
    emit('close')
    return
  }
  const cleanGroups = {}
  for (const [key, value] of Object.entries(groups.value)) {
    if (!value || typeof value !== 'object') continue
    const item = {}
    if (value.color) item.color = value.color
    if (Object.prototype.hasOwnProperty.call(value, 'icon')) item.icon = value.icon || ''
    if (value.label) item.label = value.label
    if (Object.keys(item).length) cleanGroups[key] = item
  }
  emit('update:modelValue', {
    __grouping: { version: 2, levels: levels.value.map(serializeLevel) },
    groups: cleanGroups,
  })
  emit('update:groupByField', levels.value[0]?.field || '')
  emit('close')
}

function clearGrouping() {
  levels.value = []
  localConfig.value = { __grouping: { version: 2, levels: [] }, groups: {} }
  paths.value = []
}
</script>

<template>
  <div v-if="open" class="agm" role="dialog" aria-modal="true" aria-label="Advanced grouping">
    <div class="agm__backdrop" @click="emit('close')" />
    <section class="agm__panel">
      <header class="agm__header">
        <div>
          <h3>Advanced grouping</h3>
          <p>{{ levelSummary || 'Create nested groups with inherited color and icon styles.' }}</p>
        </div>
        <button type="button" class="agm__close" aria-label="Close" @click="emit('close')">×</button>
      </header>

      <div class="agm__body">
        <aside class="agm__levels">
          <div class="agm__section-head">
            <span>Group levels</span>
            <button type="button" class="agm__ghost" @click="addLevel" :disabled="!availableFields.length">Add level</button>
          </div>
          <div v-if="!levels.length" class="agm__empty">No grouping levels.</div>
          <div v-for="(level, index) in levels" :key="index" class="agm__level">
            <span class="agm__level-index">{{ index + 1 }}</span>
            <div class="agm__level-main">
              <select class="agm__input" :value="level.field" @change="updateLevel(index, $event.target.value)">
                <option :value="level.field">{{ fieldByName.get(level.field)?.label || level.field }}</option>
                <option v-for="field in availableFields" :key="field.fieldname" :value="field.fieldname">
                  {{ field.label }} · {{ field.fieldname }}
                </option>
              </select>
              <select
                v-if="fieldSupportsBands(level.field)"
                class="agm__input agm__input--mode"
                :value="level.mode"
                @change="setLevelMode(index, $event.target.value)"
              >
                <option value="value">Exact values</option>
                <option value="bands">Bands</option>
              </select>
            </div>
            <button type="button" class="agm__icon-btn" aria-label="Remove level" @click="removeLevel(index)">×</button>
          </div>
          <p v-if="levels.length > 3" class="agm__warn">More than 3 levels can make the legend and map harder to scan.</p>
          <button type="button" class="agm__danger" @click="clearGrouping" :disabled="!hasAdvancedGrouping">Clear grouping</button>
        </aside>

        <main class="agm__tree">
          <div class="agm__toolbar">
            <input v-model="search" class="agm__input" type="search" placeholder="Search groups" />
            <button type="button" class="agm__ghost" @click="expandAll">Expand</button>
            <button type="button" class="agm__ghost" @click="collapseAll">Collapse</button>
          </div>
          <p v-if="treeTruncated" class="agm__warn">Showing the first group combinations. Narrow the data with filters for a smaller tree.</p>
          <p v-if="error" class="agm__error">{{ error }}</p>
          <div v-if="loading" class="agm__empty">Loading groups…</div>
          <div v-else-if="!levels.length" class="agm__empty">Add a level to build the group tree.</div>
          <div v-else-if="!treeRows.length" class="agm__empty">No groups found for the selected levels.</div>
          <div v-else class="agm__rows">
            <div
              v-for="row in treeRows"
              :key="row.key"
              class="agm__row"
              :style="{ '--depth': row.depth }"
            >
              <button
                type="button"
                class="agm__twisty"
                :class="{ 'agm__twisty--leaf': !row.hasChildren }"
                @click="row.hasChildren && toggleExpanded(row)"
                :aria-label="expanded.has(row.key) ? 'Collapse group' : 'Expand group'"
              >{{ row.hasChildren ? (expanded.has(row.key) ? '⌄' : '›') : '' }}</button>
              <div class="agm__node">
                <div class="agm__node-title">
                  <span class="agm__swatch" :style="{ background: effectiveStyle(row).color }" />
                  <span>{{ groupOverride(row.key).label || row.label }}</span>
                  <small v-if="row.depth > 0">{{ row.fullLabel }}</small>
                </div>
                <div class="agm__controls">
                  <div class="agm__prop">
                    <span>{{ inheritanceLabel(row, 'color') }}</span>
                    <details class="agm__color-menu">
                      <summary
                        class="agm__color-trigger"
                        :style="{ background: groupOverride(row.key).color || effectiveStyle(row).color }"
                        :title="'Color for ' + row.fullLabel"
                      />
                      <div class="agm__color-popover">
                        <button
                          v-for="color in colorPalette"
                          :key="color"
                          type="button"
                          class="agm__color-chip"
                          :class="{ 'agm__color-chip--active': groupOverride(row.key).color === color }"
                          :style="{ background: color }"
                          @click="setGroupProp(row.key, 'color', color); closeRowMenu($event)"
                        />
                        <input
                          class="agm__input agm__color-code"
                          :value="groupOverride(row.key).color || effectiveStyle(row).color"
                          placeholder="#RRGGBB"
                          @input="setGroupProp(row.key, 'color', $event.target.value)"
                        />
                        <input
                          class="agm__color-picker"
                          type="color"
                          :value="groupOverride(row.key).color || effectiveStyle(row).color"
                          title="Pick custom color"
                          @input="setGroupProp(row.key, 'color', $event.target.value)"
                        />
                      </div>
                    </details>
                    <button type="button" class="agm__reset" @click="resetGroupProp(row.key, 'color')">Reset</button>
                  </div>
                  <div class="agm__prop">
                    <span>{{ inheritanceLabel(row, 'icon') }}</span>
                    <details class="agm__icon-menu">
                      <summary class="agm__icon-trigger" :title="iconLabel(iconOption(triggerIcon(row))) || 'No icon'">
                        <template v-if="iconOption(triggerIcon(row))">
                          <svg v-if="isBuiltinIcon(iconOption(triggerIcon(row)))" class="agm__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                            <path :d="ICON_PATHS[iconOption(triggerIcon(row)).key] || ''" fill="currentColor" />
                          </svg>
                          <span v-else-if="iconOption(triggerIcon(row)).icon_format !== 'Image'" class="agm__icon-preview agm__icon-preview--custom" v-html="iconOption(triggerIcon(row)).svg_content" />
                          <img v-else class="agm__icon-preview agm__icon-preview--image" :src="iconOption(triggerIcon(row)).image_data_url" alt="" />
                        </template>
                        <span v-else class="agm__icon-preview agm__icon-preview--none">∅</span>
                      </summary>
                      <div class="agm__icons" role="radiogroup" :aria-label="'Icon for ' + row.fullLabel">
                        <button
                          type="button"
                          class="agm__icon"
                          :data-active="!Object.prototype.hasOwnProperty.call(groupOverride(row.key), 'icon')"
                          title="Inherited icon"
                          aria-label="Inherited icon"
                          @click="resetGroupProp(row.key, 'icon'); closeRowMenu($event)"
                        >
                          <template v-if="iconOption(effectiveStyle(row).icon)">
                            <svg v-if="isBuiltinIcon(iconOption(effectiveStyle(row).icon))" class="agm__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                              <path :d="ICON_PATHS[iconOption(effectiveStyle(row).icon).key] || ''" fill="currentColor" />
                            </svg>
                            <span v-else-if="iconOption(effectiveStyle(row).icon).icon_format !== 'Image'" class="agm__icon-preview agm__icon-preview--custom" v-html="iconOption(effectiveStyle(row).icon).svg_content" />
                            <img v-else class="agm__icon-preview agm__icon-preview--image" :src="iconOption(effectiveStyle(row).icon).image_data_url" alt="" />
                          </template>
                          <span v-else class="agm__icon-preview agm__icon-preview--none">∅</span>
                        </button>
                        <button
                          type="button"
                          class="agm__icon"
                          :data-active="groupOverride(row.key).icon === '__none'"
                          title="No icon"
                          aria-label="No icon"
                          @click="setGroupProp(row.key, 'icon', '__none'); closeRowMenu($event)"
                        >
                          <span class="agm__icon-preview agm__icon-preview--none">∅</span>
                        </button>
                        <button
                          v-for="icon in allIconOptions"
                          :key="icon.key"
                          type="button"
                          class="agm__icon"
                          :data-active="groupOverride(row.key).icon === icon.key"
                          :title="iconLabel(icon)"
                          :aria-label="iconLabel(icon)"
                          @click="setGroupProp(row.key, 'icon', icon.key); closeRowMenu($event)"
                        >
                          <svg v-if="isBuiltinIcon(icon)" class="agm__icon-preview" viewBox="0 0 24 24" aria-hidden="true">
                            <path :d="ICON_PATHS[icon.key] || ''" fill="currentColor" />
                          </svg>
                          <span v-else-if="icon.icon_format !== 'Image'" class="agm__icon-preview agm__icon-preview--custom" v-html="icon.svg_content" />
                          <img v-else class="agm__icon-preview agm__icon-preview--image" :src="icon.image_data_url" alt="" />
                        </button>
                      </div>
                    </details>
                    <button type="button" class="agm__reset" @click="resetGroupProp(row.key, 'icon')">Reset</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>

      <footer class="agm__footer">
        <button type="button" class="agm__ghost" @click="emit('close')">Cancel</button>
        <button type="button" class="agm__primary" @click="save">Apply grouping</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.agm {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.agm__backdrop {
  position: absolute;
  inset: 0;
  background: rgba(3, 7, 18, 0.38);
  backdrop-filter: blur(2px);
}
.agm__panel {
  position: relative;
  width: min(780px, calc(100vw - 56px));
  height: min(580px, calc(100vh - 56px));
  min-width: 500px;
  min-height: 420px;
  display: grid;
  grid-template-rows: auto 1fr auto;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 14px;
  background: rgba(11, 14, 20, 0.94);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  color: #F8FAFC;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  animation: agm-pop 160ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes agm-pop {
  from { transform: translateY(8px) scale(0.98); opacity: 0; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}
.agm__header, .agm__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 12px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.agm__footer { border-top: 1px solid rgba(255, 255, 255, 0.1); border-bottom: 0; }
.agm__header h3 { margin: 0; font-size: 14px; font-weight: 600; }
.agm__header p { margin: 4px 0 0; color: rgba(226, 232, 240, 0.72); font-size: 12px; }
.agm__close, .agm__icon-btn {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  color: #E2E8F0;
  cursor: pointer;
}
.agm__body {
  min-height: 0;
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
}
.agm__levels {
  min-height: 0;
  overflow: auto;
  padding: 12px;
  border-right: 1px solid rgba(255, 255, 255, 0.1);
}
.agm__tree { min-height: 0; overflow: hidden; display: grid; grid-template-rows: auto auto 1fr; padding: 12px; }
.agm__section-head, .agm__toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.agm__section-head { margin-bottom: 12px; color: #CBD5E1; font-size: 12px; font-weight: 700; text-transform: uppercase; }
.agm__toolbar { margin-bottom: 12px; }
.agm__level {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 30px;
  align-items: start;
  gap: 8px;
  margin-bottom: 8px;
}
.agm__level-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agm__level-index {
  display: grid;
  place-items: center;
  height: 28px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.18);
  color: #BFDBFE;
  font-size: 12px;
  font-weight: 700;
}
.agm__input {
  width: 100%;
  min-width: 0;
  height: 32px;
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.72);
  color: #F8FAFC;
  padding: 0 10px;
}
.agm__input--mode { height: 28px; font-size: 11px; color: rgba(226, 232, 240, 0.82); }
.agm__ghost, .agm__primary, .agm__danger, .agm__reset {
  height: 32px;
  border-radius: 6px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(255, 255, 255, 0.06);
  color: #E2E8F0;
  padding: 0 10px;
  cursor: pointer;
}
.agm__primary { border-color: rgba(59, 130, 246, 0.65); background: #2563EB; color: #fff; }
.agm__danger { width: 100%; margin-top: 12px; border-color: rgba(248, 113, 113, 0.28); color: #FCA5A5; }
.agm__reset { height: 26px; padding: 0 8px; font-size: 11px; }
.agm__ghost:disabled, .agm__danger:disabled { opacity: 0.45; cursor: not-allowed; }
.agm__empty, .agm__warn, .agm__error {
  padding: 12px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  color: rgba(226, 232, 240, 0.72);
  font-size: 12px;
}
.agm__warn { margin: 10px 0; background: rgba(245, 158, 11, 0.12); color: #FCD34D; }
.agm__error { margin: 0 0 10px; background: rgba(239, 68, 68, 0.14); color: #FCA5A5; }
.agm__rows { min-height: 0; overflow: auto; padding-right: 4px; }
.agm__row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: stretch;
  gap: 6px;
  margin-left: calc(var(--depth) * 24px);
  margin-bottom: 8px;
}
.agm__twisty {
  width: 26px;
  border: 0;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  color: #CBD5E1;
  cursor: pointer;
}
.agm__twisty--leaf { background: transparent; cursor: default; }
.agm__node {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 9px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.56);
}
.agm__node-title { min-width: 0; display: grid; grid-template-columns: 16px minmax(0, auto); gap: 8px; align-items: center; }
.agm__node-title span:nth-child(2) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agm__node-title small { grid-column: 2; color: rgba(203, 213, 225, 0.58); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agm__swatch { width: 14px; height: 14px; border-radius: 999px; box-shadow: 0 0 0 1px rgba(255,255,255,0.4); }
.agm__controls { display: flex; flex-wrap: wrap; justify-content: flex-start; gap: 8px; }
.agm__prop { display: flex; align-items: center; gap: 7px; color: rgba(226, 232, 240, 0.68); font-size: 11px; }
.agm__color-menu, .agm__icon-menu { position: relative; flex: none; }
.agm__color-menu > summary,
.agm__icon-menu > summary { list-style: none; }
.agm__color-menu > summary::-webkit-details-marker,
.agm__icon-menu > summary::-webkit-details-marker { display: none; }
.agm__color-trigger {
  width: 28px;
  height: 28px;
  display: block;
  border-radius: 6px;
  border: 2px solid rgba(255, 255, 255, 0.82);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
  cursor: pointer;
}
.agm__color-popover {
  position: absolute;
  z-index: 30;
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
.agm__color-chip {
  width: 20px;
  height: 20px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 5px;
  cursor: pointer;
}
.agm__color-chip--active { box-shadow: 0 0 0 2px #fff; }
.agm__color-code {
  grid-column: 1 / 7;
  width: 100%;
  height: 28px;
  margin-top: 2px;
}
.agm__color-picker {
  grid-column: 7;
  grid-row: 1 / span 3;
  width: 40px;
  height: 40px;
  margin-left: 10px;
  align-self: stretch;
  padding: 0;
  border: 2px solid rgba(255, 255, 255, 0.82);
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
}
.agm__color-picker::-webkit-color-swatch-wrapper { padding: 0; }
.agm__color-picker::-webkit-color-swatch { border: 0; border-radius: 5px; }
.agm__icon-trigger {
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
.agm__icon-trigger:hover { background: rgba(255, 255, 255, 0.06); }
.agm__icons {
  position: absolute;
  z-index: 30;
  right: 0;
  top: 34px;
  width: 188px;
  max-height: 112px;
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
.agm__icon {
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
.agm__icon:hover { background: rgba(255, 255, 255, 0.06); }
.agm__icon[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
  border-color: #3B82F6;
}
.agm__icon-preview {
  width: 15px;
  height: 15px;
  display: grid;
  place-items: center;
  color: #E2E8F0;
}
.agm__icon-trigger .agm__icon-preview {
  width: 17px;
  height: 17px;
}
.agm__icon-preview svg,
.agm__icon-preview img,
.agm__icon-preview :deep(svg),
.agm__icon-preview--custom :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
.agm__icon-preview--none {
  font-size: 14px;
  color: rgba(230, 232, 236, 0.5);
}

@media (max-width: 860px) {
  .agm { padding: 10px; align-items: stretch; }
  .agm__panel {
    width: 100%;
    height: min(640px, calc(100vh - 20px));
    min-width: 0;
    min-height: 0;
  }
  .agm__body { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
  .agm__levels { border-right: 0; border-bottom: 1px solid rgba(255, 255, 255, 0.1); max-height: 220px; }
  .agm__controls { justify-content: flex-start; }
}
</style>
