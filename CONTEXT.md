# Le Quartier

A tool that generates neighbourhood maps for short-term rental listings, showing walkable POIs around the rental's location.

## Language

### Map

**Listing GeoJSON**: Per-rental GeoJSON produced by the POI pipeline and stored in `curated/`. Contains rich fields: `website`, `video_url`, `rating`, `opening_hours`, `phone`, per-POI status (`primary`/`secondary`), and category metadata.
_Avoid_: rental GeoJSON, curated JSON

**City GeoJSON**: Static pre-generated GeoJSON in `static/data/cities/`. Contains city-level POIs with minimal fields (`name`, `category`, `color`, `fa_icon`). Used by the Landing Map as a decorative background.
_Avoid_: city data, background GeoJSON

**Rental Marker**: The accent-coloured circle with a white house icon placed at the rental's coordinates. Always larger than POI Markers. On the Landing Map it is a demo placeholder at the city centre. Current sizes: `RENTAL_SIZE=40px`, `POI_SIZE=32px` (Landing Map constants in `index.html`; mirror when porting to airbnb maps).
_Avoid_: home marker, listing pin, centre marker

**POI Marker**: A coloured circle with a category icon, representing a nearby place of interest. Interaction is 2-state: hover (opens popup) → click (locks popup open; second click closes). Primary POI Markers display a permanent name label below the circle; secondary POI Markers show no label. "No permanent visible label" in older notes referred to popup-style tooltip labels, not the pill label below the circle.

**Bakery & Food icon**: Always use `fa-cookie-bite`. Never use `fa-bread-slice`.

**Landing Map**: The decorative background map on the index page. Centred on a random city. Uses City GeoJSON. Map navigation is fully enabled (zoom, pan). POI Markers are interactive (hover preview + click popup). When a host submits their listing URL, the map flies to the listing location and switches from City GeoJSON to the listing's live POI feed.
_Avoid_: background map, index map

**Demo Mode**: The Landing Map state where the Rental Marker is placed at the city centre as a placeholder, not tied to any real listing.

**Host Map**: The `/p/{uuid}` page generated for a specific listing after a host pastes their URL. Always publicly accessible — guests can view the interactive map without a login. Export features (image download, QR code) require a one-time payment.
_Avoid_: map page, listing page, share page

**Export**: The three deliverables unlocked by a one-time $19 Stripe payment: downloadable map PNG (1200×800), QR code PNG pointing to the Host Map URL, and the permanent shareable Host Map URL itself.
_Avoid_: download, assets, files

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

## Listings

**Listing** — a rental or real-estate property page on a supported site (Airbnb or Zillow). Each listing has a **Listing ID**.

**Listing ID** — a site-specific identifier extracted from the listing URL.
- Airbnb: numeric string, e.g. `12345678`, from `/rooms/{id}`
- Zillow: full URL slug, e.g. `123-main-st-sf-ca/2061458876_zpid`, from `/homedetails/{slug}` — may contain slashes

**POI (Point of Interest)** — a nearby place of interest (shop, restaurant, transit stop, etc.) associated with a listing. POIs are grouped by **Category**.

**Category** — a named type of POI (e.g. Supermarket, Bakery & Food, Market, Restaurant). Categories can be toggled on/off on the map.

**Default visible categories** — the 3 categories shown enabled by default when any map loads: `Supermarket`, `Park`, `Playground`. All other categories start hidden (dimmed pill, markers not on map). Consistent across the Landing Map and Host Map. Export mode always shows all categories.

**GeoJSON endpoint** — the path-based backend API that returns POI data for a listing: `GET /{site}/{listing_id}.geojson`. No coordinates are sent by the client — the backend resolves them from the listing ID.

## Delivery mechanisms

**Userscript** — a self-contained GreaseMonkey/Tampermonkey script that injects the neighbourhood map directly into the listing page. Does not require a browser extension install. All rendering logic is inlined.

**Extension** — a Chrome or Firefox browser extension (Manifest v3). Uses a **content script** per site + a **service worker** for backend calls + **shared helpers** for map rendering.

**Content script** — site-specific JS injected by the extension into a listing page. Responsible for: extracting the listing ID, finding the DOM anchor, injecting the map container, and requesting POI data via the service worker.

**Service worker** — the extension background script (`browser-ext/background.js`). Receives `{ site, listing_id }` from a content script, calls `GET /{site}/{listing_id}.geojson` on the configured backend, and returns the GeoJSON.

**Shared helpers** — files in `extensions/shared/` used by all extension content scripts: `map-config.js` (constants), `map-init.js` (rendering), `styles.css` (styles).

## Backend

**Backend base URL** — configurable. Default: `https://lequartier.girard-davila.net`. Set via extension popup (`chrome.storage.sync`) or userscript `GM_getValue('backendUrl', ...)`.
