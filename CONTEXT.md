# Le Quartier

A tool that generates neighbourhood maps for short-term rental listings, showing walkable POIs around the rental's location.

## Language

### Map

**Listing GeoJSON**: Per-rental GeoJSON produced by the POI pipeline and stored in `curated/`. Contains rich fields: `website`, `video_url`, `rating`, `opening_hours`, `phone`, per-POI status (`primary`/`secondary`), and category metadata.
_Avoid_: rental GeoJSON, curated JSON

**City GeoJSON**: Static pre-generated GeoJSON in `static/data/cities/`. Contains city-level POIs with minimal fields (`name`, `category`, `color`, `fa_icon`). Used by the Landing Map as a decorative background.
_Avoid_: city data, background GeoJSON

**Rental Marker**: The accent-coloured circle with a white house icon placed at the rental's coordinates. Always larger than POI markers (28 px vs 22 px). On the Landing Map it is a demo placeholder at the city centre.
_Avoid_: home marker, listing pin, centre marker

**POI Marker**: A 22 px coloured circle with a category icon, representing a nearby place of interest.

**Landing Map**: The decorative background map on the index page. Centred on a random city. Uses City GeoJSON. Map navigation is locked; POI Markers are interactive (hover preview + click popup).
_Avoid_: background map, index map

**Demo Mode**: The Landing Map state where the Rental Marker is placed at the city centre as a placeholder, not tied to any real listing.

**Shared Listing**: A curated listing with `is_shared: true` in its curated JSON. Visible as a pin on the Explore Page. Only operator-curated listings can be shared (gated by `EDIT_ENABLED`).
_Avoid_: public listing, published listing (see: Published)

**Published**: A listing whose GeoJSON was submitted via the Step 3 GitHub PR flow and merged to the Hugo content repo. Independent of Shared — a listing can be Shared without being Published and vice versa.
_Avoid_: public, live

**Explore Page**: The `/explore` route. A world-zoom Leaflet map showing all Shared Listings as pins. Clicking a pin opens the Side Panel.
_Avoid_: world map, global map

**Side Panel**: A drawer that opens on the Explore Page when a listing pin is clicked. Shows the listing title, city, POI count, and overlays the listing's POI Markers on the map, zoomed to the listing's city.

**Listing Index**: A SQLite file (`data/listings.db`) with a `listings` table indexing metadata for each curated listing: `(listing_id, lat, lon, title, city, is_shared, n_pois, cached_at)`. Source of truth for the Explore Page pin query. The curated JSON files remain the authoritative data store.

**POI Relevance Score**: A numeric score stored in `properties.score` on each POI Feature in a Listing GeoJSON. Computed at generation time from signals: distance from rental, category weight, OSM tag completeness. Used by the Side Panel to sort POIs.

### Enriched POI Tooltip

Per-category content shown in the click popup, rendered only when the corresponding field is present in the GeoJSON (graceful fallback otherwise):

| Category | Field | Content |
|---|---|---|
| `train_station` / `transport` | `transit_url` | Link to the station's page on the transit authority website |
| `museum` | `ticket_url` | Buy-tickets link |
| `monument` | `wikipedia_url` | Wikipedia history link |
| `airport` | _(coords)_ | Live-flights link (always present — derived from coordinates) |
| `university` | `courses_url` | Courses link |
| `market` | `video_url` | YouTube embed |
