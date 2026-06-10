# Explore page: side panel with POI overlay

status: accepted

The `/explore` page shows all Shared Listings as pins on a world-zoom Leaflet map. When a pin is clicked, a side panel opens and the listing's POI GeoJSON is fetched from `/api/listing/<id>/geojson` and overlaid on the map, which zooms to the listing's city.

This was chosen over two alternatives:
- **Popup + link** — opens a small popup with a "View map →" link that navigates away. Loses the browsing feel; the user has to use the browser back button to return to `/explore`.
- **Direct navigation** — clicking a pin navigates straight to `/airbnb/<id>`. No browsing at all.

The side panel keeps the user in `/explore` while giving full POI visibility. The `/api/listing/<id>/geojson` endpoint is the only new server route required — it reads the curated file and returns its `result.geojson` field. The panel closes by clicking the map background or an explicit close button, returning the map to world zoom.

POI Markers in the panel view are sorted by POI Relevance Score (descending) so the most relevant places appear first without the user needing to scroll.

The server-side pin list is rendered into the page template as a `SHARED_LISTINGS` JS variable (not fetched via API) because at operator-only scale the full list fits in a single page render and the extra round-trip buys nothing.
