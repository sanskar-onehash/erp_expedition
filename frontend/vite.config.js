import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'

// Expedition's frontend ships as a single IIFE bundle so the
// custom /expedition page can <script src=...> it without
// needing ESM on the host page.
//
// `npm run build` writes to ../expedition/public/dist/ which
// Frappe then serves at /assets/expedition/dist/*.

const pkg = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8')
)

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  define: {
    // Replaced at build time. The SDK uses this for window.expedition.version.
    __EXPEDITION_BUILD__: JSON.stringify(pkg.version),
    // Vue runtime + some transitive deps (e.g. proxy-from-env) gate
    // their dev-only code on `process.env.NODE_ENV` and fall back to
    // `process.nextTick` for microtask scheduling. Vite's lib build
    // does NOT replace these by default in IIFE mode, so we do it
    // explicitly. Without this, the bundle throws ReferenceError at
    // load time: "process is not defined".
    'process.env.NODE_ENV': JSON.stringify('production'),
    // Vue's reactivity scheduler (and a few others) has a fallback
    // `typeof process !== "undefined" && process.nextTick || <fallback>`
    // that's unreachable in modern browsers (queueMicrotask is
    // defined), but the *check itself* throws a ReferenceError when
    // `process` is undefined, because the minifier transforms the
    // check into a strict string comparison and then *still tries to
    // resolve the member* in dead-code positions.
    //
    // esbuild's `define` only accepts an identifier or a JSON literal
    // as the replacement, so we use an identifier (`__pnt__`) and
    // declare it as a global at the top of main.js. The prepended
    // declaration must run before Vue is imported.
    'process.nextTick': '__pnt__',
  },
  build: {
    outDir: '../expedition/public/dist',
    emptyOutDir: true,
    assetsDir: '.',
    cssCodeSplit: false,
    lib: {
      entry: fileURLToPath(new URL('./src/main.js', import.meta.url)),
      name: 'expedition',
      fileName: () => 'expedition.iife.js',
      formats: ['iife'],
    },
    rollupOptions: {
      output: {
        extend: true,
        assetFileNames: (info) => {
          // The CSS extracted from the Vue SFCs is the only asset
          // we ship. Name it consistently so the hooks + template
          // can reference it deterministically.
          if (info.name && info.name.endsWith('.css')) {
            return 'expedition.css'
          }
          return '[name][extname]'
        },
      },
    },
  },
  server: {
    port: 8080,
    proxy: {
      // Proxy API calls to the bench dev server so `vite dev`
      // works without needing the Vite build to be up-to-date.
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/assets': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
