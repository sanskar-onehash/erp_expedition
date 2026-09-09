const BLANK_GROUP_VALUE = '(blank)'

function featureProperties(feature) {
  const properties = feature?.properties
  return properties && typeof properties === 'object' ? properties : {}
}

function isGroupableValue(value) {
  return value == null || ['string', 'number', 'boolean'].includes(typeof value)
}

function fieldLabel(fieldname) {
  return String(fieldname || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function displayGroupValue(value) {
  return value == null || value === '' ? BLANK_GROUP_VALUE : String(value)
}

/**
 * Python-script layers have no DocType schema to populate field selectors.
 * Treat public scalar feature properties as exact-value grouping fields.
 */
export function inferFeaturePropertyFields(featureCollection, currentField = '') {
  const fields = new Set()
  for (const feature of featureCollection?.features || []) {
    for (const [fieldname, value] of Object.entries(featureProperties(feature))) {
      if (!fieldname || fieldname.startsWith('_') || !isGroupableValue(value)) continue
      fields.add(fieldname)
    }
  }
  if (currentField && !String(currentField).startsWith('_')) fields.add(String(currentField))
  return [...fields]
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
    .map((fieldname) => ({
      fieldname,
      fieldtype: 'Data',
      label: fieldLabel(fieldname),
      options: '',
    }))
}

export function distinctFeaturePropertyValues(featureCollection, fieldname) {
  if (!fieldname) return []
  const values = new Set()
  for (const feature of featureCollection?.features || []) {
    const properties = featureProperties(feature)
    if (!Object.prototype.hasOwnProperty.call(properties, fieldname)) {
      values.add(BLANK_GROUP_VALUE)
      continue
    }
    const value = properties[fieldname]
    if (!isGroupableValue(value)) continue
    values.add(displayGroupValue(value))
  }
  return [...values].sort((a, b) => {
    if (a === BLANK_GROUP_VALUE) return 1
    if (b === BLANK_GROUP_VALUE) return -1
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
  })
}

/** Prefer grouping metadata produced by the server, then fall back to a
 * configured property. The fallback enables grouping for script layers.
 */
export function featureGroupKey(feature, groupByField = '') {
  const properties = featureProperties(feature)
  if (properties._group_value != null && properties._group_value !== '') {
    return String(properties._group_value)
  }
  if (Object.prototype.hasOwnProperty.call(properties, '_group_value')) {
    return BLANK_GROUP_VALUE
  }
  if (Array.isArray(properties._group_path) && properties._group_path.length) {
    return properties._group_path.join('\x1f')
  }
  if (groupByField) return displayGroupValue(properties[groupByField])
  return null
}
