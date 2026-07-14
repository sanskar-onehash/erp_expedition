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
import { useUiStore } from '../state/ui.js'
import { RAMP_PRESETS, parseRampJson } from '../api/heatmap.js'

const layerStore = useLayersStore()
const ui = useUiStore()

// All layers with their current enabled state, ordered by sequence
const allLayers = computed(() =>
  (layerStore.layers || []).slice().sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
)
const heatmapLayers = computed(() =>
  allLayers.value.filter((l) =>
    l.enabled !== false && l.enabled !== 0 &&
    !layerStore.locallyHidden.has(l.name) &&
    ui.isHeatmapOn(l.name)
  )
)

function colorOf(l) {
  const style = layerStore.getLayerStyle(l.name) || {}
  return style.color || l.color || '#3B82F6'
}

function toggle(name, serverEnabled) {
  // Legend chips are quick-view only — they toggle local (session) visibility
  // without persisting to the server. Server-disabled layers must be
  // re-enabled from the panel; clicking their chip here does nothing.
  if (!serverEnabled) return
  layerStore.toggleLocalVisibility(name)
}

function edit(l) {
  ui.openLayerEditor(l)
}

function heatmapMetricLabel(l) {
  const cfg = l.heatmap_config || l.style?.heatmap_config || {}
  const mode = l.heatmap_mode || cfg.mode || 'count'
  const field = l.heatmap_weight_field || cfg.weight_field || ''
  if (mode === 'sum' && field) {
    const scale = (l.heatmap_weight_scale || cfg.weight_scale) === 'log' ? ' log scale' : ''
    return `Weighted density: ${field}${scale}`
  }
  return 'Record density'
}

function heatmapRamp(l) {
  const cfg = l.heatmap_config || l.style?.heatmap_config || {}
  return parseRampJson(l.heatmap_ramp_json)
    || (Array.isArray(cfg.ramp) && cfg.ramp.length ? cfg.ramp : null)
    || RAMP_PRESETS.monochrome.build(colorOf(l))
}

function hexToRgba(hex, alpha = 1) {
  const h = String(hex || '').replace('#', '')
  if (h.length === 3) {
    const r = parseInt(h[0] + h[0], 16)
    const g = parseInt(h[1] + h[1], 16)
    const b = parseInt(h[2] + h[2], 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  if (h.length === 6) {
    const r = parseInt(h.slice(0, 2), 16)
    const g = parseInt(h.slice(2, 4), 16)
    const b = parseInt(h.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  return hex || '#3B82F6'
}

function heatmapGradient(l) {
  const stops = heatmapRamp(l)
  return `linear-gradient(90deg, ${stops.map((s) =>
    `${hexToRgba(s.color, s.alpha ?? 1)} ${Math.round(Number(s.stop) * 100)}%`
  ).join(', ')})`
}

function groupSwatches(l) {
  const fc = layerStore.features[l.name]
  const seen = new Map()
  for (const feature of fc?.features || []) {
    const props = feature.properties || {}
    const color = props._color
    const key = props._group_label || props._group_value
    if (!color || key == null || seen.has(String(key))) continue
    seen.set(String(key), { label: String(key), color })
    if (seen.size >= 6) break
  }
  return [...seen.values()]
}
</script>

<template>
  <div v-if="allLayers.length" class="legend" role="region" aria-label="Layer legend">
    <button
      v-for="l in allLayers"
      :key="l.name"
      type="button"
      class="legend__chip"
      :class="{
        'legend__chip--disabled': l.enabled === false || l.enabled === 0 || layerStore.locallyHidden.has(l.name),
        'legend__chip--server-disabled': l.enabled === false || l.enabled === 0,
      }"
      :title="(l.title || l.name) + ' (' + (l.source_doctype || '') + (l.enabled === false || l.enabled === 0 ? ' — disabled via panel' : '') + ')'"
      :aria-label="'Toggle ' + (l.title || l.name)"
      @click="toggle(l.name, l.enabled !== false && l.enabled !== 0)"
      @contextmenu.prevent="edit(l)"
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
.legend__chip--server-disabled {
  cursor: not-allowed;
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
.legend__heatmaps {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: min(440px, 72vw);
  margin-top: 6px;
  background: rgba(11, 14, 20, 0.78);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.32);
  color: rgba(230, 232, 236, 0.92);
  pointer-events: auto;
}
.legend__heatmap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.legend__heatmap-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.legend__heatmap-title {
  font-size: 11px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.legend__heatmap-metric,
.legend__heatmap-scale {
  font-size: 10px;
  color: rgba(230, 232, 236, 0.58);
  white-space: nowrap;
}
.legend__heatmap-ramp {
  display: grid;
  grid-template-columns: auto minmax(120px, 1fr) auto;
  align-items: center;
  gap: 8px;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.58);
}
.legend__heatmap-gradient {
  height: 8px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
}
.legend__groups {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 8px;
  align-items: center;
}
.legend__group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 120px;
  font-size: 10px;
  color: rgba(230, 232, 236, 0.72);
}
.legend__group-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.65);
  flex: none;
}
</style>
