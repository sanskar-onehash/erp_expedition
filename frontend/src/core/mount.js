/**
 * Core mount function — shared by /app/expedition and by external
 * embedders. The v1 implementation simply mounts the full App; v1.1
 * will let embedders opt out of certain surfaces (e.g. omit the
 * "Open Map" picker when used as a focused lead-map widget).
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from '../App.vue'

let mounted = null

export function mountExpedition(el, opts = {}) {
  if (mounted) {
    // Single-instance mount for v1. Embedders wanting multiple
    // instances should use a custom wrapper (v1.1+).
    console.warn('Expedition is already mounted; ignoring second mount() call.')
    return mounted
  }
  const app = createApp(App, { embed: opts })
  app.use(createPinia())
  app.mount(el)
  mounted = app
  return app
}
