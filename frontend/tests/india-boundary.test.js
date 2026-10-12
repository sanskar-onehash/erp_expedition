import assert from 'node:assert/strict'
import test from 'node:test'

import { applyIndiaBoundaryFix } from '../src/geolocation/india-boundary.js'

function makeMap() {
  const sources = new Map()
  const layers = new Map([
    ['boundary_2', { id: 'boundary_2', type: 'line' }],
    ['boundary_disputed', { id: 'boundary_disputed', type: 'line' }],
  ])
  const filters = new Map()
  const addedLayers = []

  return {
    addedLayers,
    getStyle: () => ({ layers: [...layers.values(), { id: 'place-label', type: 'symbol' }] }),
    getLayer: (id) => layers.get(id),
    getSource: (id) => sources.get(id),
    getFilter: (id) => filters.get(id),
    setFilter: (id, filter) => filters.set(id, filter),
    getPaintProperty: () => null,
    addSource: (id, source) => sources.set(id, source),
    addLayer: (layer, beforeId) => {
      layers.set(layer.id, layer)
      addedLayers.push({ layer, beforeId })
    },
  }
}

test('applies the India boundary overlay to an embedded MapLibre map once', () => {
  const previousWindow = globalThis.window
  globalThis.window = { location: { origin: 'https://bmscrm.bmscg.com:5554' } }

  try {
    const map = makeMap()
    applyIndiaBoundaryFix(map, { styleGraphReady: true })
    applyIndiaBoundaryFix(map, { styleGraphReady: true })

    assert.equal(map.addedLayers.length, 1)
    assert.equal(map.addedLayers[0].layer.id, 'india-boundary-layer')
    assert.equal(map.addedLayers[0].beforeId, 'place-label')
    assert.equal(map.addedLayers[0].layer.source, 'india-boundary-source')
    assert.equal(
      map.getSource('india-boundary-source').data,
      'https://bmscrm.bmscg.com:5554/assets/expedition/india-land-simplified.geojson',
    )
    assert.equal(map.getFilter('boundary_2').length, 2)
    assert.equal(map.getFilter('boundary_disputed').length, 2)
  } finally {
    globalThis.window = previousWindow
  }
})
