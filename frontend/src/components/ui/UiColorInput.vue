<script setup>
const props = defineProps({
  modelValue: { type: String, default: '' },
  presets: { type: Array, default: () => [] },
  placeholder: { type: String, default: '#3B82F6' },
  invalid: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'blur'])

function setValue(value) {
  emit('update:modelValue', value || '')
}
</script>

<template>
  <div class="ui-color" :class="{ 'ui-color--compact': compact, 'ui-color--invalid': invalid }">
    <span class="ui-color__swatch" :style="{ background: modelValue || 'transparent' }" aria-hidden="true" />
    <input
      class="ui-color__input"
      :value="modelValue"
      type="text"
      spellcheck="false"
      :placeholder="placeholder"
      @input="setValue($event.target.value)"
      @blur="emit('blur')"
    />
    <div v-if="presets.length" class="ui-color__presets">
      <button
        v-for="color in presets"
        :key="color"
        type="button"
        class="ui-color__chip"
        :class="{ 'ui-color__chip--active': String(modelValue).toLowerCase() === String(color).toLowerCase() }"
        :style="{ background: color }"
        :title="color"
        @click="setValue(color)"
      />
    </div>
  </div>
</template>

<style scoped>
.ui-color {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
}
.ui-color--compact {
  grid-template-columns: 30px minmax(0, 1fr);
}
.ui-color__swatch {
  width: 36px;
  height: 36px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.28);
  box-sizing: border-box;
}
.ui-color--compact .ui-color__swatch {
  width: 30px;
  height: 30px;
  border-radius: 7px;
}
.ui-color__input {
  width: 100%;
  min-width: 0;
  height: 36px;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  color: #E6E8EC;
  padding: 8px 10px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  outline: none;
}
.ui-color--compact .ui-color__input {
  height: 30px;
  padding: 6px 8px;
  font-size: 11px;
}
.ui-color__input:focus {
  border-color: rgba(59, 130, 246, 0.72);
}
.ui-color--invalid .ui-color__input {
  border-color: rgba(239, 68, 68, 0.85);
  box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.22);
}
.ui-color__presets {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ui-color__chip {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  border: 1px solid rgba(0, 0, 0, 0.3);
  cursor: pointer;
  padding: 0;
  outline: none;
}
.ui-color__chip--active {
  box-shadow: 0 0 0 2px #fff;
}
</style>
