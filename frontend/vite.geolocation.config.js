import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  build: {
    outDir: '../expedition/public/dist',
    emptyOutDir: false,
    assetsDir: '.',
    cssCodeSplit: false,
    lib: {
      entry: fileURLToPath(new URL('./src/geolocation-main.js', import.meta.url)),
      name: 'ExpeditionGeolocationBundle',
      fileName: () => 'geolocation.iife.js',
      formats: ['iife'],
    },
    rollupOptions: {
      output: {
        assetFileNames: (info) => info.name?.endsWith('.css')
          ? 'geolocation.css'
          : '[name][extname]',
      },
    },
  },
})
