import { createApp, h, reactive } from 'vue'
import GeolocationField from '../components/GeolocationField.vue'

const instances = new WeakMap()

export function mountGeolocationWidget(element, options = {}) {
  if (!(element instanceof Element)) {
    throw new TypeError('Expedition geolocation requires a DOM element')
  }

  const existing = instances.get(element)
  if (existing) {
    existing.update(options)
    return existing.handle
  }

  const state = reactive({ ...normalizeOptions(options) })
  const app = createApp({
    render: () => h(GeolocationField, { ...state }),
  })
  app.mount(element)

  const record = {
    update(next = {}) {
      Object.assign(state, normalizeOptions({ ...state, ...next }))
    },
    destroy() {
      if (!instances.has(element)) return
      app.unmount()
      element.replaceChildren()
      instances.delete(element)
    },
  }
  record.handle = {
    update: record.update,
    setValue: (value) => record.update({ value }),
    resize: () => window.dispatchEvent(new Event('resize')),
    destroy: record.destroy,
  }
  instances.set(element, record)
  return record.handle
}

function normalizeOptions(options) {
  return {
    value: options.value || '',
    readonly: Boolean(options.readonly),
    height: clamp(Number(options.height) || 420, 280, 900),
    styleUrl: options.styleUrl || 'https://tiles.openfreemap.org/styles/liberty',
    center: Array.isArray(options.center) ? options.center : [20, 0],
    zoom: clamp(Number(options.zoom) || 2, 0, 22),
    searchEnabled: options.searchEnabled !== false,
    language: options.language || '',
    onChange: typeof options.onChange === 'function' ? options.onChange : null,
  }
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}
