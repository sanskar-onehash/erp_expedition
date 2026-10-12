const INDIA_BOUNDARY_SOURCE = 'india-boundary-source'
const INDIA_BOUNDARY_LAYER = 'india-boundary-layer'

/**
 * Replace provider-supplied India boundary lines with the same official
 * boundary overlay used by the main Expedition map. This needs to be applied
 * to each independent MapLibre instance; the embedded field has its own map.
 */
export function applyIndiaBoundaryFix(map, { styleGraphReady = false } = {}) {
  if (!map || !map.getStyle() || (!styleGraphReady && !map.isStyleLoaded())) return

  const excludeIndiaExpression = [
    '!',
    [
      'any',
      ['==', ['get', 'adm0_l'], 'IN'],
      ['==', ['get', 'adm0_r'], 'IN'],
      ['==', ['get', 'adm0_l'], 'IND'],
      ['==', ['get', 'adm0_r'], 'IND'],
      ['==', ['get', 'adm0_l'], 'India'],
      ['==', ['get', 'adm0_r'], 'India'],
      ['==', ['get', 'adm0_l'], 'in'],
      ['==', ['get', 'adm0_r'], 'in'],
      ['==', ['get', 'adm0_l'], 'ind'],
      ['==', ['get', 'adm0_r'], 'ind'],
      ['==', ['get', 'adm0_l'], 'india'],
      ['==', ['get', 'adm0_r'], 'india'],
      ['==', ['get', 'adm0_left'], 'IN'],
      ['==', ['get', 'adm0_right'], 'IN'],
      ['==', ['get', 'adm0_left'], 'IND'],
      ['==', ['get', 'adm0_right'], 'IND'],
      ['==', ['get', 'adm0_left'], 'India'],
      ['==', ['get', 'adm0_right'], 'India'],
    ],
  ]

  for (const layerId of ['boundary_2', 'boundary_disputed']) {
    if (!map.getLayer(layerId)) continue
    try {
      let filter = map.getFilter(layerId)
      if (!filter) filter = ['all']
      else if (!Array.isArray(filter) || filter[0] !== 'all') filter = ['all', filter]

      const alreadyAdded = filter.some((expression) => (
        Array.isArray(expression)
        && expression[0] === '!'
        && Array.isArray(expression[1])
        && expression[1][0] === 'any'
        && expression[1].some((valueExpression) => (
          Array.isArray(valueExpression)
          && valueExpression[1]?.[1] === 'adm0_l'
          && (valueExpression[2] === 'IN' || valueExpression[2] === 'IND')
        ))
      ))
      if (!alreadyAdded) map.setFilter(layerId, [...filter, excludeIndiaExpression])
    } catch (error) {
      console.error(`[expedition] unable to update ${layerId} for India boundary`, error)
    }
  }

  if (!map.getSource(INDIA_BOUNDARY_SOURCE)) {
    try {
      map.addSource(INDIA_BOUNDARY_SOURCE, {
        type: 'geojson',
        data: `${window.location.origin}/assets/expedition/india-land-simplified.geojson`,
      })
    } catch (error) {
      console.error('[expedition] unable to add India boundary source', error)
    }
  }

  if (!map.getSource(INDIA_BOUNDARY_SOURCE) || map.getLayer(INDIA_BOUNDARY_LAYER)) return

  try {
    const paint = {
      'line-color': 'hsl(248,1%,41%)',
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 0, 0.4, 4, 1],
      'line-width': ['interpolate', ['linear'], ['zoom'], 3, 1, 5, 1.2, 12, 3],
    }
    if (map.getLayer('boundary_2')) {
      for (const property of ['line-color', 'line-opacity', 'line-width']) {
        const value = map.getPaintProperty('boundary_2', property)
        if (value) paint[property] = value
      }
    }

    const firstSymbol = (map.getStyle().layers || []).find((layer) => layer.type === 'symbol')
    map.addLayer({
      id: INDIA_BOUNDARY_LAYER,
      type: 'line',
      source: INDIA_BOUNDARY_SOURCE,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint,
    }, firstSymbol?.id)
  } catch (error) {
    console.error('[expedition] unable to add India boundary layer', error)
  }
}
