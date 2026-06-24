<script setup>
/**
 * LoadingOverlay — full-canvas overlay with a centered spinner + the
 * active map's title. Mounted by App.vue, shown when ui.basemapLoading
 * is true. CSS-only spinner; no JS animation. Pointer-events: none so
 * the underlying map is still pannable while the spinner is up.
 */
import { computed } from 'vue'
import { useMapStore } from '../state/map.js'
import { useUiStore } from '../state/ui.js'

const mapStore = useMapStore()
const ui = useUiStore()
const title = computed(() => mapStore.activeMap?.map?.title || 'Expedition')
</script>

<template>
  <Transition name="overlay">
    <div v-if="ui.basemapLoading" class="overlay">
      <div class="overlay__center">
        <div class="overlay__spinner" aria-hidden="true" />
        <div class="overlay__title">{{ title }}</div>
        <div class="overlay__hint">Loading basemap…</div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.overlay {
  position: absolute;
  inset: 0;
  background: #0B0E14;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 50;
}
.overlay__center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
.overlay__spinner {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.12);
  border-top-color: #E6E8EC;
  animation: overlay-spin 0.8s linear infinite;
}
.overlay__title {
  font-size: 15px;
  font-weight: 500;
  color: #E6E8EC;
  letter-spacing: -0.01em;
}
.overlay__hint {
  font-size: 12px;
  color: rgba(230, 232, 236, 0.5);
}
.overlay-enter-active, .overlay-leave-active {
  transition: opacity 200ms ease;
}
.overlay-enter-from, .overlay-leave-to {
  opacity: 0;
}
@keyframes overlay-spin {
  to { transform: rotate(360deg); }
}
</style>
