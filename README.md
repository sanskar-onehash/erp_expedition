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

### Extension Contract

Expedition is the infrastructure layer. Business-specific workflows should be added on top of it instead of being hard-coded into the app.

- `Expedition.Popups.register(doctype, renderer)` overrides the default popup for a source DocType.
- `Expedition.FocusAreas.register(name, config)` provides custom boundaries at runtime.
- `Expedition.Sources.register(name, adapter)` provides custom fetch/feature adapters for non-standard data sources.
- Page-specific JS can call the public app methods and subscribe to events such as `feature:clicked`, `layer:toggled`, and `focusArea:changed`.
- Server integrations should live in the consuming app’s own whitelisted APIs; Expedition should only provide the reusable map foundation.

#### License

mit
