const EMPTY_FEATURE_COLLECTION = Object.freeze({
  type: 'FeatureCollection',
  features: [],
})

export function parseGeoJSON(value) {
  if (!value) return null
  if (typeof value === 'object') return value
  try {
    return JSON.parse(value)
  } catch (_) {
    return null
  }
}

export function pointFromGeoJSON(value) {
  const geojson = parseGeoJSON(value)
  if (!geojson) return null

  let geometry = null
  if (geojson.type === 'FeatureCollection') {
    geometry = geojson.features?.find((feature) => feature?.geometry?.type === 'Point')?.geometry
  } else if (geojson.type === 'Feature') {
    geometry = geojson.geometry
  } else {
    geometry = geojson
  }

  if (geometry?.type !== 'Point' || !Array.isArray(geometry.coordinates)) return null
  const lng = Number(geometry.coordinates[0])
  const lat = Number(geometry.coordinates[1])
  if (!validCoordinates(lat, lng)) return null
  return { latitude: lat, longitude: lng }
}

export function setPointInGeoJSON(value, latitude, longitude) {
  const lat = Number(latitude)
  const lng = Number(longitude)
  if (!validCoordinates(lat, lng)) throw new Error('Invalid map coordinates')

  const existing = parseGeoJSON(value)
  const collection = toFeatureCollection(existing)
  const pointIndex = collection.features.findIndex(
    (feature) => feature?.type === 'Feature' && feature?.geometry?.type === 'Point'
  )
  const previous = pointIndex >= 0 ? collection.features[pointIndex] : null
  const point = {
    type: 'Feature',
    properties: { ...(previous?.properties || {}) },
    geometry: { type: 'Point', coordinates: [lng, lat] },
  }

  if (pointIndex >= 0) collection.features.splice(pointIndex, 1, point)
  else collection.features.push(point)
  return JSON.stringify(collection)
}

export function clearPointsFromGeoJSON(value) {
  const existing = parseGeoJSON(value)
  if (!existing) return JSON.stringify(EMPTY_FEATURE_COLLECTION)
  const collection = toFeatureCollection(existing)
  collection.features = collection.features.filter(
    (feature) => feature?.geometry?.type !== 'Point'
  )
  return JSON.stringify(collection)
}

function toFeatureCollection(value) {
  if (value?.type === 'FeatureCollection') {
    return {
      ...value,
      features: Array.isArray(value.features)
        ? value.features.map((feature) => structuredCloneSafe(feature))
        : [],
    }
  }
  if (value?.type === 'Feature') {
    return { type: 'FeatureCollection', features: [structuredCloneSafe(value)] }
  }
  if (value?.type && Array.isArray(value.coordinates)) {
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: structuredCloneSafe(value) }],
    }
  }
  return { type: 'FeatureCollection', features: [] }
}

function structuredCloneSafe(value) {
  if (typeof structuredClone === 'function') return structuredClone(value)
  return JSON.parse(JSON.stringify(value))
}

function validCoordinates(lat, lng) {
  return Number.isFinite(lat)
    && Number.isFinite(lng)
    && lat >= -90
    && lat <= 90
    && lng >= -180
    && lng <= 180
}
