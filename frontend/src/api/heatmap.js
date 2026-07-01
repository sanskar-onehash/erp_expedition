/**
 * Heatmap v2 helpers — shared between Basemap.vue (paint) and
 * LayerEditor.vue (gradient editor + form).
 *
 * Three responsibilities:
 *   1. RAMP_PRESETS — curated scientific color ramps the editor can
 *      apply. 5–7 stops each, sRGB hex. Not full Perceptually-Uniform
 *      colormaps (those are huge); these are short, hand-tuned
 *      approximations that read correctly at heatmap densities.
 *   2. validateRamp / validateWeightStops — server-shape mirrors for
 *      client-side guards. Defense in depth: the server validates too,
 *      but rejecting bad input in the editor gives instant feedback.
 *   3. buildHeatmapColor / buildHeatmapWeight — MapLibre expression
 *      builders that the Basemap paint fn consumes.
 *
 * Ramps are stored as `[{ stop: 0..1, color: '#rrggbb', alpha: 0..1 }]`.
 * All stops are RGBA at paint time. Alpha defaults to 1 for the dense
 * end and 0 for transparent stops — typical heatmap shape.
 */

// --- Presets ---------------------------------------------------------------

/** Viridis-like (purple → green → yellow). ~6 stops. */
const VIRIDIS = [
  { stop: 0.0, color: '#440154', alpha: 0 },
  { stop: 0.2, color: '#482878', alpha: 0.4 },
  { stop: 0.4, color: '#3e4989', alpha: 0.65 },
  { stop: 0.6, color: '#31688e', alpha: 0.85 },
  { stop: 0.8, color: '#35b779', alpha: 0.95 },
  { stop: 1.0, color: '#fde725', alpha: 1 },
]

/** Inferno-like (black → red → yellow). High-contrast for dark basemaps. */
const INFERNO = [
  { stop: 0.0, color: '#000004', alpha: 0 },
  { stop: 0.2, color: '#420a68', alpha: 0.45 },
  { stop: 0.4, color: '#932667', alpha: 0.7 },
  { stop: 0.6, color: '#dd513a', alpha: 0.9 },
  { stop: 0.8, color: '#fca50a', alpha: 0.98 },
  { stop: 1.0, color: '#fcffa4', alpha: 1 },
]

/** Plasma-like (purple → pink → yellow). */
const PLASMA = [
  { stop: 0.0, color: '#0d0887', alpha: 0 },
  { stop: 0.2, color: '#7e03a8', alpha: 0.4 },
  { stop: 0.4, color: '#cc4778', alpha: 0.7 },
  { stop: 0.6, color: '#f89540', alpha: 0.9 },
  { stop: 0.8, color: '#fdca26', alpha: 0.98 },
  { stop: 1.0, color: '#f0f921', alpha: 1 },
]

/** Red-Yellow-Green diverging. Use for "good vs bad" density (NO2, risk). */
const RDYLGN = [
  { stop: 0.0, color: '#a50026', alpha: 0 },
  { stop: 0.25, color: '#f46d43', alpha: 0.6 },
  { stop: 0.5, color: '#ffffbf', alpha: 0.9 },
  { stop: 0.75, color: '#a6d96a', alpha: 0.95 },
  { stop: 1.0, color: '#1a9850', alpha: 1 },
]

/** Single-hue blue ramp. Calm, low-saturation. */
const BLUES = [
  { stop: 0.0, color: '#f7fbff', alpha: 0 },
  { stop: 0.3, color: '#c6dbef', alpha: 0.5 },
  { stop: 0.6, color: '#6baed6', alpha: 0.85 },
  { stop: 1.0, color: '#08306b', alpha: 1 },
]

/**
 * Default monochrome ramp from a layer color. Mirrors the pre-v2
 * behavior so plain "toggle heatmap on" feels the same.
 */
function monochromeRamp(layerColor) {
  const c = hexToRgb(layerColor || '#3B82F6')
  return [
    { stop: 0.0, color: '#000000', alpha: 0 },
    { stop: 0.2, color: rgbToHex(c.r, c.g, c.b), alpha: 0.25 },
    { stop: 0.5, color: rgbToHex(c.r, c.g, c.b), alpha: 0.55 },
    { stop: 0.8, color: rgbToHex(c.r, c.g, c.b), alpha: 0.85 },
    { stop: 1.0, color: rgbToHex(Math.min(255, c.r + 60), Math.min(255, c.g + 60), Math.min(255, c.b + 60)), alpha: 1 },
  ]
}

export const RAMP_PRESETS = {
  viridis: { label: 'Viridis', stops: VIRIDIS },
  inferno: { label: 'Inferno', stops: INFERNO },
  plasma: { label: 'Plasma', stops: PLASMA },
  rdylgn: { label: 'Red-Yellow-Green', stops: RDYLGN },
  blues: { label: 'Blues', stops: BLUES },
  monochrome: { label: 'Monochrome (layer color)', build: monochromeRamp },
}

// --- Validation ------------------------------------------------------------

const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/

/**
 * Validate and normalize a ramp. Returns the cleaned array or null on
 * bad input. Mirrors the server-side _coerce_heatmap_ramp shape:
 *   [{ stop: 0..1, color: '#rrggbb', alpha: 0..1 }, ...], min 2 stops.
 */
export function validateRamp(stops) {
  if (!Array.isArray(stops) || stops.length < 2) return null
  const out = []
  for (const s of stops) {
    if (!s || typeof s !== 'object') return null
    const stop = Number(s.stop)
    const color = typeof s.color === 'string' ? s.color : ''
    const alpha = s.alpha == null ? 1 : Number(s.alpha)
    if (!Number.isFinite(stop) || stop < 0 || stop > 1) return null
    if (!HEX_RE.test(color)) return null
    if (!Number.isFinite(alpha) || alpha < 0 || alpha > 1) return null
    out.push({ stop, color, alpha })
  }
  out.sort((a, b) => a.stop - b.stop)
  return out
}

/**
 * Validate and normalize weight stops. Shape: [[v, w], ...] sorted by v.
 * Returns null on bad input.
 */
export function validateWeightStops(stops) {
  if (!Array.isArray(stops) || stops.length < 2) return null
  const out = []
  for (const pair of stops) {
    if (!Array.isArray(pair)) return null
    if (pair.length !== 2) return null
    const v = Number(pair[0])
    const w = Number(pair[1])
    if (!Number.isFinite(v) || !Number.isFinite(w)) return null
    out.push([v, w])
  }
  out.sort((a, b) => a[0] - b[0])
  return out
}

/** Serialize a validated ramp back to a JSON string for the DocType. */
export function serializeRamp(stops) {
  const v = validateRamp(stops)
  if (!v) return ''
  return JSON.stringify(v)
}

/** Serialize weight stops to JSON. */
export function serializeWeightStops(stops) {
  const v = validateWeightStops(stops)
  if (!v) return ''
  return JSON.stringify(v)
}

// --- MapLibre expression builders -----------------------------------------

/**
 * Build the `heatmap-color` expression from a ramp.
 *   stops: [{ stop, color, alpha }, ...]  (validated, sorted)
 * Returns a MapLibre expression array.
 */
export function buildHeatmapColor(stops) {
  const expr = ['interpolate', ['linear'], ['heatmap-density']]
  for (const s of stops) {
    expr.push(s.stop, rgbaFromStop(s))
  }
  return expr
}

/**
 * Build the `heatmap-radius` zoom-interp expression.
 *   r0, r12: pixel radii at zoom 0 and zoom 12 (linearly interpolated).
 */
export function buildHeatmapRadius(r0, r12) {
  return ['interpolate', ['linear'], ['zoom'], 0, r0, 12, r12]
}

export function buildHeatmapIntensity(i0, i12) {
  return ['interpolate', ['linear'], ['zoom'], 0, i0, 12, i12]
}

/**
 * Build the `heatmap-weight` expression. Three modes:
 *   - no field → constant 1
 *   - field + linear → linear interpolate [min..max] → [0..1]
 *   - field + log    → log10(1+v) interpolate [ln(1+min)..ln(1+max)] → [0..1]
 *   - weight_stops   → piecewise from stops (uses linear or log on the input)
 *
 * `field` is the source-DocType property name. `coalesce` falls back
 * to `min` (so a missing value renders at weight 0 — invisible — the
 * user can set min to 0 to invert this).
 */
export function buildHeatmapWeight({ field, log, min, max, stops }) {
  if (!field) return 1
  const input = log
    ? ['ln', ['+', 1, ['coalesce', ['to-number', ['get', field]], 0]]]
    : ['coalesce', ['to-number', ['get', field]], min]
  if (Array.isArray(stops) && stops.length >= 2) {
    const expr = ['interpolate', ['linear'], input]
    for (const [v, w] of stops) {
      expr.push(v, w)
    }
    return expr
  }
  // Linear fallback. Avoid divide-by-zero (server also validates).
  if (min == null || max == null || min === max) return 1
  return ['interpolate', ['linear'], input, min, 0, max, 1]
}

// --- Internal color helpers ------------------------------------------------

function hexToRgb(hex) {
  const raw = String(hex || '').trim()
  const rgb = raw.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)$/i)
  if (rgb) {
    const channels = rgb.slice(1, 4).map((n) => Math.max(0, Math.min(255, Number(n))))
    return { r: channels[0], g: channels[1], b: channels[2] }
  }
  let h = raw.replace('#', '')
  if (h.length === 3) {
    return {
      r: parseInt(h[0] + h[0], 16),
      g: parseInt(h[1] + h[1], 16),
      b: parseInt(h[2] + h[2], 16),
    }
  }
  if (h.length === 4) {
    h = h.slice(0, 3).split('').map((c) => c + c).join('')
  } else if (h.length === 8) {
    h = h.slice(0, 6)
  }
  if (h.length !== 6) return { r: 255, g: 59, b: 48 }
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  }
}

function rgbToHex(r, g, b) {
  const toHex = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  return '#' + toHex(r) + toHex(g) + toHex(b)
}

function rgbaFromStop(s) {
  const c = hexToRgb(s.color)
  return `rgba(${c.r}, ${c.g}, ${c.b}, ${s.alpha})`
}

/**
 * Parse a stored JSON string from the DocType into a validated ramp
 * array. Returns null on bad JSON or bad shape; callers should fall
 * back to a default.
 */
export function parseRampJson(raw) {
  if (!raw) return null
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    return validateRamp(parsed)
  } catch (_) {
    return null
  }
}

export function parseWeightStopsJson(raw) {
  if (!raw) return null
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    return validateWeightStops(parsed)
  } catch (_) {
    return null
  }
}

/** A 2-stop default ramp used by the editor when none is configured. */
export const DEFAULT_RAMP = (color) => monochromeRamp(color)
