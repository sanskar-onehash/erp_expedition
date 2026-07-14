/**
 * Expedition public SDK — `window.expedition` and `window.Expedition`.
 *
 * Lowercase to match the namespace the www/expedition.html template
 * injects for user/csrf/build context. Two ways to use it:
 *   1. As a full app at /expedition (default; no work needed)
 *   2. Embedded into another Frappe page via `expedition.mount(el, opts)`
 */

import { mountExpedition } from './core/mount.js'

export function initExpeditionSDK({ app }) {
  window.expedition = Object.assign(window.expedition || {}, {
    /** Mount the full Expedition app into `el`. */
    mount: (el, opts) => mountExpedition(el, opts),
  })
  window.expedition.version = __EXPEDITION_BUILD__

  // Initialize the developer extension registries
  window.Expedition = window.Expedition || {}
  window.Expedition.Actions = window.Expedition.Actions || {
    registry: {},
    register(id, config) {
      this.registry[id] = config
    },
    get(id) {
      return this.registry[id]
    },
    list() {
      return Object.values(this.registry)
    }
  }
  window.Expedition.Sources = window.Expedition.Sources || {
    registry: {},
    register(id, config) {
      this.registry[id] = config
    },
    get(id) {
      return this.registry[id]
    }
  }
}
