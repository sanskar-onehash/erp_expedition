<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import { call } from '../api/client.js'
import {
	pointFromGeoJSON,
	setPointInGeoJSON,
} from '../geolocation/geojson.js'

const props = defineProps({
  value: { type: [String, Object], default: '' },
  readonly: { type: Boolean, default: false },
  height: { type: Number, default: 420 },
  styleUrl: { type: [String, Object], default: 'https://tiles.openfreemap.org/styles/liberty' },
  center: { type: Array, default: () => [20, 0] },
  zoom: { type: Number, default: 2 },
  searchEnabled: { type: Boolean, default: true },
  language: { type: String, default: '' },
  onChange: { type: Function, default: null },
})

const mapEl = ref(null)
const searchInput = ref(null)
const query = ref('')
const places = ref([])
const searching = ref(false)
const searchError = ref('')
const searchOpen = ref(false)
const copied = ref('')
const contextAction = ref(null)
const hasUnsupportedGeometry = computed(() => {
  if (!props.value) return false
  try {
    const parsed = typeof props.value === 'string' ? JSON.parse(props.value) : props.value
    const features = parsed?.type === 'FeatureCollection' ? parsed.features : [parsed]
    return features.some((feature) => {
      const geometry = feature?.type === 'Feature' ? feature.geometry : feature
      return geometry?.type && geometry.type !== 'Point'
    })
  } catch (_) {
    return false
  }
})

let map = null
let marker = null
let resizeObserver = null
let copiedTimer = null

function openSearch() {
  searchOpen.value = true
  nextTick(() => searchInput.value?.focus())
}

function closeSearch() {
  searchOpen.value = false
  places.value = []
  searchError.value = ''
}

function initialCenter() {
  const point = pointFromGeoJSON(props.value)
  if (point) return [point.longitude, point.latitude]
  const lat = Number(props.center?.[0])
  const lng = Number(props.center?.[1])
  return Number.isFinite(lat) && Number.isFinite(lng) ? [lng, lat] : [0, 20]
}

function setMarker(latitude, longitude, { emit = false, source = 'map' } = {}) {
  const lat = Number(latitude)
  const lng = Number(longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || !map) return

  if (!marker) {
    marker = new maplibregl.Marker({ color: '#FF3B30', draggable: !props.readonly })
      .setLngLat([lng, lat])
      .addTo(map)
    marker.on('dragend', () => {
      if (props.readonly) return
      const position = marker.getLngLat()
      setMarker(position.lat, position.lng, { emit: true, source: 'drag' })
    })
  } else {
    marker.setLngLat([lng, lat])
    marker.setDraggable(!props.readonly)
  }

  if (emit) emitPoint(lat, lng, source)
}

function emitPoint(latitude, longitude, source) {
  const value = setPointInGeoJSON(props.value, latitude, longitude)
  notifyChange({ value, latitude, longitude, source })
}

function notifyChange(change) {
  try {
    Promise.resolve(props.onChange?.(change)).catch((error) => {
      console.error('[expedition] unable to update geolocation field', error)
    })
  } catch (error) {
    console.error('[expedition] unable to update geolocation field', error)
  }
}

async function searchPlaces() {
  const value = query.value.trim()
  if (value.length < 2 || value.length > 200) {
    places.value = []
    searchError.value = value ? 'Enter between 2 and 200 characters.' : ''
    return
  }

  searching.value = true
  searchError.value = ''
  try {
    const response = await call('expedition.api.place.search', {
      query: value,
      language: props.language || navigator.language || '',
      limit: 5,
    })
    places.value = Array.isArray(response?.results) ? response.results : []
    if (!places.value.length) searchError.value = 'No matching places found.'
  } catch (error) {
    console.warn('[expedition] geolocation place search failed', error)
    places.value = []
    searchError.value = error?.message || 'Place search is unavailable.'
  } finally {
    searching.value = false
  }
}

function choosePlace(place) {
  const lat = Number(place?.latitude)
  const lng = Number(place?.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return

  setMarker(lat, lng, { emit: !props.readonly, source: 'search' })
  const bounds = place?.bounds
  const south = Number(bounds?.south)
  const north = Number(bounds?.north)
  const west = Number(bounds?.west)
  const east = Number(bounds?.east)
  if ([south, north, west, east].every(Number.isFinite) && south <= north && west <= east) {
    map.fitBounds([[west, south], [east, north]], { padding: 70, maxZoom: 17, duration: 700 })
  } else {
    map.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), 16), duration: 700 })
  }
  query.value = place.name || place.display_name || query.value
  closeSearch()
}

async function copyText(text, confirmation) {
  try {
    await navigator.clipboard.writeText(text)
  } catch (_) {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
  }
  copied.value = confirmation
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = '' }, 1800)
}

function copyCoordinates(latitude, longitude) {
  const text = `${Number(latitude).toFixed(6)}, ${Number(longitude).toFixed(6)}`
  closeContextMenu()
  return copyText(text, `Coordinates copied`)
}

function copyOpenStreetMapLink(latitude, longitude) {
  const lat = Number(latitude).toFixed(6)
  const lng = Number(longitude).toFixed(6)
  closeContextMenu()
  return copyText(
    `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=18/${lat}/${lng}`,
    'Map link copied'
  )
}

function openContextMenu(event) {
  event.originalEvent?.preventDefault?.()
  const point = event.point || { x: 16, y: 16 }
  contextAction.value = {
    latitude: event.lngLat.lat,
    longitude: event.lngLat.lng,
    x: Math.max(10, point.x),
    y: Math.max(10, point.y),
  }
}

function closeContextMenu() {
  contextAction.value = null
}

function setPinFromContextMenu() {
  const location = contextAction.value
  if (!location || props.readonly) return
  closeContextMenu()
  setMarker(location.latitude, location.longitude, { emit: true, source: 'context-menu' })
}

watch(() => props.value, (value) => {
  const point = pointFromGeoJSON(value)
  if (!map) return
  if (!point) {
    marker?.remove()
    marker = null
    return
  }
  const previous = marker?.getLngLat()
  if (!previous || Math.abs(previous.lat - point.latitude) > 1e-9 || Math.abs(previous.lng - point.longitude) > 1e-9) {
    setMarker(point.latitude, point.longitude)
  }
})

watch(() => props.readonly, (readonly) => marker?.setDraggable(!readonly))

onMounted(async () => {
  await nextTick()
  map = new maplibregl.Map({
    container: mapEl.value,
    style: props.styleUrl,
    center: initialCenter(),
    zoom: pointFromGeoJSON(props.value) ? 16 : props.zoom,
    // The default compact attribution is the floating "i" control. We render
    // the required OSM credit as quiet static text instead.
    attributionControl: false,
    renderWorldCopies: true,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
  map.addControl(new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: false,
    showUserLocation: true,
  }), 'bottom-right')

  map.on('load', () => {
    const point = pointFromGeoJSON(props.value)
    if (point) setMarker(point.latitude, point.longitude)
  })
  map.on('click', (event) => {
    closeContextMenu()
    if (!props.readonly) setMarker(event.lngLat.lat, event.lngLat.lng, { emit: true, source: 'map' })
  })
  map.on('contextmenu', openContextMenu)
  map.on('movestart', closeContextMenu)

  if (typeof ResizeObserver === 'function') {
    resizeObserver = new ResizeObserver(() => map?.resize())
    resizeObserver.observe(mapEl.value)
  }
})

onBeforeUnmount(() => {
  clearTimeout(copiedTimer)
  resizeObserver?.disconnect()
  marker?.remove()
  map?.remove()
  marker = null
  map = null
})
</script>

<template>
  <section class="egf" :style="{ '--egf-height': `${height}px` }">
    <button
      v-if="searchEnabled"
      type="button"
      class="egf__search-trigger"
      aria-label="Search places"
      title="Search places"
      @click="openSearch"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.25"></circle><path d="m16 16 4.1 4.1"></path></svg>
    </button>

    <div v-if="searchEnabled && searchOpen" class="egf__search-wrap">
      <form class="egf__search" @submit.prevent="searchPlaces">
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          autocomplete="off"
          spellcheck="false"
          placeholder="Search hotels, addresses, and places"
          aria-label="Search OpenStreetMap places"
          @input="places = []; searchError = ''"
          @keydown.esc.prevent="closeSearch"
        />
        <button type="submit" class="egf__search-submit" :disabled="searching" aria-label="Search places" title="Search places">
          <span v-if="searching" aria-hidden="true">…</span>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.25"></circle><path d="m16 16 4.1 4.1"></path></svg>
        </button>
        <button type="button" class="egf__search-close" aria-label="Close search" title="Close search" @click="closeSearch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"></path></svg>
        </button>
      </form>
      <div v-if="places.length" class="egf__results">
        <button v-for="place in places" :key="place.id" type="button" @click="choosePlace(place)">
          <span class="egf__result-dot" aria-hidden="true"></span>
          <span>
            <strong>{{ place.name }}</strong>
            <small>{{ place.display_name }}</small>
          </span>
          <em>{{ place.type || place.category }}</em>
        </button>
        <footer>© OpenStreetMap contributors</footer>
      </div>
      <p v-else-if="searchError" class="egf__search-error">{{ searchError }}</p>
    </div>

    <div ref="mapEl" class="egf__map"></div>

    <div v-if="contextAction" class="egf__context-menu" :style="{ left: `${contextAction.x}px`, top: `${contextAction.y}px` }">
      <p>{{ contextAction.latitude.toFixed(6) }}, {{ contextAction.longitude.toFixed(6) }}</p>
      <button type="button" @click="copyCoordinates(contextAction.latitude, contextAction.longitude)">
        Copy coordinates
      </button>
      <button type="button" @click="copyOpenStreetMapLink(contextAction.latitude, contextAction.longitude)">
        Copy map link
      </button>
      <button v-if="!readonly" type="button" @click="setPinFromContextMenu">
        Set pin here
      </button>
    </div>

    <div v-if="copied" class="egf__toast" role="status">{{ copied }}</div>

    <span class="egf__attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors</span>

    <p v-if="hasUnsupportedGeometry" class="egf__geometry-note">
      Existing non-point geometry is preserved but cannot be edited in this point selector.
    </p>
  </section>
</template>

<style scoped>
.egf {
  --egf-control-size: 38px;
  position: relative;
  width: 100%;
  min-height: min(var(--egf-height), 70vh);
  height: var(--egf-height);
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.14);
  border-radius: 12px;
  background: #0b0e14;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.10);
  font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.egf__map { position: absolute; inset: 0; }
.egf__search-trigger {
  position: absolute;
  z-index: 6;
  top: 12px;
  left: 12px;
  display: grid;
  width: var(--egf-control-size);
  height: var(--egf-control-size);
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 10px;
  background: rgba(11, 14, 20, 0.92);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.32);
  color: rgba(246, 247, 249, 0.92);
  cursor: pointer;
  backdrop-filter: blur(18px) saturate(150%);
}
.egf__search-trigger:hover { background: rgba(28, 34, 45, 0.95); color: #fff; }
.egf__search-trigger svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
.egf__search-wrap {
  position: absolute;
  z-index: 5;
  top: 12px;
  left: 50%;
  width: min(520px, calc(100% - 112px));
  transform: translateX(-50%);
}
.egf__search {
  display: grid;
  box-sizing: border-box;
  grid-template-columns: minmax(0, 1fr) 30px 30px;
  gap: 6px;
  align-items: center;
  height: var(--egf-control-size);
  padding: 3px 4px 3px 12px;
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 10px;
  background: rgba(11, 14, 20, 0.92);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.34);
  backdrop-filter: blur(18px) saturate(150%);
}
.egf__search input {
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: #f5f7fa;
  font: inherit;
  font-size: 13px;
}
.egf__search input::placeholder { color: rgba(230, 232, 236, 0.48); }
.egf__search button,
.egf__context-menu button {
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(246, 247, 249, 0.92);
  padding: 6px 10px;
  font: inherit;
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
}
.egf__search button:hover,
.egf__context-menu button:hover { background: rgba(255, 255, 255, 0.14); }
.egf__search button:disabled { opacity: 0.55; cursor: default; }
.egf__search-submit,
.egf__search-close {
  display: grid;
  width: 30px;
  height: 30px;
  padding: 0 !important;
  place-items: center;
}
.egf__search-submit svg,
.egf__search-close svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-width: 2;
}
.egf__search-close { color: rgba(230, 232, 236, 0.65) !important; }
.egf__results {
  margin-top: 7px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 10px;
  background: rgba(11, 14, 20, 0.96);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.42);
  color: #e6e8ec;
}
.egf__results > button {
  width: 100%;
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 9px 11px;
  border: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.egf__results > button:hover,
.egf__results > button:focus-visible { background: rgba(59, 130, 246, 0.16); outline: 0; }
.egf__results button > span:nth-child(2) { display: flex; flex-direction: column; min-width: 0; }
.egf__results strong,
.egf__results small,
.egf__results em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.egf__results strong { font-size: 12px; font-weight: 650; }
.egf__results small { margin-top: 2px; color: rgba(230, 232, 236, 0.58); font-size: 10px; }
.egf__results em { max-width: 90px; color: rgba(230, 232, 236, 0.43); font-size: 9px; font-style: normal; }
.egf__result-dot {
  width: 8px;
  height: 8px;
  border: 1.5px solid #fff;
  border-radius: 50%;
  background: #ff3b30;
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.55);
}
.egf__results footer {
  padding: 6px 11px;
  color: rgba(230, 232, 236, 0.42);
  font-size: 9px;
  text-align: right;
}
.egf__search-error {
  display: inline-block;
  margin: 7px 0 0;
  padding: 5px 8px;
  border-radius: 6px;
  background: rgba(11, 14, 20, 0.9);
  color: #fda4af;
  font-size: 10px;
}
.egf__context-menu {
  position: absolute;
  z-index: 8;
  display: flex;
  min-width: 180px;
  padding: 6px;
  transform: translate(6px, 6px);
  flex-direction: column;
  align-items: center;
  gap: 2px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  background: rgba(11, 14, 20, 0.96);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.40);
  backdrop-filter: blur(16px) saturate(150%);
  color: rgba(230, 232, 236, 0.78);
}
.egf__context-menu p { width: 100%; margin: 2px 5px 5px; color: rgba(230, 232, 236, 0.48); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9px; }
.egf__context-menu button { width: 100%; border: 0; background: transparent; padding: 8px 9px; text-align: left; }
.egf__toast {
  position: absolute;
  z-index: 7;
  top: 62px;
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 10px;
  border: 1px solid rgba(110, 231, 183, 0.28);
  border-radius: 999px;
  background: rgba(11, 14, 20, 0.92);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.30);
  color: #6ee7b7;
  font-size: 10px;
  white-space: nowrap;
}
.egf__attribution {
  position: absolute;
  z-index: 3;
  right: 8px;
  bottom: 6px;
  color: rgba(17, 24, 39, 0.62);
  font-size: 9px;
  line-height: 1;
  text-shadow: 0 1px 1px rgba(255, 255, 255, 0.65);
}
.egf__attribution a { color: inherit; text-decoration: none; }
.egf__attribution a:hover { text-decoration: underline; }
.egf__geometry-note {
  position: absolute;
  z-index: 4;
  right: 12px;
  top: 62px;
  max-width: 280px;
  margin: 0;
  padding: 7px 9px;
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 7px;
  background: rgba(11, 14, 20, 0.88);
  color: #fde68a;
  font-size: 10px;
}
@media (max-width: 700px) {
  .egf { height: max(340px, min(var(--egf-height), 60vh)); }
  .egf__search-wrap { width: calc(100% - 112px); }
  .egf__context-menu { max-width: calc(100% - 28px); }
}
</style>
