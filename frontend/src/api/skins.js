/**
 * Curated basemap gallery — every entry is genuinely free + commercial-friendly
 * AND verified reachable from the browser. Adding a new skin: verify it with
 * curl (HEAD on the style URL and a tile), append an entry, and rebuild.
 *
 * Each entry shape:
 *   {
 *     id:        string                       // stable slug, also localStorage key
 *     label:     string                       // human-readable name
 *     kind:      'vector-style' | 'raster'    // how Basemap.vue wraps it
 *     style:     string | undefined           // for vector-style: full MapLibre style URL
 *     sources:   object | undefined           // for raster: MapLibre `sources` block
 *     layers:    array  | undefined           // for raster: MapLibre `layers` block
 *     glyphs:    string | undefined           // for vector-style: glyphs URL (rarely needed)
 *     attribution: string                     // HTML, shown on hover
 *     defaultZoom: number                     // suggested fit on open (some skins need z>=4)
 *     notes:     string                       // free-form "what this is"
 *   }
 *
 * The user picks the default later. The picker UI lets you cycle all of these
 * live to evaluate. Right now `currentSkinId` defaults to `ofm-liberty`.
 */

// --- Vector style URLs (full MapLibre style spec, fetched as-is) ---

const OPENFREEMAP = 'https://tiles.openfreemap.org/styles'

// Local style specs shipped in expedition/public/styles/. The Frappe
// asset pipeline copies them to /assets/expedition/styles/. We use
// a `site_url` injected at runtime (in Basemap.vue) to resolve the
// absolute URL — we can't hardcode origin because tenants may run
// Expedition on a subpath. The skins here use a placeholder
// `${site_url}/assets/expedition/styles/<file>.json` that Basemap.vue
// rewrites before applying.
const LOCAL_STYLES_BASE = '__SITE_URL__/assets/expedition/styles'

const LOCAL_LIBERTY_VARIANTS = [
  {
    id: 'liberty-green',
    label: 'Liberty · Green',
    kind: 'local-style',
    style: `${LOCAL_STYLES_BASE}/liberty-green.json`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Google-Maps-style greener land. Patched Liberty.',
  },
  {
    id: 'liberty-sat',
    label: 'Liberty · Saturated',
    kind: 'local-style',
    style: `${LOCAL_STYLES_BASE}/liberty-sat.json`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Apple-maps feel. Saturation boost over Liberty.',
  },
  {
    id: 'liberty-pale',
    label: 'Liberty · Pale',
    kind: 'local-style',
    style: `${LOCAL_STYLES_BASE}/liberty-pale.json`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Muted atlas look. Low saturation. Good for print.',
  },
]

const OPENFREEMAP_SKINS = [
  {
    id: 'ofm-liberty',
    label: 'Liberty',
    kind: 'vector-style',
    style: `${OPENFREEMAP}/liberty`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Most polished. Neutral palette, OSM-derived. Recommended default.',
  },
  {
    id: 'ofm-bright',
    label: 'Bright',
    kind: 'vector-style',
    style: `${OPENFREEMAP}/bright`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Higher contrast than Liberty. Saturated greens and blues.',
  },
  {
    id: 'ofm-positron',
    label: 'Positron',
    kind: 'vector-style',
    style: `${OPENFREEMAP}/positron`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Carto Positron-like. Minimal, low-contrast.',
  },
  {
    id: 'ofm-dark',
    label: 'Dark',
    kind: 'vector-style',
    style: `${OPENFREEMAP}/dark`,
    attribution:
      '© <a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    defaultZoom: 2,
    notes: 'Felt-tier dark mode. Pops orange and teal pins.',
  },
]

const MAPLIBRE_DEMO = [
  {
    id: 'maplibre-demo',
    label: 'MapLibre Demo',
    kind: 'vector-style',
    style: 'https://demotiles.maplibre.org/style.json',
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    attribution:
      '© <a href="https://maplibre.org" target="_blank">MapLibre</a> — demo data',
    defaultZoom: 1,
    notes: 'The "Hello world" of MapLibre. Quirky, low detail.',
  },
]

// --- Raster XYZ sources (wrapped in a minimal MapLibre style) ---

const cartoAttribution =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions" target="_blank">CARTO</a>'

const CARTO_DOMAINS = 'abcd'.split('')
const cartoTileUrl = (styleId) =>
  `https://${CARTO_DOMAINS.map((d) => `${d}.basemaps.cartocdn.com`).join('|')}/${styleId}/{z}/{x}/{y}.png`

function cartoRasterEntry({ id, label, styleId, defaultZoom, notes }) {
  // MapLibre's URL templating supports the {a-d} subdomain rotation only on
  // a single host; the multi-host pattern above works with a single-host
  // variant — we emit one and let MapLibre's `tiles` array take multiple.
  const tiles = ['a', 'b', 'c', 'd'].map(
    (s) => `https://${s}.basemaps.cartocdn.com/${styleId}/{z}/{x}/{y}.png`
  )
  return {
    id,
    label,
    kind: 'raster',
    sources: {
      [id]: {
        type: 'raster',
        tiles,
        tileSize: 256,
        attribution: cartoAttribution,
      },
    },
    layers: [{ id, type: 'raster', source: id, paint: { 'raster-opacity': 1 } }],
    attribution: cartoAttribution,
    defaultZoom: defaultZoom ?? 2,
    notes,
  }
}

const CARTO_SKINS = [
  cartoRasterEntry({
    id: 'carto-light',
    label: 'Carto Light',
    styleId: 'light_all',
    defaultZoom: 2,
    notes: 'Recognizable Carto Positron. Soft greys, subtle labels.',
  }),
  cartoRasterEntry({
    id: 'carto-light-nolabels',
    label: 'Carto Light (no labels)',
    styleId: 'light_nolabels',
    defaultZoom: 2,
    notes: 'Same as Light, no labels. Good for very dense pin layers.',
  }),
  cartoRasterEntry({
    id: 'carto-dark',
    label: 'Carto Dark',
    styleId: 'dark_all',
    defaultZoom: 2,
    notes: 'Carto Dark Matter. Strong contrast on bright pins.',
  }),
  cartoRasterEntry({
    id: 'carto-dark-nolabels',
    label: 'Carto Dark (no labels)',
    styleId: 'dark_nolabels',
    defaultZoom: 2,
    notes: 'Dark, no labels. Maximum pin contrast.',
  }),
]

// Wikimedia OSM tiles are removed: maps.wikimedia.org does not send
// CORS headers, so the browser blocks cross-origin tile fetches.
// Use OpenFreeMap, Carto, or local PMTiles instead.

const ESRI_SKINS = [
  {
    id: 'esri-imagery',
    label: 'Esri Imagery (satellite)',
    kind: 'raster',
    sources: {
      esri: {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        ],
        tileSize: 256,
        attribution:
          'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
      },
    },
    layers: [{ id: 'esri', type: 'raster', source: 'esri' }],
    attribution:
      'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics',
    defaultZoom: 2,
    notes: 'Satellite imagery. Pins pop against terrain.',
  },
]

export const SKINS = [
  ...OPENFREEMAP_SKINS,
  ...LOCAL_LIBERTY_VARIANTS,
  ...MAPLIBRE_DEMO,
  ...CARTO_SKINS,
  ...ESRI_SKINS,
]

const SKINS_BY_ID = Object.fromEntries(SKINS.map((s) => [s.id, s]))

export function getSkin(id) {
  return SKINS_BY_ID[id] || SKINS_BY_ID['ofm-liberty']
}

/**
 * Resolve the absolute URL for a local-style skin. The skin's `style`
 * field has a `__SITE_URL__` placeholder that we replace with the
 * current page's origin. Called from Basemap.vue on every apply so
 * the skins work on any tenant path (e.g. /app/expedition on a
 * subpath-hosted Frappe site).
 */
export function resolveStyleUrl(skin) {
  if (!skin) return null
  if (skin.kind === 'local-style' && typeof window !== 'undefined') {
    return skin.style.replace('__SITE_URL__', window.location.origin)
  }
  return skin.style
}

export const DEFAULT_SKIN_ID = 'ofm-liberty'