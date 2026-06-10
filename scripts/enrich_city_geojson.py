#!/usr/bin/env python3
"""Enrich city GeoJSON files with tooltip content from Wikidata and YouTube APIs.

For each POI:
  monument   → wikipedia_url  (Wikidata API; Overpass fallback for missing wikidata IDs)
  museum     → ticket_url     (OSM website tag; Overpass fallback if absent)
  university → courses_url    (OSM website tag; Overpass fallback if absent)
  market     → video_url      (YouTube Data API, one search per city)
  airport    → no enrichment  (ADSB link derived from coordinates client-side)
  train_station → deferred

Skips fields already present (idempotent). Use --force to re-fetch everything.
Requires YOUTUBE_API_KEY in .env for market enrichment.

Usage:
  uv run scripts/enrich_city_geojson.py               # all 100 cities
  uv run scripts/enrich_city_geojson.py --city paris  # single city
  uv run scripts/enrich_city_geojson.py --force       # re-fetch even if field already set
  uv run scripts/enrich_city_geojson.py --dry-run     # print changes without writing files
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
TOP100_PATH = ROOT / "src/web/static/data/top100.json"
CITIES_DIR = ROOT / "src/web/static/data/cities"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_HEADERS = {"User-Agent": "LeQuartier/1.0 city-geojson-enricher"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/search"

_CATEGORY_OSM_SELECTOR = {
    "monument":   '"tourism"="attraction"',
    "museum":     '"tourism"="museum"',
    "university": '"amenity"="university"',
    "market":     '"amenity"="marketplace"',
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
                print(f"    [overpass] failed: {exc}", file=sys.stderr)
                return {}
            time.sleep((2 ** attempt) * (1 + random.random() * 0.3))
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
            for wd_id, entity in r.json().get("entities", {}).items():
                url = entity.get("sitelinks", {}).get("enwiki", {}).get("url")
                if url:
                    result[wd_id] = url
        except Exception as exc:
            print(f"    [wikidata] batch failed: {exc}", file=sys.stderr)
        if i + 50 < len(wikidata_ids):
            time.sleep(0.3)
    return result


# ── YouTube ───────────────────────────────────────────────────────────────────

def search_youtube(query: str, api_key: str) -> str | None:
    """Return a YouTube embed URL for the top result, or None on failure."""
    try:
        r = requests.get(
            YOUTUBE_API,
            params={"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            return f"https://www.youtube.com/embed/{items[0]['id']['videoId']}"
    except Exception as exc:
        print(f"    [youtube] search failed: {exc}", file=sys.stderr)
    return None


# ── Per-city enrichment ───────────────────────────────────────────────────────

def enrich_city(city: dict, force: bool, dry_run: bool, youtube_key: str | None) -> int:
    """Enrich one city's GeoJSON file in-place. Returns count of fields added."""
    path = CITIES_DIR / f"{city['slug']}.geojson"
    if not path.exists():
        print(f"  [skip] {path.name} not found")
        return 0

    gj = json.loads(path.read_text())
    features = gj.get("features", [])
    changed = 0

    def _poi_features(category: str) -> list[dict]:
        return [
            f for f in features
            if f.get("properties", {}).get("kind") == "poi"
            and f.get("properties", {}).get("category") == category
        ]

    # ── monuments → wikipedia_url ─────────────────────────────────────────────
    monument_queue: list[tuple[dict, str]] = []
    for f in _poi_features("monument"):
        props = f["properties"]
        if not force and props.get("wikipedia_url"):
            continue
        wd = props.get("wikidata")
        if not wd:
            coords = f["geometry"]["coordinates"]
            tags = fetch_osm_tags(coords[1], coords[0], "monument")
            wd = tags.get("wikidata")
            if wd and not dry_run:
                props["wikidata"] = wd
            time.sleep(0.5)
        if wd:
            monument_queue.append((f, wd))

    if monument_queue:
        unique_ids = list({wd for _, wd in monument_queue})
        wiki_map = batch_wikipedia_urls(unique_ids)
        for f, wd in monument_queue:
            url = wiki_map.get(wd)
            if url:
                f["properties"]["wikipedia_url"] = url
                changed += 1
                print(f"  monument   {f['properties']['name'][:50]!r}")
                print(f"             {url}")

    # ── museums → ticket_url (OSM website) ───────────────────────────────────
    for f in _poi_features("museum"):
        props = f["properties"]
        if not force and props.get("ticket_url"):
            continue
        website = props.get("website")
        if not website:
            coords = f["geometry"]["coordinates"]
            tags = fetch_osm_tags(coords[1], coords[0], "museum")
            website = tags.get("website")
            if website and not dry_run:
                props["website"] = website
            time.sleep(0.5)
        if website:
            props["ticket_url"] = website
            changed += 1
            print(f"  museum     {props['name'][:50]!r}")
            print(f"             {website}")

    # ── universities → courses_url (OSM website) ─────────────────────────────
    for f in _poi_features("university"):
        props = f["properties"]
        if not force and props.get("courses_url"):
            continue
        website = props.get("website")
        if not website:
            coords = f["geometry"]["coordinates"]
            tags = fetch_osm_tags(coords[1], coords[0], "university")
            website = tags.get("website")
            if website and not dry_run:
                props["website"] = website
            time.sleep(0.5)
        if website:
            props["courses_url"] = website
            changed += 1
            print(f"  university {props['name'][:50]!r}")
            print(f"             {website}")

    # ── markets → video_url (one YouTube search per city) ────────────────────
    if youtube_key:
        markets_needing = [
            f for f in _poi_features("market")
            if force or not f.get("properties", {}).get("video_url")
        ]
        if markets_needing:
            query = f"{city['name']} local food market"
            video_url = search_youtube(query, youtube_key)
            if video_url:
                for f in markets_needing:
                    f["properties"]["video_url"] = video_url
                    changed += 1
                    print(f"  market     {f['properties']['name'][:50]!r}")
                    print(f"             {video_url}")

    # ── write back ────────────────────────────────────────────────────────────
    if changed and not dry_run:
        path.write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")))

    return changed


def main() -> None:
    load_dotenv(ROOT / ".env")
    youtube_key = os.environ.get("YOUTUBE_API_KEY")

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--city",    metavar="SLUG", help="Enrich only this city slug")
    parser.add_argument("--force",   action="store_true", help="Re-fetch even if fields already present")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Print changes without writing files")
    args = parser.parse_args()

    if not youtube_key:
        print("Warning: YOUTUBE_API_KEY not set — market video_url will be skipped.", file=sys.stderr)

    cities: list[dict] = json.loads(TOP100_PATH.read_text())["cities"]
    if args.city:
        cities = [c for c in cities if c["slug"] == args.city]
        if not cities:
            print(f"City '{args.city}' not found in top100.json.", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        print("[dry-run] no files will be written\n")

    total_changed = 0
    for i, city in enumerate(cities, 1):
        print(f"\n[{i}/{len(cities)}] {city['name']}, {city['country']}")
        n = enrich_city(city, force=args.force, dry_run=args.dry_run, youtube_key=youtube_key)
        if not n:
            print("  (nothing to enrich)")
        total_changed += n
        if i < len(cities):
            time.sleep(0.5)

    print(f"\nDone — {total_changed} field{'s' if total_changed != 1 else ''} added across {len(cities)} {'city' if len(cities) == 1 else 'cities'}.")


if __name__ == "__main__":
    main()
