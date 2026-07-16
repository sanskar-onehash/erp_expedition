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
 * with the same generated image ids are no-ops.
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
function _renderSymbol(symbolEl, size, color = 'currentColor') {
  return new Promise((resolve, reject) => {
    if (!symbolEl) {
      resolve(null)
      return
    }
    // Re-emit the symbol's inner SVG as a standalone <svg> so the
    // browser renders it (browsers don't render <symbol> directly).
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    svg.setAttribute('xmlns', ns)
    svg.setAttribute('viewBox', symbolEl.getAttribute('viewBox') || '0 0 24 24')
    svg.setAttribute('width', String(size))
    svg.setAttribute('height', String(size))
    svg.style.color = color
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

function _renderSvgText(svgText, size, color = 'currentColor') {
  return new Promise((resolve, reject) => {
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml')
    const svg = doc.documentElement
    if (!svg || svg.nodeName.toLowerCase() !== 'svg') {
      reject(new Error('Invalid custom SVG'))
      return
    }
    svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    svg.setAttribute('width', String(size))
    svg.setAttribute('height', String(size))
    svg.style.color = color

    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' })
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

function _renderImageDataUrl(dataUrl, size) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = size
      canvas.height = size
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Could not prepare image canvas'))
        return
      }
      if (!img.naturalWidth || !img.naturalHeight) {
        reject(new Error('Invalid image dimensions'))
        return
      }
      const scale = Math.min(size / img.naturalWidth, size / img.naturalHeight)
      const width = img.naturalWidth * scale
      const height = img.naturalHeight * scale
      const x = (size - width) / 2
      const y = (size - height) / 2
      ctx.clearRect(0, 0, size, size)
      ctx.drawImage(img, x, y, width, height)
      resolve(ctx.getImageData(0, 0, size, size))
    }
    img.onerror = (e) => reject(e)
    img.src = dataUrl
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
    map.addImage(id, img, { sdf: false })
  }
}

export function coloredIconImageId(id, color) {
  const safeColor = encodeURIComponent(color || '#3B82F6')
  return `${id}--${safeColor}`
}

export async function registerColoredIcons(map, specs, pixelSize = 28) {
  if (!map || !specs || specs.length === 0) return
  const needsBuiltins = specs.some((spec) => !spec?.svg && !spec?.imageDataUrl)
  const available = needsBuiltins
    ? new Map((await _loadSvgText()).symbols.map((s) => [s.id, s]))
    : new Map()
  for (const spec of specs) {
    const id = spec?.id
    const color = spec?.color || '#3B82F6'
    const imageId = spec?.imageId || coloredIconImageId(id, color)
    if (!id || !imageId) continue
    if (map.hasImage(imageId)) continue
    const img = spec.svg
      ? await _renderSvgText(spec.svg, pixelSize, color)
      : spec.imageDataUrl
        ? await _renderImageDataUrl(spec.imageDataUrl, pixelSize)
      : await _renderSymbol(available.get(id), pixelSize, color)
    if (!img) continue
    if (map.hasImage(imageId)) continue
    try {
      map.addImage(imageId, img, { sdf: false })
    } catch (e) {
      if (!String(e?.message || e).includes(`An image named "${imageId}" already exists`)) {
        throw e
      }
    }
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
  'pin-x': 'M18.3 5.7L12 12l6.3 6.3-1.4 1.4L10.6 13.4l-6.3 6.3-1.4-1.4L9.2 12 2.9 5.7l1.4-1.4L10.6 10.6l6.3-6.3z',
  'pin-dollar': 'M12 2v20M16 6H10a3 3 0 0 0 0 6h4a3 3 0 0 1 0 6H8',
}
