const NUMBER_FIELD_TYPES = new Set(['Currency', 'Duration', 'Float', 'Int', 'Percent', 'Rating'])
const DATE_FIELD_TYPES = new Set(['Date', 'Datetime', 'Time'])
const BOOLEAN_FIELD_TYPES = new Set(['Check'])
const OPERATOR_RE = /^(>=|<=|!=|=|>|<)\s*(.*)$/
const ORDER_OPERATORS = new Set(['>', '>=', '<', '<='])

export function parseMapSearch(input) {
  const raw = String(input || '').trim()
  if (!raw) return null
  const tree = hasStructuredSyntax(raw)
    ? parseBooleanExpression(tokenizeSearch(raw))
    : parseSearchExpression(raw)
  const expressions = collectExpressions(tree)
  if (!expressions.length) return null
  return {
    raw,
    tree,
    groups: treeToGroups(tree),
    expressions,
    mode: tree?.type === 'or' ? 'disjunction' : expressions.length === 1 ? expressions[0].mode : 'compound',
  }
}

function hasStructuredSyntax(raw) {
  return /(^|\s)or(\s|$)/i.test(raw)
    || /[()]/.test(raw)
    || splitTopLevelWhitespace(raw).some((token) => splitTopLevel(token, ':').length > 1)
}

function tokenizeSearch(source) {
  const tokens = []
  let buf = ''
  let quote = ''
  let escaping = false
  for (const ch of String(source || '').replace(/\n+/g, ' ')) {
    if (escaping) {
      buf += ch
      escaping = false
      continue
    }
    if (ch === '\\') {
      escaping = true
      buf += ch
      continue
    }
    if (quote) {
      if (ch === quote) quote = ''
      buf += ch
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      buf += ch
      continue
    }
    if (ch === '(' || ch === ')') {
      if (buf.trim()) tokens.push(buf.trim())
      tokens.push(ch)
      buf = ''
      continue
    }
    if (/\s/.test(ch)) {
      if (buf.trim()) tokens.push(buf.trim())
      buf = ''
      continue
    }
    buf += ch
  }
  if (buf.trim()) tokens.push(buf.trim())
  return tokens
}

function parseBooleanExpression(tokens) {
  let index = 0

  function parseOr() {
    let node = parseAnd()
    while (isOr(tokens[index])) {
      index += 1
      const right = parseAnd()
      if (!right) break
      node = combineNode('or', node, right)
    }
    return node
  }

  function parseAnd() {
    const nodes = []
    while (index < tokens.length && tokens[index] !== ')' && !isOr(tokens[index])) {
      const node = parsePrimary()
      if (node) nodes.push(node)
      else break
    }
    if (!nodes.length) return null
    return nodes.length === 1 ? nodes[0] : { type: 'and', children: nodes }
  }

  function parsePrimary() {
    const token = tokens[index]
    if (token === '(') {
      index += 1
      const node = parseOr()
      if (tokens[index] === ')') index += 1
      return node
    }
    index += 1
    const expression = parseSearchExpression(token)
    return expression ? { type: 'leaf', expression } : null
  }

  return parseOr()
}

function isOr(token) {
  return String(token || '').trim().toLowerCase() === 'or'
}

function combineNode(type, left, right) {
  const children = []
  if (left?.type === type) children.push(...left.children)
  else if (left) children.push(left)
  if (right?.type === type) children.push(...right.children)
  else if (right) children.push(right)
  return { type, children }
}

function collectExpressions(node) {
  if (!node) return []
  if (node.mode) return [node]
  if (node.type === 'leaf') return node.expression ? [node.expression] : []
  return (node.children || []).flatMap(collectExpressions)
}

function treeToGroups(node) {
  if (!node) return []
  if (node.type === 'or') return (node.children || []).map((child) => collectExpressions(child))
  return [collectExpressions(node)]
}

function splitTopLevelWhitespace(source) {
  const parts = []
  let buf = ''
  let quote = ''
  let escaping = false
  for (const ch of String(source || '')) {
    if (escaping) {
      buf += ch
      escaping = false
      continue
    }
    if (ch === '\\') {
      escaping = true
      buf += ch
      continue
    }
    if (quote) {
      if (ch === quote) quote = ''
      buf += ch
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      buf += ch
      continue
    }
    if (/\s/.test(ch)) {
      if (buf.trim()) parts.push(buf.trim())
      buf = ''
      continue
    }
    buf += ch
  }
  if (buf.trim()) parts.push(buf.trim())
  return parts
}

function parseSearchExpression(raw) {
  if (!raw) return null
  const parts = splitTopLevel(raw, ':').map((part) => unquote(part.trim()))
  if (parts.length === 1) {
    return { mode: 'text', raw, value: parts[0] }
  }
  if (parts.length === 2 && parts[0] && parts[1]) {
    return {
      mode: 'field',
      raw,
      field: parts[0],
      ...parseCondition(parts[1]),
    }
  }
  if (parts.length >= 3 && parts[0] && parts[1]) {
    return {
      mode: 'doctype_field',
      raw,
      doctype: parts[0],
      field: parts[1],
      ...parseCondition(parts.slice(2).join(':')),
    }
  }
  return { mode: 'text', raw, value: raw }
}

function parseCondition(rawValue) {
  const source = String(rawValue || '').trim()
  const match = source.match(OPERATOR_RE)
  if (match) {
    return {
      operator: match[1],
      value: unquote(match[2].trim()),
    }
  }
  return { operator: null, value: unquote(source) }
}

function splitTopLevel(source, separator) {
  const parts = []
  let buf = ''
  let quote = ''
  let escaping = false
  for (const ch of String(source || '')) {
    if (escaping) {
      buf += ch
      escaping = false
      continue
    }
    if (ch === '\\') {
      escaping = true
      buf += ch
      continue
    }
    if (quote) {
      if (ch === quote) quote = ''
      buf += ch
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      buf += ch
      continue
    }
    if (ch === separator) {
      parts.push(buf)
      buf = ''
      continue
    }
    buf += ch
  }
  parts.push(buf)
  return parts
}

function unquote(value) {
  const text = String(value ?? '').trim()
  if (text.length >= 2) {
    const first = text[0]
    const last = text[text.length - 1]
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return text.slice(1, -1).replace(/\\(["'\\])/g, '$1')
    }
  }
  return text
}

export function applyParsedMapSearchToFeatureCollection(fc, layer, fieldMeta, parsed, context = {}) {
  if (!fc || !Array.isArray(fc.features) || !parsed) return fc
  const features = fc.features.filter((feature) =>
    matchesParsedSearch(feature, layer, fieldMeta, parsed, context)
  )
  return { ...fc, features }
}

export function countParsedMapSearchMatches(fc, layer, fieldMeta, parsed, context = {}) {
  if (!fc || !Array.isArray(fc.features) || !parsed) return 0
  return fc.features.reduce(
    (total, feature) => total + (matchesParsedSearch(feature, layer, fieldMeta, parsed, context) ? 1 : 0),
    0,
  )
}

function matchesParsedSearch(feature, layer, fieldMeta, parsed, context = {}) {
  if (parsed?.tree) return matchesNode(feature, layer, fieldMeta, parsed.tree, context)
  return (parsed?.expressions || []).every((expression) =>
    matchesExpression(feature, layer, fieldMeta, expression, context)
  )
}

function matchesNode(feature, layer, fieldMeta, node, context = {}) {
  if (!node) return true
  if (node.mode) return matchesExpression(feature, layer, fieldMeta, node, context)
  if (node.type === 'leaf') return matchesExpression(feature, layer, fieldMeta, node.expression, context)
  if (node.type === 'or') return (node.children || []).some((child) => matchesNode(feature, layer, fieldMeta, child, context))
  if (node.type === 'and') return (node.children || []).every((child) => matchesNode(feature, layer, fieldMeta, child, context))
  return true
}

function matchesExpression(feature, layer, fieldMeta, expression, context = {}) {
  if (!expression) return true
  if (expression.mode === 'text') return matchesGlobalText(feature, expression.value, context)
  if (expression.mode === 'doctype_field' && !sameDoctype(layer?.source_doctype, expression.doctype)) {
    return false
  }
  const props = feature?.properties || {}
  const meta = resolveFieldMeta(fieldMeta, expression.field) || inferFieldMeta(props, expression.field)
  if (!meta) return false
  const field = meta.fieldname || expression.field
  if (!Object.prototype.hasOwnProperty.call(props, field)) return false
  return compareFieldValue(props[field], expression, meta)
}

function sameDoctype(a, b) {
  return normalizeToken(a) === normalizeToken(b)
}

function resolveFieldMeta(fields, field) {
  const wanted = normalizeToken(field)
  return (fields || []).find((item) =>
    normalizeToken(item?.fieldname) === wanted
    || normalizeToken(item?.label) === wanted
  ) || null
}

function inferFieldMeta(props, field) {
  const wanted = normalizeToken(field)
  const key = Object.keys(props || {}).find((item) => normalizeToken(item) === wanted)
  if (!key) return null
  const value = props[key]
  let fieldtype = 'Data'
  if (typeof value === 'number') fieldtype = Number.isInteger(value) ? 'Int' : 'Float'
  else if (typeof value === 'boolean') fieldtype = 'Check'
  else if (looksLikeDate(value)) fieldtype = 'Datetime'
  return { fieldname: key, fieldtype, label: key }
}

function looksLikeDate(value) {
  if (value instanceof Date) return true
  return /^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?/.test(String(value || '').trim())
}

function normalizeToken(value) {
  return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, '')
}

function matchesGlobalText(feature, query, context = {}) {
  const featureName = feature?.properties?._name || feature?.properties?.name || feature?.id
  const textMatches = context.textMatchesByQuery?.[String(query || '')] || context.textMatches
  if (textMatches instanceof Set && textMatches.has(String(featureName))) return true
  const needle = normalizeText(query)
  if (!needle) return true
  const props = feature?.properties || {}
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith('_') && key !== '_name' && key !== '_label' && key !== '_id') continue
    if (valueMatchesText(value, needle)) return true
  }
  return false
}

function valueMatchesText(value, needle) {
  if (value == null) return false
  if (Array.isArray(value)) return value.some((item) => valueMatchesText(item, needle))
  if (typeof value === 'object') return normalizeText(JSON.stringify(value)).includes(needle)
  return normalizeText(value).includes(needle)
}

function normalizeText(value) {
  return String(value ?? '').trim().toLowerCase()
}

function compareFieldValue(actual, expression, meta) {
  const fieldtype = meta?.fieldtype || 'Data'
  if (actual == null || actual === '') return false
  if (ORDER_OPERATORS.has(expression.operator)) {
    const ordered = compareInferredOrdered(actual, expression)
    if (ordered != null) return ordered
  }
  if (NUMBER_FIELD_TYPES.has(fieldtype)) return compareNumber(actual, expression)
  if (DATE_FIELD_TYPES.has(fieldtype)) return compareDateLike(actual, expression, fieldtype)
  if (BOOLEAN_FIELD_TYPES.has(fieldtype)) return compareBoolean(actual, expression)
  return compareString(actual, expression)
}

function compareInferredOrdered(actual, expression) {
  if (isNumericLike(actual) && isNumericLike(expression.value)) {
    return compareNumber(actual, expression)
  }

  const actualText = String(actual ?? '').trim()
  const queryText = String(expression.value ?? '').trim()
  const fieldtype = actualText.includes(':') || queryText.includes(':') ? 'Datetime' : 'Date'
  const actualDate = parseDateComparable(actualText, fieldtype)
  const queryDate = parseDateQuery(queryText, fieldtype)
  if (actualDate != null && queryDate) {
    const pivot = expression.operator === '<' || expression.operator === '<='
      ? queryDate.start
      : queryDate.end
    return compareOrdered(actualDate, pivot, expression.operator)
  }
  return null
}

function isNumericLike(value) {
  return parseNumericValue(value) != null
}

function compareNumber(actual, expression) {
  const left = parseNumericValue(actual)
  const right = parseNumericValue(expression.value)
  if (left == null || right == null) return false
  return compareOrdered(left, right, expression.operator || '=')
}

function parseNumericValue(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const raw = String(value ?? '').trim()
  if (!raw) return null
  const normalized = raw
    .replace(/,/g, '')
    .replace(/[^\d.+\-eE]/g, '')
  if (!normalized || normalized === '-' || normalized === '+' || normalized === '.') return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function compareBoolean(actual, expression) {
  const left = parseBoolean(actual)
  const right = parseBoolean(expression.value)
  if (left == null || right == null) return false
  return compareEquality(left, right, expression.operator || '=')
}

function parseBoolean(value) {
  const text = String(value ?? '').trim().toLowerCase()
  if (['1', 'true', 'yes', 'y', 'on', 'checked'].includes(text)) return true
  if (['0', 'false', 'no', 'n', 'off', 'unchecked'].includes(text)) return false
  return null
}

function compareString(actual, expression) {
  const left = normalizeText(actual)
  const rawRight = String(expression.value ?? '')
  const right = normalizeText(rawRight)
  const op = expression.operator || (rawRight.includes('%') ? 'like' : 'contains')
  if (op === '=' && rawRight.includes('%')) return wildcardMatch(left, right)
  if (op === 'like' || op === 'contains') return wildcardOrContains(left, right)
  if (op === '!=') return rawRight.includes('%') ? !wildcardMatch(left, right) : left !== right
  if (op === '=') return left === right
  return false
}

function wildcardOrContains(left, pattern) {
  if (!pattern.includes('%')) return left.includes(pattern)
  return wildcardMatch(left, pattern)
}

function wildcardMatch(left, pattern) {
  const escaped = pattern
    .split('%')
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('.*')
  return new RegExp(`^${escaped}$`, 'i').test(left)
}

function compareDateLike(actual, expression, fieldtype) {
  const left = parseDateComparable(actual, fieldtype)
  const right = parseDateQuery(expression.value, fieldtype)
  if (left == null || !right) return false
  const op = expression.operator || '='
  if (op === '=' || op == null) return left >= right.start && left <= right.end
  if (op === '!=') return !(left >= right.start && left <= right.end)
  const pivot = op === '<' || op === '<=' ? right.start : right.end
  return compareOrdered(left, pivot, op)
}

function parseDateComparable(value, fieldtype) {
  if (fieldtype === 'Time') return parseTimeToMs(value)
  const date = value instanceof Date ? value : new Date(String(value).replace(' ', 'T'))
  const ms = date.getTime()
  return Number.isFinite(ms) ? ms : null
}

function parseDateQuery(value, fieldtype) {
  const raw = String(value || '').trim()
  if (!raw) return null
  if (fieldtype === 'Time') {
    const ms = parseTimeToMs(raw)
    if (ms == null) return null
    const precision = raw.split(':').length
    const width = precision <= 1 ? 60 * 60 * 1000 - 1 : precision === 2 ? 60 * 1000 - 1 : 999
    return { start: ms, end: ms + width }
  }
  const monthMatch = raw.match(/^(\d{4})-(\d{2})$/)
  if (monthMatch) {
    const start = new Date(Number(monthMatch[1]), Number(monthMatch[2]) - 1, 1).getTime()
    const end = new Date(Number(monthMatch[1]), Number(monthMatch[2]), 1).getTime() - 1
    return { start, end }
  }
  const yearMatch = raw.match(/^(\d{4})$/)
  if (yearMatch) {
    const year = Number(yearMatch[1])
    return {
      start: new Date(year, 0, 1).getTime(),
      end: new Date(year + 1, 0, 1).getTime() - 1,
    }
  }
  const dayMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (dayMatch) {
    const year = Number(dayMatch[1])
    const month = Number(dayMatch[2]) - 1
    const day = Number(dayMatch[3])
    return {
      start: new Date(year, month, day).getTime(),
      end: new Date(year, month, day + 1).getTime() - 1,
    }
  }
  const date = new Date(raw.replace(' ', 'T'))
  const ms = date.getTime()
  if (!Number.isFinite(ms)) return null
  return { start: ms, end: ms }
}

function parseTimeToMs(value) {
  const match = String(value || '').trim().match(/^(\d{1,2})(?::(\d{2}))?(?::(\d{2})(?:\.(\d{1,3}))?)?$/)
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2] || 0)
  const seconds = Number(match[3] || 0)
  const millis = Number((match[4] || '').padEnd(3, '0') || 0)
  if (hours > 23 || minutes > 59 || seconds > 59) return null
  return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis
}

function compareOrdered(left, right, op) {
  if (op === '>') return left > right
  if (op === '>=') return left >= right
  if (op === '<') return left < right
  if (op === '<=') return left <= right
  return compareEquality(left, right, op)
}

function compareEquality(left, right, op) {
  if (op === '!=') return left !== right
  return left === right
}

export function describeParsedMapSearch(parsed, fieldLabelResolver = null) {
  if (!parsed?.expressions?.length) return ''
  return describeNode(parsed.tree || { type: 'and', children: parsed.expressions }, fieldLabelResolver)
}

function describeNode(node, fieldLabelResolver = null) {
  if (!node) return ''
  if (node.mode) return describeExpression(node, fieldLabelResolver)
  if (node.type === 'leaf') return describeExpression(node.expression, fieldLabelResolver)
  const separator = node.type === 'or' ? ' or ' : ' and '
  return (node.children || [])
    .map((child) => {
      const text = describeNode(child, fieldLabelResolver)
      return child?.type === 'or' && node.type === 'and' ? `(${text})` : text
    })
    .filter(Boolean)
    .join(separator)
}

function describeExpression(expression, fieldLabelResolver = null) {
  if (!expression) return ''
  if (expression.mode === 'text') return `Searching all loaded pin fields for "${expression.value}"`
  const field = fieldLabelResolver?.(expression) || expression.field
  const subject = expression.mode === 'doctype_field'
    ? `${expression.doctype}.${field}`
    : field
  return `Filtering ${subject} ${operatorLabel(expression.operator)} ${expression.value}`
}

function operatorLabel(operator) {
  if (!operator || operator === '=') return 'is'
  if (operator === '!=') return 'is not'
  if (operator === '>') return '>'
  if (operator === '>=') return '>='
  if (operator === '<') return '<'
  if (operator === '<=') return '<='
  return operator
}
