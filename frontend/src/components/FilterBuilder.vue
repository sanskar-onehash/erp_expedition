<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { call } from '../api/client.js'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  sourceDoctype: { type: String, default: '' },
  compact: { type: Boolean, default: false },
  title: { type: String, default: 'Filters' },
})
const emit = defineEmits(['update:modelValue', 'schema-loaded', 'error'])

const schema = ref({ doctype: '', fields: [] })
const loading = ref(false)
const error = ref('')
const rootEl = ref(null)
const fieldSearch = ref({})
const valueHints = ref({})
const activeFieldIndex = ref(null)
const activeSelectIndex = ref(null)
const activeHintIndex = ref(null)
const activeMenu = ref(null)
let valueRequestSeq = 0

const rows = computed({
  get: () => Array.isArray(props.modelValue) ? props.modelValue : [],
  set: (value) => emit('update:modelValue', value),
})

const fields = computed(() => schema.value.fields || [])
const fieldMap = computed(() => new Map(fields.value.map((f) => [f.fieldname, f])))
const floatingMenuStyle = computed(() => {
  const style = activeMenu.value?.style || {}
  return {
    top: `${style.top || 0}px`,
    left: `${style.left || 0}px`,
    width: `${style.width || 280}px`,
    maxHeight: `${style.maxHeight || 184}px`,
  }
})
const floatingMenuItems = computed(() => {
  const menu = activeMenu.value
  if (!menu) return []
  const row = rows.value[menu.index] || {}
  if (menu.kind === 'field') {
    return filteredFieldOptions(menu.index, row).map((f) => ({
      key: f.fieldname,
      label: f.label || f.fieldname,
      meta: f.fieldname,
      value: f.fieldname,
    }))
  }
  if (menu.kind === 'operator') {
    return operatorOptions(row).map((op) => ({
      key: op.key,
      label: op.label,
      value: op.key,
    }))
  }
  if (menu.kind === 'value-select') {
    return selectOptions(row).map((opt) => ({
      key: String(optionValue(opt)),
      label: optionLabel(opt),
      value: opt,
    }))
  }
  if (menu.kind === 'value-hint') {
    return hintsFor(menu.index).map((opt) => {
      const label = optionLabel(opt)
      const value = optionValue(opt)
      return {
        key: String(value),
        label,
        meta: String(label) !== String(value) ? value : '',
        value: opt,
      }
    })
  }
  return []
})
const floatingMenuOpen = computed(() => !!activeMenu.value && floatingMenuItems.value.length > 0)

watch(
  () => props.sourceDoctype,
  () => loadSchema(),
  { immediate: true }
)

onMounted(() => loadSchema())

async function loadSchema() {
  if (!props.sourceDoctype) {
    schema.value = { doctype: '', fields: [] }
    return
  }
  if (schema.value.doctype === props.sourceDoctype && fields.value.length) return
  loading.value = true
  error.value = ''
  try {
    const resp = await call('expedition.api.layer.get_filter_schema', {
      source_doctype: props.sourceDoctype,
    })
    schema.value = normalizeSchema(resp, props.sourceDoctype)
    emit('schema-loaded', schema.value)
  } catch (e) {
    error.value = e.message || String(e)
    emit('error', e)
    schema.value = { doctype: props.sourceDoctype, fields: [] }
  } finally {
    loading.value = false
  }
}

function normalizeSchema(resp, fallbackDoctype) {
  const next = resp || { doctype: fallbackDoctype, fields: [] }
  return {
    ...next,
    doctype: next.doctype || fallbackDoctype,
    fields: sortFields(next.fields),
  }
}

function sortFields(items) {
  return [...(Array.isArray(items) ? items : [])].sort((a, b) => {
    const aLabel = String(a?.label || a?.fieldname || '')
    const bLabel = String(b?.label || b?.fieldname || '')
    const byLabel = aLabel.localeCompare(bLabel, undefined, { sensitivity: 'base' })
    if (byLabel) return byLabel
    return String(a?.fieldname || '').localeCompare(String(b?.fieldname || ''), undefined, {
      sensitivity: 'base',
    })
  })
}

function addRow() {
  rows.value = [...rows.value, { field: '', op: '=', value: '' }]
  nextTick(() => {
    const fieldInput = rootEl.value?.querySelector?.('.fb__row:last-child .fb__field input')
    fieldInput?.focus?.()
  })
}

function removeRow(index) {
  rows.value = rows.value.filter((_, i) => i !== index)
}

function clearRows() {
  rows.value = []
}

function updateRow(index, patch) {
  rows.value = rows.value.map((row, i) => i === index ? { ...row, ...patch } : row)
}

function fieldLabel(fieldname) {
  const f = fieldMap.value.get(fieldname)
  return f ? `${f.label || f.fieldname} (${f.fieldname})` : fieldname
}

function fieldText(index, row) {
  return fieldSearch.value[index] ?? (row.field ? fieldLabel(row.field) : '')
}

function placeFloatingMenu(event, { align = 'left', width = 280 } = {}) {
  const rect = event?.currentTarget?.getBoundingClientRect?.()
  if (!rect) return activeMenu.value?.style || {}
  const margin = 12
  const menuWidth = Math.min(width, window.innerWidth - margin * 2)
  const rawLeft = align === 'right' ? rect.right - menuWidth : rect.left
  const left = Math.min(Math.max(margin, rawLeft), window.innerWidth - menuWidth - margin)
  const below = window.innerHeight - rect.bottom - margin
  const above = rect.top - margin
  const preferredHeight = 184
  const openAbove = below < 132 && above > below
  const maxHeight = Math.max(132, Math.min(preferredHeight, openAbove ? above : below))
  const top = openAbove
    ? Math.max(margin, rect.top - maxHeight - 4)
    : Math.min(rect.bottom + 4, window.innerHeight - maxHeight - margin)
  return { top, left, width: menuWidth, maxHeight }
}

function onFieldInput(index, text, event) {
  fieldSearch.value = { ...fieldSearch.value, [index]: text }
  activeFieldIndex.value = index
  activeSelectIndex.value = null
  activeHintIndex.value = null
  activeMenu.value = { kind: 'field', index, style: placeFloatingMenu(event, { align: 'left', width: 280 }) }
  const picked = fields.value.find((f) =>
    f.fieldname === text ||
    `${f.label || f.fieldname} (${f.fieldname})` === text ||
    (f.label || '').toLowerCase() === String(text || '').toLowerCase()
  )
  if (picked) selectField(index, picked.fieldname)
}

function selectField(index, fieldname) {
  const meta = fieldMap.value.get(fieldname)
  const first = meta?.operators?.[0]
  const nextOp = first?.value || '='
  const nextValue = first?.fixed_value ?? ''
  fieldSearch.value = { ...fieldSearch.value, [index]: fieldLabel(fieldname) }
  updateRow(index, { field: fieldname, op: nextOp, value: nextValue })
  closeMenus()
}

function openFieldPicker(index, event) {
  activeFieldIndex.value = index
  activeSelectIndex.value = null
  activeHintIndex.value = null
  activeMenu.value = { kind: 'field', index, style: placeFloatingMenu(event, { align: 'left', width: 280 }) }
}

function filteredFieldOptions(index, row) {
  const text = String(fieldText(index, row) || '').trim().toLowerCase()
  if (row.field && text === fieldLabel(row.field).toLowerCase()) return []
  const matches = !text
    ? fields.value
    : fields.value.filter((f) => {
      const label = String(f.label || f.fieldname).toLowerCase()
      const fieldname = String(f.fieldname || '').toLowerCase()
      return label.includes(text) || fieldname.includes(text) || `${label} (${fieldname})`.includes(text)
    })
  return matches.slice(0, 40)
}

function fieldMenuOpen(index, row) {
  return activeFieldIndex.value === index && filteredFieldOptions(index, row).length > 0
}

function operatorOptions(row) {
  const ops = fieldMap.value.get(row.field)?.operators || [
    { value: '=', label: 'equals', requires_value: true },
    { value: '!=', label: 'not equals', requires_value: true },
  ]
  return ops.map((op) => ({
    ...op,
    key: op.value === 'is' && op.fixed_value ? `is:${op.fixed_value}` : op.value,
  }))
}

function currentOperator(row) {
  const ops = operatorOptions(row)
  if (row.op === 'is') {
    const value = String(row.value || 'set').toLowerCase()
    return ops.find((op) => op.value === 'is' && String(op.fixed_value).toLowerCase() === value) || ops[0]
  }
  return ops.find((op) => op.value === row.op) || ops[0]
}

function onOperatorChange(index, row, key) {
  const op = operatorOptions(row).find((item) => item.key === key)
  if (!op) return
  let value = op.fixed_value ?? row.value ?? ''
  if (op.range && !Array.isArray(value)) value = ['', '']
  if (op.multi && Array.isArray(value)) value = value.join(', ')
  if (!op.requires_value) value = op.fixed_value ?? ''
  updateRow(index, { op: op.value, value })
  closeMenus()
}

function toggleOperatorSelect(index, row, event) {
  if (!row.field) return
  activeFieldIndex.value = null
  activeSelectIndex.value = null
  activeHintIndex.value = null
  activeMenu.value = activeMenu.value?.kind === 'operator' && activeMenu.value?.index === index
    ? null
    : { kind: 'operator', index, style: placeFloatingMenu(event, { align: 'left', width: 180 }) }
}

function rowField(row) {
  return fieldMap.value.get(row.field) || {}
}

function needsValue(row) {
  return currentOperator(row)?.requires_value !== false
}

function isRange(row) {
  return !!currentOperator(row)?.range
}

function isMulti(row) {
  return !!currentOperator(row)?.multi
}

function valueInputMode(row) {
  const ft = rowField(row).fieldtype
  if (['Currency', 'Duration', 'Float', 'Int', 'Percent', 'Rating'].includes(ft)) return 'decimal'
  return 'text'
}

function valuePlaceholder(row) {
  const ft = rowField(row).fieldtype
  if (ft === 'Date') return 'YYYY-MM-DD'
  if (ft === 'Datetime') return 'YYYY-MM-DD HH:mm'
  if (ft === 'Time') return 'HH:mm'
  if (['Currency', 'Duration', 'Float', 'Int', 'Percent', 'Rating'].includes(ft)) return 'Number'
  return isMulti(row) ? 'value, value...' : 'Value'
}

function selectOptions(row) {
  const f = rowField(row)
  if (Array.isArray(f.select_options)) return f.select_options.map((item) => {
    if (typeof item === 'object') return item
    return { label: item, value: item }
  })
  return []
}

function valueHintKey(index) {
  return `${props.sourceDoctype}:${index}`
}

async function loadValueHints(index, row, txt = '') {
  if (!props.sourceDoctype || !row.field || isMulti(row) || isRange(row)) return
  const ft = rowField(row).fieldtype
  if (!['Link', 'Data', 'Read Only', 'Text', 'Small Text', 'Long Text'].includes(ft)) return
  const seq = ++valueRequestSeq
  try {
    const resp = await call('expedition.api.layer.get_filter_value_options', {
      source_doctype: props.sourceDoctype,
      field: row.field,
      txt,
      limit: 20,
    })
    if (seq !== valueRequestSeq) return
    valueHints.value = { ...valueHints.value, [valueHintKey(index)]: resp?.options || [] }
  } catch (_) {
    valueHints.value = { ...valueHints.value, [valueHintKey(index)]: [] }
  }
}

function hintsFor(index) {
  return valueHints.value[valueHintKey(index)] || []
}

function optionLabel(option) {
  if (option && typeof option === 'object') return option.label ?? option.value ?? ''
  return option ?? ''
}

function optionValue(option) {
  if (option && typeof option === 'object') return option.value ?? option.label ?? ''
  return option ?? ''
}

function valueSelectLabel(row) {
  const current = String(row.value ?? '')
  const picked = selectOptions(row).find((opt) => String(optionValue(opt)) === current)
  return picked ? optionLabel(picked) : (current || 'Value')
}

function toggleValueSelect(index, event) {
  activeSelectIndex.value = activeSelectIndex.value === index ? null : index
  activeFieldIndex.value = null
  activeHintIndex.value = null
  activeMenu.value = activeSelectIndex.value === index
    ? { kind: 'value-select', index, style: placeFloatingMenu(event, { align: 'right', width: 260 }) }
    : null
}

function chooseValueOption(index, option) {
  updateRow(index, { value: optionValue(option) })
  closeMenus()
}

function openValueHints(index, row, event) {
  activeHintIndex.value = index
  activeFieldIndex.value = null
  activeSelectIndex.value = null
  activeMenu.value = { kind: 'value-hint', index, style: placeFloatingMenu(event, { align: 'right', width: 260 }) }
  loadValueHints(index, row, row.value)
}

function onValueInput(index, row, value, event) {
  updateRow(index, { value })
  activeHintIndex.value = index
  activeMenu.value = { kind: 'value-hint', index, style: placeFloatingMenu(event, { align: 'right', width: 260 }) }
  loadValueHints(index, row, value)
}

function hintMenuOpen(index) {
  return activeHintIndex.value === index && hintsFor(index).length > 0
}

function chooseHintValue(index, option) {
  updateRow(index, { value: optionValue(option) })
  closeMenus()
}

function chooseFloatingMenuItem(item) {
  const menu = activeMenu.value
  if (!menu) return
  const row = rows.value[menu.index] || {}
  if (menu.kind === 'field') selectField(menu.index, item.value)
  else if (menu.kind === 'operator') onOperatorChange(menu.index, row, item.value)
  else if (menu.kind === 'value-select') chooseValueOption(menu.index, item.value)
  else if (menu.kind === 'value-hint') chooseHintValue(menu.index, item.value)
}

function closeMenus() {
  activeFieldIndex.value = null
  activeSelectIndex.value = null
  activeHintIndex.value = null
  activeMenu.value = null
}

function closeMenusSoon() {
  window.setTimeout(closeMenus, 120)
}

function updateRange(index, row, rangeIndex, value) {
  const values = Array.isArray(row.value) ? [...row.value] : ['', '']
  values[rangeIndex] = value
  updateRow(index, { value: values })
}

</script>

<template>
  <div ref="rootEl" class="fb" :class="{ 'fb--compact': compact }">
    <div class="fb__header">
      <span class="fb__title">{{ title }}</span>
      <div class="fb__actions">
        <button v-if="rows.length" type="button" class="fb__btn fb__btn--ghost" @click="clearRows">Clear</button>
        <button type="button" class="fb__btn" @click="addRow">+ Add</button>
      </div>
    </div>

    <div v-if="loading" class="fb__note">Loading fields...</div>
    <div v-else-if="error" class="fb__error">{{ error }}</div>
    <div v-else-if="!sourceDoctype" class="fb__note">Choose a source DocType first.</div>

    <div v-if="rows.length" class="fb__rows">
      <div v-for="(row, index) in rows" :key="index" class="fb__row">
        <div class="fb__field">
          <input
            class="fb__input"
            type="text"
            :value="fieldText(index, row)"
            placeholder="Field"
            autocomplete="off"
            @focus="openFieldPicker(index, $event)"
            @input="onFieldInput(index, $event.target.value, $event)"
            @change="onFieldInput(index, $event.target.value, $event)"
            @blur="closeMenusSoon"
          />
        </div>

        <button
          type="button"
          class="fb__input fb__select-btn fb__op"
          :disabled="!row.field"
          @click="toggleOperatorSelect(index, row, $event)"
          @blur="closeMenusSoon"
        >
          <span>{{ currentOperator(row)?.label || 'Operator' }}</span>
        </button>

        <template v-if="row.field && needsValue(row)">
          <div v-if="isRange(row)" class="fb__range">
            <input
              class="fb__input"
              type="text"
              :inputmode="valueInputMode(row)"
              :placeholder="valuePlaceholder(row)"
              :value="Array.isArray(row.value) ? row.value[0] : ''"
              @input="updateRange(index, row, 0, $event.target.value)"
            />
            <input
              class="fb__input"
              type="text"
              :inputmode="valueInputMode(row)"
              :placeholder="valuePlaceholder(row)"
              :value="Array.isArray(row.value) ? row.value[1] : ''"
              @input="updateRange(index, row, 1, $event.target.value)"
            />
          </div>
          <div
            v-else-if="selectOptions(row).length && !isMulti(row)"
            class="fb__value-wrap"
          >
            <button
              type="button"
              class="fb__input fb__select-btn"
              :class="{ 'fb__select-btn--empty': !row.value }"
              @click="toggleValueSelect(index, $event)"
              @blur="closeMenusSoon"
            >
              <span>{{ valueSelectLabel(row) }}</span>
            </button>
          </div>
          <div v-else class="fb__value-wrap">
            <input
              class="fb__input fb__value"
              type="text"
              :inputmode="isMulti(row) ? 'text' : valueInputMode(row)"
              :value="Array.isArray(row.value) ? row.value.join(', ') : row.value"
              :placeholder="valuePlaceholder(row)"
              autocomplete="off"
              @focus="openValueHints(index, row, $event)"
              @input="onValueInput(index, row, $event.target.value, $event)"
              @blur="closeMenusSoon"
            />
          </div>
        </template>

        <div v-else-if="row.field" class="fb__fixed">{{ currentOperator(row)?.label }}</div>

        <button type="button" class="fb__remove" aria-label="Remove filter" @click="removeRow(index)">x</button>
      </div>
    </div>

    <p v-else-if="!loading && sourceDoctype" class="fb__note">No filters. All source rows are shown.</p>
    <Teleport to="body">
      <div
        v-if="floatingMenuOpen"
        class="fb__floating-menu"
        :style="floatingMenuStyle"
        role="listbox"
        @mousedown.stop
        @click.stop
      >
        <button
          v-for="item in floatingMenuItems"
          :key="item.key"
          type="button"
          class="fb__menu-item"
          @mousedown.prevent="chooseFloatingMenuItem(item)"
        >
          <span class="fb__menu-label">{{ item.label }}</span>
          <span v-if="item.meta" class="fb__menu-meta">{{ item.meta }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.fb {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  min-height: 0;
}
.fb__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.fb__title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.62);
  font-weight: 500;
}
.fb__actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
}
.fb__btn {
  padding: 5px 9px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.07);
  color: #E6E8EC;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.fb__btn:hover {
  background: rgba(255, 255, 255, 0.11);
}
.fb__btn--ghost {
  background: transparent;
}
.fb__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
}
.fb--compact .fb__rows {
  max-height: var(--fb-compact-rows-max-height, 154px);
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}
.fb--compact .fb__rows:hover { scrollbar-color: rgba(255, 255, 255, 0.32) transparent; }
.fb--compact .fb__rows::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.fb--compact .fb__rows::-webkit-scrollbar-track { background: transparent; }
.fb--compact .fb__rows::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}
.fb--compact .fb__rows:hover::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.32); }
.fb--compact .fb__rows::-webkit-scrollbar-thumb:vertical { min-height: 24px; }
.fb__row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(88px, 0.7fr) minmax(0, 1fr) 24px;
  gap: 6px;
  align-items: start;
  position: relative;
}
.fb--compact .fb__row {
  grid-template-columns: minmax(0, 1.08fr) minmax(58px, 0.52fr) minmax(0, 0.95fr) 22px;
  gap: 4px;
}
.fb__field,
.fb__range,
.fb__value-wrap {
  min-width: 0;
}
.fb__field,
.fb__value-wrap {
  position: relative;
}
.fb__field:focus-within,
.fb__value-wrap:focus-within {
  z-index: 50;
}
.fb__range {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 5px;
}
.fb__input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  color: #E6E8EC;
  padding: 7px 9px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
}
.fb--compact .fb__input {
  padding: 5px 6px;
  font-size: 10.5px;
}
.fb--compact .fb__op {
  padding-left: 5px;
  padding-right: 4px;
}
.fb__input:focus {
  border-color: rgba(59, 130, 246, 0.85);
}
.fb__op {
  color: rgba(230, 232, 236, 0.9);
}
.fb__value {
  min-width: 0;
}
.fb__select-btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  text-align: left;
  cursor: pointer;
}
.fb__select-btn::after {
  content: "";
  width: 6px;
  height: 6px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(45deg) translateY(-2px);
  opacity: 0.65;
  flex: none;
}
.fb__select-btn--empty {
  color: rgba(230, 232, 236, 0.45);
}
.fb__floating-menu {
  position: fixed;
  z-index: 120;
  overflow-y: auto;
  box-sizing: border-box;
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  font-feature-settings: 'cv11', 'ss01', 'ss03';
  background: rgba(11, 14, 20, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 8px;
  padding: 4px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.42);
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}
.fb__floating-menu:hover { scrollbar-color: rgba(255, 255, 255, 0.32) transparent; }
.fb__floating-menu::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.fb__floating-menu::-webkit-scrollbar-track { background: transparent; }
.fb__floating-menu::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}
.fb__floating-menu:hover::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.32); }
.fb__floating-menu::-webkit-scrollbar-thumb:vertical { min-height: 24px; }
.fb__menu-item {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(230, 232, 236, 0.88);
  font: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}
.fb__menu-item:hover {
  background: rgba(59, 130, 246, 0.16);
  color: #fff;
}
.fb__menu-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fb__menu-meta {
  color: rgba(230, 232, 236, 0.42);
  font-size: 10px;
  flex: none;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fb__fixed {
  font-size: 11px;
  color: rgba(230, 232, 236, 0.62);
  padding-top: 7px;
}
.fb__remove {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: rgba(230, 232, 236, 0.45);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.fb__remove:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.fb__note,
.fb__error {
  margin: 0;
  font-size: 11px;
  color: rgba(230, 232, 236, 0.52);
  line-height: 1.4;
}
.fb__error {
  color: #FCA5A5;
}
@media (max-width: 560px) {
  .fb__row,
  .fb--compact .fb__row {
    grid-template-columns: 1fr;
  }
}
</style>
