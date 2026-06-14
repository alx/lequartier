# TODOS

Deferred work captured during eng review. Each item has context so it's actionable when revisited.

---

## TODO-1: Walking-time labels on POI markers (Phase 2 spike)

**What:** Display walking time from the rental to each POI directly on the exported map image (e.g. "8 min" label on each marker).

**Why:** Step 3 of the current export plan is optional — gated on whether the Step 2 branded header alone justifies $19. If the human judgment call says "not yet," walking times are the next lever. This is the most requested signal from hosts ("how far away is each place?").

**How to start:**
- Formula: `max(1, round(haversine(rental_lat, rental_lon, poi_lat, poi_lon) / 80))` minutes (80 m/min ≈ 4.8 km/h walking speed; floor at 1 to avoid "0 min")
- Compute server-side at render time in `host_map_page()` — pass as JSON array to template
- Render labels in Leaflet JS only when `export_mode=True`
- Rental coordinates are already in the DB (`lat`, `lon` columns) — no new DB query

**Depends on:** Step 2 export (branded header) must ship first. Gate on judgment call after seeing the Step 2 output.

---

## TODO-2: Support email fallback in 202 "generating" page

**What:** Add a fallback message to the HTML meta-refresh page returned when the PNG is missing on download. Currently: "Your map is generating. This page will refresh in 30 seconds." Should add: "If this page keeps refreshing after 2 minutes, email support@lequartier.co and we'll send your map manually."

**Why:** The 202 path fires `subprocess.Popen()` which is best-effort. If the Node process fails silently (wrong PATH, script error, disk full), the host retries forever and never gets a file. No error is visible to them or the ops team. The email fallback gives hosts a way out and ops a signal.

**How to start:** One-line addition to the HTML string in `download_map_image()` after the meta-refresh tag. No new infrastructure needed.

**Depends on:** The 202 download path rewrite (T1 in the implementation tasks).
