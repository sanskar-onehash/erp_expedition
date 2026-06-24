// Vite rewrites process.nextTick -> __pnt__ (esbuild's define only accepts
// identifiers or JSON literals). The declaration must run before any import
// that pulls in Vue or its scheduler.
globalThis.__pnt__ = (f) => Promise.resolve().then(f)
globalThis.process = globalThis.process || { env: {} }
if (typeof globalThis.process.nextTick !== 'function') {
  globalThis.process.nextTick = globalThis.__pnt__
}

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { initExpeditionSDK } from './sdk.js'

const app = createApp(App)
app.use(createPinia())
app.mount('#expedition-root')

initExpeditionSDK({ app })
