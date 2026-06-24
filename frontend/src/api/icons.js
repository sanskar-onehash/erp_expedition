/**
 * Icon sprite loader for MapLibre.
 *
 * `expedition/public/icons.svg` is a single SVG with one <symbol> per
 * glyph, each with an id like `pin-marker`. MapLibre's addImage wants
 * an ImageData per id, not an SVG sprite, so we parse the SVG once,
 * draw each symbol to an offscreen canvas at the requested pixel size,
 * and register the result.
 *
 * Caching: the parse happens once per page load. Subsequent calls
 * with the same ids are no-ops.
 */
import maplibregl from 'maplibre-gl'

const SPRITE_PATH = '/assets/expedition/icons.svg'

let _cache = null // Promise<Set<string>>

async function _loadSvgText() {
  if (_cache) return _cache
  _cache = (async () => {
    const url = `${SPRITE_PATH}?v=${Date.now()}`
    const res = await fetch(url, { credentials: 'same-origin' })
    if (!res.ok) throw new Error(`icons.svg fetch ${res.status}`)
    const text = await res.text()
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml')
    const symbols = Array.from(doc.querySelectorAll('symbol'))
    const ids = new Set(symbols.map((s) => s.id))
    // Stash the parsed nodes so renderIcon can clone them cheaply.
    _cache = Promise.resolve({ ids, symbols })
    return _cache
  })()
  return _cache
}

/**
 * Render a single symbol id to an HTMLImageElement at the given pixel
 * size. Returns a promise that resolves to the image, or rejects if
 * the id isn't in the sprite.
 */
function _renderSymbol(symbolEl, size) {
  return new Promise((resolve, reject) => {
    // Re-emit the symbol's inner SVG as a standalone <svg> so the
    // browser renders it (browsers don't render <symbol> directly).
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    svg.setAttribute('xmlns', ns)
    svg.setAttribute('viewBox', symbolEl.getAttribute('viewBox') || '0 0 24 24')
    svg.setAttribute('width', String(size))
    svg.setAttribute('height', String(size))
    svg.innerHTML = symbolEl.innerHTML

    const blob = new Blob([svg.outerHTML], { type: 'image/svg+xml' })
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = (e) => {
      URL.revokeObjectURL(url)
      reject(e)
    }
    img.src = url
  })
}

/**
 * Register a list of icon ids onto the given MapLibre map. Safe to
 * call multiple times — already-registered ids are skipped.
 */
export async function registerIcons(map, ids, pixelSize = 24) {
  if (!map || !ids || ids.length === 0) return
  const { symbols } = await _loadSvgText()
  const available = new Map(symbols.map((s) => [s.id, s]))
  for (const id of ids) {
    if (!id) continue
    if (map.hasImage(id)) continue          // already on the map
    const sym = available.get(id)
    if (!sym) continue
    const img = await _renderSymbol(sym, pixelSize)
    // White glyph over the colored pin body. No halo — the pin's white
    // ring already provides contrast against arbitrary basemap styles.
    map.addImage(id, img, { sdf: false })
  }
}

/**
 * Return the list of icon ids defined in the sprite. Used by the
 * layer editor to render the picker grid.
 */
export async function listIconIds() {
  const { ids } = await _loadSvgText()
  return [...ids].sort()
}

export const ICON_NAMES = [
  'pin-marker',
  'pin-dot',
  'pin-star',
  'pin-square',
  'pin-diamond',
  'pin-triangle',
  'pin-flag',
  'pin-crosshair',
  'pin-building',
  'pin-home',
  'pin-cart',
  'pin-truck',
  'pin-warn',
  'pin-check',
  'pin-x',
  'pin-dollar',
]

/**
 * Inline SVG path data for each icon. Used by the layer editor picker
 * to render previews without depending on cross-document <use href>
 * references, which can fail in some browsers / when the SVG is served
 * with the wrong MIME type. Paths match the <symbol>s in
 * expedition/public/icons.svg exactly.
 */
export const ICON_PATHS = {
  'pin-marker': 'M12 2c-4.4 0-8 3.4-8 7.6 0 5.5 7.2 11.6 7.5 11.9.3.3.7.3 1 0 .3-.3 7.5-6.4 7.5-11.9C20 5.4 16.4 2 12 2zm0 10.5a3 3 0 1 1 0-6 3 3 0 0 1 0 6z',
  'pin-dot': 'M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0',
  'pin-star': 'M12 2.6l2.85 5.77 6.37.93-4.61 4.49 1.09 6.34L12 17.1l-5.7 2.99 1.09-6.34-4.61-4.49 6.37-.93L12 2.6z',
  'pin-square': 'M4 4h16v16H4z',
  'pin-diamond': 'M12 2 L22 12 L12 22 L2 12 Z',
  'pin-triangle': 'M12 3 L22 21 L2 21 Z',
  'pin-flag': 'M5 3v18M5 4h14l-3 4 3 4H5',
  'pin-crosshair': 'M12 2v6M12 16v6M2 12h6M16 12h6M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  'pin-building': 'M4 21V7l8-4 8 4v14M9 21v-6h6v6M7 11h2M7 14h2M15 11h2M15 14h2',
  'pin-home': 'M3 11l9-8 9 8v10H3zM9 21v-7h6v7',
  'pin-cart': 'M3 4h2l3 12h11l2-8H7M9 20a1 1 0 1 1 0-2 1 1 0 0 1 0 2zM18 20a1 1 0 1 1 0-2 1 1 0 0 1 0 2z',
  'pin-truck': 'M3 6h11v10H3zM14 9h4l3 4v3h-7M6 19a1 1 0 1 1 0-2 1 1 0 0 1 0 2zM18 19a1 1 0 1 1 0-2 1 1 0 0 1 0 2z',
  'pin-warn': 'M12 2L2 21h20L12 2zM12 9v5M12 17v.5',
  'pin-check': 'M5 12l5 5 9-11',
  'pin-x': 'M6 6l12 12M18 6L6 18',
  'pin-dollar': 'M12 2v20M16 6H10a3 3 0 0 0 0 6h4a3 3 0 0 1 0 6H8',
}
