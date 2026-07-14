export function parseFilterRows(json) {
  if (!json) return []
  try {
    const parsed = typeof json === 'string' ? JSON.parse(json) : json
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((row) => Array.isArray(row) && (row.length === 2 || row.length === 3 || row.length === 4))
      .map((row) => {
        if (row.length === 4) {
          const [child_doctype, field, op, value] = row
          return { field: `${child_doctype}:${field}`, op: op || '=', value: value ?? '' }
        } else {
          const [field, op, value] = row
          return { field, op: op || '=', value: value ?? '' }
        }
      })
  } catch {
    return []
  }
}

export function serializeFilterRows(rows) {
  const out = (rows || [])
    .filter((r) => r && r.field && r.op)
    .map((r) => {
      if (r.field.includes(':')) {
        const [child_doctype, fieldname] = r.field.split(':')
        return [child_doctype, fieldname, r.op, r.value]
      }
      return [r.field, r.op, r.value]
    })
  return out.length ? JSON.stringify(out) : ''
}

export function filterCount(filterJson) {
  return parseFilterRows(filterJson).length
}

export function summarizeFilterRows(rows, fields = []) {
  const fieldMap = new Map((fields || []).map((f) => [f.fieldname, f]))
  return (rows || [])
    .filter((r) => r && r.field && r.op)
    .map((r) => {
      const meta = fieldMap.get(r.field)
      const label = meta?.label || r.field
      const op = operatorLabel(r.op, r.value)
      const value = valueLabel(r.value, meta)
      return value ? `${label} ${op} ${value}` : `${label} ${op}`
    })
}

export function operatorLabel(op, value) {
  if (op === '=') return '='
  if (op === '!=') return '!='
  if (op === 'like') return 'contains'
  if (op === 'not like') return 'not contains'
  if (op === 'in') return 'in'
  if (op === 'not in') return 'not in'
  if (op === 'between') return 'between'
  if (op === 'is') return String(value || '').toLowerCase() === 'not set' ? 'is not set' : 'is set'
  return op || '='
}

export function valueLabel(value, meta = null) {
  if (value == null || value === '') return ''
  if (Array.isArray(value)) return value.map((item) => valueLabel(item, meta)).join(', ')
  const options = Array.isArray(meta?.select_options) ? meta.select_options : []
  const match = options.find((item) => {
    const optionValue = item && typeof item === 'object' ? item.value : item
    return String(optionValue) === String(value)
  })
  if (match && typeof match === 'object') return String(match.label ?? match.value ?? '')
  if (match != null) return String(match)
  return String(value)
}
