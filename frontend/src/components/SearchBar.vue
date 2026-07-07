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
const metaDismissed = ref(false)

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
    metaDismissed.value = false
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
  metaDismissed.value = false
  layers.clearSearch()
}

function closeMeta() {
  metaDismissed.value = true
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
  </div>
  <div v-if="open && (error || activeSearch) && !metaDismissed" class="sb__meta">
    <button type="button" class="sb__meta-close" aria-label="Hide search details" @click="closeMeta">×</button>
    <p v-if="error" class="sb__error">{{ error }}</p>
    <p v-else-if="activeSearch" class="sb__hint">
      <span v-if="activeSearch.total === 0" class="sb__empty">No pins matched this search.</span>
      <span v-else>{{ activeSearch.total }} pin{{ activeSearch.total === 1 ? '' : 's' }} visible</span>
    </p>
    <p v-if="activeSearch?.summary" class="sb__summary">{{ activeSearch.summary }}</p>
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
.sb__meta {
  position: fixed;
  left: 50%;
  bottom: 54px;
  transform: translateX(-50%);
  width: fit-content;
  max-width: 92vw;
  padding: 9px 34px 9px 11px;
  background: rgba(11, 14, 20, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.34);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  z-index: 1201;
  text-align: center;
}
.sb__hint, .sb__error {
  margin: 0;
  max-width: min(560px, calc(92vw - 52px));
  font-size: 11px;
  line-height: 1.35;
  color: rgba(246, 247, 249, 0.86);
}
.sb__summary {
  margin: 5px 0 0;
  max-width: min(560px, calc(92vw - 52px));
  font-size: 10px;
  line-height: 1.35;
  color: rgba(230, 232, 236, 0.68);
  text-align: center;
}
.sb__empty {
  color: #FCA5A5;
}
.sb__error { color: #FCA5A5; }
.sb__meta-close {
  position: absolute;
  top: 7px;
  right: 8px;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(246, 247, 249, 0.78);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}
.sb__meta-close:hover {
  background: rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.95);
}
</style>
