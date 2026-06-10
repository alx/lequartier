#!/usr/bin/env python3
"""Pre-generate city GeoJSON files for the landing page background map.

Each output file contains:
- 3 circle features (kind=circle) for 20min/1h/2h car-distance approximations
- 10-20 POI features (kind=poi) spread across the city via greedy min-distance filtering

Usage:
  uv run scripts/generate_city_geojson.py               # all 100 cities
  uv run scripts/generate_city_geojson.py --city paris  # single city
  uv run scripts/generate_city_geojson.py --dry-run     # print counts only
"""

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
TOP100_PATH = ROOT / "src/web/static/data/top100.json"
OUTPUT_DIR = ROOT / "src/web/static/data/cities"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_HEADERS = {"User-Agent": "LeQuartier/1.0 city-geojson-generator"}

CIRCLE_FEATURES = [
    {"radius_m": 400,  "label": "5 min walk"},
    {"radius_m": 800,  "label": "10 min walk"},
    {"radius_m": 1200, "label": "15 min walk"},
]

# Each entry: (category_key, color, fa_icon, tag_match_fn, filter_fragment)
# All queries use a single radius R substituted at build time.
# tag_match_fn(tags) -> True if this element belongs to this category.
CITY_CATEGORIES = [
    (
        "airport", "#7c3aed", "fa-plane",
        lambda t: t.get("aeroway") == "aerodrome" and t.get("iata"),
        'node(around:{R},{lat},{lon})["aeroway"="aerodrome"]["iata"];\n'
        'way(around:{R},{lat},{lon})["aeroway"="aerodrome"]["iata"];',
    ),
    (
        "train_station", "#2563eb", "fa-train",
        lambda t: t.get("railway") == "station",
        'node(around:{R},{lat},{lon})["railway"="station"]["name"];\n'
        'way(around:{R},{lat},{lon})["railway"="station"]["name"];\n'
        'relation(around:{R},{lat},{lon})["railway"="station"]["name"];',
    ),
    (
        "monument", "#be123c", "fa-monument",
        lambda t: t.get("tourism") == "attraction" and t.get("wikidata"),
        'node(around:{R},{lat},{lon})["tourism"="attraction"]["wikidata"]["name"];\n'
        'way(around:{R},{lat},{lon})["tourism"="attraction"]["wikidata"]["name"];\n'
        'relation(around:{R},{lat},{lon})["tourism"="attraction"]["wikidata"]["name"];',
    ),
    (
        "museum", "#b45309", "fa-landmark",
        lambda t: t.get("tourism") == "museum",
        'node(around:{R},{lat},{lon})["tourism"="museum"]["name"];\n'
        'way(around:{R},{lat},{lon})["tourism"="museum"]["name"];\n'
        'relation(around:{R},{lat},{lon})["tourism"="museum"]["name"];',
    ),
    (
        "park", "#16a34a", "fa-tree",
        lambda t: t.get("leisure") in ("park", "garden"),
        'way(around:{R},{lat},{lon})["leisure"~"^(park|garden)$"]["name"];\n'
        'relation(around:{R},{lat},{lon})["leisure"~"^(park|garden)$"]["name"];',
    ),
    (
        "university", "#0891b2", "fa-graduation-cap",
        lambda t: t.get("amenity") == "university",
        'node(around:{R},{lat},{lon})["amenity"="university"]["name"];\n'
        'way(around:{R},{lat},{lon})["amenity"="university"]["name"];\n'
        'relation(around:{R},{lat},{lon})["amenity"="university"]["name"];',
    ),
    (
        "stadium", "#ea580c", "fa-futbol",
        lambda t: t.get("leisure") == "stadium",
        'node(around:{R},{lat},{lon})["leisure"="stadium"]["name"];\n'
        'way(around:{R},{lat},{lon})["leisure"="stadium"]["name"];\n'
        'relation(around:{R},{lat},{lon})["leisure"="stadium"]["name"];',
    ),
    (
        "market", "#65a30d", "fa-store",
        lambda t: t.get("amenity") == "marketplace",
        'node(around:{R},{lat},{lon})["amenity"="marketplace"]["name"];\n'
        'way(around:{R},{lat},{lon})["amenity"="marketplace"]["name"];',
    ),
    (
        "beach", "#0284c7", "fa-umbrella-beach",
        lambda t: t.get("natural") == "beach",
        'node(around:{R},{lat},{lon})["natural"="beach"]["name"];\n'
        'way(around:{R},{lat},{lon})["natural"="beach"]["name"];\n'
        'relation(around:{R},{lat},{lon})["natural"="beach"]["name"];',
    ),
]

SEARCH_RADIUS_M = 1_000  # 15km — fast enough for Overpass within the 60s timeout
SPREAD_MIN_DIST_KM = 0.5
TARGET_MAX = 20


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def score_elem(elem: dict) -> int:
    tags = elem.get("tags", {})
    s = 0
    if tags.get("wikidata"):
        s += 10
    if tags.get("wikipedia"):
        s += 5
    if tags.get("iata"):
        s += 8
    s += min(len(tags.get("name", "")) // 5, 4)
    return s


def classify(tags: dict) -> tuple[str, str, str] | None:
    """Return (category_key, color, fa_icon) for the first matching category."""
    for key, color, fa_icon, match_fn, _ in CITY_CATEGORIES:
        if match_fn(tags):
            return key, color, fa_icon
    return None


def build_combined_query(lat: float, lon: float, radius_m: int) -> str:
    parts = []
    for _key, _color, _icon, _match, fragment in CITY_CATEGORIES:
        parts.append(fragment.format(R=radius_m, lat=lat, lon=lon))
    union = "\n".join(parts)
    return f"[out:json][timeout:60];\n(\n{union}\n);\nout center tags;"


def fetch_city_pois(lat: float, lon: float) -> list[dict]:
    query = build_combined_query(lat, lon, SEARCH_RADIUS_M)
    print(f"  Querying Overpass (r={SEARCH_RADIUS_M//1000}km, timeout=60s)...", end=" ", flush=True)
    t0 = time.monotonic()
    for attempt in range(3):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=70,
            )
            resp.raise_for_status()
            response = resp.json()
            break
        except Exception as exc:
            if attempt == 2:
                print(f"\n  Overpass failed after 3 attempts: {exc}")
                return []
            delay = (2 ** attempt) * (1 + random.random() * 0.3)
            print(f"\n  Retry {attempt + 1}/3 in {delay:.1f}s...", end=" ", flush=True)
            time.sleep(delay)
    else:
        return []

    elapsed = time.monotonic() - t0
    elements = response.get("elements", [])
    print(f"{len(elements)} elements in {elapsed:.1f}s")

    pois = []
    seen: set[str] = set()
    cat_counts: dict[str, int] = {}
    for elem in elements:
        tags = elem.get("tags", {})
        name = tags.get("name", "").strip()
        if not name:
            continue

        classified = classify(tags)
        if not classified:
            continue
        key, color, fa_icon = classified

        if elem["type"] == "node":
            plat, plon = elem["lat"], elem["lon"]
        else:
            center = elem.get("center", {})
            plat, plon = center.get("lat"), center.get("lon")
            if plat is None:
                continue

        dedup_key = f"{key}:{name.lower()[:30]}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        cat_counts[key] = cat_counts.get(key, 0) + 1
        pois.append({
            "lat": plat, "lon": plon,
            "name": name,
            "category": key,
            "color": color,
            "fa_icon": fa_icon,
            "score": score_elem(elem),
            "wikidata": tags.get("wikidata"),
            "website": tags.get("website"),
        })

    if cat_counts:
        breakdown = "  " + "  ".join(f"{k}:{n}" for k, n in sorted(cat_counts.items()))
        print(breakdown)

    return pois


def greedy_spread(candidates: list[dict], min_dist_km: float, target_max: int) -> list[dict]:
    candidates_sorted = sorted(candidates, key=lambda p: -p["score"])
    selected: list[dict] = []
    for poi in candidates_sorted:
        if all(haversine_km(poi["lat"], poi["lon"], s["lat"], s["lon"]) >= min_dist_km for s in selected):
            selected.append(poi)
        if len(selected) >= target_max:
            break
    return selected


def build_geojson(city: dict, pois: list[dict]) -> dict:
    features = []
    for c in CIRCLE_FEATURES:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [city["lon"], city["lat"]]},
            "properties": {"kind": "circle", "radius_m": c["radius_m"], "label": c["label"]},
        })
    for poi in pois:
        props: dict = {
            "kind": "poi",
            "name": poi["name"],
            "category": poi["category"],
            "color": poi["color"],
            "fa_icon": poi["fa_icon"],
        }
        if poi.get("wikidata"):
            props["wikidata"] = poi["wikidata"]
        if poi.get("website"):
            props["website"] = poi["website"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [poi["lon"], poi["lat"]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def process_city(city: dict, dry_run: bool = False) -> int:
    all_pois = fetch_city_pois(city["lat"], city["lon"])
    spread = greedy_spread(all_pois, SPREAD_MIN_DIST_KM, TARGET_MAX)
    count = len(spread)
    print(f"  {len(all_pois)} raw → {count} spread (min dist {SPREAD_MIN_DIST_KM}km)")
    if spread:
        names = ", ".join(p["name"] for p in spread[:5])
        if len(spread) > 5:
            names += f" … +{len(spread) - 5} more"
        print(f"  Selected: {names}")

    if not dry_run:
        gj = build_geojson(city, spread)
        out_path = OUTPUT_DIR / f"{city['slug']}.geojson"
        out_path.write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")))

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", metavar="SLUG", help="Process only this city slug")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write files")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cities = json.loads(TOP100_PATH.read_text())["cities"]

    if args.city:
        cities = [c for c in cities if c["slug"] == args.city]
        if not cities:
            print(f"City '{args.city}' not found in top100.json", file=sys.stderr)
            sys.exit(1)

    total = len(cities)
    print(f"Generating GeoJSON for {total} {'city' if total == 1 else 'cities'}...")

    for i, city in enumerate(cities, 1):
        print(f"\n[{i}/{total}] {city['name']}, {city['country']}")
        process_city(city, dry_run=args.dry_run)
        if i < total:
            time.sleep(1)  # 1s between cities, polite to Overpass

    print("\nDone.")


if __name__ == "__main__":
    main()
