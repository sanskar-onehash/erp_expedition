## Expedition

Build, visualize, and explore business data on interactive maps with customizable layers, styling, analytics, and gamified insights.

### Embedding

`window.Expedition` is an embeddable map component, not just the `/app/expedition` page.

```html
<div id="customer-map" class="expedition-map-container"></div>
<script>
  const app = new Expedition.App("customer-map", {
    mode: "view",
    center: [28.6139, 77.2090],
    zoom: 10
  });

  app.addLayer({
    source: "Customer",
    color_field: "status",
    cluster_enabled: true
  });
</script>
```

### Map API

- `new Expedition.App(container, options)` creates a map in any container.
- `app.addLayer(config)` adds a mappable DocType layer.
- `app.addLayerFromDoctype(doctype)` adds a configured mappable DocType by name.
- `app.save(name)` and `app.load(name)` persist `Expedition Map` documents.
- `app.setFocusArea(name)` applies a saved focus boundary mask.
- `app.startTerritoryDrawing(type)` lets a user draw `Polygon`, `Rectangle`, or `Circle` territories directly on the map.
- `app.saveTerritory(payload)` persists a drawn territory as an `Expedition Territory` document.

### Place Search

The canvas search input searches both loaded Expedition pins and OpenStreetMap
places. Place lookup runs only when the user submits the query (Enter or Go),
uses the Nominatim search API through a server-side proxy, caches responses for
24 hours, and limits upstream requests to one per second across the site.

The Nominatim-compatible endpoint and identifying user agent can be changed in
`site_config.json` without rebuilding the frontend:

```json
{
  "expedition_nominatim_url": "https://nominatim.openstreetmap.org/search",
  "expedition_geocoder_user_agent": "Your Company Expedition (maps@example.com)"
}
```

Public Nominatim usage remains subject to the OpenStreetMap Foundation usage
policy. Deployments with sustained or high-volume search should configure a
hosted provider or their own Nominatim-compatible service.

### Embedded Geolocation Field

Expedition provides an opt-in MapLibre geolocation field for Frappe forms. It
uses the Liberty basemap, OpenStreetMap place search, a draggable point marker,
right-click coordinate copying, and standard Frappe GeoJSON storage. The small
Desk loader is included globally, while the map bundle is loaded only when a
developer mounts a field.

Field names are always supplied explicitly by the integrating client script.
For an Address customization, use the actual Custom Field names from the site:

```javascript
frappe.ui.form.on("Address", {
  refresh(frm) {
    Expedition.mountGeolocationField({
      frm,
      fieldname: "custom_location",
      latitudeField: "custom_latitude",
      longitudeField: "custom_longitude",
      height: 420,
    });
  },
});
```

Calling `mountGeolocationField` again updates the existing widget, so the same
configuration may also be called from the Geolocation field's change handler
when another script updates its value. Nothing is replaced globally: only the
configured field is mounted. Use `Expedition.unmountGeolocationField` with the
same `frm` and `fieldname` to restore the standard Frappe control.

### Extension Contract

Expedition is the infrastructure layer. Business-specific workflows should be added on top of it instead of being hard-coded into the app.

- `Expedition.Popups.register(doctype, renderer)` overrides the default popup for a source DocType.
- `Expedition.FocusAreas.register(name, config)` provides custom boundaries at runtime.
- `Expedition.Sources.register(name, adapter)` provides custom fetch/feature adapters for non-standard data sources.
- Page-specific JS can call the public app methods and subscribe to events such as `feature:clicked`, `layer:toggled`, and `focusArea:changed`.
- Server integrations should live in the consuming app’s own whitelisted APIs; Expedition should only provide the reusable map foundation.

#### License

mit
