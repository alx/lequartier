# SearXNG for enrichment search fallback

status: accepted

The city GeoJSON enrichment pipeline needs to find ticket URLs for museums, courses URLs for universities, and transit pages for train stations. OSM tags cover this for roughly half the top-100 cities; the rest have no `website` tag in Overpass. A general web search fills the gap.

We use a self-hosted SearXNG instance (Docker, localhost-only) rather than a commercial search API (Google Custom Search, Bing Search API) because SearXNG requires no API key, has no per-query cost, and is sufficient for a one-off 100-city enrichment run. The enrichment script is a CLI tool, not a web service, so the instance only needs to be running when the script executes.

SearXNG is configured with DuckDuckGo and Bing engines only. Google was excluded because self-hosted SearXNG instances are routinely blocked by Google's bot detection within hours of use.

## Considered options

- **Google Custom Search API** — reliable results but $5/1000 queries and requires a GCP project. Not worth it for a one-off 100-city run (~300 queries total).
- **Bing Search API** — free tier (1000 queries/month) but adds an Azure dependency. SearXNG wraps Bing without an API key.
- **No search fallback** — leaves ~50% of museums, universities, and all train stations without enrichment. Rejected because the whole point of this pipeline is to fill those gaps.
