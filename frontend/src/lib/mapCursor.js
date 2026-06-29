function svgCursor(body, hotspot = 12, fallback = 'crosshair') {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">${body}</svg>`
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${hotspot} ${hotspot}, ${fallback}`
}

const CURSORS = {
  crosshair: svgCursor([
    '<path d="M12 4v6M12 14v6M4 12h6M14 12h6" stroke="white" stroke-width="3" stroke-linecap="square"/>',
    '<path d="M12 4v6M12 14v6M4 12h6M14 12h6" stroke="#0f172a" stroke-width="1.5" stroke-linecap="square"/>',
    '<circle cx="12" cy="12" r="1.75" fill="none" stroke="white" stroke-width="3"/>',
    '<circle cx="12" cy="12" r="1.75" fill="none" stroke="#0f172a" stroke-width="1.5"/>',
  ].join('')),
  cross: svgCursor([
    '<path d="M7 7l10 10M17 7L7 17" stroke="white" stroke-width="3" stroke-linecap="round"/>',
    '<path d="M7 7l10 10M17 7L7 17" stroke="#0f172a" stroke-width="1.5" stroke-linecap="round"/>',
  ].join('')),
  circle: svgCursor([
    '<circle cx="12" cy="12" r="6" fill="none" stroke="white" stroke-width="3"/>',
    '<circle cx="12" cy="12" r="6" fill="none" stroke="#0f172a" stroke-width="1.5"/>',
  ].join('')),
  dot: svgCursor([
    '<circle cx="12" cy="12" r="4" fill="white"/>',
    '<circle cx="12" cy="12" r="3" fill="#0f172a"/>',
  ].join('')),
}

export function mapCursorStyle(kind) {
  if (kind === 'default') return 'default'
  if (kind === 'pointer') return 'pointer'
  if (kind === 'ns-resize') return 'ns-resize'
  return CURSORS[kind] || CURSORS.crosshair
}

export function applyMapCursor(canvas, kind) {
  if (!canvas) return
  canvas.style.cursor = mapCursorStyle(kind)
}

export function activeMapCursor(ui) {
  return ui?.prefs?.cursor || 'crosshair'
}
