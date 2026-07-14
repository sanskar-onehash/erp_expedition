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
const CHROME_LAYOUT_VERSION = 3
const DEFAULT_CHROME_LAYOUT = {
  map: { col: 0, row: 0, anchorX: 'start', anchorY: 'start' },
  toolsPrimary: { col: 0, row: 5, anchorX: 'start', anchorY: 'start' },
  toolsStyle: { col: 0, row: 24, anchorX: 'start', anchorY: 'start' },
  list: { col: 0, row: 0, anchorX: 'end', anchorY: 'center' },
  search: { col: 4, row: 0, anchorX: 'end', anchorY: 'start' },
  settings: { col: 0, row: 0, anchorX: 'end', anchorY: 'start' },
  fit: { col: 0, row: 10, anchorX: 'end', anchorY: 'end' },
  tilt: { col: 0, row: 5, anchorX: 'end', anchorY: 'end' },
  layout: { col: 8, row: 0, anchorX: 'end', anchorY: 'end' },
  visibility: { col: 4, row: 0, anchorX: 'end', anchorY: 'end' },
  basemap: { col: 0, row: 0, anchorX: 'end', anchorY: 'end' },
  coords: { col: 0, row: 0, anchorX: 'start', anchorY: 'end' },
  legend: { col: 0, row: 0, anchorX: 'center', anchorY: 'end' },
}

// User-level preferences. Every key is a real personalization
// setting — not a per-map state. Persisted to localStorage. `blurOnPanel`
// is kept as a top-level ref for back-compat (it was the first such
// preference and is mirrored into prefs.blurOnPanel).
const DEFAULT_PREFS = {
  // Map
  defaultZoom: 3,
  defaultPitch: 0,
  tiltJoystickInverted: true,
  // Panels
  blurOnPanel: true,        // preferred default: ON — dims the map behind open panels
  autoCloseOthers: true,    // opening Layers closes Tools
  showLegend: true,
  // Pins & popups
  clickBehavior: 'popup',   // 'popup' | 'select' | 'fly'

  // Units & cursor
  coordUnits: 'decimal',    // 'decimal' | 'dms'
  distanceUnits: 'km',      // 'km' | 'mi' | 'nm'
  cursor: 'crosshair',      // 'default' | 'pointer' | 'dot' | 'circle' | 'cross' | 'crosshair'
  // UI scale: 'xs' | 's' | 'm' (default) | 'lg' | 'xlg'.
  // Drives the size of floating toolbar buttons and similar chrome.
  toolbarSize: 'm',
  chromeLayoutVersion: CHROME_LAYOUT_VERSION,
  chromeLayout: DEFAULT_CHROME_LAYOUT,
  // Startup
  openRecentOnLaunch: true,
  showTemplatesOnEmpty: true,
}

function cloneDefaultPrefs() {
  return JSON.parse(JSON.stringify(DEFAULT_PREFS))
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
  const layoutCustomizing = ref(false)
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
  const selectedZone = ref(null)
  function selectZone(zone) {
    selectedZone.value = zone || null
    if (zone) selectedFeature.value = null
  }
  function clearSelectedZone() { selectedZone.value = null }

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

  // Quiet-canvas panels (PR-2). Both closed at rest.
  const leftPanel = ref('closed') // 'closed' | 'layers'
  function openLeftPanel(name) { leftPanel.value = name }
  function closeLeftPanel() { leftPanel.value = 'closed' }
  function toggleLeftPanel(name) { leftPanel.value = leftPanel.value === name ? 'closed' : name }

  // Settings popover (top-right cog). Distinct from a panel because it
  // doesn't slide in from an edge; it anchors to its trigger.
  const settingsOpen = ref(false)
  const settingsInitialTab = ref(null)
  const settingsTabRequest = ref(0)
  function openSettings() { settingsOpen.value = true }
  function openSettingsTab(tab) {
    settingsInitialTab.value = tab || null
    settingsTabRequest.value += 1
    settingsOpen.value = true
  }
  function closeSettings() { settingsOpen.value = false }
  function toggleSettings() { settingsOpen.value = !settingsOpen.value }
  function toggleSettingsTab(tab) {
    settingsInitialTab.value = tab || null
    settingsTabRequest.value += 1
    settingsOpen.value = !settingsOpen.value
  }

  // Keyboard shortcut hint overlay. Holding Alt reveals the shortcut
  // on the hovered button; Shift+Alt pins every visible front-button
  // shortcut at once.
  const shortcutAltDown = ref(false)
  const shortcutHintsAll = ref(false)
  function setShortcutModifiers(event) {
    shortcutAltDown.value = !!event?.altKey
    shortcutHintsAll.value = !!event?.altKey && !!event?.shiftKey
  }
  function clearShortcutModifiers() {
    shortcutAltDown.value = false
    shortcutHintsAll.value = false
  }

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
  const prefs = ref({ ...cloneDefaultPrefs(), ...readLs(PREFS_LS_KEY, {}) })
  if (prefs.value.chromeLayoutVersion !== CHROME_LAYOUT_VERSION) {
    prefs.value.chromeLayout = cloneDefaultPrefs().chromeLayout
    prefs.value.chromeLayoutVersion = CHROME_LAYOUT_VERSION
  }
  prefs.value.chromeLayout = normalizeChromeLayout(prefs.value.chromeLayout)
  if (prefs.value.cursor === 'grab' || !VALID_CURSOR_VALUES.has(prefs.value.cursor)) {
    prefs.value = { ...prefs.value, cursor: 'crosshair' }
  }
  watch(prefs, (v) => writeLs(PREFS_LS_KEY, v), { deep: true })
  function setPref(key, value) {
    if (!(key in DEFAULT_PREFS)) return
    if (key === 'cursor' && !VALID_CURSOR_VALUES.has(value)) value = DEFAULT_PREFS.cursor
    if (key === 'chromeLayout') value = normalizeChromeLayout(value)
    if (key === 'chromeLayoutVersion') value = CHROME_LAYOUT_VERSION
    prefs.value = { ...prefs.value, [key]: value }
  }
  function resetSettings() {
    prefs.value = cloneDefaultPrefs()
    blurOnPanel.value = DEFAULT_PREFS.blurOnPanel
  }
  function normalizeChromeLayout(layout) {
    const next = {}
    for (const [key, fallback] of Object.entries(DEFAULT_CHROME_LAYOUT)) {
      const item = layout?.[key] || {}
      next[key] = {
        col: clampGridInt(item.col, fallback.col, -200, 200),
        row: clampGridInt(item.row, fallback.row, -120, 120),
        anchorX: ['start', 'center', 'end'].includes(item.anchorX) ? item.anchorX : fallback.anchorX,
        anchorY: ['start', 'center', 'end'].includes(item.anchorY) ? item.anchorY : fallback.anchorY,
      }
    }
    return next
  }
  function clampGridInt(value, fallback, min, max) {
    const n = Number(value)
    if (!Number.isFinite(n)) return fallback
    return Math.max(min, Math.min(max, Math.round(n)))
  }
  function setChromeLayoutItem(key, patch = {}) {
    if (!(key in DEFAULT_CHROME_LAYOUT)) return
    const current = prefs.value.chromeLayout?.[key] || DEFAULT_CHROME_LAYOUT[key]
    const merged = normalizeChromeLayout({ ...prefs.value.chromeLayout, [key]: { ...current, ...patch } })
    prefs.value = { ...prefs.value, chromeLayout: merged }
  }
  function resetChromeLayout() {
    prefs.value = {
      ...prefs.value,
      chromeLayoutVersion: CHROME_LAYOUT_VERSION,
      chromeLayout: normalizeChromeLayout(DEFAULT_CHROME_LAYOUT),
    }
  }
  function setLayoutCustomizing(on) {
    layoutCustomizing.value = !!on
    if (layoutCustomizing.value) chromeHidden.value = false
  }
  function toggleLayoutCustomizing() {
    setLayoutCustomizing(!layoutCustomizing.value)
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
    layoutCustomizing.value = false
    chromeSnapshot.value = {
      leftPanel: leftPanel.value,
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

  // Canvas drawing / zones. Draw tools are map-first, not panel-first:
  // a drawn area can be saved as a zone. Polygon is point-to-point;
  // circle/rectangle use a start point + live pointer preview.
  const drawMode = ref('off')
  const draftVertices = ref([])
  const draftPointer = ref(null)
  const draftSnapIndex = ref(-1)
  const zoneDraftTitle = ref('')
  const drawingColor = ref('#3B82F6')
  const drawingStrokeColor = ref('#1E40AF')
  const drawingFillOpacity = ref(0.22)
  const drawingStrokeWidth = ref(2)
  const drawingStrokeStyle = ref('solid')
  const zoneEditMode = ref(false)
  function setZoneEditMode(on) {
    zoneEditMode.value = !!on
    if (!zoneEditMode.value) clearSelectedZone()
  }
  function toggleZoneEditMode() {
    setZoneEditMode(!zoneEditMode.value)
  }
  function startDrawTool(tool) {
    if (!['polygon', 'circle', 'rectangle'].includes(tool)) return
    drawMode.value = tool
    draftVertices.value = []
    draftPointer.value = null
    draftSnapIndex.value = -1
  }
  function startDrawPolygon() {
    startDrawTool('polygon')
  }
  function cancelDraw() {
    drawMode.value = 'off'
    draftVertices.value = []
    draftPointer.value = null
    draftSnapIndex.value = -1
  }
  function pushDraftVertex(lngLat) {
    draftVertices.value = [...draftVertices.value, lngLat]
  }
  function setDraftPointer(lngLat, snapIndex = -1) {
    draftPointer.value = lngLat || null
    draftSnapIndex.value = Number.isInteger(snapIndex) ? snapIndex : -1
  }
  function undoDraftVertex() {
    draftVertices.value = draftVertices.value.slice(0, -1)
  }
  function setDrawingColor(color) {
    drawingColor.value = color || '#3B82F6'
  }
  function setDrawingStrokeColor(color) {
    drawingStrokeColor.value = color || '#1E40AF'
  }
  function setDrawingFillOpacity(value) {
    const n = Number(value)
    drawingFillOpacity.value = Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0.22
  }
  function setDrawingStrokeWidth(value) {
    const n = Number(value)
    drawingStrokeWidth.value = Number.isFinite(n) ? Math.max(1, Math.min(8, n)) : 2
  }
  function setDrawingStrokeStyle(value) {
    drawingStrokeStyle.value = ['solid', 'dashed', 'dotted'].includes(value) ? value : 'solid'
  }

  // GPS Timeline playback. Session-only — not persisted.
  // timelineEnabled gates the TimelineSlider widget. When the slider
  // is open the layers store filters features by _time via setTimeline().
  const timelineEnabled = ref(false)
  function enableTimeline() { timelineEnabled.value = true }
  function disableTimeline() { timelineEnabled.value = false }
  function toggleTimeline() { timelineEnabled.value = !timelineEnabled.value }

  // Map Cards — named snapshots of the active layer set.
  // Stored in localStorage so they survive page reloads.
  // Each card: { id, title, createdAt, layers: [{name, title, enabled, filter_json}] }
  const CARDS_LS_KEY = 'expedition.mapCards'
  const mapCards = ref(readLs(CARDS_LS_KEY, []))
  function saveMapCard(title, layerStates) {
    const card = {
      id: 'card_' + Date.now(),
      title: String(title || 'Untitled').trim() || 'Untitled',
      createdAt: Date.now(),
      layers: layerStates,
    }
    mapCards.value = [card, ...mapCards.value].slice(0, 20)
    writeLs(CARDS_LS_KEY, mapCards.value)
    return card
  }
  function deleteMapCard(id) {
    mapCards.value = mapCards.value.filter((c) => c.id !== id)
    writeLs(CARDS_LS_KEY, mapCards.value)
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
    layoutCustomizing,
    setLayoutCustomizing,
    toggleLayoutCustomizing,
    setChromeLayoutItem,
    resetChromeLayout,
    leftRailOpen,
    bottomDrawerOpen,
    basemapSkins,
    currentSkinId,
    previewSkinId,
    setPreviewSkin,
    commitPreviewSkin,
    cancelPreviewSkin,
    selectedFeature,
    selectedZone,
    selectZone,
    clearSelectedZone,
    drawMode,
    draftVertices,
    draftPointer,
    draftSnapIndex,
    zoneDraftTitle,
    drawingColor,
    drawingStrokeColor,
    drawingFillOpacity,
    drawingStrokeWidth,
    drawingStrokeStyle,
    zoneEditMode,
    setZoneEditMode,
    toggleZoneEditMode,
    startDrawTool,
    startDrawPolygon,
    cancelDraw,
    pushDraftVertex,
    setDraftPointer,
    undoDraftVertex,
    setDrawingColor,
    setDrawingStrokeColor,
    setDrawingFillOpacity,
    setDrawingStrokeWidth,
    setDrawingStrokeStyle,
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
    openLeftPanel,
    closeLeftPanel,
    toggleLeftPanel,
    settingsOpen,
    settingsInitialTab,
    settingsTabRequest,
    openSettings,
    openSettingsTab,
    closeSettings,
    toggleSettings,
    toggleSettingsTab,
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
    shortcutAltDown,
    shortcutHintsAll,
    setShortcutModifiers,
    clearShortcutModifiers,
    timelineEnabled,
    enableTimeline,
    disableTimeline,
    toggleTimeline,
    mapCards,
    saveMapCard,
    deleteMapCard,
  }
})
