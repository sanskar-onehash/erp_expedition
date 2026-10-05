import 'maplibre-gl/dist/maplibre-gl.css'
import { mountGeolocationWidget } from './geolocation/mount.js'

window.ExpeditionGeolocation = Object.assign(window.ExpeditionGeolocation || {}, {
  mountWidget: mountGeolocationWidget,
})
