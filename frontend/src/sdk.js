/**
 * Expedition public SDK — `window.expedition` and `window.Expedition`.
 *
 * Lowercase to match the namespace the www/expedition.html template
 * injects for user/csrf/build context. Two ways to use it:
 *   1. As a full app at /expedition (default; no work needed)
 *   2. Embedded into another Frappe page via `expedition.mount(el, opts)`
 */

import { mountExpedition } from './core/mount.js'
import { useLayersStore } from './state/layers.js'

export function initExpeditionSDK({ app }) {
  window.expedition = Object.assign(window.expedition || {}, {
    /** Mount the full Expedition app into `el`. */
    mount: (el, opts) => mountExpedition(el, opts),
  })
  window.expedition.version = __EXPEDITION_BUILD__

  // Initialize the developer extension registries
  window.Expedition = window.Expedition || {}
  
  window.Expedition.getFeaturesInZone = function(zone, layerName) {
    if (!zone || !zone.geometry) return []
    const geom = typeof zone.geometry === 'string' ? JSON.parse(zone.geometry) : zone.geometry
    if (!geom) return []

    function pointInRing(lng, lat, ring) {
      let inside = false
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = ring[i][0], yi = ring[i][1]
        const xj = ring[j][0], yj = ring[j][1]
        const intersects = ((yi > lat) !== (yj > lat)) &&
          (lng < (xj - xi) * (lat - yi) / ((yj - yi) || 1e-12) + xi)
        if (intersects) inside = !inside
      }
      return inside
    }

    function pointInGeometry(lng, lat, geometry) {
      if (!geometry) return false
      const g = geometry.type === 'Feature' ? geometry.geometry : geometry
      if (!g) return false
      
      if (g.type === 'Polygon') {
        const ring = g.coordinates?.[0]
        return ring ? pointInRing(lng, lat, ring) : false
      }
      if (g.type === 'MultiPolygon') {
        const polys = g.coordinates || []
        return polys.some(poly => poly[0] ? pointInRing(lng, lat, poly[0]) : false)
      }
      return false
    }

    const layersStore = useLayersStore()
    const allFeatures = []
    
    // Some proxies don't play well with Object.keys, so just grab the object directly
    const featsMap = layersStore.features || {}
    const layerNames = layerName ? [layerName] : Object.keys(featsMap)

    for (const name of layerNames) {
      const fc = featsMap[name]
      if (!fc || !Array.isArray(fc.features)) continue
      for (const f of fc.features) {
        const coords = f.geometry?.coordinates
        if (Array.isArray(coords) && coords.length >= 2) {
          const [lng, lat] = coords
          if (pointInGeometry(lng, lat, geom)) {
            allFeatures.push(f)
          }
        }
      }
    }
    return allFeatures
  }

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

  window.Expedition.Popup = window.Expedition.Popup || {
    registry: {},
    tabs: {},
    registerCustomHtml(doctype, fn) {
      if (!this.registry[doctype]) this.registry[doctype] = []
      this.registry[doctype].push(fn)
    },
    registerTab(doctype, id, title, renderFn) {
      if (!this.tabs[doctype]) this.tabs[doctype] = []
      this.tabs[doctype].push({ id, title, renderFn })
    }
  }
}
