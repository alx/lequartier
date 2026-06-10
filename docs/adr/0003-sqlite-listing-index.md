# SQLite listing index alongside file store

status: accepted

Curated listing files (`curated/*.json`) are the authoritative data store. For the Explore Page to query all Shared Listings efficiently (filter by `is_shared`, sort by `cached_at`, paginate), scanning every file on each request is sufficient today but does not survive growth or future filtering needs (by city, by category, etc.).

We add a SQLite file (`data/listings.db`) as a lightweight index. It holds only metadata — `(listing_id, lat, lon, title, city, is_shared, n_pois, cached_at)` — not GeoJSON content. The `save_curated` route upserts a row whenever a curated file is written. The Explore Page queries the index; the `/api/listing/<id>/geojson` endpoint reads the file. The two stay in sync through the single write path.

SQLite was chosen over PostgreSQL because it requires no Docker service, no connection pooling, and no migration tooling at current scale. The GeoJSON content stays in files because it is document-shaped (variable properties, nested features) and is served whole — a relational row buys nothing for content retrieval.

The index can be rebuilt at any time by scanning `curated/*.json`, so it is not a source of truth and can be deleted without data loss.

## Considered options

- **Scan files on every request** — zero infrastructure, works fine under ~100 listings, but no cheap filter/sort path and forces a full directory read on every `/explore` load. Rejected as a dead end.
- **PostgreSQL** — right when multi-process writes or multi-tenant ownership become real requirements. Premature now: adds a Docker service, schema migrations, and connection management with no payoff at current scale.
- **Redis / key-value store** — no query capability; would still need a full scan for filtered listing lists. Rejected.
