# Unified map config pattern

status: proposed

The landing page and rental page both render a Leaflet map with walking rings, a Rental Marker, and interactive POI Markers, but they currently duplicate this logic across `index.html` (inline JS) and `_map_js.html` (fragment). When the time comes to unify them, inject a `window.MAP_CONFIG` object from the Jinja template rather than using template inheritance blocks or a separate Jinja base template.

```js
// landing page injects:
window.MAP_CONFIG = { mode: 'landing', center: { lat, lon, demo: true }, accent: '#1a6b3c', poiSource: '/static/data/cities/paris.geojson' }
// rental page injects:
window.MAP_CONFIG = { mode: 'rental', center: { lat, lon, airbnb_url, photo }, accent: '#b33f43', poiSource: 'inline' }
```

A single `map-core.js` reads the config and branches on `mode`. This was preferred over Jinja `{% block %}` inheritance because the config object is language-agnostic, trivially testable in isolation, and does not require changes to the Flask template hierarchy.

## Considered options

- **Jinja block inheritance** (`{% block map_config %}`) — rejected because it couples map behaviour to the template engine and makes the JS hard to reason about independently.
- **Web component (`<lq-map>`)** — rejected as over-engineered for a Flask/vanilla-JS stack with no build tools.
