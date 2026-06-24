/**
 * Expedition public SDK — `window.expedition`.
 *
 * Lowercase to match the namespace the www/expedition.html template
 * injects for user/csrf/build context. Two ways to use it:
 *   1. As a full app at /expedition (default; no work needed)
 *   2. Embedded into another Frappe page via `expedition.mount(el, opts)`
 */

import { mountExpedition } from './core/mount.js'

export function initExpeditionSDK({ app }) {
  // Expose a small, stable surface. Resist the urge to add to this
  // without a versioned contract — every addition is a promise.
  window.expedition = Object.assign(window.expedition || {}, {
    /** Mount the full Expedition app into `el`. */
    mount: (el, opts) => mountExpedition(el, opts),
  })
  // `version` is replaced at build time by the __EXPEDITION_BUILD__
  // define in vite.config.js.
  window.expedition.version = __EXPEDITION_BUILD__
}
