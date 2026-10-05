import test from 'node:test'
import assert from 'node:assert/strict'

import {
  clearPointsFromGeoJSON,
  pointFromGeoJSON,
  setPointInGeoJSON,
} from '../src/geolocation/geojson.js'

test('reads a point from standard Frappe GeoJSON', () => {
  const value = JSON.stringify({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: [105.8542, 21.0285] },
    }],
  })

  assert.deepEqual(pointFromGeoJSON(value), {
    latitude: 21.0285,
    longitude: 105.8542,
  })
})

test('updates the point without discarding other geometry', () => {
  const polygon = {
    type: 'Feature',
    properties: { name: 'service area' },
    geometry: {
      type: 'Polygon',
      coordinates: [[[1, 1], [2, 1], [2, 2], [1, 1]]],
    },
  }
  const value = JSON.stringify({
    type: 'FeatureCollection',
    features: [
      polygon,
      {
        type: 'Feature',
        properties: { source: 'existing' },
        geometry: { type: 'Point', coordinates: [10, 20] },
      },
    ],
  })

  const updated = JSON.parse(setPointInGeoJSON(value, 30, 40))
  assert.deepEqual(updated.features[0], polygon)
  assert.deepEqual(updated.features[1].geometry.coordinates, [40, 30])
  assert.equal(updated.features[1].properties.source, 'existing')
})

test('clearing removes points but preserves other geometry', () => {
  const value = JSON.stringify({
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [1, 2] } },
      { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[1, 2], [3, 4]] } },
    ],
  })

  const cleared = JSON.parse(clearPointsFromGeoJSON(value))
  assert.equal(cleared.features.length, 1)
  assert.equal(cleared.features[0].geometry.type, 'LineString')
})

test('rejects invalid coordinates', () => {
  assert.throws(() => setPointInGeoJSON('', 91, 10), /Invalid map coordinates/)
  assert.equal(pointFromGeoJSON('{invalid'), null)
})
