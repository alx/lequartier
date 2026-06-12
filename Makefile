.PHONY: dev searxng-up searxng-down enrich enrich-city enrich-curated rebuild-index backfill-scores scrub-urls

dev:
	uv run flask --app src.web.app run --port 5010 --debug

# Start SearXNG and wait until it responds
searxng-up:
	docker compose up -d
	@echo "Waiting for SearXNG…"
	@until curl -sf http://127.0.0.1:8888 > /dev/null 2>&1; do sleep 1; done
	@echo "SearXNG ready."

searxng-down:
	docker compose down

# Run full enrichment (all 100 cities). Starts SearXNG if not already up.
# Usage: make enrich
#        make enrich ARGS="--force"
#        make enrich ARGS="--dry-run"
enrich: searxng-up
	uv run scripts/enrich_city_geojson.py $(ARGS)

# Run enrichment for a single city.
# Usage: make enrich-city CITY=paris
#        make enrich-city CITY=tokyo ARGS="--force"
enrich-city: searxng-up
	uv run scripts/enrich_city_geojson.py --city $(CITY) $(ARGS)

# Enrich curated listing GeoJSONs (Transit, Market, Culture).
# Usage: make enrich-curated
#        make enrich-curated ARGS="--force"
#        make enrich-curated ARGS="--dry-run"
enrich-curated: searxng-up
	uv run scripts/enrich_city_geojson.py --curated $(ARGS)

# Rebuild the SQLite listing index from curated JSON files.
rebuild-index:
	uv run scripts/rebuild_listing_index.py

# Backfill properties.score on existing curated listing GeoJSONs.
backfill-scores:
	uv run scripts/backfill_scores.py

# Remove garbage enriched URLs from all GeoJSON files.
# Usage: make scrub-urls
#        make scrub-urls ARGS="--dry-run"
scrub-urls:
	uv run scripts/scrub_enriched_urls.py $(ARGS)
