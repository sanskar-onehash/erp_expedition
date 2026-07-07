<script setup>
/**
 * SearchBar — global cross-layer search input.
 *
 * Sits at the top-center of the canvas. Search is session-only and
 * filters the currently loaded map pins without mutating layer filters.
 *
 * Supported forms:
 *   acme                    -> global value search across loaded fields
 *   count:<1000             -> field filter across doctypes with `count`
 *   lead:count:>=10000      -> doctype-scoped field filter
 *   lead_owner:adslk%       -> wildcard string filter
 *   creation:2026-07        -> date/month filter
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'

const layers = useLayersStore()
const ui = useUiStore()

const open = ref(false)
const value = ref('')
const error = ref('')
const loading = ref(false)
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
const activeSearch = computed(() => layers.activeSearch)
const parsedChips = computed(() => activeSearch.value?.chips || [])

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

async function run() {
  error.value = ''
  const raw = value.value.trim()
  if (!raw) {
    layers.clearSearch()
    return
  }
  loading.value = true
  try {
    await layers.applySearch(raw)
    requestAnimationFrame(() => {
      window.dispatchEvent(new CustomEvent('expedition:fit-data', {
        detail: { mode: 'all' },
      }))
    })
  } catch (e) {
    console.error('[expedition] map search failed', e)
    error.value = e?.message || 'Search failed.'
  } finally {
    loading.value = false
  }
}

function clear() {
  value.value = ''
  error.value = ''
  layers.clearSearch()
}

function chipLabel(expression) {
  if (!expression) return ''
  if (expression.mode === 'text') return `"${expression.value}"`
  const prefix = expression.mode === 'doctype_field' ? `${expression.doctype}:` : ''
  const op = expression.operator || ':'
  return `${prefix}${expression.field} ${op === ':' ? 'contains' : op} ${expression.value}`
}

function chipClass(expression) {
  return {
    'sb__chip--text': expression?.mode === 'text',
    'sb__chip--field': expression?.mode === 'field' || expression?.mode === 'doctype_field',
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
          ? 'Search ' + (visibleLayers[0].title || visibleLayers[0].source_doctype) + ' (e.g. count:<1000, owner:me)'
          : 'Search pins (e.g. slkdj, count:<1000, lead:creation:2026-07)'"
        autocomplete="off"
        spellcheck="false"
        @focus="focused = true"
        @blur="focused = false"
        @keydown.enter.prevent="run()"
      />
      <button v-if="value" type="button" class="sb__clear" @click="clear()" aria-label="Clear">×</button>
      <button type="button" class="sb__run" :disabled="loading" @click="run()">{{ loading ? '…' : 'go' }}</button>
      <button type="button" class="sb__close" @click="close()" aria-label="Close search">esc</button>
    </div>
    <p v-if="error" class="sb__error">{{ error }}</p>
    <p v-else-if="activeSearch" class="sb__hint">
      <span v-if="activeSearch.total === 0" class="sb__empty">No pins matched this search.</span>
      <span v-else>{{ activeSearch.total }} pin{{ activeSearch.total === 1 ? '' : 's' }} visible.</span>
      <span class="sb__summary">{{ activeSearch.summary }}</span>
    </p>
    <p v-else class="sb__hint">
      <span>Plain text searches every loaded field. Spaces combine filters with AND; use <code>OR</code> for alternatives.</span>
    </p>
    <div v-if="parsedChips.length" class="sb__chips" aria-label="Parsed search filters">
      <span
        v-for="(chip, idx) in parsedChips"
        :key="idx + ':' + chip.raw"
        class="sb__chip"
        :class="chipClass(chip)"
      >
        {{ chipLabel(chip) }}
      </span>
    </div>
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
.sb__run,
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
.sb__run {
  color: rgba(230, 232, 236, 0.8);
  min-width: 28px;
}
.sb__run:disabled {
  cursor: default;
  opacity: 0.65;
}
.sb__hint, .sb__error {
  margin: 6px 4px 0;
  font-size: 11px;
  color: rgba(230, 232, 236, 0.5);
}
.sb__summary {
  margin-left: 4px;
}
.sb__empty {
  color: #FCA5A5;
}
.sb__error { color: #FCA5A5; }
.sb__hint code {
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  padding: 0 4px;
  font-size: 10px;
}
.sb__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 7px 4px 0;
}
.sb__chip {
  min-width: 0;
  max-width: 100%;
  padding: 3px 7px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(230, 232, 236, 0.78);
  font-size: 10px;
  line-height: 1.25;
  overflow-wrap: anywhere;
}
.sb__chip--field {
  border-color: rgba(59, 130, 246, 0.24);
}
.sb__chip--text {
  border-color: rgba(16, 185, 129, 0.24);
}
</style>
