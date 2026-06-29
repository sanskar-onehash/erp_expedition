/**
 * UI store — global UI state that doesn't belong to a specific domain.
 * Kept tiny: if a piece of state is used in only one place, it should
 * live in that component, not here.
 */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { SKINS, DEFAULT_SKIN_ID } from '../api/skins.js'

const SKIN_LS_KEY = 'expedition.skinId'
const RECENT_LS_KEY = 'expedition.recent'
const BLUR_LS_KEY = 'expedition.blurOnPanel'
const PREFS_LS_KEY = 'expedition.prefs'
const RECENT_MAX = 5
const VALID_CURSOR_VALUES = new Set(['default', 'pointer', 'dot', 'circle', 'cross', 'crosshair'])

// User-level preferences. Every key is a real personalization
// setting — not a per-map state. Persisted to localStorage. `blurOnPanel`
// is kept as a top-level ref for back-compat (it was the first such
// preference and is mirrored into prefs.blurOnPanel).
const DEFAULT_PREFS = {
  // Map
  defaultZoom: 3,
  defaultPitch: 0,
  // Panels
  blurOnPanel: true,        // preferred default: ON — dims the map behind open panels
  autoCloseOthers: true,    // opening Layers closes Tools
  showLegend: true,
  // Pins & popups
  labelDensity: 'hover',    // 'off' | 'hover' | 'always'
  popupAnchor: 'auto',      // 'auto' | 'top' | 'right' | 'bottom' | 'left'
  clickBehavior: 'popup',   // 'popup' | 'select' | 'fly'
  // Overlays
  showCompass: false,
  showScale: true,
  showMinimap: false,
  // Units & cursor
  coordUnits: 'decimal',    // 'decimal' | 'dms'
  distanceUnits: 'km',      // 'km' | 'mi' | 'nm'
  cursor: 'crosshair',      // 'default' | 'pointer' | 'dot' | 'circle' | 'cross' | 'crosshair'
  // UI scale: 'xs' | 's' | 'm' (default) | 'lg' | 'xlg'.
  // Drives the size of floating toolbar buttons and similar chrome.
  toolbarSize: 'm',
  // Startup
  openRecentOnLaunch: true,
  showTemplatesOnEmpty: true,
}

function readLs(key, fallback) {
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw)
  } catch (_) {
    return fallback
  }
}

function writeLs(key, value) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch (_) {
    /* quota / private mode — ignore */
  }
}

export const useUiStore = defineStore('ui', () => {
  const commandKOpen = ref(false)
  const leftRailOpen = ref(true)
  const bottomDrawerOpen = ref(true)
  // Basemap style: a key into SKINS (e.g. 'ofm-liberty'). The legacy
  // values 'light' / 'dark' / 'monochrome' are also accepted for maps
  // saved before the gallery existed; we resolve them on read.
  const basemapSkins = ref(SKINS)
  const currentSkinId = ref(
    readLs(SKIN_LS_KEY, DEFAULT_SKIN_ID)
  )
  watch(currentSkinId, (id) => writeLs(SKIN_LS_KEY, id))

  // Transient: set by Basemap.vue on pin click, cleared on Esc/×.
  // Shape: { layer: {...} (from get_features response), properties: {...} }
  const selectedFeature = ref(null)

  // Recent maps the user opened. Array of { name, title, openedAt }.
  // Used by the switcher. Persisted to localStorage.
  const recent = ref(readLs(RECENT_LS_KEY, []))
  function rememberRecent(map) {
    if (!map || !map.name) return
    const next = [
      { name: map.name, title: map.title || map.name, basemap_style: map.basemap_style, openedAt: Date.now() },
      ...recent.value.filter((r) => r.name !== map.name),
    ].slice(0, RECENT_MAX)
    recent.value = next
    writeLs(RECENT_LS_KEY, next)
  }

  // Zone tag filter: an array of tag strings to keep. Empty array means
  // "show all zones" (no filter). The Zone source feature expression
  // reads this via a watcher; the ToolsPanel also reads it to filter
  // the zone list. Persisted to localStorage so users don't have to
  // re-pick on every session.
  const ZONE_TAGS_LS_KEY = 'expedition.zoneTags'
  const zoneTags = ref(readLs(ZONE_TAGS_LS_KEY, []))
  function setZoneTags(tags) {
    zoneTags.value = Array.isArray(tags) ? tags.slice() : []
    writeLs(ZONE_TAGS_LS_KEY, zoneTags.value)
  }
  function toggleZoneTag(tag) {
    if (!tag) return
    const cur = zoneTags.value
    setZoneTags(cur.includes(tag) ? cur.filter((t) => t !== tag) : [...cur, tag])
  }
  function clearZoneTags() { setZoneTags([]) }

  // Right-click context menu on the map. When set, the
  // ContextMenu component reads { x, y, lat, lng } and shows
  // quick actions: save view, drop pin, copy coords, start polygon.
  const contextMenu = ref(null)
  function openContextMenu(x, y, lat, lng) {
    contextMenu.value = { x, y, lat, lng, openedAt: Date.now() }
  }
  function closeContextMenu() { contextMenu.value = null }

  // Loading state for the initial basemap style fetch. Set by Basemap.vue
  // when it calls setStyle(), cleared on 'load'.
  const basemapLoading = ref(false)

  // Incrementing counter: how many layer fetches are in flight. The
  // bottom-right "loading" pill is visible iff this is > 0.
  const fetchingFeatures = ref(0)
  function beginFetch() { fetchingFeatures.value++ }
  function endFetch() {
    if (fetchingFeatures.value > 0) fetchingFeatures.value--
  }

  // Picker preview: when the user hovers a skin in the picker, the skin
  // id is set here; the actual basemap is changed. On commit, the picked
  // skin id is written to currentSkinId (which persists). On cancel
  // (mouseout without click), the basemap reverts to currentSkinId.
  const previewSkinId = ref(null)
  function setPreviewSkin(id) {
    previewSkinId.value = id
  }
  function commitPreviewSkin() {
    if (previewSkinId.value) {
      currentSkinId.value = previewSkinId.value
      previewSkinId.value = null
    }
  }
  function cancelPreviewSkin() {
    previewSkinId.value = null
  }

  // Layer editor / add modal state. v1.1.
  // editorOpen: { mode: 'create' | 'edit', layer?: {...}, asMaster?: bool } | null
  // asMaster is read in create mode and controls whether the new layer
  // is saved with map=NULL (master) or with map=<active map> (instance).
  const editorOpen = ref(null)
  function openLayerEditor(layer = null, opts = {}) {
    if (layer) {
      editorOpen.value = { mode: 'edit', layer }
    } else {
      editorOpen.value = { mode: 'create', asMaster: !!opts.asMaster }
    }
  }
  function closeLayerEditor() {
    editorOpen.value = null
  }

  // Global command bar (top search)
  const searchOpen = ref(false)
  const searchValue = ref('')
  function openSearch() { searchOpen.value = true }
  function closeSearch() { searchOpen.value = false; searchValue.value = '' }
  function toggleSearch() {
    if (searchOpen.value) closeSearch()
    else openSearch()
  }

  // Quiet-canvas panels (PR-2). Both closed at rest. PR-3 will add
  // 'tools'. The stack policy (`autoCloseOthers`) is implemented in
  // PR-3 once we have multiple same-edge panels to coordinate.
  const leftPanel = ref('closed') // 'closed' | 'layers'
  const rightPanel = ref('closed') // 'closed' | 'tools'
  function openLeftPanel(name) { leftPanel.value = name }
  function closeLeftPanel() { leftPanel.value = 'closed' }
  function toggleLeftPanel(name) { leftPanel.value = leftPanel.value === name ? 'closed' : name }
  function openRightPanel(name) { rightPanel.value = name }
  function closeRightPanel() { rightPanel.value = 'closed' }
  function toggleRightPanel(name) { rightPanel.value = rightPanel.value === name ? 'closed' : name }

  // Settings popover (top-right cog). Distinct from a panel because it
  // doesn't slide in from an edge; it anchors to its trigger.
  const settingsOpen = ref(false)
  function openSettings() { settingsOpen.value = true }
  function closeSettings() { settingsOpen.value = false }
  function toggleSettings() { settingsOpen.value = !settingsOpen.value }

  // Heatmap per-layer toggle (Phase 2, PR-7). Session-only — not
  // persisted to the layer DocType yet. Map: layerName -> bool.
  const heatmapEnabled = ref({})
  function isHeatmapOn(layerName) { return !!heatmapEnabled.value[layerName] }
  function setHeatmap(layerName, on) {
    heatmapEnabled.value = { ...heatmapEnabled.value, [layerName]: !!on }
  }
  function toggleHeatmap(layerName) { setHeatmap(layerName, !isHeatmapOn(layerName)) }

  // Radius rendering (Phase 2, PR-8). Per-layer toggle + an optional
  // source field whose numeric value drives the halo size in pixels.
  // Session-only. radiusMeters is the fallback when no field is set.
  const radiusEnabled = ref({})
  const radiusField = ref({})      // layerName -> fieldname or ''
  const radiusMeters = ref({})     // layerName -> number (px-scale basis)
  const radiusScale = ref({})      // layerName -> number (multiplier)
  function isRadiusOn(layerName) { return !!radiusEnabled.value[layerName] }
  function setRadius(layerName, on) {
    radiusEnabled.value = { ...radiusEnabled.value, [layerName]: !!on }
  }
  function toggleRadius(layerName) { setRadius(layerName, !isRadiusOn(layerName)) }
  function setRadiusField(layerName, field) {
    radiusField.value = { ...radiusField.value, [layerName]: field || '' }
  }
  function setRadiusMeters(layerName, m) {
    radiusMeters.value = { ...radiusMeters.value, [layerName]: Number(m) || 5000 }
  }
  function setRadiusScale(layerName, s) {
    radiusScale.value = { ...radiusScale.value, [layerName]: Number(s) || 1 }
  }

  // User-level preferences (settings panel). Mirror blurOnPanel on
  // read so the two stay in sync. Persisted to localStorage.
  const prefs = ref({ ...DEFAULT_PREFS, ...readLs(PREFS_LS_KEY, {}) })
  if (prefs.value.cursor === 'grab' || !VALID_CURSOR_VALUES.has(prefs.value.cursor)) {
    prefs.value = { ...prefs.value, cursor: 'crosshair' }
  }
  watch(prefs, (v) => writeLs(PREFS_LS_KEY, v), { deep: true })
  function setPref(key, value) {
    if (!(key in DEFAULT_PREFS)) return
    if (key === 'cursor' && !VALID_CURSOR_VALUES.has(value)) value = DEFAULT_PREFS.cursor
    prefs.value = { ...prefs.value, [key]: value }
  }
  function resetSettings() {
    prefs.value = { ...DEFAULT_PREFS }
    blurOnPanel.value = DEFAULT_PREFS.blurOnPanel
  }

  // Map blur on panel open. Default ON (preferred default) — when a
  // panel is open the user is doing something with it, so the map
  // context fades. Mirrored into prefs.blurOnPanel for the settings
  // panel. Backed by localStorage. Declared after `prefs` so the
  // watcher can mirror values without a forward-reference error.
  const blurOnPanel = ref(readLs(BLUR_LS_KEY, DEFAULT_PREFS.blurOnPanel))
  if (prefs.value.blurOnPanel !== blurOnPanel.value) {
    prefs.value = { ...prefs.value, blurOnPanel: blurOnPanel.value }
  }
  watch(blurOnPanel, (v) => {
    writeLs(BLUR_LS_KEY, v)
    if (prefs.value.blurOnPanel !== v) {
      prefs.value = { ...prefs.value, blurOnPanel: v }
    }
  })
  function toggleBlur() { blurOnPanel.value = !blurOnPanel.value }

  // Chrome-hidden mode. When on, every chrome element except the
  // eye button itself fades out — only the map remains. Session-only
  // (no localStorage); closing the page resets.
  // When toggled on, capture which panels were open so we can
  // restore them on toggle-off. We don't auto-close them — they
  // stay mounted; the visibility is gated by the hidden flag.
  const chromeHidden = ref(false)
  // Snapshot of which panels were open at the moment of hiding.
  // Restored when the user un-hides.
  const chromeSnapshot = ref(null)
  function hideChrome() {
    if (chromeHidden.value) return
    chromeSnapshot.value = {
      leftPanel: leftPanel.value,
      rightPanel: rightPanel.value,
      editorOpen: editorOpen.value,
      searchOpen: searchOpen.value,
      commandKOpen: commandKOpen.value,
      measureMode: measureMode.value,
    }
    chromeHidden.value = true
  }
  function showChrome() {
    if (!chromeHidden.value) return
    const snap = chromeSnapshot.value
    chromeHidden.value = false
    if (snap) {
      leftPanel.value = snap.leftPanel
      rightPanel.value = snap.rightPanel
      editorOpen.value = snap.editorOpen
      searchOpen.value = snap.searchOpen
      commandKOpen.value = snap.commandKOpen
      measureMode.value = snap.measureMode
      chromeSnapshot.value = null
    }
  }
  function toggleChrome() {
    if (chromeHidden.value) showChrome()
    else hideChrome()
  }

  // 3D pitch (Phase 2, PR-9). Camera state — applies globally, not
  // per-layer. extrusionHeight/Field are per-layer because different
  // layers naturally extrude on different fields.
  // 3D is implicit: any non-zero degree means on. Setting 0 disables it.
  const pitchDegrees = ref(0)
  const pitchEnabled = computed(() => pitchDegrees.value > 0)
  const extrusionHeight = ref({})      // layerName -> number (meters)
  const extrusionField = ref({})       // layerName -> fieldname
  function setPitch(deg) {
    const n = Math.max(0, Math.min(80, Number(deg) || 0))
    pitchDegrees.value = n
  }
  function setExtrusionHeight(layerName, h) {
    extrusionHeight.value = { ...extrusionHeight.value, [layerName]: Number(h) || 200 }
  }
  function setExtrusionField(layerName, f) {
    extrusionField.value = { ...extrusionField.value, [layerName]: f || '' }
  }

  // Measure tool (Phase 2, PR-11). 'line' | 'polygon' | null.
  const measureMode = ref(null)
  function startMeasure(kind) { measureMode.value = kind === measureMode.value ? null : kind }
  function cancelMeasure() { measureMode.value = null }

  // Lasso / box-select (Alt+drag). selection is a flat array of
  // { layerName, id, properties, lng, lat } — one entry per
  // selected feature. Cleared when the active map changes, when
  // Esc is pressed, or when a new lasso starts.
  const selection = ref([])
  // lassoBox is a transient rect while the user is dragging:
  // { x0, y0, x1, y1 } in screen px (CSS), or null when idle.
  const lassoBox = ref(null)
  // Count by layer, derived. Read by the selection chip.
  const selectionByLayer = ref({})
  function setSelection(items) {
    const arr = Array.isArray(items) ? items : []
    selection.value = arr
    const counts = {}
    for (const s of arr) counts[s.layerName] = (counts[s.layerName] || 0) + 1
    selectionByLayer.value = counts
  }
  function clearSelection() {
    selection.value = []
    selectionByLayer.value = {}
  }
  function startLasso(box) { lassoBox.value = box }
  function updateLasso(box) { lassoBox.value = box }
  function endLasso() { lassoBox.value = null }

  // Zone drawing. v1: polygon only. 'off' = no tool active,
  // 'polygon' = next click adds a vertex; 'polygon-finish' = the
  // last click closes the ring. Cleared on commit / cancel.
  const drawMode = ref('off')
  const draftVertices = ref([])
  // Title the user is typing for the polygon being drawn. Read by
  // Basemap.vue on dblclick (instead of `window.prompt`). Cleared on
  // commit / cancel so a stale value never leaks into the next draw.
  const zoneDraftTitle = ref('')
  function startDrawPolygon() {
    drawMode.value = 'polygon'
    draftVertices.value = []
    zoneDraftTitle.value = ''
  }
  function cancelDraw() {
    drawMode.value = 'off'
    draftVertices.value = []
    zoneDraftTitle.value = ''
  }
  function pushDraftVertex(lngLat) {
    draftVertices.value = [...draftVertices.value, lngLat]
  }

  // In-app confirm modal — replaces window.confirm. Any component
  // can call `ask({ title, body, ... })` and get a Promise<boolean>.
  // The result resolves when the user clicks a button in the global
  // ConfirmModal mounted in App.vue. While a question is pending,
  // asking again replaces the current one (the older promise is
  // rejected with false so callers don't hang).
  //
  // We keep two pieces of state: the request (drives the modal's
  // visuals) and the resolver (the pending promise's resolve fn).
  // Splitting them keeps the API simple — a single ask() call.
  const confirmRequest = ref(null)
  let confirmResolver = null

  function ask(opts) {
    // If a previous question is unanswered, resolve it as false so
    // the caller doesn't hang.
    if (confirmResolver) {
      try { confirmResolver(false) } catch (_) { /* ignore */ }
      confirmResolver = null
    }
    confirmRequest.value = {
      title: opts.title || 'Are you sure?',
      body: opts.body || '',
      confirmLabel: opts.confirmLabel || 'Confirm',
      cancelLabel: opts.cancelLabel || 'Cancel',
      destructive: !!opts.destructive,
    }
    return new Promise((resolve) => { confirmResolver = resolve })
  }
  function resolveConfirm(ok) {
    if (!confirmResolver) {
      confirmRequest.value = null
      return
    }
    const r = confirmResolver
    confirmResolver = null
    confirmRequest.value = null
    try { r(!!ok) } catch (_) { /* ignore */ }
  }

  return {
    commandKOpen,
    leftRailOpen,
    bottomDrawerOpen,
    basemapSkins,
    currentSkinId,
    previewSkinId,
    setPreviewSkin,
    commitPreviewSkin,
    cancelPreviewSkin,
    selectedFeature,
    drawMode,
    draftVertices,
    zoneDraftTitle,
    startDrawPolygon,
    cancelDraw,
    pushDraftVertex,
    recent,
    rememberRecent,
    zoneTags,
    setZoneTags,
    toggleZoneTag,
    clearZoneTags,
    contextMenu,
    openContextMenu,
    closeContextMenu,
    basemapLoading,
    fetchingFeatures,
    beginFetch,
    endFetch,
    editorOpen,
    openLayerEditor,
    closeLayerEditor,
    searchOpen,
    openSearch,
    toggleSearch,
    closeSearch,
    searchValue,
    leftPanel,
    rightPanel,
    openLeftPanel,
    closeLeftPanel,
    toggleLeftPanel,
    openRightPanel,
    closeRightPanel,
    toggleRightPanel,
    settingsOpen,
    openSettings,
    closeSettings,
    toggleSettings,
    heatmapEnabled,
    isHeatmapOn,
    setHeatmap,
    toggleHeatmap,
    radiusEnabled,
    radiusField,
    radiusMeters,
    radiusScale,
    isRadiusOn,
    setRadius,
    toggleRadius,
    setRadiusField,
    setRadiusMeters,
    setRadiusScale,
    pitchEnabled,
    pitchDegrees,
    extrusionHeight,
    extrusionField,
    setPitch,
    setExtrusionHeight,
    setExtrusionField,
    measureMode,
    startMeasure,
    cancelMeasure,
    prefs,
    setPref,
    resetSettings,
    chromeHidden,
    chromeSnapshot,
    hideChrome,
    showChrome,
    toggleChrome,
    selection,
    lassoBox,
    selectionByLayer,
    setSelection,
    clearSelection,
    startLasso,
    updateLasso,
    endLasso,
    confirmRequest,
    ask,
    resolveConfirm,
  }
})
