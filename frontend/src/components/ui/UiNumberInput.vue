<script setup>
const props = defineProps({
  modelValue: { type: [Number, String], default: '' },
  min: { type: [Number, String], default: null },
  max: { type: [Number, String], default: null },
  step: { type: [Number, String], default: 1 },
  placeholder: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

function numberOrNull(value) {
  if (value === '' || value == null) return ''
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : value
}

function update(value) {
  emit('update:modelValue', numberOrNull(value))
}

function n(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function stepBy(delta) {
  const current = n(props.modelValue, 0)
  const step = n(props.step, 1)
  let next = current + delta * step
  if (props.min !== null && props.min !== '') next = Math.max(next, n(props.min))
  if (props.max !== null && props.max !== '') next = Math.min(next, n(props.max))
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="ui-number" :class="{ 'ui-number--compact': compact }">
    <input
      class="ui-number__input"
      type="number"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :placeholder="placeholder"
      @input="update($event.target.value)"
    />
    <div class="ui-number__steps" aria-hidden="false">
      <button type="button" tabindex="-1" @click="stepBy(1)">⌃</button>
      <button type="button" tabindex="-1" @click="stepBy(-1)">⌄</button>
    </div>
  </div>
</template>

<style scoped>
.ui-number {
  width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  overflow: hidden;
  box-sizing: border-box;
}
.ui-number:focus-within {
  border-color: rgba(59, 130, 246, 0.72);
}
.ui-number__input {
  width: 100%;
  min-width: 0;
  border: 0;
  background: transparent;
  color: #E6E8EC;
  padding: 7px 8px;
  font: inherit;
  font-size: 11px;
  outline: none;
  box-sizing: border-box;
}
.ui-number__input::-webkit-outer-spin-button,
.ui-number__input::-webkit-inner-spin-button {
  appearance: none;
  margin: 0;
}
.ui-number__steps {
  display: grid;
  grid-template-rows: 1fr 1fr;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
}
.ui-number__steps button {
  min-width: 0;
  border: 0;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(230, 232, 236, 0.68);
  font: inherit;
  font-size: 9px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.ui-number__steps button:hover {
  background: rgba(59, 130, 246, 0.16);
  color: #fff;
}
.ui-number--compact .ui-number__input {
  padding: 6px 8px;
}
</style>
