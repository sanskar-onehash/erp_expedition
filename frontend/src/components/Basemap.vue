<script setup>
/**
 * Basemap — the MapLibre canvas. v1 visual: Felt-tier pins, hover/select
 * via feature-state, flyTo on map open, scale bar, loading overlay.
 *
 * Source / layer id convention (deterministic, stable across renders):
 *   source:           src-<layerName>           (with promoteId: '_id')
 *   shadow:           lyr-<layerName>-shadow    (dark, offset (0, 2), blurred)
 *   white ring:       lyr-<layerName>-ring      (crisp 1.5px white border)
 *   pin body:         lyr-<layerName>           (vibrant fill)
 *   cluster circle:   lyr-<layerName>-clusters
 *   cluster count:    lyr-<layerName>-cluster-count
 *
 * Each feature gets a stable _id so feature-state can track "selected".
 * get_features already returns _id on every feature (we add it server-
 * side if missing). When a pin is clicked we set feature-state.selected
 * = true on it; the halo layer's stroke-width keys on that state.
 *
 * Click handling:
 *   - pin click  → set ui.selectedFeature → open right rail
 *   - cluster    → zoom to cluster expansion
 *
 * Skin / basemap:
 *   - The current skin comes from ui.currentSkinId (or ui.previewSkinId
 *     during hover-preview). The skin is resolved to a MapLibre style
 *     spec via the SKINS gallery.
 *   - We rebuild the style with map.setStyle() and re-add all our sources
 *     on 'styledata'. setStyle wipes our custom layers.
 */
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import maplibregl from 'maplibre-gl'
import { useMapStore } from '../state/map.js'
import { useLayersStore } from '../state/layers.js'
import { useUiStore } from '../state/ui.js'
import { useZonesStore } from '../state/zones.js'
import { getSkin, resolveStyleUrl } from '../api/skins.js'
import { registerIcons } from '../api/icons.js'

// Pin radii. Apple/Google-tier: default `m` is 11px (22px disk
// visually with the white ring). xs/s are for dense layers; l/xl
// for primary layers.
const SIZE_TO_RADIUS = { xs: 7, s: 9, m: 11, l: 14, xl: 18 }
// Punchy Apple-red fallback so even unconfigured layers read as
// primary data points.
const FALLBACK_COLOR = '#FF3B30'

/**
 * Compute the lowest zoom at which a single Mercator world exactly
 * fills the canvas without wrapping or repeating. A Mercator world is
 * a 512px tile wide at zoom 0, scaling as 512 * 2^z at zoom z, and is
 * square in pixel space (high latitudes are clamped to ~85°). So the
 * limiting dimension is the smaller of the canvas sides — at the
 * returned zoom, the world just fits inside the shorter side. Any
 * further zoom-out would either repeat the world (with
 * renderWorldCopies=true) or leave a void (with it off).
 */
function computeGlobeFitZoom(width, height) {
  if (!width || !height) return 0
  const TILE_PX = 512
  return Math.max(0, Math.log2(Math.min(width, height) / TILE_PX))
}

function sourceId(layerName) { return `src-${layerName}` }
function shadowLayerId(layerName) { return `lyr-${layerName}-shadow` }
function ringLayerId(layerName) { return `lyr-${layerName}-ring` }
function layerId(layerName) { return `lyr-${layerName}` }
function clusterLayerId(layerName) { return `lyr-${layerName}-clusters` }
function clusterCountId(layerName) { return `lyr-${layerName}-cluster-count` }
function iconLayerId(layerName) { return `lyr-${layerName}-icon` }
function heatmapLayerId(layerName) { return `lyr-${layerName}-heatmap` }
function haloLayerId(layerName) { return `lyr-${layerName}-halo` }
function extrusionLayerId(layerName) { return `lyr-${layerName}-extrude` }
function extrusionSourceId(layerName) { return `src-${layerName}-extrude` }

// MapLibre's fill-extrusion only renders polygons, but Expedition
// layers are Point sources. To get a 3D column per pin, we synthesize
// a tiny 10m×10m square footprint polygon at each point's lat/lng and
// feed those to the extrusion layer. The conversion uses a fixed
// degree offset, which is good enough at the spatial scales we render
// (city / region) and keeps the column footprint visually constant.
const EXTRUSION_FOOTPRINT_DEG = 0.0001   // ≈ 11m at the equator
function _extrusionPolygonForPoint(lng, lat) {
  const d = EXTRUSION_FOOTPRINT_DEG
  return {
    type: 'Polygon',
    coordinates: [[
      [lng - d, lat - d],
      [lng + d, lat - d],
      [lng + d, lat + d],
      [lng - d, lat + d],
      [lng - d, lat - d],
    ]],
  }
}
function _extrusionFeatureCollection(pointFc, layerName) {
  const field = ui.extrusionField[layerName]
  return {
    type: 'FeatureCollection',
    features: (pointFc.features || []).map((f) => {
      const [lng, lat] = (f.geometry && f.geometry.coordinates) || []
      if (lng == null || lat == null) return null
      const props = { ...(f.properties || {}) }
      if (f._id != null) props._id = f._id
      return { type: 'Feature', geometry: _extrusionPolygonForPoint(lng, lat), properties: props }
    }).filter(Boolean),
  }
}

const mapEl = ref(null)
const ui = useUiStore()
const mapStore = useMapStore()
const layerStore = useLayersStore()
const zoneStore = useZonesStore()
let map = null
let unsubscribeFeatures = null
let lastLoadedFeatures = {}  // layer.name -> last FeatureCollection, for re-add on styledata
let unsubscribeZones = null
let unsubscribeZoneTags = null

// Tracks whether each visible layer has ever been fetched with the
// current viewport. On first paint we deliberately pass `null` bounds
// to `get_features` so the server returns up to 5000 rows from the
// whole source table (the viewport is just `[78.96, 20.59] z4` at
// boot — almost certainly outside the data envelope, so a bounded
// first fetch returns zero features and the canvas stays empty
// until the user moves the map).
let _layerFetchedOnce = new Set()

function _resetFirstFetch() {
  _layerFetchedOnce = new Set()
}

// Zone source/layer ids. All zones live in a single source so we
// don't pay addSource/removeSource overhead per draw.
function zoneSourceId() { return 'src-zones' }
function zoneFillLayerId() { return 'lyr-zones-fill' }
function zoneStrokeLayerId() { return 'lyr-zones-stroke' }
function zoneLabelLayerId() { return 'lyr-zones-label' }
function zoneDraftLayerId() { return 'lyr-zones-draft' }

// True while a polygon draw is in progress and doubleClickZoom has been
// disabled. Module-scope so the bare `map.on('click', ...)` handler and
// the `watch(() => ui.drawMode, ...)` cleanup branch share state.
let _dblClickDisabled = false

const activeSkinId = computed(() => ui.previewSkinId || ui.currentSkinId)

/**
 * Resolve a skin entry to a MapLibre style spec URL or a full inline
 * style object. Vector-style and local-style return a URL (the
 * browser fetches the spec). Raster entries are wrapped into a
 * minimal inline style spec.
 */
function resolveStyle(skin) {
  if (!skin) return null
  if (skin.kind === 'vector-style' || skin.kind === 'local-style') {
    return resolveStyleUrl(skin)
  }
  // Raster: build a minimal style spec.
  return {
    version: 8,
    sources: skin.sources,
    layers: skin.layers,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  }
}

/**
 * Pin paint — Apple/Google-tier.
 *
 *   Layer order (bottom → top):
 *     1. shadow        — dark circle, radius+3, offset (0, 2) in pixels,
 *                        blur 1.2, opacity 0.45. Real drop shadow, not
 *                        a halo. Without this the pin looks "stuck on"
 *                        the map.
 *     2. white ring    — circle at radius+1.5, color #fff, full opacity.
 *                        Apple-style crisp border that separates the
 *                        pin from the basemap regardless of skin.
 *     3. pin body      — radius, vibrant color, full opacity. The
 *                        "wow" surface.
 *
 *   Selected: pin body scales 1.25× (radius*1.25) and white ring widens
 *   from 1.5px to 2.5px. No glow — that's iOS 6, not iOS 18.
 */
function shadowPaint(radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      (radius + 3) * 1.18,
      radius + 3,
    ],
    'circle-color': '#000000',
    'circle-opacity': 0.45,
    'circle-blur': 1.2,
    'circle-translate': [0, 2],
    'circle-translate-anchor': 'viewport',
  }
}

function ringPaint(radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      (radius + 1.5) * 1.18,
      radius + 1.5,
    ],
    'circle-color': '#FFFFFF',
    'circle-stroke-color': 'rgba(0,0,0,0.12)',
    'circle-stroke-width': 0.5,
    'circle-opacity': 1,
  }
}

function pinPaint(color, radius) {
  return {
    'circle-radius': [
      'case',
      ['boolean', ['feature-state', 'selected'], false],
      radius * 1.18,
      radius,
    ],
    'circle-color': color,
    'circle-opacity': 1,
  }
}

/**
 * Cluster paint — Apple-style rounded-square equivalent.
 *
 * For v1 we still use a circle (MapLibre `symbol` with a text label).
 * We use a step radius that grows with point count so 5000+ pins
 * don't dwarf 50, and we keep the white ring + drop shadow so clusters
 * feel like the same family as single pins (Apple's visual rule).
 */
function clusterPaint(color) {
  return {
    'circle-radius': [
      'step',
      ['get', 'point_count'],
      16, 10,
      20, 50,
      26, 200,
      32,
    ],
    'circle-color': color,
    'circle-stroke-color': '#FFFFFF',
    'circle-stroke-width': 2.5,
    'circle-opacity': 1,
    'circle-translate': [0, 1],
    'circle-translate-anchor': 'viewport',
  }
}

/**
 * Heatmap paint — PR-7.
 *
 * Uses MapLibre's built-in `heatmap` layer type. Two-tier weight via
 * the optional `weight_field` (numeric property); falls back to a
 * constant weight when no field is set, so a plain point layer still
 * renders as a usable density map.
 *
 *   - Radius scales with zoom so the blob stays roughly the same on
 *     screen (10 → 30px from z0 to z12). Apple/Google-tier "always
 *     readable" feel.
 *   - Intensity scales with zoom too, so far-out views stay soft and
 *     close-up views pop.
 *   - Color ramp is the user's layer color → transparent. We feed the
 *     heatmap `heatmap-color` an interpolate expression over the layer
 *     color so it matches the rest of the chrome.
 */
function heatmapPaint(color) {
  // 5-stop ramp: transparent → light tint → mid → full color → bright.
  // Built from a single base hue by varying alpha; interpolation between
  // rgba stops handles the gradient.
  const c = hexToRgb(color || FALLBACK_COLOR)
  return {
    'heatmap-weight': 1,
    'heatmap-intensity': [
      'interpolate', ['linear'], ['zoom'],
      0, 1,
      12, 2.5,
    ],
    'heatmap-radius': [
      'interpolate', ['linear'], ['zoom'],
      0, 10,
      6, 18,
      12, 30,
    ],
    'heatmap-opacity': [
      'interpolate', ['linear'], ['zoom'],
      0, 0.85,
      12, 0.7,
    ],
    'heatmap-color': [
      'interpolate', ['linear'], ['heatmap-density'],
      0,   'rgba(0, 0, 0, 0)',
      0.2, `rgba(${c.r}, ${c.g}, ${c.b}, 0.25)`,
      0.5, `rgba(${c.r}, ${c.g}, ${c.b}, 0.55)`,
      0.8, `rgba(${c.r}, ${c.g}, ${c.b}, 0.85)`,
      1,   `rgba(${Math.min(255, c.r + 60)}, ${Math.min(255, c.g + 60)}, ${Math.min(255, c.b + 60)}, 1)`,
    ],
  }
}

function hexToRgb(hex) {
  const h = (hex || '').replace('#', '')
  if (h.length !== 6) return { r: 255, g: 59, b: 48 }
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  }
}

/**
 * Extrusion paint — PR-9 3D / pitch.
 *
 * A `fill-extrusion` layer that turns each point into a column. The
 * base height is either a constant (set via ui.extrusionHeight) or a
 * per-feature numeric property (ui.extrusionField). The fill uses the
 * layer's color at ~85% opacity; the top face is slightly brighter so
 * the column reads as a solid object, not a flat rectangle.
 *
 * `fill-extrusion-base` is at 0 so the columns stand on the ground.
 * `fill-extrusion-vertical-gradient` lights the side facing the
 * camera (MapLibre's approximation of "sun from above").
 */
function extrusionPaint(color, layerName) {
  const field = ui.extrusionField[layerName]
  const fallback = ui.extrusionHeight[layerName] || 200
  return {
    'fill-extrusion-color': color,
    'fill-extrusion-opacity': 0.85,
    'fill-extrusion-height': field
      ? ['coalesce', ['to-number', ['get', field]], fallback]
      : fallback,
    'fill-extrusion-base': 0,
    'fill-extrusion-vertical-gradient': true,
  }
}

/**
 * Halo paint — PR-8 radius rendering.
 *
 * The halo is a translucent disk drawn behind the pin stack. Its
 * radius scales with zoom and (optionally) a per-feature numeric
 * property, so a layer with a `service_radius_km` field gets a halo
 * proportional to that value. Without a field, a fixed halo in
 * pixel-equivalent units (configurable via ui.radiusMeters) is used.
 *
 * Note: MapLibre circle-radius is in pixels, not meters. We approximate
 * meters-to-pixels via a per-zoom curve calibrated so that at zoom 10
 * (city-block scale), 1000 "units" ≈ 1000 pixels on a 96dpi screen.
 * The exact conversion is a known compromise; the visual goal is "a
 * bigger number reads as a bigger disk" rather than survey-grade
 * accuracy.
 */
function haloPaint(color, layerName) {
  const field = ui.radiusField[layerName]
  const fallback = ui.radiusMeters[layerName] || 5000
  const scale = ui.radiusScale[layerName] || 1
  // Per-layer opacity. Falls back to 0.18 if the layer doc didn't
  // ship the field (older saved maps).
  const layerDoc = layerStore.layers.find((l) => l.name === layerName)
  const opacity = (layerDoc && layerDoc.radius_opacity != null)
    ? Number(layerDoc.radius_opacity) || 0.18
    : 0.18
  // Pixel-equivalent radius: take the property (or fallback), divide
  // by 10 so a typical 5km radius renders as ~25px on a city zoom,
  // then multiply by the per-zoom scale so far-out zooms grow.
  const base = field
    ? ['coalesce', ['to-number', ['get', field]], fallback]
    : fallback
  return {
    'circle-radius': [
      'interpolate', ['linear'], ['zoom'],
      // base is in "units" (e.g. meters or km as the user chose).
      // We scale down for pixel-pleasant rendering. Users wanting
      // different scaling can change the per-layer multiplier.
      0,  ['/', base, 200],
      6,  ['/', base, 80],
      10, ['/', base, 40],
      14, ['/', base, 20],
    ],
    'circle-color': color,
    'circle-opacity': opacity * scale,
    'circle-stroke-color': color,
    'circle-stroke-width': 1,
    'circle-stroke-opacity': Math.min(1, opacity * 2.5) * scale,
    'circle-pitch-alignment': 'map',
  }
}

function _addLayerOnMap(layerName) {
  // `isStyleLoaded()` returns false when basemap tile sources are
  // still loading, which can take 1-2s after a `setStyle`. But
  // addSource/addLayer only need the style *object* to be loaded,
  // not the sources. `map.getStyle()` returns null until the style
  // is parseable; once it returns a non-null object we can safely
  // add sources and layers to it.
  if (!map || !map.getStyle()) {
    return
  }
  const fc = layerStore.features[layerName]
  if (!fc) return
  const layerDoc = layerStore.layers.find((l) => l.name === layerName)
  if (!layerDoc) return
  const style = layerStore.getLayerStyle(layerName) || {}
  const color = style.color || layerDoc.color || FALLBACK_COLOR
  const sizeKey = style.size || layerDoc.size || 'm'
  const radius = SIZE_TO_RADIUS[sizeKey] || SIZE_TO_RADIUS.m
  const wantsCluster = !!(style.cluster || layerDoc.cluster)
  const enabled = layerDoc.enabled !== false

  const sid = sourceId(layerName)
  const lid = layerId(layerName)
  const shid = shadowLayerId(layerName)
  const rid = ringLayerId(layerName)

  const sourceSpec = {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: fc.features || [] },
    promoteId: '_id',
  }
  if (wantsCluster) {
    sourceSpec.cluster = true
    sourceSpec.clusterRadius = 40
    sourceSpec.clusterMaxZoom = 12
  }

  const existing = map.getSource(sid)
  if (existing && existing.type === 'geojson') {
    existing.setData(sourceSpec.data)
  } else {
    map.addSource(sid, sourceSpec)
  }
  lastLoadedFeatures[layerName] = fc

  // Z-order: shadow → ring → pin. Shadow first so it renders behind.
  // 1. Shadow
  if (!map.getLayer(shid)) {
    map.addLayer({
      id: shid,
      type: 'circle',
      source: sid,
      filter: wantsCluster ? ['!', ['has', 'point_count']] : ['all'],
      paint: shadowPaint(radius),
    })
  }
  // 2. White ring
  if (!map.getLayer(rid)) {
    map.addLayer({
      id: rid,
      type: 'circle',
      source: sid,
      filter: wantsCluster ? ['!', ['has', 'point_count']] : ['all'],
      paint: ringPaint(radius),
    })
  }
  // 3. Pin body
  if (!map.getLayer(lid)) {
    map.addLayer({
      id: lid,
      type: 'circle',
      source: sid,
      filter: wantsCluster ? ['!', ['has', 'point_count']] : ['all'],
      paint: pinPaint(color, radius),
    })
  } else {
    map.setPaintProperty(lid, 'circle-color', color)
    map.setPaintProperty(lid, 'circle-radius', radius)
  }
  // Toggle via opacity (cheap; no add/remove).
  if (enabled) {
    map.setPaintProperty(lid, 'circle-opacity', 1)
    map.setPaintProperty(rid, 'circle-opacity', 1)
    map.setPaintProperty(shid, 'circle-opacity', 0.45)
  } else {
    map.setPaintProperty(lid, 'circle-opacity', 0)
    map.setPaintProperty(rid, 'circle-opacity', 0)
    map.setPaintProperty(shid, 'circle-opacity', 0)
  }

  // 4. Icon glyph (optional). Sits above the pin body so it reads as
  // "icon over dot", Apple/Google-tier. Only for non-clustered pins —
  // clusters already have their own visual language.
  const iid = iconLayerId(layerName)
  const iconName = style.icon || layerDoc.icon
  if (iconName && !wantsCluster) {
    // Register the sprite image on the map (no-op if already present).
    registerIcons(map, [iconName], 28).then(() => {
      if (!map.getLayer(iid)) {
        map.addLayer({
          id: iid,
          type: 'symbol',
          source: sid,
          filter: ['!', ['has', 'point_count']],
          layout: {
            'icon-image': iconName,
            // The icon's viewBox is 24x24 with ~14px glyph; we want the
            // glyph to fit inside radius-1 on a screen-anchored pin.
            'icon-size': [
              'interpolate', ['linear'], ['zoom'],
              2, 0.55,
              6, 0.75,
              12, 0.95,
            ],
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
            'icon-anchor': 'center',
            // White glyph — the colored pin body provides contrast.
            // For dark icons the user picked, the pin body color
            // controls whether the icon reads (dark pin + white icon
            // is always legible).
          },
          paint: {
            'icon-color': '#FFFFFF',
            'icon-opacity': enabled ? 1 : 0,
          },
        })
      } else {
        // Layer exists; refresh the icon image and color.
        map.setLayoutProperty(iid, 'icon-image', iconName)
        map.setPaintProperty(iid, 'icon-opacity', enabled ? 1 : 0)
      }
    }).catch((e) => {
      console.warn('[expedition] icon register failed', iconName, e)
    })
  } else if (map.getLayer(iid)) {
    map.removeLayer(iid)
  }

  // Cluster layers
  const cid = clusterLayerId(layerName)
  const ccid = clusterCountId(layerName)
  if (wantsCluster) {
    if (!map.getLayer(cid)) {
      map.addLayer({
        id: cid,
        type: 'circle',
        source: sid,
        filter: ['has', 'point_count'],
        paint: clusterPaint(color),
      })
    } else {
      map.setPaintProperty(cid, 'circle-color', color)
    }
    if (!map.getLayer(ccid)) {
      map.addLayer({
        id: ccid,
        type: 'symbol',
        source: sid,
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 11,
          'text-font': ['Noto Sans Regular'],
        },
        paint: { 'text-color': '#FFFFFF' },
      })
    }
    map.on('click', cid, onClusterClick)
  } else {
    if (map.getLayer(ccid)) map.removeLayer(ccid)
    if (map.getLayer(cid)) map.removeLayer(cid)
    map.off('click', cid, onClusterClick)
  }

  // Extrusion / 3D columns (PR-9). Sits BELOW everything else so the
  // pin stack + halos + heatmap all read on top of the columns.
  // MapLibre's fill-extrusion needs polygon geometry, so we maintain
  // a parallel polygon source (one 10m square per point).
  const eid = extrusionLayerId(layerName)
  const esid = extrusionSourceId(layerName)
  const extrudeOn = enabled && ui.pitchEnabled
  if (extrudeOn) {
    const polyFc = _extrusionFeatureCollection(fc, layerName)
    const esrc = map.getSource(esid)
    if (esrc && esrc.type === 'geojson') {
      esrc.setData(polyFc)
    } else {
      map.addSource(esid, { type: 'geojson', data: polyFc, promoteId: '_id' })
    }
    if (!map.getLayer(eid)) {
      map.addLayer({
        id: eid,
        type: 'fill-extrusion',
        source: esid,
        paint: extrusionPaint(color, layerName),
      })
    } else {
      map.setPaintProperty(eid, 'fill-extrusion-color', color)
      map.setPaintProperty(eid, 'fill-extrusion-height', extrusionPaint(color, layerName)['fill-extrusion-height'])
    }
  } else if (map.getLayer(eid)) {
    map.removeLayer(eid)
    if (map.getSource(esid)) map.removeSource(esid)
  }

  // Halo / radius rendering (PR-8). Drawn between the pin body and
  // the heatmap — it's part of the per-feature visual identity and
  // should sit below heatmap density but above the pin (so a heatmap
  // turn-off reveals pin-on-halo cleanly).
  const haloId = haloLayerId(layerName)
  const radiusOn = enabled && ui.isRadiusOn(layerName)
  if (radiusOn) {
    if (!map.getLayer(haloId)) {
      map.addLayer({
        id: haloId,
        type: 'circle',
        source: sid,
        filter: wantsCluster ? ['!', ['has', 'point_count']] : ['all'],
        paint: haloPaint(color, layerName),
      })
    } else {
      map.setPaintProperty(haloId, 'circle-color', color)
      map.setPaintProperty(haloId, 'circle-radius', haloPaint(color, layerName)['circle-radius'])
    }
  } else if (map.getLayer(haloId)) {
    map.removeLayer(haloId)
  }

  // Heatmap (PR-7). Adds a heatmap layer above the source and
  // (importantly) above the cluster circles when those exist, so the
  // density visualization dominates. When heatmap is on, the pin body
  // layers go invisible (they would otherwise crowd the gradient).
  const hid = heatmapLayerId(layerName)
  const heatOn = enabled && ui.isHeatmapOn(layerName)
  if (heatOn) {
    if (!map.getLayer(hid)) {
      map.addLayer({
        id: hid,
        type: 'heatmap',
        source: sid,
        maxzoom: 14,
        paint: heatmapPaint(color),
      })
    } else {
      map.setPaintProperty(hid, 'heatmap-color', heatmapPaint(color)['heatmap-color'])
    }
    // Hide the pin-stack layers — heatmap replaces them at low zoom.
    map.setPaintProperty(lid, 'circle-opacity', 0)
    map.setPaintProperty(rid, 'circle-opacity', 0)
    map.setPaintProperty(shid, 'circle-opacity', 0)
    if (map.getLayer(iid)) map.setPaintProperty(iid, 'icon-opacity', 0)
    // Clusters still show on top of the heatmap at very low zooms so
    // the user can click into a dense blob.
  } else {
    if (map.getLayer(hid)) map.removeLayer(hid)
    // Restore pin-stack opacity to whatever `enabled` says.
    map.setPaintProperty(lid, 'circle-opacity', enabled ? 1 : 0)
    map.setPaintProperty(rid, 'circle-opacity', enabled ? 1 : 0)
    map.setPaintProperty(shid, 'circle-opacity', enabled ? 0.45 : 0)
    if (map.getLayer(iid)) map.setPaintProperty(iid, 'icon-opacity', enabled ? 1 : 0)
  }

  map.on('click', lid, onPointClick)
  map.on('mouseenter', lid, () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', lid, () => { map.getCanvas().style.cursor = '' })
  // Force a redraw. Without this, on first paint the canvas can
  // appear blank even though the source + layers exist — MapLibre
  // has been observed to skip the next render frame when a source
  // is added during an idle tick.
  if (typeof map.triggerRepaint === 'function') map.triggerRepaint()
}

function onPointClick(e) {
  const f = e.features && e.features[0]
  if (!f) return
  // The clicked layer id is one of: lyr-<name>, lyr-<name>-ring,
  // lyr-<name>-shadow, lyr-<name>-clusters. Strip the prefix and
  // any of the suffixes to recover the layer name.
  const rawId = e.targetLayer || (f.layer && f.layer.id) || ''
  const layerName = rawId
    .replace(/^lyr-/, '')
    .replace(/-(ring|shadow|clusters|cluster-count)$/, '')
  const fc = layerStore.features[layerName]
  const layerMeta = fc?.layer || layerStore.layers.find((l) => l.name === layerName)
  // Clear previous selection on the source, then mark the clicked one.
  const sid = sourceId(layerName)
  const src = map.getSource(sid)
  if (src && f.id != null) {
    // Clear all features' selected state.
    for (const feat of layerStore.features[layerName]?.features || []) {
      if (feat._id != null && feat._id !== f.id) {
        try { map.setFeatureState({ source: sid, id: feat._id }, { selected: false }) } catch (_) {}
      }
    }
    try { map.setFeatureState({ source: sid, id: f.id }, { selected: true }) } catch (_) {}
  }
  ui.selectedFeature = {
    layer: layerMeta || { name: layerName },
    properties: f.properties || {},
    _id: f.id,
    // Capture the click point so MapPopup can anchor + camera-follow.
    _lngLat: { lng: e.lngLat.lng, lat: e.lngLat.lat },
  }
}

async function onClusterClick(e) {
  const cid = (e.targetLayer || (e.features && e.features[0]?.layer?.id) || '')
  const layerName = cid.replace(/^lyr-/, '').replace(/-clusters$/, '')
  const features = map.queryRenderedFeatures(e.point, { layers: [clusterLayerId(layerName)] })
  const clusterId = features[0]?.properties?.cluster_id
  if (clusterId == null) return
  const src = map.getSource(sourceId(layerName))
  if (!src) return
  try {
    const zoom = await src.getClusterExpansionZoom(clusterId)
    map.easeTo({
      center: features[0].geometry.coordinates,
      zoom,
      duration: 500,
      easing: t => 1 - Math.pow(1 - t, 3),
    })
  } catch (_) { /* cluster can race — safe to ignore */ }
}

function _removeLayerFromMap(layerName) {
  if (!map) return
  for (const id of [
    heatmapLayerId(layerName),
    haloLayerId(layerName),
    extrusionLayerId(layerName),
    clusterCountId(layerName),
    clusterLayerId(layerName),
    iconLayerId(layerName),
    layerId(layerName),
    ringLayerId(layerName),
    shadowLayerId(layerName),
  ]) {
    if (map.getLayer(id)) map.removeLayer(id)
  }
  if (map.getSource(sourceId(layerName))) map.removeSource(sourceId(layerName))
}

function _fetchAllVisibleBounds() {
  if (!map || !layerStore.visibleLayers.length) return
  for (const layer of layerStore.visibleLayers) {
    // First fetch per layer per map is unbounded (null) so the
    // server returns up to 5000 rows from the whole source table.
    // Subsequent moveend fetches are viewport-bounded. Without this,
    // the boot-time viewport is almost certainly outside the data
    // envelope and the server returns zero features, leaving the
    // canvas empty until the user moves the map.
    if (!_layerFetchedOnce.has(layer.name)) {
      _layerFetchedOnce.add(layer.name)
      layerStore.fetchFeatures(layer.name, null)
      continue
    }
    const b = map.getBounds()
    layerStore.fetchFeatures(layer.name, {
      south: b.getSouth(),
      west: b.getWest(),
      north: b.getNorth(),
      east: b.getEast(),
    })
  }
}

function _reAddAllLayers() {
  if (!map || !map.isStyleLoaded()) return
  for (const layer of layerStore.layers) {
    if (layerStore.features[layer.name]) {
      _addLayerOnMap(layer.name)
    }
  }
}

// -------- Zones (drawn regions) --------
//
// All zones are merged into a single GeoJSON FeatureCollection per
// map. The fill color is per-feature via `data-color`; the stroke is
// `data-stroke`. We use data-driven paint expressions so each zone
// keeps its own color without needing a layer per zone.

function _zonesFeatureCollection() {
  const mapName = mapStore.activeMap?.map?.name
  const list = (mapName && zoneStore.byMap[mapName]) || []
  // Apply the tag filter: if `ui.zoneTags` is non-empty, only zones
  // whose tag is in the filter list pass through. Empty filter means
  // "show all" (the default).
  const tagFilter = ui.zoneTags || []
  return {
    type: 'FeatureCollection',
    features: list
      .filter((z) => {
        if (!z.geometry || z.zone_type !== 'polygon') return false
        if (tagFilter.length === 0) return true
        if (!z.tag) return false
        return tagFilter.includes(z.tag)
      })
      .map((z) => ({
        type: 'Feature',
        id: z.name,
        geometry: z.geometry,
        properties: {
          name: z.name,
          title: z.title,
          color: z.color || '#3B82F6',
          stroke: z.stroke_color || '#1E40AF',
          fill_opacity: z.fill_opacity ?? 0.25,
          stroke_width: z.stroke_width ?? 2,
        },
      })),
  }
}

function _reAddZones() {
  if (!map || !map.isStyleLoaded()) return
  const fc = _zonesFeatureCollection()
  const sid = zoneSourceId()
  if (map.getSource(sid)) {
    map.getSource(sid).setData(fc)
  } else {
    map.addSource(sid, { type: 'geojson', data: fc, promoteId: 'name' })
  }
  // Fill layer (below labels / pins)
  if (!map.getLayer(zoneFillLayerId())) {
    map.addLayer({
      id: zoneFillLayerId(),
      type: 'fill',
      source: sid,
      paint: {
        'fill-color': ['coalesce', ['get', 'color'], '#3B82F6'],
        'fill-opacity': ['coalesce', ['get', 'fill_opacity'], 0.25],
      },
    })
  }
  // Stroke layer
  if (!map.getLayer(zoneStrokeLayerId())) {
    map.addLayer({
      id: zoneStrokeLayerId(),
      type: 'line',
      source: sid,
      paint: {
        'line-color': ['coalesce', ['get', 'stroke'], '#1E40AF'],
        'line-width': ['coalesce', ['get', 'stroke_width'], 2],
      },
    })
  }
  // Label layer (centroid + title) — uses the centroid we already store
  // server-side so labels stay anchored when the polygon is irregular.
  const mapName = mapStore.activeMap?.map?.name
  const list = (mapName && zoneStore.byMap[mapName]) || []
  // Reuse the same tag filter as the polygon layer.
  const tagFilter = ui.zoneTags || []
  const labelFC = {
    type: 'FeatureCollection',
    features: list
      .filter((z) => {
        if (z.zone_type !== 'polygon') return false
        if (z.centroid_lat == null || z.centroid_lng == null) return false
        if (tagFilter.length === 0) return true
        if (!z.tag) return false
        return tagFilter.includes(z.tag)
      })
      .map((z) => ({
        type: 'Feature',
        id: z.name + '-label',
        geometry: {
          type: 'Point',
          coordinates: [z.centroid_lng, z.centroid_lat],
        },
        // Carry color so the symbol paint expression can pick it up.
        properties: { title: z.title || z.name, color: z.color || '#8B9DB8' },
      })),
  }
  const labelSourceId = 'src-zones-labels'
  if (map.getSource(labelSourceId)) {
    map.getSource(labelSourceId).setData(labelFC)
  } else {
    map.addSource(labelSourceId, {
      type: 'geojson',
      data: labelFC,
    })
  }
  // Build a color lookup so labels can match their zone.
  const zoneColorMap = {}
  for (const z of list) {
    if (z.color) zoneColorMap[z.name] = z.color
  }
  if (!map.getLayer(zoneLabelLayerId())) {
    map.addLayer({
      id: zoneLabelLayerId(),
      type: 'symbol',
      source: labelSourceId,
      layout: {
        'text-field': ['get', 'title'],
        'text-size': 11,
        'text-font': ['Open Sans Regular'],
        'text-allow-overlap': false,
        'text-anchor': 'center',
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.06,
      },
      paint: {
        // Label color matches zone's border color (fallback to gray)
        'text-color': ['coalesce', ['get', 'color'], '#8B9DB8'],
        'text-halo-color': 'rgba(11, 14, 20, 0.7)',
        'text-halo-width': 1.6,
      },
    })
  }
}

function _flyToInitialViewport() {
  return _flyToHome()
}

function _flatCoords(geom) {
  if (!geom) return []
  if (geom.type === 'Point') return [geom.coordinates]
  if (geom.type === 'Polygon') return geom.coordinates.flat()
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2)
  return []
}

/**
 * Frame the active map's data on first render and on map switch.
 * Always fits all enabled layers + zones — saved viewports are
 * ignored, so a fresh map opens framed around its data. The
 * "Fit to all" toolbar button covers the same case after first
 * render.
 */
function _flyToHome() {
  if (!map || !mapStore.activeMap) return
  const allFeats = Object.values(layerStore.features || {}).flatMap(
    (fc) => (fc && fc.features) || []
  )
  const activeMapName = mapStore.activeMap?.map?.name
  if (activeMapName) {
    const list = zoneStore.byMap?.[activeMapName] || []
    for (const z of list) {
      if (z && z.geometry) allFeats.push({ type: 'Feature', geometry: z.geometry })
    }
  }
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity
  for (const f of allFeats) {
    for (const [lng, lat] of _flatCoords(f?.geometry)) {
      if (typeof lng !== 'number' || typeof lat !== 'number') continue
      if (lng < west) west = lng
      if (lng > east) east = lng
      if (lat < south) south = lat
      if (lat > north) north = lat
    }
  }
  // No features in the viewport-bounded cache yet (first render) —
  // fall back to the per-layer *full* envelope from the bounds
  // metadata cache. This is the same data the 'Fit to all' button
  // uses, so first-render framing matches what the user gets on
  // click. Triggered in parallel; the first response wins.
  if (!isFinite(west) || !isFinite(east) || east - west < 0.001) {
    for (const lyr of layerStore.visibleLayers || []) {
      const b = layerStore.bounds?.[lyr.name]
      if (!b || typeof b.south !== 'number' || b.count === 0) continue
      if (b.west < west) west = b.west
      if (b.east > east) east = b.east
      if (b.south < south) south = b.south
      if (b.north > north) north = b.north
    }
    // Kick off fetches for any layer that's still missing bounds
    // so the next call has them — fire-and-forget.
    for (const lyr of layerStore.visibleLayers || []) {
      if (!layerStore.bounds?.[lyr.name]) {
        layerStore.fetchBounds(lyr.name).catch(() => {})
      }
    }
  }
  if (!isFinite(west) || !isFinite(east) || east - west < 0.001) return
  map.fitBounds(
    [[west, south], [east, north]],
    { padding: 64, duration: 1200, maxZoom: 12, essential: true }
  )
}

function applySkin(skinId) {
  if (!map) return
  const skin = getSkin(skinId)
  const spec = resolveStyle(skin)
  if (!spec) return
  ui.basemapLoading = true
  if (typeof spec === 'string') {
    map.setStyle(spec, { diff: false })
  } else {
    map.setStyle(spec, { diff: false })
  }
}

onMounted(() => {
  const startCenter = [78.9629, 20.5937]
  const startZoom = 4
  const initialSkin = getSkin(activeSkinId.value)
  // Floor the zoom-out to the largest size at which a single world
  // exactly fills the canvas, and disable world-copy wrap so the
  // user can never see the same country twice in one view. The
  // floor is recomputed on resize — the world doesn't get smaller
  // when the user makes the window narrower.
  const rect = mapEl.value.getBoundingClientRect()
  const minZoom = computeGlobeFitZoom(rect.width, rect.height)
  map = new maplibregl.Map({
    container: mapEl.value,
    style: resolveStyle(initialSkin),
    center: startCenter,
    zoom: startZoom,
    pitch: 0,
    minZoom,
    renderWorldCopies: false,
    attributionControl: { compact: true },
  })
  map.on('resize', () => {
    const r = mapEl.value.getBoundingClientRect()
    map.setMinZoom(computeGlobeFitZoom(r.width, r.height))
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-left')
  // Geolocate — Google's "where am I" blue dot.
  map.addControl(new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true, timeout: 6000 },
    trackUserLocation: false,           // one-shot, not constant tracking
    showUserHeading: false,
    showAccuracyCircle: true,
  }), 'bottom-right')

  // The toolbar (App.vue) owns the Fit / Reset-tilt camera controls.
  // No in-map recenter control — keeps the bottom-right cluster
  // single-source-of-truth and avoids duplicating fit behaviour.

  // Sources/layers re-add hooks. setStyle wipes everything, so we
  // re-add on every styledata.
  map.on('styledata', () => {
    if (ui.basemapLoading) ui.basemapLoading = false
    _reAddAllLayers()
    _reAddZones()
  })

  // Initial bootstrap: paint any layers that already have features.
  map.on('load', () => {
    _reAddAllLayers()
    _reAddZones()
    _flyToInitialViewport()
    _fetchAllVisibleBounds()
  })

  // Right-click context menu. We use MapLibre's `contextmenu` event
  // because it gives us the geographic coordinates directly and
  // suppresses the browser's default context menu over the canvas.
  // We also suppress the browser menu on the canvas DOM in case the
  // event fires before this listener is attached.
  map.on('contextmenu', (e) => {
    // e.point gives canvas-relative coords; e.lngLat gives lat/lng.
    e.preventDefault?.()
    ui.openContextMenu(e.point.x, e.point.y, e.lngLat.lat, e.lngLat.lng)
  })

  // PR-13 — modifier-drag rotate/tilt.
  //   Alt  + drag horizontal → rotate (bearing)
  //   Ctrl + drag vertical   → tilt (pitch)
  // Default scroll-wheel zoom and shift-drag pan remain in effect.
  // We track the gesture manually so we don't have to add a second
  // DragPan handler (which would fight MapLibre's own).
  //
  // The gesture is bidirectional: drag-down raises pitch, drag-up
  // lowers it. To avoid the "stuck at 0" trap when starting from
  // a flat view, we shift the pitch up by 30° on mousedown (still
  // clamped to 75) so the user is always in a useful gesture range
  // from the first frame. The up/down drag then feels symmetric
  // around the click position.
  const canvas = map.getCanvas()
  // Prevent the browser's context menu over the canvas DOM too, so a
  // right-click anywhere on the map (not just on a feature) is
  // captured by the contextmenu handler above.
  canvas.addEventListener('contextmenu', (e) => e.preventDefault())
  let gesture = null
  canvas.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return
    if (e.altKey) {
      gesture = { kind: 'rotate', x: e.clientX, y: e.clientY, startBearing: map.getBearing() }
      e.preventDefault()
      canvas.style.cursor = 'grabbing'
    } else if (e.ctrlKey) {
      // Shift up by 30° on grab so the gesture is bidirectional
      // from any starting pitch (including 0°).
      const startPitch = Math.min(75, map.getPitch() + 30)
      map.setPitch(startPitch)
      gesture = { kind: 'pitch', x: e.clientX, y: e.clientY, startPitch }
      e.preventDefault()
      canvas.style.cursor = 'ns-resize'
    }
  })
  window.addEventListener('mousemove', (e) => {
    if (!gesture) return
    const dx = e.clientX - gesture.x
    const dy = e.clientY - gesture.y
    if (gesture.kind === 'rotate') {
      // 1 px ≈ 0.4° of bearing — feels close to Google Earth.
      map.setBearing(gesture.startBearing + dx * 0.4)
    } else if (gesture.kind === 'pitch') {
      // Pitch 0–75°; drag-down (dy > 0) raises the camera, drag-up
      // (dy < 0) lowers it. Anchor to startPitch (set on mousedown
      // with the +30° shift) so the gesture is symmetric.
      const next = Math.max(0, Math.min(75, gesture.startPitch + dy * 0.4))
      map.setPitch(next)
    }
  })
  const endGesture = () => {
    if (!gesture) return
    gesture = null
    canvas.style.cursor = ''
  }
  window.addEventListener('mouseup', endGesture)
  // Cancel if the page loses focus mid-gesture.
  window.addEventListener('blur', endGesture)
  // Debounce viewport-based fetches: a fast pinch/wheel otherwise
  // spams the server with one request per intermediate frame. 180ms
  // feels instant to the user and saves the last call if multiple
  // moveend events fire in quick succession.
  let _moveEndTimer = null
  map.on('moveend', () => {
    if (_moveEndTimer) clearTimeout(_moveEndTimer)
    _moveEndTimer = setTimeout(() => {
      _moveEndTimer = null
      _fetchAllVisibleBounds()
    }, 180)
  })

  // Subscribe to feature updates from the layer store. We retry on
// every `styledata` until `_addLayerOnMap` actually adds the source
// (signalled by `map.getSource(sid)` being truthy). This handles
// the race where features arrive while the basemap style is still
// parsing — `isStyleLoaded()` would block us, but `_addLayerOnMap`
// guards on `map.getStyle()` (the parseable style object) instead,
// which becomes truthy on the next `styledata` event.
  unsubscribeFeatures = layerStore.onFeaturesUpdated((layerName) => {
    if (!map) return
    const tryAdd = () => {
      if (!map) return true
      _addLayerOnMap(layerName)
      if (typeof map.triggerRepaint === 'function') map.triggerRepaint()
      return !!map.getSource(sourceId(layerName))
    }
    if (tryAdd()) return
    const onStyle = () => {
      if (tryAdd()) map.off('styledata', onStyle)
    }
    map.on('styledata', onStyle)
  })

  // Subscribe to zone updates. We don't have a real event channel
  // for the store yet; instead we watch the byMap object reference
  // and re-paint zones whenever it changes. Cheap because zone
  // counts are small (single-digit dozens for v1).
  unsubscribeZones = watch(
    () => zoneStore.byMap,
    () => {
      if (map.isStyleLoaded()) _reAddZones()
      else map.once('styledata', _reAddZones)
    },
    { deep: true }
  )

  // Re-paint zones when the tag filter changes (ui.zoneTags).
  unsubscribeZoneTags = watch(
    () => ui.zoneTags,
    () => {
      if (map && map.isStyleLoaded()) _reAddZones()
    },
    { deep: true }
  )

  // Expose a thin facade on window for non-Vue consumers (e.g. insight
  // chips in BottomDrawer). The native maplibre instance is large and
  // has a dozen methods; we expose only what the canvas-overlays need.
  if (typeof window !== 'undefined') {
    window.expeditionMap = {
      flyTo: (opts) => map && map.flyTo(opts),
      easeTo: (opts) => map && map.easeTo(opts),
      jumpTo: (opts) => map && map.jumpTo(opts),
      getCenter: () => map && map.getCenter(),
      getZoom: () => map && map.getZoom(),
      getMap: () => map,
      // Return the active map's zones as a GeoJSON FeatureCollection.
      // Used by fit-to-data in App.vue so custom territories count
      // as "data" alongside pin layers.
      getZones: () => {
        const activeMapName = mapStore.activeMap?.map?.name
        if (!activeMapName) return { type: 'FeatureCollection', features: [] }
        const list = zoneStore.byMap?.[activeMapName] || []
        return {
          type: 'FeatureCollection',
          features: list
            .filter((z) => z && z.geometry)
            .map((z) => ({
              type: 'Feature',
              geometry: z.geometry,
              properties: { name: z.name, title: z.title, kind: 'zone' },
            })),
        }
      },
    }
  }

  // ----- Zone draw mode -----
  // While drawMode === 'polygon', clicks add vertices; double-click
  // finishes the polygon and commits via the zone store.
  //
  // doubleClickZoom is disabled SYNCHRONOUSLY on the first vertex click
  // (not via the drawMode watcher), so a user who double-clicks
  // immediately after entering draw mode finishes the polygon instead
  // of triggering a map zoom. The watcher still re-enables it on cancel.
  map.on('click', (e) => {
    if (ui.drawMode !== 'polygon') return
    // If the click landed on an existing pin, let the pin handler take
    // it instead of adding a vertex there. queryRenderedFeatures is the
    // canonical MapLibre way to detect this without layer handlers
    // racing each other.
    const pinLayers = layerStore.layers
      .map((l) => layerId(l.name))
      .filter((id) => map.getLayer(id))
    if (pinLayers.length > 0) {
      const hits = map.queryRenderedFeatures(e.point, { layers: pinLayers })
      if (hits.length > 0) return
    }
    if (!_dblClickDisabled) {
      map.doubleClickZoom.disable()
      _dblClickDisabled = true
    }
    ui.pushDraftVertex([e.lngLat.lng, e.lngLat.lat])
    _renderDraft()
  })
  map.on('dblclick', async (e) => {
    if (ui.drawMode !== 'polygon') return
    e.preventDefault() // suppress default dblclick zoom
    if (ui.draftVertices.length < 3) return // not a polygon
    const ring = [...ui.draftVertices, ui.draftVertices[0]] // close ring
    const geometry = {
      type: 'Polygon',
      coordinates: [ring],
    }
    const mapName = mapStore.activeMap?.map?.name
    if (!mapName) return
    try {
      const title = (ui.zoneDraftTitle || '').trim() || 'Zone'
      if (!ui.zoneDraftTitle || !ui.zoneDraftTitle.trim()) {
        console.warn(
          '[expedition] zone saved with default title (no user input)'
        )
      }
      await zoneStore.createZone(mapName, {
        title,
        zone_type: 'polygon',
        geometry,
        color: '#3B82F6',
        fill_opacity: 0.2,
        stroke_color: '#1E40AF',
        stroke_width: 2,
      })
    } catch (err) {
      console.error('[expedition] zone create failed', err)
    } finally {
      ui.cancelDraw()
      _clearDraft()
    }
  })
  map.getCanvas().style.cursor = 'crosshair'

  // Esc cancels an in-progress polygon draw.
  const onKey = (e) => {
    if (e.key === 'Escape' && ui.drawMode === 'polygon') ui.cancelDraw()
  }
  window.addEventListener('keydown', onKey)
  // Tear down on unmount via closure capture.
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
})

// Toggle the canvas cursor + draft rendering as drawMode flips.
watch(
  () => ui.drawMode,
  (mode) => {
    if (!map) return
    const c = map.getCanvas()
    if (mode === 'polygon') {
      c.style.cursor = 'crosshair'
    } else {
      c.style.cursor = ''
      map.doubleClickZoom.enable()
      _dblClickDisabled = false
      _clearDraft()
    }
  }
)

// Re-render the draft polygon whenever the vertex list changes.
watch(
  () => ui.draftVertices,
  () => _renderDraft(),
  { deep: true }
)

function _draftFeatureCollection() {
  const v = ui.draftVertices
  if (!v || v.length < 1) return { type: 'FeatureCollection', features: [] }
  if (v.length === 1) {
    return {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'Point', coordinates: v[0] }, properties: { kind: 'draft-vertex' } },
      ],
    }
  }
  // 2+ vertices: render as a line. We close the ring visually only
  // when the user finishes; until then it's an open polyline.
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: v },
        properties: { kind: 'draft-line' },
      },
    ],
  }
}

function _renderDraft() {
  if (!map) return
  const sid = 'src-zones-draft'
  if (!map.getSource(sid)) {
    map.addSource(sid, { type: 'geojson', data: _draftFeatureCollection() })
  } else {
    map.getSource(sid).setData(_draftFeatureCollection())
  }
  const lid = zoneDraftLayerId()
  if (!map.getLayer(lid)) {
    map.addLayer({
      id: lid,
      type: 'line',
      source: sid,
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: {
        'line-color': '#3B82F6',
        'line-width': 2,
        'line-dasharray': [2, 1],
      },
    })
    map.addLayer({
      id: lid + '-verts',
      type: 'circle',
      source: sid,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 4,
        'circle-color': '#3B82F6',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5,
      },
    })
  }
}

function _clearDraft() {
  if (!map) return
  const sid = 'src-zones-draft'
  if (map.getLayer(zoneDraftLayerId() + '-verts'))
    map.removeLayer(zoneDraftLayerId() + '-verts')
  if (map.getLayer(zoneDraftLayerId()))
    map.removeLayer(zoneDraftLayerId())
  if (map.getSource(sid)) map.removeSource(sid)
}

onBeforeUnmount(() => {
  if (unsubscribeFeatures) unsubscribeFeatures()
  if (unsubscribeZones) unsubscribeZones()
  if (unsubscribeZoneTags) unsubscribeZoneTags()
  if (typeof window !== 'undefined' && window.expeditionMap) {
    window.expeditionMap = null
  }
  if (map) map.remove()
})

// Skin change → swap basemap.
watch(activeSkinId, (id) => {
  if (!map) return
  applySkin(id)
})

// Layer list changes (e.g. map switched) → reconcile.
watch(
  () => layerStore.layers.map((l) => ({ name: l.name, enabled: !!l.enabled })),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (map.getLayer(layerId(layer.name))) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// Heatmap toggle (PR-7). When a layer's heatmap flag flips, re-add
// that layer's source layers so the heatmap layer is created or
// removed and the pin stack is shown/hidden accordingly.
watch(
  () => ({ ...ui.heatmapEnabled }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (map.getLayer(layerId(layer.name))) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// Radius toggle (PR-8). When a layer's radius flag flips, or its
// radiusField / radiusMeters changes, re-add so the halo layer is
// created/removed/updated with the new paint expression.
watch(
  () => ({
    enabled: { ...ui.radiusEnabled },
    field: { ...ui.radiusField },
    meters: { ...ui.radiusMeters },
    scale: { ...ui.radiusScale },
  }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (map.getLayer(layerId(layer.name))) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// 3D pitch toggle (PR-9). Drives the camera (pitch is a camera
// property, not a layer property). On → ease to pitchDegrees; off
// → ease back to 0.
watch(
  () => ({ enabled: ui.pitchEnabled, deg: ui.pitchDegrees }),
  ({ enabled, deg }) => {
    if (!map) return
    map.easeTo({ pitch: enabled ? deg : 0, duration: 600 })
  },
)

// Extrusion per-layer state (PR-9). Re-add layers so the
// fill-extrusion paint updates.
watch(
  () => ({
    heights: { ...ui.extrusionHeight },
    fields: { ...ui.extrusionField },
    on: ui.pitchEnabled,
  }),
  () => {
    if (!map || !map.isStyleLoaded()) return
    for (const layer of layerStore.layers) {
      if (map.getLayer(layerId(layer.name))) {
        _addLayerOnMap(layer.name)
      }
    }
  },
  { deep: true },
)

// When the active map changes, fly to the new viewport (or fitBounds).
watch(
  () => mapStore.activeMap && mapStore.activeMap.map && mapStore.activeMap.map.name,
  () => {
    if (!map) return
    _resetFirstFetch()
    _flyToInitialViewport()
  },
)

// When the selection changes (e.g. cleared by Esc), clear the feature state
// so the halo doesn't linger.
watch(
  () => ui.selectedFeature,
  (sel) => {
    if (!map) return
    if (!sel) {
      // Clear all features in the previously selected source.
      for (const layer of layerStore.layers) {
        const sid = sourceId(layer.name)
        const src = map.getSource(sid)
        if (!src) continue
        for (const feat of layerStore.features[layer.name]?.features || []) {
          if (feat._id != null) {
            try { map.setFeatureState({ source: sid, id: feat._id }, { selected: false }) } catch (_) {}
          }
        }
      }
    }
  }
)
</script>

<template>
  <div class="basemap-shell">
    <div ref="mapEl" class="basemap" />
    <!-- Subtle radial vignette so the canvas feels framed. -->
    <div class="basemap__vignette" aria-hidden="true" />
    <!-- Fetch indicator. Visible while at least one layer fetch is in
         flight. Sits in the bottom-right, just above MapLibre's controls. -->
    <Transition name="exp-fade">
      <div v-if="ui.fetchingFeatures > 0" class="basemap__fetch-pill" aria-live="polite">
        <span class="basemap__fetch-dot" />
        Loading…
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.basemap-shell { position: absolute; inset: 0; overflow: hidden; }
.basemap { position: absolute; inset: 0; }
.basemap__vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    ellipse at center,
    transparent 55%,
    rgba(0, 0, 0, 0.10) 85%,
    rgba(0, 0, 0, 0.22) 100%
  );
  z-index: 1;
}
/* Fetch indicator pill — scoped (it's inside the Vue root). */
.basemap__fetch-pill {
  position: absolute;
  right: 50px;
  bottom: 12px;
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px;
  background: rgba(11, 14, 20, 0.78);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: rgba(230, 232, 236, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  z-index: 5;
  pointer-events: none;
}
.basemap__fetch-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #3B82F6;
  box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.55);
  animation: exp-pulse 1.2s ease-out infinite;
}
@keyframes exp-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.55); }
  70%  { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
  100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
}
.exp-fade-enter-active, .exp-fade-leave-active {
  transition: opacity 180ms ease;
}
.exp-fade-enter-from, .exp-fade-leave-to { opacity: 0; }
</style>
<style>
/* MapLibre controls live outside the Vue component root (appended
   to the map container), so these must be unscoped. They use
   class names unique to Expedition, so leakage is nil. */
.maplibregl-ctrl-attrib { font-size: 10px; opacity: 0.65; }
.maplibregl-ctrl-attrib a { color: rgba(255, 255, 255, 0.7); }
.maplibregl-ctrl-scale {
  background: rgba(11, 14, 20, 0.7);
  border-color: rgba(255, 255, 255, 0.2);
  color: rgba(255, 255, 255, 0.8);
  font-size: 10px;
  padding: 1px 4px;
}
</style>
