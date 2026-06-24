<script setup>
/**
 * SearchBar — global cross-layer search input.
 *
 * Sits at the top-center of the canvas. Parses queries like:
 *
 *   customer:title:acme           -> doctype:field:value
 *   lead:lead_owner:foo@bar.com   -> doctype:field:value
 *   customer:outstanding:>20000   -> doctype:field:operator:value
 *   lead:status:in:Open,Quoted    -> doctype:field:operator:value
 *   acme                          -> bare value (matches label_field across all layers,
 *                                    or if only one layer is visible, its first label
 *                                    field)
 *
 * On submit, builds a `Frappe filter list` per matched layer and calls
 * `layerStore.refetchLayer(name)`. The server-side `get_features` reads
 * `filter_json` from the layer doc, so we update the layer's filter_json
 * first (cached in the store, not persisted — search is session-only).
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useLayersStore } from '../state/layers.js'
import { useMapStore } from '../state/map.js'
import { useUiStore } from '../state/ui.js'

const layers = useLayersStore()
const mapStore = useMapStore()
const ui = useUiStore()

const open = ref(false)
const value = ref('')
const error = ref('')
const results = ref(null) // { layerName, n } or null
const focused = ref(false)
const inputEl = ref(null)

// Sync with global ui.searchOpen so toolbar button opens us.
watch(() => ui.searchOpen, (val) => {
  open.value = val
  if (val) {
    focused.value = true
    // Focus the input after it mounts. requestAnimationFrame waits
    // past the click's default focus side-effect on the toolbar
    // button, and a second rAF is needed for the v-if to actually
    // commit before the ref is non-null.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { inputEl.value?.focus() })
    })
  }
})
// When we close internally, also update global state.
function close() {
  open.value = false
  focused.value = false
  ui.closeSearch()
}

const visibleLayers = computed(() =>
  (layers.layers || []).filter((l) => l.enabled !== false && l.enabled !== 0)
)

function onKey(e) {
  // Ctrl/Cmd+K focuses search (handled globally elsewhere too).
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault()
    open.value = true
    focused.value = true
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { inputEl.value?.focus() })
    })
  } else if (e.key === 'Escape' && open.value) {
    e.preventDefault()
    close()
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

/**
 * Parse a query line like "doctype:field:op:value" or "field:value".
 * Returns null on parse failure. Operators recognized: =, !=, >, <,
 * >=, <=, like, in (followed by comma-separated list).
 */
function parseQuery(q) {
  q = q.trim()
  if (!q) return null
  const parts = q.split(':')
  if (parts.length === 1) {
    return { bare: true, value: parts[0] }
  }
  if (parts.length === 2) {
    return { field: parts[0].trim(), op: 'like', value: parts[1].trim() }
  }
  if (parts.length >= 3) {
    const op = parts[1].trim().toLowerCase()
    const value = parts.slice(2).join(':').trim()
    return { doctype: parts[0].trim(), field: parts[1].trim(), op, value }
  }
  return null
}

function opToFrappe(op, value) {
  switch ((op || 'like').toLowerCase()) {
    case '=': case 'eq':       return ['=', value]
    case '!=': case 'ne':      return ['!=', value]
    case '>': case 'gt':       return ['>', value]
    case '<': case 'lt':       return ['<', value]
    case '>=': case 'gte':     return ['>=', value]
    case '<=': case 'lte':     return ['<=', value]
    case 'in':                  return ['in', value.split(',').map((s) => s.trim()).filter(Boolean)]
    case 'like':
    default:                    return ['like', `%${value}%`]
  }
}

async function run() {
  error.value = ''
  results.value = null
  const raw = value.value.trim()
  if (!raw) return

  // Multi-query supported via newlines.
  const lines = raw.split('\n').map((l) => l.trim()).filter(Boolean)
  const parsed = []
  for (const l of lines) {
    const p = parseQuery(l)
    if (!p) { error.value = `Could not parse: ${l}`; return }
    parsed.push(p)
  }

  // Resolve doctype -> layer names that match. For bare queries with
  // exactly one visible layer, treat as field-less like across that
  // layer's label_field (or fall back to `name`).
  const layerFilters = new Map() // layerName -> Frappe filter list
  for (const p of parsed) {
    let candidates
    if (p.bare) {
      candidates = visibleLayers.value
      if (candidates.length === 1) {
        const l = candidates[0]
        const field = (l.label_field || 'name').trim() || 'name'
        const flt = layerFilters.get(l.name) || []
        flt.push([field, 'like', `%${p.value}%`])
        layerFilters.set(l.name, flt)
      }
    } else {
      const dt = p.doctype.toLowerCase()
      candidates = visibleLayers.value.filter(
        (l) => (l.source_doctype || '').toLowerCase() === dt
      )
      if (!candidates.length) {
        // doctype didn't match — try treating `doctype` as the field
        candidates = visibleLayers.value
        for (const l of candidates) {
          const flt = layerFilters.get(l.name) || []
          flt.push([p.doctype, ...opToFrappe(p.op || 'like', p.value)])
          layerFilters.set(l.name, flt)
        }
        continue
      }
      for (const l of candidates) {
        const flt = layerFilters.get(l.name) || []
        flt.push([p.field, ...opToFrappe(p.op || 'like', p.value)])
        layerFilters.set(l.name, flt)
      }
    }
  }

  // Apply filters and refetch.
  let total = 0
  for (const [layerName, flt] of layerFilters.entries()) {
    layers._searchFilter = { layerName, filter: flt } // ephemeral; see below
    // We update the layer's local filter_rows so the popup keeps it
    // for the rest of the session (not persisted to server).
    const l = layers.layers.find((x) => x.name === layerName)
    if (l) {
      l.filter_rows = flt.map(([field, op, value]) => ({ field, op, value }))
      // Match the server's filter_json format.
      l.filter_json = JSON.stringify(flt)
    }
    const fc = await layers.fetchFeatures(layerName, layers.lastBounds[layerName])
    if (fc && Array.isArray(fc.features)) total += fc.features.length
  }
  results.value = { matched: Array.from(layerFilters.keys()), total }
}

function clear() {
  value.value = ''
  error.value = ''
  results.value = null
  // Restore: clear filter on every visible layer.
  for (const l of visibleLayers.value) {
    if (l.filter_rows && l.filter_rows.length) {
      l.filter_rows = []
      l.filter_json = ''
      layers.refetchLayer(l.name)
    }
  }
}
</script>

<template>
  <div v-if="open" class="sb" role="search">
    <div class="sb__inner" :class="{ 'sb__inner--focused': focused }">
      <svg class="sb__icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path d="M11 4a7 7 0 1 0 4.95 11.95L21 21 M11 4a7 7 0 0 1 7 7"
              fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      </svg>
      <input
        ref="inputEl"
        v-model="value"
        class="sb__input"
        :placeholder="visibleLayers.length === 1
          ? 'Search ' + (visibleLayers[0].title || visibleLayers[0].source_doctype) + ' (e.g. title:acme, status:in:Open)'
          : 'Search — doctype:field:op:value (e.g. customer:title:acme)'"
        autocomplete="off"
        spellcheck="false"
        @focus="focused = true"
        @blur="focused = false"
        @keydown.enter.prevent="run()"
      />
      <button v-if="value" type="button" class="sb__clear" @click="clear()" aria-label="Clear">×</button>
      <button type="button" class="sb__close" @click="close()" aria-label="Close search">esc</button>
    </div>
    <p v-if="error" class="sb__error">{{ error }}</p>
    <p v-else-if="results" class="sb__hint">
      {{ results.total }} feature{{ results.total === 1 ? '' : 's' }} matched across {{ results.matched.length }} layer{{ results.matched.length === 1 ? '' : 's' }}
    </p>
    <p v-else class="sb__hint">
      <span v-if="visibleLayers.length === 1">Only one layer visible — doctype is optional. Field-only query searches the layer's label field.</span>
      <span v-else>Multi-line supported. Operators: <code>=</code>, <code>!=</code>, <code>&gt;</code>, <code>&lt;</code>, <code>in</code>, <code>like</code> (default).</span>
    </p>
  </div>
</template>

<style scoped>
.sb {
  position: fixed;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  width: min(560px, 92vw);
  z-index: 1200;
  pointer-events: auto;
  font-family: 'Inter', system-ui, sans-serif;
}
.sb__inner {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px;
  background: rgba(11, 14, 20, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
}
.sb__inner--focused {
  border-color: rgba(59, 130, 246, 0.6);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4), 0 0 0 3px rgba(59, 130, 246, 0.18);
}
.sb__icon { color: rgba(230, 232, 236, 0.7); flex: none; }
.sb__input {
  flex: 1;
  background: transparent;
  border: 0;
  color: #E6E8EC;
  font-family: inherit;
  font-size: 13px;
  outline: none;
}
.sb__input::placeholder { color: rgba(230, 232, 236, 0.4); }
.sb__clear {
  background: rgba(255, 255, 255, 0.06);
  border: 0;
  color: rgba(230, 232, 236, 0.75);
  border-radius: 6px;
  width: 22px; height: 22px;
  cursor: pointer;
  font-size: 14px; line-height: 1;
}
.sb__close {
  background: rgba(255, 255, 255, 0.06);
  border: 0;
  color: rgba(230, 232, 236, 0.55);
  border-radius: 6px;
  padding: 0 8px;
  height: 22px;
  cursor: pointer;
  font-size: 10px;
  font-family: inherit;
}
.sb__hint, .sb__error {
  margin: 6px 4px 0;
  font-size: 11px;
  color: rgba(230, 232, 236, 0.5);
}
.sb__error { color: #FCA5A5; }
.sb__hint code {
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  padding: 0 4px;
  font-size: 10px;
}
</style>
