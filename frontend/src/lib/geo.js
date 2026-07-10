const WORLD_WEST = -180
const WORLD_EAST = 180
const MERCATOR_SOUTH = -85.051129
const MERCATOR_NORTH = 85.051129

export function wrapLng(lng) {
  const n = Number(lng)
  if (!Number.isFinite(n)) return n
  const wrapped = ((((n + 180) % 360) + 360) % 360) - 180
  return Object.is(wrapped, -0) ? 0 : wrapped
}

export function normalizeLngLat(lngLat) {
  if (!lngLat) return lngLat
  if (Array.isArray(lngLat)) return [wrapLng(lngLat[0]), lngLat[1]]
  return { ...lngLat, lng: wrapLng(lngLat.lng) }
}

export function shortestLngDelta(fromLng, toLng) {
  return wrapLng(Number(toLng) - Number(fromLng))
}

export function normalizeGeometryLngs(geometry) {
  if (!geometry) return geometry
  const normalizeCoords = (coords, depth) => {
    if (depth === 0) return normalizeLngLat(coords)
    return (coords || []).map((item) => normalizeCoords(item, depth - 1))
  }
  if (geometry.type === 'Point') {
    return { ...geometry, coordinates: normalizeCoords(geometry.coordinates, 0) }
  }
  if (geometry.type === 'LineString' || geometry.type === 'MultiPoint') {
    return { ...geometry, coordinates: normalizeCoords(geometry.coordinates, 1) }
  }
  if (geometry.type === 'Polygon' || geometry.type === 'MultiLineString') {
    return { ...geometry, coordinates: normalizeCoords(geometry.coordinates, 2) }
  }
  if (geometry.type === 'MultiPolygon') {
    return { ...geometry, coordinates: normalizeCoords(geometry.coordinates, 3) }
  }
  return geometry
}

function clampLat(lat) {
  const n = Number(lat)
  if (!Number.isFinite(n)) return n
  return Math.max(MERCATOR_SOUTH, Math.min(MERCATOR_NORTH, n))
}

export function viewportBoundsForServer(bounds) {
  if (!bounds) return null
  const south = clampLat(bounds.getSouth())
  const north = clampLat(bounds.getNorth())
  const rawWest = Number(bounds.getWest())
  const rawEast = Number(bounds.getEast())
  if (!Number.isFinite(rawWest) || !Number.isFinite(rawEast)) return null

  const span = rawEast - rawWest
  if (span >= 360) {
    return [{ south, west: WORLD_WEST, north, east: WORLD_EAST }]
  }

  const west = wrapLng(rawWest)
  const east = wrapLng(rawEast)
  if (west <= east) return [{ south, west, north, east }]
  return [
    { south, west, north, east: WORLD_EAST },
    { south, west: WORLD_WEST, north, east },
  ]
}
