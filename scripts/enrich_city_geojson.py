#!/usr/bin/env python3
"""Enrich city GeoJSON files with tooltip content from Wikidata, YouTube, and SearXNG.

City GeoJSON mode (default):
  monument      → wikipedia_url  (Wikidata API; Overpass fallback for missing wikidata IDs)
  museum        → ticket_url     (OSM website → SearXNG: "{name} {city} buy tickets")
  university    → courses_url    (OSM website → SearXNG: "{name} {city} courses")
  train_station → transit_url    (SearXNG: "{name}" {city} lines schedules)
  market        → video_url      (YouTube Data API, one search per city)
  airport       → no enrichment  (ADSB link derived from coordinates client-side)

Curated listing mode (--curated):
  Transit       → transit_url    (SearXNG: "{name}" {city} lines schedules)
  Market        → video_url      (YouTube Data API, one search per city)
  Culture       → wikipedia_url  (SearXNG: "{name}" {city} wikipedia)

Skips fields already present (idempotent). Use --force to re-fetch everything.
Requires YOUTUBE_API_KEY and SEARXNG_BASE_URL in .env.

Usage:
  uv run scripts/enrich_city_geojson.py                    # all 100 cities
  uv run scripts/enrich_city_geojson.py --city paris       # single city
  uv run scripts/enrich_city_geojson.py --force            # re-fetch even if field already set
  uv run scripts/enrich_city_geojson.py --dry-run          # print changes without writing files
  uv run scripts/enrich_city_geojson.py --no-overpass      # skip Overpass, use SearXNG directly
  uv run scripts/enrich_city_geojson.py --curated          # enrich curated listing GeoJSONs
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from tqdm import tqdm

ROOT = Path(__file__).parent.parent
TOP100_PATH = ROOT / "src/web/static/data/top100.json"
CITIES_DIR = ROOT / "src/web/static/data/cities"
CURATED_DIR = ROOT / "src/web/curated"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_HEADERS = {"User-Agent": "LeQuartier/1.0 city-geojson-enricher"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/search"

# ── URL validators (applied to every SearXNG result before accepting) ─────────

_JUNK_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "web.whatsapp.com", "instagram.com", "linkedin.com",
    "microsoft.com", "support.microsoft.com", "office.com",
    "google.com", "support.google.com", "accounts.google.com",
    "zhihu.com", "baidu.com", "jingyan.baidu.com", "xmind.cn",
    "ef.com.tw", "gamewith.jp", "mumu.163.com", "answers.com",
    "lowyat.net", "forum.lowyat.net", "reddit.com",
    "tenforums.com", "investopedia.com", "hibcc.org", "mcafee.com",
    "palaceskateboards.com", "trustoo.nl", "dafont.com",
    "rosacomputer.vn", "lmskincentre.com", "xylem.live",
    "hk01.com", "tw.stock.yahoo.com", "cmoney.tw",
    "trip.com", "unirank.org", "guldborgsund.dk",
    "st.com", "joto.com", "stackoverflow.com",
    "tripadvisor.com", "th.tripadvisor.com",
    "rome2rio.com",
}

_TRANSIT_DOMAINS = {
    "moovitapp.com", "busmaps.com",
    "bahnhof.de", "bvg.de", "mvg.de", "mvv-muenchen.de", "s-bahn-berlin.de",
    "ratp.fr", "tisseo.fr", "sncf.com", "sncf-connect.com",
    "tfl.gov.uk", "nationalrail.co.uk", "transportforwales.wales",
    "treniamo.it", "trenitalia.com", "atac.roma.it",
    "oasa.gr", "stasy.gr",
    "metromadrid.es", "metro.cat", "tmb.cat", "metrobilbao.eus",
    "nmbs.be", "stib.brussels", "stib-mivb.be",
    "ns.nl", "gvb.nl", "ret.nl",
    "wienerlinien.at", "oebb.at",
    "sbb.ch", "zvv.ch",
    "ztm.waw.pl", "dpp.cz", "idos.cz", "bkk.hu",
    "sl.se", "sj.se", "ruter.no", "dsb.dk", "rejseplanen.dk", "hsl.fi",
    "carris.pt", "metrolisboa.pt", "metrorex.ro",
    "mta.info", "bart.gov", "wmata.com", "mbta.com", "ttc.ca", "translink.ca",
    "trimet.org", "seattletransitblog.com",
    "transportnsw.info", "ptv.vic.gov.au", "transperth.wa.gov.au",
    "tokyometro.jp", "jreast.co.jp", "mtr.com.hk",
    "lta.gov.sg", "bts.co.th", "mrt.co.th",
    "delhimetrorail.com", "bmrc.co.in", "seoulmetro.co.kr",
    "metro.sp.gov.br", "metro.df.gov.br", "metrovias.com.ar",
    "metro.cdmx.gob.mx", "mosmetro.ru",
}

_GENERIC_WIKI_SLUGS = {
    "The", "Basilica", "Rock_(geology)", "Fidel_Castro",
    "Comt%C3%A9_cheese", "Berliner_(doughnut)",
}


def _url_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _is_junk(url: str) -> bool:
    h = _url_host(url)
    return h in _JUNK_DOMAINS or any(h.endswith("." + d) for d in _JUNK_DOMAINS)


def _valid_wikipedia(url: str) -> bool:
    if _is_junk(url):
        return False
    try:
        p = urlparse(url)
        if "wikipedia.org" not in p.netloc:
            return False
        parts = p.path.split("/")
        if len(parts) < 3 or parts[1] != "wiki":
            return False
        slug = parts[2]
        return len(slug) > 4 and slug not in _GENERIC_WIKI_SLUGS
    except Exception:
        return False


def _valid_transit(url: str) -> bool:
    if _is_junk(url):
        return False
    h = _url_host(url)
    return any(h == d or h.endswith("." + d) for d in _TRANSIT_DOMAINS)


def _valid_ticket(url: str) -> bool:
    return not _is_junk(url)


def _valid_courses(url: str) -> bool:
    return not _is_junk(url)


# ── OSM category → SearXNG validator ─────────────────────────────────────────

_CATEGORY_OSM_SELECTOR = {
    "monument":     '"tourism"="attraction"',
    "museum":       '"tourism"="museum"',
    "university":   '"amenity"="university"',
    "market":       '"amenity"="marketplace"',
    "train_station": '"railway"="station"',
}


# ── Overpass ──────────────────────────────────────────────────────────────────

def _overpass_post(query: str) -> dict:
    for attempt in range(3):
        try:
            r = requests.post(
                OVERPASS_URL, data={"data": query},
                headers=_HEADERS, timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == 2:
                tqdm.write(f"    [overpass] failed after 3 attempts: {exc}", file=sys.stderr)
                return {}
            delay = (2 ** attempt) * (1 + random.random() * 0.3)
            tqdm.write(f"    [overpass] retry {attempt + 1}/3 in {delay:.1f}s…")
            time.sleep(delay)
    return {}


def fetch_osm_tags(lat: float, lon: float, category: str) -> dict:
    """Targeted Overpass lookup at known coordinates to retrieve missing OSM tags."""
    selector = _CATEGORY_OSM_SELECTOR.get(category)
    if not selector:
        return {}
    q = (
        f"[out:json][timeout:15];\n"
        f"(\n"
        f"  node(around:300,{lat},{lon})[{selector}];\n"
        f"  way(around:300,{lat},{lon})[{selector}];\n"
        f"  relation(around:300,{lat},{lon})[{selector}];\n"
        f");\n"
        f"out center tags;"
    )
    data = _overpass_post(q)
    elements = data.get("elements", [])
    return elements[0].get("tags", {}) if elements else {}


# ── Wikidata ──────────────────────────────────────────────────────────────────

def batch_wikipedia_urls(wikidata_ids: list[str]) -> dict[str, str]:
    """Batch-fetch English Wikipedia URLs for Wikidata IDs. Returns {id: url}."""
    result: dict[str, str] = {}
    for i in range(0, len(wikidata_ids), 50):
        chunk = wikidata_ids[i : i + 50]
        tqdm.write(f"    [wikidata] fetching {len(chunk)} ID(s)…")
        try:
            r = requests.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "sitelinks",
                    "sitefilter": "enwiki",
                    "format": "json",
                },
                headers=_HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            found = 0
            for wd_id, entity in r.json().get("entities", {}).items():
                url = entity.get("sitelinks", {}).get("enwiki", {}).get("url")
                if url:
                    result[wd_id] = url
                    found += 1
            tqdm.write(f"    [wikidata] {found}/{len(chunk)} had an English Wikipedia article")
        except Exception as exc:
            tqdm.write(f"    [wikidata] batch failed: {exc}", file=sys.stderr)
        if i + 50 < len(wikidata_ids):
            time.sleep(0.3)
    return result


# ── YouTube ───────────────────────────────────────────────────────────────────

def search_youtube(query: str, api_key: str) -> str | None:
    """Return a YouTube embed URL for the top result, or None on failure."""
    tqdm.write(f"    [youtube] searching: {query!r}")
    try:
        r = requests.get(
            YOUTUBE_API,
            params={"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            video_id = items[0]["id"]["videoId"]
            title = items[0]["snippet"]["title"]
            url = f"https://www.youtube.com/embed/{video_id}"
            tqdm.write(f"    [youtube] top result: {title!r}")
            return url
        tqdm.write("    [youtube] no results found")
    except Exception as exc:
        tqdm.write(f"    [youtube] search failed: {exc}", file=sys.stderr)
    return None


# ── SearXNG ───────────────────────────────────────────────────────────────────

def search_searxng(query: str, base_url: str, validator=None) -> str | None:
    """Return the first SearXNG result URL that passes validator(), or None.

    Scans up to 10 results so that a junk top result doesn't block a good #2.
    """
    tqdm.write(f"    [searxng] searching: {query!r}")
    try:
        r = requests.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "categories": "general",
                    "language": "auto", "pageno": 1},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        for result in results[:10]:
            url = result.get("url")
            if not url:
                continue
            title = result.get("title", "")
            if validator is None or validator(url):
                tqdm.write(f"    [searxng] accepted: {title!r} → {url}")
                return url
            tqdm.write(f"    [searxng] skipped:  {title!r} → {url}")
        tqdm.write("    [searxng] no valid result found")
    except Exception as exc:
        tqdm.write(f"    [searxng] failed: {exc}", file=sys.stderr)
    return None


# ── Per-city enrichment ───────────────────────────────────────────────────────

def enrich_city(city: dict, force: bool, dry_run: bool, youtube_key: str | None,
                searxng_url: str | None, no_overpass: bool = False) -> int:
    """Enrich one city's GeoJSON file in-place. Returns count of fields added."""
    path = CITIES_DIR / f"{city['slug']}.geojson"
    if not path.exists():
        tqdm.write(f"  [skip] {path.name} not found")
        return 0

    gj = json.loads(path.read_text())
    features = gj.get("features", [])
    changed = 0

    def _pois(category: str) -> list[dict]:
        return [
            f for f in features
            if f.get("properties", {}).get("kind") == "poi"
            and f.get("properties", {}).get("category") == category
        ]

    def _needs(f: dict, field: str) -> bool:
        return force or not f.get("properties", {}).get(field)

    # ── monuments → wikipedia_url ─────────────────────────────────────────────
    to_enrich = [f for f in _pois("monument") if _needs(f, "wikipedia_url")]
    if to_enrich:
        tqdm.write(f"  monuments  — {len(to_enrich)} to enrich")
        monument_queue: list[tuple[dict, str]] = []

        needing_fallback = [f for f in to_enrich if not f["properties"].get("wikidata")]
        if needing_fallback and no_overpass:
            tqdm.write(f"    [overpass] skipped (--no-overpass) — {len(needing_fallback)} monument(s) will try SearXNG directly")
        elif needing_fallback:
            tqdm.write(f"    [overpass] {len(needing_fallback)} monument(s) missing wikidata ID, querying…")
            for f in needing_fallback:
                name = f["properties"]["name"]
                coords = f["geometry"]["coordinates"]
                tqdm.write(f"    [overpass] → {name!r}")
                tags = fetch_osm_tags(coords[1], coords[0], "monument")
                wd = tags.get("wikidata")
                if wd:
                    tqdm.write(f"    [overpass]   found wikidata={wd}")
                    if not dry_run:
                        f["properties"]["wikidata"] = wd
                else:
                    tqdm.write(f"    [overpass]   no wikidata tag found")
                time.sleep(0.5)

        for f in to_enrich:
            wd = f["properties"].get("wikidata")
            if wd:
                monument_queue.append((f, wd))
            else:
                tqdm.write(f"    ✗ {f['properties']['name']!r} — no wikidata ID, will try SearXNG")

        # Batch-fetch Wikipedia URLs for those with wikidata IDs
        no_article: list[dict] = []
        if monument_queue:
            unique_ids = list({wd for _, wd in monument_queue})
            wiki_map = batch_wikipedia_urls(unique_ids)
            for f, wd in monument_queue:
                url = wiki_map.get(wd)
                name = f["properties"]["name"]
                if url:
                    f["properties"]["wikipedia_url"] = url
                    changed += 1
                    tqdm.write(f"    ✓ {name!r}")
                    tqdm.write(f"      {url}")
                else:
                    tqdm.write(f"    ✗ {name!r} — no English Wikipedia article, will try SearXNG")
                    no_article.append(f)

        # SearXNG fallback for monuments with no wikidata ID or no English article
        searxng_needed = [f for f in to_enrich if not f["properties"].get("wikidata")] + no_article
        if searxng_needed and searxng_url:
            tqdm.write(f"    [searxng] fallback for {len(searxng_needed)} monument(s)")
            for f in searxng_needed:
                name = f["properties"]["name"]
                url = search_searxng(f'"{name}" {city["name"]} wikipedia', searxng_url,
                                     validator=_valid_wikipedia)
                time.sleep(0.5)
                if url:
                    f["properties"]["wikipedia_url"] = url
                    changed += 1
                    tqdm.write(f"    ✓ {name!r}")
                    tqdm.write(f"      {url}")
                else:
                    tqdm.write(f"    ✗ {name!r} — no result found")
        elif searxng_needed:
            for f in searxng_needed:
                tqdm.write(f"    ✗ {f['properties']['name']!r} — skipped (SearXNG not available)")
    else:
        tqdm.write("  monuments  — all up to date")

    # ── museums → ticket_url (OSM website → SearXNG fallback) ────────────────
    to_enrich = [f for f in _pois("museum") if _needs(f, "ticket_url")]
    if to_enrich:
        tqdm.write(f"  museums    — {len(to_enrich)} to enrich")
        for f in to_enrich:
            props = f["properties"]
            name = props["name"]
            website = props.get("website")
            if not website and not no_overpass:
                coords = f["geometry"]["coordinates"]
                tqdm.write(f"    [overpass] → {name!r}")
                tags = fetch_osm_tags(coords[1], coords[0], "museum")
                website = tags.get("website")
                if website:
                    tqdm.write(f"    [overpass]   found website")
                    if not dry_run:
                        props["website"] = website
                else:
                    tqdm.write(f"    [overpass]   no website tag found")
                time.sleep(0.5)
            elif not website and no_overpass:
                tqdm.write(f"    [overpass] skipped for {name!r} (--no-overpass)")
            if not website and searxng_url:
                website = search_searxng(f'"{name}" {city["name"]} buy tickets', searxng_url,
                                         validator=_valid_ticket)
                time.sleep(0.5)
            if website:
                props["ticket_url"] = website
                changed += 1
                tqdm.write(f"    ✓ {name!r}")
                tqdm.write(f"      {website}")
            else:
                tqdm.write(f"    ✗ {name!r} — no website found")
    else:
        tqdm.write("  museums    — all up to date")

    # ── universities → courses_url (OSM website → SearXNG fallback) ──────────
    to_enrich = [f for f in _pois("university") if _needs(f, "courses_url")]
    if to_enrich:
        tqdm.write(f"  universities — {len(to_enrich)} to enrich")
        for f in to_enrich:
            props = f["properties"]
            name = props["name"]
            website = props.get("website")
            if not website and not no_overpass:
                coords = f["geometry"]["coordinates"]
                tqdm.write(f"    [overpass] → {name!r}")
                tags = fetch_osm_tags(coords[1], coords[0], "university")
                website = tags.get("website")
                if website:
                    tqdm.write(f"    [overpass]   found website")
                    if not dry_run:
                        props["website"] = website
                else:
                    tqdm.write(f"    [overpass]   no website tag found")
                time.sleep(0.5)
            elif not website and no_overpass:
                tqdm.write(f"    [overpass] skipped for {name!r} (--no-overpass)")
            if not website and searxng_url:
                website = search_searxng(f'"{name}" {city["name"]} courses', searxng_url,
                                         validator=_valid_courses)
                time.sleep(0.5)
            if website:
                props["courses_url"] = website
                changed += 1
                tqdm.write(f"    ✓ {name!r}")
                tqdm.write(f"      {website}")
            else:
                tqdm.write(f"    ✗ {name!r} — no website found")
    else:
        tqdm.write("  universities — all up to date")

    # ── train stations → transit_url (SearXNG primary) ───────────────────────
    to_enrich = [f for f in _pois("train_station") if _needs(f, "transit_url")]
    if to_enrich:
        tqdm.write(f"  train stations — {len(to_enrich)} to enrich")
        if searxng_url:
            for f in to_enrich:
                props = f["properties"]
                name = props["name"]
                query = f'"{name}" {city["name"]} lines schedules'
                url = search_searxng(query, searxng_url, validator=_valid_transit)
                time.sleep(0.5)
                if url:
                    props["transit_url"] = url
                    changed += 1
                    tqdm.write(f"    ✓ {name!r}")
                    tqdm.write(f"      {url}")
                else:
                    tqdm.write(f"    ✗ {name!r} — no result found")
        else:
            tqdm.write("    [skip] SEARXNG_BASE_URL not set")
    else:
        tqdm.write("  train stations — all up to date")

    # ── markets → video_url ───────────────────────────────────────────────────
    to_enrich = [f for f in _pois("market") if _needs(f, "video_url")]
    if to_enrich:
        tqdm.write(f"  markets    — {len(to_enrich)} to enrich")
        if youtube_key:
            query = f"{city['name']} local food market"
            video_url = search_youtube(query, youtube_key)
            if video_url:
                for f in to_enrich:
                    f["properties"]["video_url"] = video_url
                    changed += 1
                    tqdm.write(f"    ✓ {f['properties']['name']!r}")
                tqdm.write(f"      {video_url}")
            else:
                tqdm.write("    ✗ no video found")
        else:
            tqdm.write("    [skip] YOUTUBE_API_KEY not set")
    else:
        tqdm.write("  markets    — all up to date")

    # ── write back ────────────────────────────────────────────────────────────
    if changed:
        if dry_run:
            tqdm.write(f"  → {changed} field(s) would be added (dry-run, not written)")
        else:
            path.write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")))
            tqdm.write(f"  → {changed} field(s) added, file written")
    else:
        tqdm.write("  → nothing changed")

    return changed


def _english_city(result: dict) -> str:
    """Return a search-friendly (ASCII-safe) city name for a curated listing.

    Priority:
      1. location.city if it's ASCII (e.g. 'Toulouse', 'London')
      2. Extract from listing_title: 'Bungalow in Amphoe Ko Samui' → 'Amphoe Ko Samui'
      3. Fall back to location.city as-is (non-ASCII, may hurt search quality)
    """
    import re as _re
    location = result.get("location") or {}
    city = location.get("city") or ""
    if city and city.isascii():
        return city
    title = result.get("custom_listing_title") or result.get("listing_title") or ""
    m = _re.search(r"\bin ([A-Z][^·\|•]+?)(?:\s*[·\|•]|$)", title)
    if m:
        return m.group(1).strip()
    return city


def enrich_curated(path: Path, force: bool, dry_run: bool,
                   youtube_key: str | None, searxng_url: str | None) -> int:
    """Enrich one curated listing GeoJSON file in-place. Returns count of fields added."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        tqdm.write(f"  [skip] {path.name}: {exc}")
        return 0

    result = data.get("result") or {}
    geojson = result.get("geojson") or {}
    features = geojson.get("features") or []
    city_name = _english_city(result) or path.stem
    changed = 0

    def _pois(category: str) -> list[dict]:
        return [f for f in features if (f.get("properties") or {}).get("category") == category]

    def _needs(f: dict, field: str) -> bool:
        return force or not (f.get("properties") or {}).get(field)

    # ── Transit → transit_url ─────────────────────────────────────────────────
    to_enrich = [f for f in _pois("Transit") if _needs(f, "transit_url")]
    if to_enrich:
        tqdm.write(f"  transit    — {len(to_enrich)} to enrich")
        if searxng_url:
            for f in to_enrich:
                name = f["properties"]["name"]
                url = search_searxng(f'"{name}" {city_name} lines schedules', searxng_url,
                                     validator=_valid_transit)
                time.sleep(0.5)
                if url:
                    f["properties"]["transit_url"] = url
                    changed += 1
                    tqdm.write(f"    ✓ {name!r}")
                    tqdm.write(f"      {url}")
                else:
                    tqdm.write(f"    ✗ {name!r} — no result")
        else:
            tqdm.write("    [skip] SearXNG not available")
    else:
        tqdm.write("  transit    — all up to date")

    # ── Culture → wikipedia_url ───────────────────────────────────────────────
    to_enrich = [f for f in _pois("Culture") if _needs(f, "wikipedia_url")]
    if to_enrich:
        tqdm.write(f"  culture    — {len(to_enrich)} to enrich")
        if searxng_url:
            for f in to_enrich:
                name = f["properties"]["name"]
                url = search_searxng(f'"{name}" {city_name} wikipedia', searxng_url,
                                     validator=_valid_wikipedia)
                time.sleep(0.5)
                if url:
                    f["properties"]["wikipedia_url"] = url
                    changed += 1
                    tqdm.write(f"    ✓ {name!r}")
                    tqdm.write(f"      {url}")
                else:
                    tqdm.write(f"    ✗ {name!r} — no result")
        else:
            tqdm.write("    [skip] SearXNG not available")
    else:
        tqdm.write("  culture    — all up to date")

    # ── Market → video_url ────────────────────────────────────────────────────
    to_enrich = [f for f in _pois("Market") if _needs(f, "video_url")]
    if to_enrich:
        tqdm.write(f"  markets    — {len(to_enrich)} to enrich")
        if youtube_key:
            video_url = search_youtube(f"{city_name} local food market", youtube_key)
            if video_url:
                for f in to_enrich:
                    f["properties"]["video_url"] = video_url
                    changed += 1
                    tqdm.write(f"    ✓ {f['properties']['name']!r}")
                tqdm.write(f"      {video_url}")
            else:
                tqdm.write("    ✗ no video found")
        else:
            tqdm.write("    [skip] YOUTUBE_API_KEY not set")
    else:
        tqdm.write("  markets    — all up to date")

    # ── write back ────────────────────────────────────────────────────────────
    if changed:
        if dry_run:
            tqdm.write(f"  → {changed} field(s) would be added (dry-run, not written)")
        else:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tqdm.write(f"  → {changed} field(s) added, file written")
    else:
        tqdm.write("  → nothing changed")

    return changed


def main() -> None:
    load_dotenv(ROOT / ".env")
    youtube_key = os.environ.get("YOUTUBE_API_KEY")
    searxng_url = os.environ.get("SEARXNG_BASE_URL", "http://127.0.0.1:8080")

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--city",        metavar="SLUG", help="Enrich only this city slug (city mode only)")
    parser.add_argument("--force",       action="store_true", help="Re-fetch even if fields already present")
    parser.add_argument("--dry-run",     action="store_true", dest="dry_run",
                        help="Print changes without writing files")
    parser.add_argument("--no-overpass", action="store_true", dest="no_overpass",
                        help="Skip all Overpass lookups; fall straight through to SearXNG")
    parser.add_argument("--curated",     action="store_true",
                        help="Enrich curated listing GeoJSONs instead of city GeoJSONs")
    args = parser.parse_args()

    if not youtube_key:
        print("Warning: YOUTUBE_API_KEY not set — market video_url will be skipped.", file=sys.stderr)

    # Verify SearXNG is reachable before starting
    try:
        requests.get(f"{searxng_url.rstrip('/')}/healthz", timeout=3)
        tqdm.write(f"SearXNG reachable at {searxng_url}")
    except Exception:
        try:
            requests.get(searxng_url, timeout=3)
            tqdm.write(f"SearXNG reachable at {searxng_url}")
        except Exception:
            print(
                f"Warning: SearXNG not reachable at {searxng_url} — "
                "museum/university/transit enrichment via search will be skipped.\n"
                "Start it with: docker compose up -d",
                file=sys.stderr,
            )
            searxng_url = None

    if args.dry_run:
        print("[dry-run] no files will be written\n")

    if args.curated:
        paths = sorted(CURATED_DIR.glob("*.json")) + sorted((CURATED_DIR / "zillow").glob("*.json") if (CURATED_DIR / "zillow").exists() else [])
        if not paths:
            print("No curated GeoJSON files found.", file=sys.stderr)
            sys.exit(1)
        total_changed = 0
        bar = tqdm(paths, desc="listings", unit="listing")
        for path in bar:
            bar.set_postfix_str(path.stem[:30])
            tqdm.write(f"\n── {path.stem} ──")
            n = enrich_curated(path, force=args.force, dry_run=args.dry_run,
                               youtube_key=youtube_key, searxng_url=searxng_url)
            total_changed += n
            if len(paths) > 1:
                time.sleep(0.3)
        print(
            f"\nDone — {total_changed} field{'s' if total_changed != 1 else ''} added"
            f" across {len(paths)} listing{'s' if len(paths) != 1 else ''}."
        )
        return

    cities: list[dict] = json.loads(TOP100_PATH.read_text())["cities"]
    if args.city:
        cities = [c for c in cities if c["slug"] == args.city]
        if not cities:
            print(f"City '{args.city}' not found in top100.json.", file=sys.stderr)
            sys.exit(1)

    total_changed = 0
    city_bar = tqdm(cities, desc="cities", unit="city")
    for city in city_bar:
        city_bar.set_postfix_str(f"{city['name']}, {city['country']}")
        tqdm.write(f"\n── {city['name']}, {city['country']} ──")
        n = enrich_city(city, force=args.force, dry_run=args.dry_run,
                        youtube_key=youtube_key, searxng_url=searxng_url,
                        no_overpass=args.no_overpass)
        total_changed += n
        if len(cities) > 1:
            time.sleep(0.5)

    n_cities = len(cities)
    print(
        f"\nDone — {total_changed} field{'s' if total_changed != 1 else ''} added"
        f" across {n_cities} {'city' if n_cities == 1 else 'cities'}."
    )


if __name__ == "__main__":
    main()
