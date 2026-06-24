<script setup>
/**
 * CommandPalette — Spotlight-style overlay. PR-4 of the quiet-canvas plan.
 *
 * Replaces CommandK.vue. Cmd/Ctrl-K opens. Sections:
 *   - Maps     (recent + templates)
 *   - Layers   (active map's layers — toggle visibility, fly to centroid)
 *   - Zones    (active map's zones — fly to centroid)
 *   - Commands (camera: Recenter, Toggle Layers, Toggle Tools, Toggle Basemap)
 *
 * Arrow-key navigation across the flat list (sections are visual headers
 * only). Enter runs. Esc closes. Mouse click runs + closes.
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useZonesStore } from '../state/zones.js'
import { useUiStore } from '../state/ui.js'

const mapStore = useMapStore()
const layerStore = useLayersStore()
const zoneStore = useZonesStore()
const ui = useUiStore()

const q = ref('')
const inputEl = ref(null)
const flatList = ref([])   // current flat items matching the query
const cursor = ref(0)

const activeMapName = computed(() => mapStore.activeMap?.map?.name)
const recent = computed(() => ui.recent || [])
const templates = computed(() => mapStore.templates || [])
const activeLayers = computed(() => (layerStore.layers || []).filter((l) => l.map === activeMapName.value))
const zones = computed(() => activeMapName.value ? zoneStore.byMap[activeMapName.value] || [] : [])

function buildFlat() {
  const s = q.value.toLowerCase().trim()
  const items = []

  // Maps
  const maps = [...recent.value, ...templates.value]
  for (const m of maps) {
    if (!s || (m.title || m.name || '').toLowerCase().includes(s)) {
      items.push({
        kind: 'map',
        id: 'map:' + m.name,
        label: m.title || m.name,
        meta: m.basemap_style,
        run: () => { mapStore.switchMap(m.name); close() },
      })
    }
  }

  // Layers
  for (const l of activeLayers.value) {
    if (!s || (l.title || l.name).toLowerCase().includes(s)) {
      items.push({
        kind: 'layer',
        id: 'layer:' + l.name,
        label: l.title || l.name,
        meta: l.source_doctype,
        run: () => { layerStore.updateLayer(l.name, { enabled: l.enabled === false ? 1 : 0 }); close() },
      })
    }
  }

  // Zones
  for (const z of zones.value) {
    if (!s || (z.title || z.name).toLowerCase().includes(s)) {
      items.push({
        kind: 'zone',
        id: 'zone:' + z.name,
        label: z.title || z.name,
        meta: 'zone',
        run: () => {
          if (z.centroid_lat != null && z.centroid_lng != null && window.expeditionMap) {
            window.expeditionMap.flyTo({
              center: [z.centroid_lng, z.centroid_lat],
              zoom: Math.max(window.expeditionMap.getZoom?.() || 4, 8),
              duration: 700,
            })
          }
          close()
        },
      })
    }
  }

  // Commands
  const cmds = [
    { id: 'cmd:recenter', label: 'Recenter on map home', meta: 'command', run: () => { window.expeditionMap?.getMap && (() => {
      // Mirror the Basemap.vue RecenterControl inline.
      const m = window.expeditionMap
      if (!m) return
      const row = mapStore.activeMap?.map || {}
      const vp = row.viewport
      const parsed = typeof vp === 'string' ? (() => { try { return JSON.parse(vp) } catch { return null } })() : vp
      if (parsed && Array.isArray(parsed.center)) {
        m.flyTo({ center: parsed.center, zoom: parsed.zoom || 4, duration: 1200, essential: true })
      }
    })(); close() } },
    { id: 'cmd:layers', label: 'Toggle layers panel', meta: 'command', run: () => { ui.toggleLeftPanel('layers'); close() } },
    { id: 'cmd:tools', label: 'Toggle tools panel', meta: 'command', run: () => { ui.toggleRightPanel('tools'); close() } },
  ]
  for (const c of cmds) {
    if (!s || c.label.toLowerCase().includes(s)) items.push(c)
  }

  return items
}

watch([q, recent, templates, activeLayers, zones], () => {
  flatList.value = buildFlat()
  cursor.value = 0
}, { immediate: true })

onMounted(async () => {
  await nextTick()
  inputEl.value?.focus()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

function close() { ui.commandKOpen = false }
function onKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); close(); return }
  if (e.key === 'ArrowDown') { e.preventDefault(); cursor.value = Math.min(flatList.value.length - 1, cursor.value + 1); scrollIntoView() }
  if (e.key === 'ArrowUp') { e.preventDefault(); cursor.value = Math.max(0, cursor.value - 1); scrollIntoView() }
  if (e.key === 'Enter') { e.preventDefault(); const item = flatList.value[cursor.value]; if (item) item.run() }
}
function scrollIntoView() {
  // The list element scrolls; the watcher isn't reactive to cursor,
  // so just nudge after the next tick.
  nextTick(() => {
    const el = document.querySelector('.cp__item[data-active="true"]')
    if (el) el.scrollIntoView({ block: 'nearest' })
  })
}

// Sections for the rendered groups. Just visual headers — the flat
// list still drives cursor + Enter.
const sections = computed(() => {
  const out = []
  const byKind = (k) => flatList.value.filter((i) => i.kind === k)
  const maps = byKind('map')
  const layers = byKind('layer')
  const zones = byKind('zone')
  const cmds = byKind('cmd') // kind: 'cmd'
  if (maps.length) out.push({ title: 'Maps', items: maps })
  if (layers.length) out.push({ title: 'Layers', items: layers })
  if (zones.length) out.push({ title: 'Zones', items: zones })
  if (cmds.length) out.push({ title: 'Commands', items: cmds })
  return out
})
</script>

<template>
  <div class="cp" @click.self="close">
    <div class="cp__panel" role="dialog" aria-label="Command palette">
      <input
        ref="inputEl"
        v-model="q"
        placeholder="Jump to map, layer, zone, or command…"
        class="cp__input"
        @keydown.stop
      />
      <div class="cp__list" v-if="flatList.length">
        <template v-for="sec in sections" :key="sec.title">
          <div class="cp__section">{{ sec.title }}</div>
          <button
            v-for="(it, idx) in sec.items"
            :key="it.id"
            type="button"
            class="cp__item"
            :data-active="flatList.indexOf(it) === cursor"
            @click="it.run()"
            @mousemove="cursor = flatList.indexOf(it)"
          >
            <span class="cp__item-label">{{ it.label }}</span>
            <span class="cp__item-meta">{{ it.meta }}</span>
          </button>
        </template>
      </div>
      <div v-else class="cp__empty">No matches.</div>
      <div class="cp__foot">
        <span><kbd>↑↓</kbd> navigate</span>
        <span><kbd>↵</kbd> run</span>
        <span><kbd>esc</kbd> close</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cp {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 14vh;
  z-index: 1100;
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}
.cp__panel {
  width: 600px; max-width: 92vw;
  background: rgba(11, 14, 20, 0.94);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 14px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.55);
  overflow: hidden;
  color: #E6E8EC;
  display: flex; flex-direction: column;
  max-height: 70vh;
}
.cp__input {
  width: 100%;
  border: 0; outline: 0;
  background: transparent;
  padding: 18px 20px;
  font-size: 16px;
  color: #E6E8EC;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  font-family: inherit;
}
.cp__input::placeholder { color: rgba(230, 232, 236, 0.4); }
.cp__list { overflow-y: auto; padding: 6px 0; }
.cp__section {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(230, 232, 236, 0.4); padding: 10px 20px 4px;
}
.cp__item {
  display: flex; align-items: center; justify-content: space-between;
  width: 100%;
  padding: 8px 20px;
  background: transparent;
  border: 0;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  border-radius: 0;
}
.cp__item[data-active="true"] {
  background: rgba(59, 130, 246, 0.18);
}
.cp__item-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.cp__item-meta { color: rgba(230, 232, 236, 0.5); font-size: 11px; margin-left: 12px; flex: none; }
.cp__empty { padding: 24px; font-size: 13px; color: rgba(230, 232, 236, 0.5); text-align: center; }
.cp__foot {
  display: flex; gap: 14px;
  padding: 10px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 10px;
  color: rgba(230, 232, 236, 0.4);
}
.cp__foot kbd {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: inherit;
  font-size: 10px;
  margin-right: 4px;
}
</style>