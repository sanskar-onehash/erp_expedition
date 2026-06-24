# Expedition — Frontend

Vue 3 + Vite + MapLibre. Builds to `expedition/public/dist/`.

## Develop

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` and `/assets` to the bench dev server
on `:8000`.

## Build

```bash
npm run build
```

Writes `expedition.iife.js` and `expedition.css` to
`expedition/public/dist/`.

## Basemap

Default is OpenFreeMap (`tiles.openfreemap.org`) — free, no API key.