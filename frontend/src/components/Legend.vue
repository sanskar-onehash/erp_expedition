<script setup>
/**
 * Legend — bottom-left pill listing ALL layers, not just enabled ones.
 *
 * Enabled layers appear as solid chips with color swatches. Disabled layers
 * appear as strikethrough/blurred placeholders so users can re-enable without
 * opening the panel. This addresses feedback: clicking a layer chip should not
 * hide it from the list; instead, it deactivates visually but stays visible.
 */
import { computed } from 'vue'
import { useLayersStore } from '../state/layers.js'

const layerStore = useLayersStore()

// All layers with their current enabled state, ordered by sequence
const allLayers = computed(() =>
  (layerStore.layers || []).slice().sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
)

function colorOf(l) {
  const style = layerStore.getLayerStyle(l.name) || {}
  return style.color || l.color || '#3B82F6'
}

function toggle(name, enabled) {
  layerStore.updateLayer(name, { enabled: enabled ? 0 : 1 })
}
</script>

<template>
  <div v-if="allLayers.length" class="legend" role="region" aria-label="Layer legend">
    <button
      v-for="l in allLayers"
      :key="l.name"
      type="button"
      class="legend__chip"
      :class="{ 'legend__chip--disabled': l.enabled === false || l.enabled === 0 }"
      :title="(l.title || l.name) + ' (' + (l.source_doctype || '') + ')'"
      :aria-label="'Toggle ' + (l.title || l.name)"
      @click="toggle(l.name, l.enabled !== false && l.enabled !== 0)"
    >
      <span class="legend__swatch" :style="{ background: colorOf(l) }" />
      <span class="legend__label">{{ l.title || l.name }}</span>
    </button>
  </div>
</template>

<style scoped>
.legend {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 60vw;
  background: rgba(11, 14, 20, 0.72);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 4px 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.32);
  pointer-events: auto;
}
.legend__chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px 4px 6px;
  background: transparent;
  border: 0;
  border-radius: 999px;
  color: rgba(230, 232, 236, 0.92);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  transition: background 100ms ease;
}
.legend__chip:hover { background: rgba(255, 255, 255, 0.08); }
.legend__chip--disabled {
  opacity: 0.5;
}
.legend__chip--disabled .legend__label {
  text-decoration: line-through;
}
.legend__chip--disabled .legend__swatch {
  box-shadow: none;
  border-color: rgba(255, 255, 255, 0.3);
}
.legend__swatch {
  width: 10px; height: 10px; border-radius: 50%;
  border: 1.5px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.4);
  flex: none;
}
.legend__label {
  white-space: nowrap;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
