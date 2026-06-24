/**
 * Frappe API client — wraps frappe.call with the right auth headers
 * for our environment. Reads the CSRF token and user context that
 * expedition.html injected into window.expedition.
 *
 * v1: a thin fetch wrapper. We do not use `frappe-js-sdk` because:
 *   1. The SDK pulls in `process.env.*` polyfills via proxy-from-env
 *      that crash in a vanilla browser IIFE bundle. The fix is
 *      non-trivial (vite-plugin-node-polyfills or careful define
 *      rules), and we'd rather not ship 30KB of unused SDK runtime
 *      for one POST helper.
 *   2. Frappe's `frappe.call` (the Desk's RPC helper) does more than
 *      we need — it serializes documents, manages xss-safe forms,
 *      and assumes a Desk context. A direct `/api/method/<path>` POST
 *      is what every modern Frappe client (frappe-ui, the Frappe
 *      React/Vue templates) ends up writing anyway.
 * v1.1: introduce a typed `client` module that wraps this and adds
 *       the high-level RPC patterns (list_doc, get_doc, submit_form)
 *       with the same auth contract.
 */

export async function call(method, args = {}) {
  const csrf = window.expedition?.csrfToken
  const res = await fetch('/api/method/' + method, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-Frappe-CSRF-Token': csrf || '',
    },
    body: JSON.stringify(args),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw Object.assign(new Error(err.message || res.statusText), {
      status: res.status,
      _server: err,
    })
  }
  const data = await res.json()
  return data.message
}
