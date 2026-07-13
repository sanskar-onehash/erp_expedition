<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, Object, Array], default: '' },
  options: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'value' },
  labelKey: { type: String, default: 'label' },
  metaKey: { type: String, default: 'meta' },
  placeholder: { type: String, default: 'Select' },
  selectedLabel: { type: String, default: '' },
  emptyText: { type: String, default: 'No options found.' },
  disabled: { type: Boolean, default: false },
  searchable: { type: Boolean, default: true },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'select', 'open', 'close'])

const rootEl = ref(null)
const searchInput = ref(null)
const open = ref(false)
const query = ref('')

const normalized = computed(() =>
  (Array.isArray(props.options) ? props.options : []).map((option) => {
    if (option && typeof option === 'object') {
      return {
        raw: option,
        value: option[props.valueKey],
        label: option[props.labelKey] ?? option[props.valueKey] ?? '',
        meta: option[props.metaKey] ?? '',
        disabled: !!option.disabled,
      }
    }
    return { raw: option, value: option, label: String(option ?? ''), meta: '', disabled: false }
  })
)

const selected = computed(() =>
  normalized.value.find((option) => sameValue(option.value, props.modelValue))
)

const displayLabel = computed(() =>
  props.selectedLabel || selected.value?.label || props.placeholder
)

const filtered = computed(() => {
  const text = query.value.trim().toLowerCase()
  if (!text) return normalized.value
  return normalized.value.filter((option) =>
    `${option.label} ${option.meta} ${option.value}`.toLowerCase().includes(text)
  )
})

watch(open, async (value) => {
  if (!value) {
    query.value = ''
    emit('close')
    return
  }
  emit('open')
  await nextTick()
  if (props.searchable) searchInput.value?.focus?.()
})

function sameValue(a, b) {
  return String(a ?? '') === String(b ?? '')
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
}

function close() {
  open.value = false
}

function choose(option) {
  if (!option || option.disabled) return
  emit('update:modelValue', option.value)
  emit('select', option.raw)
  close()
}

function onDocumentPointerDown(event) {
  if (!open.value) return
  if (rootEl.value?.contains?.(event.target)) return
  close()
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
})
</script>

<template>
  <div ref="rootEl" class="ui-select" :class="{ 'ui-select--compact': compact, 'ui-select--disabled': disabled }">
    <button
      type="button"
      class="ui-select__trigger"
      :disabled="disabled"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span class="ui-select__value">{{ displayLabel }}</span>
      <span class="ui-select__chevron" aria-hidden="true">⌄</span>
    </button>
    <div v-if="open" class="ui-select__menu">
      <input
        v-if="searchable"
        ref="searchInput"
        v-model="query"
        class="ui-select__search"
        type="search"
        autocomplete="off"
        placeholder="Search..."
        @keydown="onKeydown"
      />
      <div class="ui-select__options">
        <button
          v-for="option in filtered"
          :key="String(option.value)"
          type="button"
          class="ui-select__option"
          :data-active="sameValue(option.value, modelValue)"
          :disabled="option.disabled"
          @click="choose(option)"
        >
          <span>{{ option.label }}</span>
          <small v-if="option.meta">{{ option.meta }}</small>
        </button>
        <p v-if="!filtered.length" class="ui-select__empty">{{ emptyText }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ui-select {
  position: relative;
  min-width: 0;
  width: 100%;
}
.ui-select__trigger {
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
.ui-select--compact .ui-select__trigger {
  min-height: 31px;
  padding: 6px 8px;
  font-size: 11px;
}
.ui-select__trigger:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.ui-select__value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ui-select__chevron {
  flex: none;
  color: rgba(230, 232, 236, 0.55);
}
.ui-select__menu {
  position: absolute;
  z-index: 120;
  left: 0;
  right: 0;
  top: calc(100% + 6px);
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(18, 20, 24, 0.98);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.38);
  box-sizing: border-box;
}
.ui-select__search {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.28);
  color: #E6E8EC;
  padding: 7px 8px;
  font: inherit;
  font-size: 11px;
  outline: none;
}
.ui-select__search:focus {
  border-color: rgba(59, 130, 246, 0.72);
}
.ui-select__options {
  max-height: 238px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ui-select__option {
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
.ui-select__option span,
.ui-select__option small {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ui-select__option small {
  color: rgba(230, 232, 236, 0.48);
  font-size: 10px;
}
.ui-select__option:hover,
.ui-select__option[data-active="true"] {
  background: rgba(59, 130, 246, 0.16);
  color: #fff;
}
.ui-select__option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ui-select__empty {
  margin: 0;
  padding: 8px;
  color: rgba(230, 232, 236, 0.55);
  font-size: 11px;
}
</style>
