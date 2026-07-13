export function deskDocRoute(doctype, name) {
  if (!doctype || !name) return ''
  const slug = String(doctype).toLowerCase().replace(/ /g, '-')
  return `/app/${encodeURIComponent(slug)}/${encodeURIComponent(name)}`
}

export function openDeskDoc(doctype, name) {
  const route = deskDocRoute(doctype, name)
  if (!route) return
  if (window.frappe?.set_route) {
    try {
      window.frappe.set_route(route)
      return
    } catch {}
  }
  window.open(route, '_blank')
}
