#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "python-dotenv", "rich", "beautifulsoup4", "overpass", "staticmap"]
# ///
"""
Given an Airbnb listing URL and optionally a Google Maps URL (from the host
page map embed), find nearby family-friendly POIs: supermarkets, parks,
playgrounds, transit stops, and kid activities.

Coordinates are resolved in priority order:
  1. --lat / --lon flags
  2. --gmaps URL  (parses ?ll=lat,lon — fast, exact, no HTTP request)
  3. Airbnb page HTML scraping (fallback)

Usage:
    uv run --script scripts/airbnb_env/airbnb_nearby.py \\
        https://www.airbnb.fr/rooms/1612148974271274765 \\
        --gmaps "https://www.google.com/maps?ll=4.588657,101.095776&z=16&..."

    # With PR:
    uv run --script scripts/airbnb_env/airbnb_nearby.py \\
        https://www.airbnb.fr/rooms/1612148974271274765 \\
        --gmaps "https://www.google.com/maps?ll=4.588657,101.095776&z=16&..." \\
        --slug ipoh-airbnb --title "Ipoh — Autour de l'Airbnb" --pr
"""

import argparse
import base64
import csv
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import overpass as overpass_lib
import requests
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Fixed infrastructure constants (not user-configurable)
# ---------------------------------------------------------------------------

GOOGLE_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
NOMINATIM_URL     = "https://nominatim.openstreetmap.org/search"
UPSTREAM_REPO     = "alx/travel-guide"
GITHUB_API        = "https://api.github.com"
REPO_ROOT         = Path(__file__).parent.parent.parent

# Default config file path (sibling of this script)
DEFAULT_CONFIG_PATH = Path(__file__).parent / "airbnb_nearby.toml"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_overpass_api = overpass_lib.API(timeout=40, headers={"User-Agent": "travel-guide-airbnb-nearby/1.0"})

COLOR_PALETTE = [
    "#16a34a", "#2563eb", "#f97316", "#9333ea", "#dc2626",
    "#0891b2", "#ca8a04", "#be185d", "#15803d", "#1d4ed8",
    "#ea580c", "#7c3aed", "#b91c1c", "#0e7490",
]

PRICE_MAP = {
    "PRICE_LEVEL_FREE": "free",
    "PRICE_LEVEL_INEXPENSIVE": "€",
    "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€",
    "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}

# ---------------------------------------------------------------------------
# Config loader
# Reads airbnb_nearby.toml and exposes typed accessors.
# Falls back to safe defaults if the config file is absent.
# ---------------------------------------------------------------------------

class Config:
    """
    Loads airbnb_nearby.toml and provides typed access to all settings.

    Structure expected in TOML:
        [defaults]          — global filtering knobs
        [categories.<key>]  — one section per category
        [trim_priority]     — order array for global cap trimming
    """

    # Hardcoded fallback defaults (used when no config file is found)
    _FALLBACK_DEFAULTS = {
        "max_per_category": 5,
        "min_rating":       3.5,
        "min_reviews":      10,
        "max_total_pois":   30,
        "dedup_radius_m":   80,
        "hard_dist_cap_m":  1400,
        "search_radius_m":  1000,
        "default_categories": [
            "supermarket", "park", "playground", "transit",
            "activities", "restaurant", "pharmacy", "bike_share",
        ],
    }

    _FALLBACK_TRIM_PRIORITY = [
        "transit", "pharmacy", "supermarket", "playground",
        "park", "bike_share", "restaurant", "activities",
    ]

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._raw: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "rb") as f:
                    self._raw = tomllib.load(f)
                print(f"  Config loaded: {self._path}", file=sys.stderr)
            except Exception as e:
                print(f"  Warning: could not parse {self._path}: {e} — using built-in defaults", file=sys.stderr)
        else:
            print(f"  No config file at {self._path} — using built-in defaults", file=sys.stderr)

    # -- Scalar defaults --

    def _d(self, key: str):
        return self._raw.get("defaults", {}).get(key, self._FALLBACK_DEFAULTS[key])

    @property
    def max_per_category(self) -> int:
        return int(self._d("max_per_category"))

    @property
    def min_rating(self) -> float:
        return float(self._d("min_rating"))

    @property
    def min_reviews(self) -> int:
        return int(self._d("min_reviews"))

    @property
    def max_total_pois(self) -> int:
        return int(self._d("max_total_pois"))

    @property
    def dedup_radius_m(self) -> float:
        return float(self._d("dedup_radius_m"))

    @property
    def hard_dist_cap_m(self) -> float:
        return float(self._d("hard_dist_cap_m"))

    @property
    def search_radius_m(self) -> float:
        return float(self._d("search_radius_m"))

    @property
    def default_categories(self) -> list[str]:
        cats = self._d("default_categories")
        # Honour the per-category "default = false" flag
        all_cats = self.categories
        return [c for c in cats if c in all_cats and all_cats[c].get("default", True)]

    # -- Categories --

    @property
    def categories(self) -> dict[str, dict]:
        """
        Return normalised category dict. Each entry is guaranteed to have:
          label, icon, overpass (joined string), google_types (list),
          max, dist_cap, radius, default (bool).
        """
        raw_cats = self._raw.get("categories", {})
        if not raw_cats:
            # No config — return empty; callers must handle gracefully
            return {}
        result: dict[str, dict] = {}
        for key, cat in raw_cats.items():
            # Overpass may be a multiline string in TOML; normalise to single-line fragments
            overpass_raw = cat.get("overpass", "")
            # Strip blank lines, join into one string (the Overpass API expects it)
            overpass_str = "".join(
                line.strip()
                for line in overpass_raw.splitlines()
                if line.strip()
            )
            result[key] = {
                "label":        cat.get("label", key.title()),
                "icon":         cat.get("icon", "📍"),
                "overpass":     overpass_str,
                "google_types": cat.get("google_types", []),
                "max":          int(cat.get("max", self.max_per_category)),
                "dist_cap":     float(cat.get("dist_cap", self.hard_dist_cap_m)),
                "radius":       float(cat.get("radius", self.search_radius_m)),
                "default":      bool(cat.get("default", True)),
            }
        return result

    # -- Trim priority --

    @property
    def trim_priority(self) -> list[str]:
        return self._raw.get("trim_priority", {}).get("order", self._FALLBACK_TRIM_PRIORITY)

    # -- New configurable properties --

    @property
    def units(self) -> str:
        return self._raw.get("defaults", {}).get("units", "metric")

    @property
    def preferred_lang(self) -> str:
        return self._raw.get("defaults", {}).get("preferred_lang", "en")

    @property
    def ui_lang(self) -> str:
        return self._raw.get("defaults", {}).get("ui_lang", "en")

    @property
    def cache_ttl_days(self) -> int:
        return int(self._raw.get("defaults", {}).get("cache_ttl_days", 7))

    @property
    def routing(self) -> bool:
        return bool(self._raw.get("defaults", {}).get("routing", True))

    # -- Convenience: validate a list of category keys --

    def validate_categories(self, requested: list[str]) -> list[str]:
        """Return unknown category keys (not in config)."""
        return [c for c in requested if c not in self.categories]


# Module-level singleton — populated after CLI args are parsed
_config: Config | None = None


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("Config not loaded yet — call load_config() first")
    return _config


def load_config(path: Path | None = None) -> Config:
    global _config
    _config = Config(path)
    return _config


# Shim: keep call-sites that reference CATEGORIES / CAT_PRIORITY working
# without touching every function. Populated after load_config().
CATEGORIES: dict[str, dict] = {}
CAT_PRIORITY: list[str] = []
DEFAULT_CATEGORIES: list[str] = []

# Global filtering constants — overwritten from config after load_config()
MAX_PER_CAT     = 5
MIN_RATING      = 3.5
MIN_REVIEWS     = 10
MAX_TOTAL_POIS  = 30
DEDUP_RADIUS_M  = 80
HARD_DIST_CAP_M = 1400

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Find family-friendly POIs near an Airbnb listing"
    )
    p.add_argument("airbnb_url", help="Airbnb listing URL (used as the listing link in output)")
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to TOML config file (default: airbnb_nearby.toml next to this script)",
    )
    p.add_argument(
        "--gmaps",
        metavar="URL",
        help="Google Maps URL from Airbnb host page (?ll=lat,lon) — preferred coordinate source",
    )
    p.add_argument("--lat", type=float, help="Latitude override (skips all URL-based extraction)")
    p.add_argument("--lon", type=float, help="Longitude override (skips all URL-based extraction)")
    p.add_argument("--radius", type=float, default=None, help="Search radius in metres (default: from config, typically 1000)")
    p.add_argument(
        "--max-poi",
        type=int,
        default=None,
        help="Hard cap on total POIs across all categories (default: from config)",
    )
    p.add_argument(
        "--dedup-radius",
        type=float,
        default=None,
        help="Intra-category proximity dedup radius in metres (default: from config)",
    )
    p.add_argument(
        "--output",
        choices=["table", "json", "geojson"],
        default="table",
        help="Output format without --pr (default: table)",
    )
    p.add_argument(
        "--categories",
        default=None,
        help="Comma-separated categories to search (default: all)",
    )
    p.add_argument(
        "--no-google",
        action="store_true",
        help="Disable Google Places fallback even if GOOGLE_MAPS_API_KEY is set",
    )
    p.add_argument("--env", default=None, help="Path to .env file")
    # PR options
    p.add_argument("--pr", action="store_true", help="Create a GitHub PR against alx/travel-guide")
    p.add_argument(
        "--slug",
        default=None,
        help="URL slug for the map page (e.g. ipoh-airbnb). Auto-derived from listing ID if omitted.",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Map title shown in the sidebar (default: auto-generated from slug)",
    )
    p.add_argument(
        "--description",
        default=None,
        help="Map description for the Hugo content file",
    )
    p.add_argument(
        "--github-token",
        default=os.getenv("GITHUB_TOKEN", ""),
        help="GitHub PAT with repo scope (or set GITHUB_TOKEN env var)",
    )
    # A03 — dry run
    p.add_argument("--dry-run", action="store_true",
                   help="Preview what the PR would contain without creating it or calling APIs")
    # B08 — cache control
    p.add_argument("--force", action="store_true",
                   help="Bypass GeoJSON cache and re-fetch all data")
    p.add_argument("--cache-dir", default=None, metavar="PATH",
                   help="Directory for GeoJSON cache files (default: static/{slug}/)")
    # B09 — duplicate PR
    p.add_argument("--force-update", action="store_true",
                   help="Close existing open PR for this slug and create a new one")
    # B10 — coord confidence
    p.add_argument("--allow-low-accuracy", action="store_true",
                   help="Proceed even when coordinate confidence is low")
    # C03 — coord sanity check
    p.add_argument("--skip-coord-check", action="store_true",
                   help="Skip city-centroid distance sanity check (for rural listings)")
    # C04 — batch mode
    p.add_argument("--batch", default=None, metavar="PATH",
                   help="CSV file for batch processing (columns: url,slug,title,gmaps_url,lat,lon)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def load_env(env_arg: str | None) -> None:
    if env_arg:
        load_dotenv(env_arg)
        return
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent
    for candidate in [Path(".env"), script_dir / ".env", repo_root / ".env"]:
        if candidate.exists():
            load_dotenv(candidate)
            return


# ---------------------------------------------------------------------------
# Coordinate resolution  [UNCHANGED from original]
# ---------------------------------------------------------------------------

def coords_from_gmaps_url(url: str) -> tuple[float, float]:
    qs = parse_qs(urlparse(url).query)
    ll = qs.get("ll", [None])[0]
    if ll:
        parts = ll.split(",")
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
    raise ValueError(f"No valid 'll' parameter in Google Maps URL: {url}")


def _find_coords_recursive(obj, depth: int = 0) -> tuple[float, float] | None:
    if depth > 20:
        return None
    if isinstance(obj, dict):
        lat = obj.get("lat") or obj.get("latitude")
        lng = obj.get("lng") or obj.get("longitude")
        if lat is not None and lng is not None:
            try:
                lat_f, lng_f = float(lat), float(lng)
                if -90 <= lat_f <= 90 and -180 <= lng_f <= 180 and (lat_f != 0 or lng_f != 0):
                    return lat_f, lng_f
            except (TypeError, ValueError):
                pass
        for v in obj.values():
            result = _find_coords_recursive(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_coords_recursive(item, depth + 1)
            if result:
                return result
    return None


_airbnb_soup_cache: dict[str, BeautifulSoup] = {}


def _get_airbnb_soup(url: str) -> BeautifulSoup:
    if url not in _airbnb_soup_cache:
        print("Fetching Airbnb page to extract coordinates...", file=sys.stderr)
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"Error: Airbnb returned HTTP {resp.status_code}", file=sys.stderr)
            sys.exit(1)
        _airbnb_soup_cache[url] = BeautifulSoup(resp.text, "html.parser")
    return _airbnb_soup_cache[url]


def title_from_airbnb_url(url: str) -> str | None:
    soup = _get_airbnb_soup(url)
    h2 = soup.find("h2")
    if isinstance(h2, Tag):
        text = h2.get_text(strip=True)
        if len(text) > 4:
            return text
    og = soup.find("meta", property="og:title")
    if isinstance(og, Tag):
        content = og.get("content", "")
        if content:
            return str(content)
    t = soup.find("title")
    if isinstance(t, Tag):
        return t.get_text(strip=True).split(" - ")[0].split(" | ")[0]
    return None


def photo_from_airbnb_url(url: str) -> str | None:
    try:
        soup = _get_airbnb_soup(url)
        og = soup.find("meta", property="og:image")
        if isinstance(og, Tag):
            content = og.get("content", "")
            if content:
                return str(content)
    except Exception:
        pass
    return None


def coords_from_airbnb_url(url: str) -> tuple[float, float, str]:
    """Returns (lat, lon, confidence) where confidence is 'medium' or 'low'."""
    soup = _get_airbnb_soup(url)

    for script_id in ("data-deferred-state", "data-state", "__NEXT_DATA__"):
        tag = soup.find("script", id=script_id)
        if isinstance(tag, Tag) and tag.string:
            try:
                data = json.loads(tag.string)
                result = _find_coords_recursive(data)
                if result:
                    print(f"  Coordinates found in <script id={script_id!r}>", file=sys.stderr)
                    return result[0], result[1], "medium"
            except (json.JSONDecodeError, ValueError):
                pass

    for tag in soup.find_all("script", type="application/json"):
        if isinstance(tag, Tag) and tag.string:
            try:
                data = json.loads(tag.string)
                result = _find_coords_recursive(data)
                if result:
                    print("  Coordinates found in <script type=application/json>", file=sys.stderr)
                    return result[0], result[1], "medium"
            except (json.JSONDecodeError, ValueError):
                pass

    coord_re = re.compile(
        r'"(?:lat|latitude)"\s*:\s*(-?\d{1,3}\.\d{4,})'
        r'.*?"(?:lng|longitude)"\s*:\s*(-?\d{1,3}\.\d{4,})',
        re.DOTALL,
    )
    for tag in soup.find_all("script"):
        if not isinstance(tag, Tag) or tag.get("src"):
            continue
        text = tag.string or ""
        m = coord_re.search(text)
        if m:
            lat_f, lng_f = float(m.group(1)), float(m.group(2))
            if -90 <= lat_f <= 90 and -180 <= lng_f <= 180 and (lat_f != 0 or lng_f != 0):
                print("  Coordinates found via regex scan", file=sys.stderr)
                return lat_f, lng_f, "medium"

    title_tag = soup.find("title")
    og_desc = soup.find("meta", property="og:description")
    title_str = title_tag.string if isinstance(title_tag, Tag) else None
    og_content = og_desc.get("content") if isinstance(og_desc, Tag) else None  # type: ignore[union-attr]
    query_text = title_str or og_content
    if query_text:
        print(f"  Falling back to Nominatim geocode of: {query_text!r}", file=sys.stderr)
        nom_resp = requests.get(
            NOMINATIM_URL,
            params={"q": query_text, "format": "json", "limit": 1},
            headers={"User-Agent": "travel-guide-airbnb-nearby/1.0"},
            timeout=10,
        )
        if nom_resp.ok and nom_resp.json():
            hit = nom_resp.json()[0]
            print("  Warning: coordinates from Nominatim (low accuracy)", file=sys.stderr)
            return float(hit["lat"]), float(hit["lon"]), "low"

    print(
        "Error: could not extract coordinates from Airbnb page.\n"
        "  Use --gmaps or --lat/--lon to provide coordinates directly.",
        file=sys.stderr,
    )
    sys.exit(1)


def listing_id_from_url(url: str) -> str:
    m = re.search(r"/rooms/(\d+)", url)
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------------------
# Distance (Haversine, metres)  [UNCHANGED]
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fmt_dist(metres: float, units: str = "metric") -> str:
    if units == "imperial":
        miles = metres / 1609.344
        return f"{miles:.1f} mi" if miles >= 0.1 else f"{int(metres * 3.281)} ft"
    return f"{int(metres)} m" if metres < 1000 else f"{metres/1000:.1f} km"


# ---------------------------------------------------------------------------
# Overpass  [UNCHANGED]
# ---------------------------------------------------------------------------

def _overpass_query(category_key: str, lat: float, lon: float, radius: float) -> list[dict]:
    overpass_radius = int(CATEGORIES[category_key].get("radius", radius))
    filters = CATEGORIES[category_key]["overpass"].format(r=overpass_radius, lat=lat, lon=lon)
    # B06 — retry with exponential backoff
    response = None
    for attempt in range(3):
        try:
            response = _overpass_api.get(
                f"({filters});",
                responseformat="json",
                verbosity="tags center",  # B01/B04 — full tags for name variants, hours, phone, website
            )
            break
        except Exception:
            if attempt == 2:
                print(f"  ⚠ {category_key}: Overpass failed after 3 attempts — results incomplete", file=sys.stderr)
                return []
            delay = (2 ** attempt) * (1 + random.random() * 0.3)
            time.sleep(delay)
    if response is None:
        return []
    elements = response.get("elements", [])
    cfg = get_config()
    lang = cfg.preferred_lang  # B04 — multilingual name preference
    pois = []
    for el in elements:
        tags = el.get("tags", {})
        # B04 — multilingual name lookup chain
        name = (tags.get(f"name:{lang}") or tags.get("name:en")
                or tags.get("name") or tags.get("ref") or tags.get("operator"))
        # B03 — handle unnamed POIs instead of silently skipping
        generated_name = False
        if not name:
            name = f"{CATEGORIES[category_key]['label']} #{el['id']}"
            generated_name = True
        if el["type"] == "node":
            elat, elon = el["lat"], el["lon"]
        else:
            center = el.get("center", {})
            elat, elon = center.get("lat"), center.get("lon")
            if elat is None:
                continue
        # B01 — opening hours
        oh_raw = tags.get("opening_hours")
        opening_hours = {"raw": oh_raw, "open_now": None, "source": "osm"} if oh_raw else None
        # B02 — phone and website
        phone = tags.get("contact:phone") or tags.get("phone")
        website = tags.get("contact:website") or tags.get("website")
        pois.append({
            "name": name,
            "lat": elat,
            "lon": elon,
            "category": CATEGORIES[category_key]["label"],
            "icon": CATEGORIES[category_key]["icon"],
            "source": "osm",
            "coord_source": "osm",
            "coord_accuracy": "high",
            "generated_name": generated_name,
            "opening_hours": opening_hours,
            "phone": phone,
            "website": website,
        })
    return pois


def query_overpass(categories: list[str], lat: float, lon: float, radius: float) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for i, cat in enumerate(categories):
        if i > 0:
            time.sleep(1)
        print(f"  Querying OSM for {cat}...", file=sys.stderr)
        results[cat] = _overpass_query(cat, lat, lon, radius)
    return results


# ---------------------------------------------------------------------------
# Google Places (optional)  [UNCHANGED]
# ---------------------------------------------------------------------------

def query_google_nearby(
    api_key: str,
    categories: list[str],
    lat: float,
    lon: float,
    radius: float,
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    # B05 — extended field mask for hours, phone, website, price level
    field_mask = (
        "places.id,places.displayName,places.location,"
        "places.types,places.rating,places.userRatingCount,"
        "places.regularOpeningHours,places.nationalPhoneNumber,"
        "places.websiteUri,places.priceLevel"
    )
    for cat in categories:
        types = CATEGORIES[cat]["google_types"]
        body = {
            "includedTypes": types,
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": radius,
                }
            },
        }
        try:
            resp = requests.post(
                GOOGLE_NEARBY_URL,
                json=body,
                headers={
                    "X-Goog-Api-Key": api_key,
                    "X-Goog-FieldMask": field_mask,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if not resp.ok:
                print(f"  Google Places error for {cat}: {resp.status_code} {resp.text[:120]}", file=sys.stderr)
                results[cat] = []
                continue
        except requests.RequestException as e:
            print(f"  Google Places request error for {cat}: {e}", file=sys.stderr)
            results[cat] = []
            continue

        pois = []
        for place in resp.json().get("places", []):
            loc = place.get("location", {})
            plat = loc.get("latitude")
            plon = loc.get("longitude")
            if plat is None:
                continue
            name = place.get("displayName", {}).get("text") or place.get("id", "")
            # B01 — opening hours from Google
            oh_data = place.get("regularOpeningHours", {})
            opening_hours = None
            if oh_data:
                open_now = oh_data.get("openNow")
                opening_hours = {"raw": None, "open_now": open_now, "source": "google"}
            # B02 — phone and website
            phone = place.get("nationalPhoneNumber")
            website = place.get("websiteUri")
            # B05 — price level
            price_raw = place.get("priceLevel")
            price_level = PRICE_MAP.get(price_raw) if price_raw else None
            pois.append({
                "name": name,
                "lat": plat,
                "lon": plon,
                "category": CATEGORIES[cat]["label"],
                "icon": CATEGORIES[cat]["icon"],
                "source": "google",
                "coord_source": "google_maps_pin",
                "coord_accuracy": "high",
                "generated_name": False,
                "rating": place.get("rating"),
                "user_rating_count": place.get("userRatingCount", 0),
                "opening_hours": opening_hours,
                "phone": phone,
                "website": website,
                "price_level": price_level,
            })
        results[cat] = pois
    return results


# ---------------------------------------------------------------------------
# Merge + dedup  [UNCHANGED]
# ---------------------------------------------------------------------------

def _dedup_key(poi: dict) -> tuple[float, float]:
    return round(poi["lat"], 4), round(poi["lon"], 4)


def _normalize_name(name: str) -> str:
    n = name.lower().strip()
    n = "".join(c for c in unicodedata.normalize("NFD", n) if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^\w\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _names_similar(a: str, b: str) -> bool:
    na, nb = _normalize_name(a), _normalize_name(b)
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 5 and shorter in longer


def merge_results(
    osm: dict[str, list[dict]],
    google: dict[str, list[dict]] | None,
) -> dict[str, list[dict]]:
    merged: dict[str, list[dict]] = {}
    for cat in osm:
        seen_coords: set[tuple[float, float]] = set()
        pois = list(osm.get(cat, []))
        for p in pois:
            seen_coords.add(_dedup_key(p))
        for gp in (google or {}).get(cat, []):
            if _dedup_key(gp) in seen_coords:
                continue
            matched = next(
                (
                    p for p in pois
                    if _names_similar(p["name"], gp["name"])
                    and haversine(p["lat"], p["lon"], gp["lat"], gp["lon"]) < 150
                ),
                None,
            )
            if matched:
                matched.setdefault("rating", gp.get("rating"))
                matched.setdefault("user_rating_count", gp.get("user_rating_count", 0))
            else:
                pois.append(gp)
                seen_coords.add(_dedup_key(gp))
        merged[cat] = pois
    return merged


# ---------------------------------------------------------------------------
# Filter, dedup, limit  [REWRITTEN]
# ---------------------------------------------------------------------------

def filter_and_limit(
    results: dict[str, list[dict]],
    lat: float,
    lon: float,
    max_per_cat: int = MAX_PER_CAT,
    dedup_radius: float = DEDUP_RADIUS_M,
    max_total: int = MAX_TOTAL_POIS,
) -> dict[str, list[dict]]:
    """
    Filter and limit POIs per category with:
    - Hard distance cap (per-category or global HARD_DIST_CAP_M)
    - Intra-category proximity dedup (within dedup_radius metres)
      Kills bus stop direction pairs, Caniparc clones, Léo Lagrange sub-entries
    - Rating filter (only applied when review count is sufficient)
    - Per-category count cap (CATEGORIES["max"] or max_per_cat)
    - Global total cap (max_total), trimming least-essential categories first
    """
    filtered: dict[str, list[dict]] = {}

    for cat, pois in results.items():
        cat_meta = CATEGORIES.get(cat, {})
        cat_max = cat_meta.get("max", max_per_cat)
        dist_cap = cat_meta.get("dist_cap", HARD_DIST_CAP_M)

        # Sort by distance from Airbnb centre
        by_dist = sorted(pois, key=lambda p: haversine(lat, lon, p["lat"], p["lon"]))

        kept: list[dict] = []
        for p in by_dist:
            dist = haversine(lat, lon, p["lat"], p["lon"])

            # Hard distance cap
            if dist > dist_cap:
                continue

            # Rating filter (only trust score when review volume is high enough)
            rating = p.get("rating")
            reviews = p.get("user_rating_count", 0)
            if rating is not None and rating < MIN_RATING and reviews >= MIN_REVIEWS:
                continue

            # Intra-category proximity dedup:
            # if we already have a POI of this category within dedup_radius, skip.
            # This handles: bus stop pairs (opposite directions), Caniparc variants,
            # Léo Lagrange sub-facilities, Carrefour City duplicates.
            too_close = any(
                haversine(p["lat"], p["lon"], kept_p["lat"], kept_p["lon"]) < dedup_radius
                for kept_p in kept
            )
            if too_close:
                continue

            kept.append(p)
            if len(kept) >= cat_max:
                break

        filtered[cat] = kept

    # Global total cap: trim from least-essential categories first
    total = sum(len(v) for v in filtered.values())
    if total > max_total:
        overage = total - max_total
        # Trim from the end of the priority list (least essential first)
        all_cats = list(filtered.keys())
        trim_order = [c for c in reversed(CAT_PRIORITY) if c in all_cats]
        # Any category not in CAT_PRIORITY gets trimmed first
        trim_order = [c for c in all_cats if c not in CAT_PRIORITY] + trim_order
        for cat in trim_order:
            if overage <= 0:
                break
            if filtered[cat]:
                trim = min(overage, len(filtered[cat]))
                filtered[cat] = filtered[cat][:-trim]
                overage -= trim

    return filtered


# ---------------------------------------------------------------------------
# GeoJSON builder  [UNCHANGED]
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New helpers: reverse geocode, GeoJSON cache, preview image
# ---------------------------------------------------------------------------

def reverse_geocode(lat: float, lon: float) -> dict:
    """B11 — reverse geocode lat/lon to neighbourhood/city/timezone via Nominatim."""
    try:
        r = requests.get(
            NOMINATIM_URL.replace("search", "reverse"),
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "travel-guide/1.0"},
            timeout=10,
        )
        if not r.ok:
            return {}
        data = r.json()
        addr = data.get("address", {})
        return {
            "neighbourhood": addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter"),
            "city": addr.get("city") or addr.get("town") or addr.get("village"),
            "country": addr.get("country"),
            "timezone": data.get("extratags", {}).get("timezone"),
        }
    except Exception:
        return {}


def cache_save(slug: str, lat: float, lon: float, categories: list[str], n_pois: int) -> None:
    """B08 — save GeoJSON fetch metadata for staleness checks."""
    cache_dir = REPO_ROOT / "static" / slug
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ".cache.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lat": lat, "lon": lon,
        "categories": sorted(categories),
        "n_pois": n_pois,
    }), encoding="utf-8")


def cache_load(slug: str, lat: float, lon: float, categories: list[str], ttl_days: int) -> dict | None:
    """B08 — return cached GeoJSON if fresh, else None."""
    cache_path = REPO_ROOT / "static" / slug / ".cache.json"
    geojson_path = REPO_ROOT / "static" / slug / "locations.geojson"
    if not cache_path.exists() or not geojson_path.exists():
        return None
    try:
        meta = json.loads(cache_path.read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["generated_at"])).days
        if (age >= ttl_days
                or haversine(lat, lon, meta["lat"], meta["lon"]) > 50
                or sorted(categories) != sorted(meta.get("categories", []))):
            return None
        return json.loads(geojson_path.read_text())
    except Exception:
        return None


def generate_preview(lat: float, lon: float, pois: list[dict], output_path: Path) -> None:
    """C05 — generate a static map preview image."""
    try:
        from staticmap import StaticMap, CircleMarker  # type: ignore[import]
        m = StaticMap(1200, 630)
        m.add_marker(CircleMarker((lon, lat), "#ff5a5f", 16))
        for i, poi in enumerate(pois[:20]):
            color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
            m.add_marker(CircleMarker((poi["lon"], poi["lat"]), color, 8))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        m.render(zoom=15).save(str(output_path))
    except Exception as e:
        print(f"  ⚠ Preview image generation failed: {e}", file=sys.stderr)


def _curate_statuses(features: list) -> None:
    """Auto-assign primary/secondary status based on category-specific curation rules.

    Features within each category are already sorted by distance (nearest first)
    because build_geojson iterates pois sorted by haversine distance.
    """
    # Only the nearest instance is primary; duplicates are secondary
    single_primary = {"Health", "Supermarket"}
    # Generated-name entries (no real label from OSM) are secondary noise
    generated_name_secondary = {"Park", "Playground", "Bakery & Food"}

    seen_primary: set = set()
    for f in features:
        props = f["properties"]
        cat = props["category"]
        if cat in single_primary:
            props["status"] = "primary" if cat not in seen_primary else "secondary"
            seen_primary.add(cat)
        elif props.get("generated_name") and cat in generated_name_secondary:
            props["status"] = "secondary"
        else:
            props["status"] = "primary"


def build_geojson(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
    slug: str,
    coord_confidence: str = "high",
    location: dict | None = None,
) -> dict:
    features = []
    seq = 1
    for pois in results.values():
        for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"])):
            props: dict = {
                "name": p["name"],
                "category": p["category"],
                "icon": p["icon"],
                "coord_source": p["coord_source"],
                "coord_accuracy": p["coord_accuracy"],
                "source": p["source"],
                "listing_url": airbnb_url,
                "generated_name": p.get("generated_name", False),
            }
            if p.get("opening_hours"):
                props["opening_hours"] = p["opening_hours"]
            if p.get("phone"):
                props["phone"] = p["phone"]
            if p.get("website"):
                props["website"] = p["website"]
            if p.get("price_level") and p.get("category") == "Restaurant":
                props["price_level"] = p["price_level"]
            if p.get("rating") is not None:
                props["rating"] = p["rating"]
            if p.get("user_rating_count"):
                props["user_rating_count"] = p["user_rating_count"]
            features.append({
                "type": "Feature",
                "id": f"{slug}-{seq:03d}",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": props,
            })
            seq += 1
    _curate_statuses(features)
    # B07 — category_meta for dynamic colors/icons in the frontend
    cat_keys = list(CATEGORIES.keys())
    category_meta: dict = {}
    for i, cat_key in enumerate(cat_keys):
        label = CATEGORIES[cat_key]["label"]
        category_meta[label] = {
            "icon": CATEGORIES[cat_key]["icon"],
            "color": COLOR_PALETTE[i % len(COLOR_PALETTE)],
        }

    # B13 — localised labels from config
    cfg = get_config()
    ui_lang = cfg.ui_lang
    labels: dict[str, str] = {}
    for cat_key in cat_keys:
        label = CATEGORIES[cat_key]["label"]
        localised = CATEGORIES[cat_key].get(f"label_{ui_lang}", label)
        if localised != label:
            labels[label] = localised

    meta: dict = {
        "crs": "EPSG:4326",
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": f"OSM Overpass + Google Places — {airbnb_url}",
        "listing_url": airbnb_url,
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius,
        "coord_confidence": coord_confidence,
        "units": cfg.units,
        "routing": cfg.routing,
        "category_meta": category_meta,
    }
    if labels:
        meta["labels"] = labels
    if location:
        meta["location"] = location
    return {
        "type": "FeatureCollection",
        "_meta": meta,
        "features": features,
    }


# ---------------------------------------------------------------------------
# Output  [UNCHANGED except table shows total count]
# ---------------------------------------------------------------------------

def output_table(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
) -> None:
    console = Console()
    total = sum(len(v) for v in results.values())
    console.print(f"\n[bold]Airbnb listing:[/bold] {airbnb_url}")
    console.print(f"[bold]Coordinates:[/bold] {lat:.6f}°, {lon:.6f}°  (radius: {_fmt_dist(radius)})")
    console.print(f"[bold]Total POIs:[/bold] {total}\n")

    for cat, pois in results.items():
        meta = CATEGORIES[cat]
        label = f"{meta['icon']} {meta['label'].upper()}"
        if not pois:
            console.print(f"[dim]{label} — none found within {_fmt_dist(meta.get('dist_cap', radius))}[/dim]\n")
            continue
        has_ratings = any(p.get("rating") is not None for p in pois)
        t = Table(title=f"{label} ({len(pois)})", show_header=True, header_style="bold")
        t.add_column("Name", style="cyan", no_wrap=False)
        t.add_column("Distance", justify="right")
        if has_ratings:
            t.add_column("Rating", justify="right", style="yellow")
        t.add_column("Source", style="dim")
        for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"])):
            dist = haversine(lat, lon, p["lat"], p["lon"])
            row = [p["name"], _fmt_dist(dist)]
            if has_ratings:
                r = p.get("rating")
                row.append(f"{r:.1f} ★" if r is not None else "—")
            row.append(p["source"].upper())
            t.add_row(*row)
        console.print(t)
        console.print()


def output_json(
    airbnb_url: str,
    lat: float,
    lon: float,
    results: dict[str, list[dict]],
    radius: float,
) -> None:
    payload = {
        "listing_url": airbnb_url,
        "coordinates": {"lat": lat, "lon": lon},
        "radius_m": radius,
        "generated": datetime.now(timezone.utc).isoformat(),
        "results": {
            cat: [
                {**p, "distance_m": round(haversine(lat, lon, p["lat"], p["lon"]))}
                for p in sorted(pois, key=lambda x: haversine(lat, lon, x["lat"], x["lon"]))
            ]
            for cat, pois in results.items()
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Local Hugo file writer  [UNCHANGED]
# ---------------------------------------------------------------------------

def _update_maps_json(slug: str, title: str, description: str, categories: list[str], n_pois: int) -> None:
    maps_path = REPO_ROOT / "data" / "maps.json"
    try:
        doc = json.loads(maps_path.read_text(encoding="utf-8")) if maps_path.exists() else {"maps": [], "_meta": {}}
    except Exception:
        doc = {"maps": [], "_meta": {}}
    doc["maps"] = [m for m in doc.get("maps", []) if m.get("slug") != slug]
    doc["maps"].append({
        "slug": slug,
        "url": f"/{slug}/",
        "title": title,
        "description": description,
        "emoji": "🏠",
        "section": "airbnb",
        "weight": 55,
        "accent_color": "#1a6b3c",
        "tags": [f"{CATEGORIES[c]['icon']} {CATEGORIES[c]['label']}" for c in categories if c in CATEGORIES],
        "poi_count": n_pois,
        "categories": sorted({CATEGORIES[c]["label"] for c in categories if c in CATEGORIES}),
        "has_geojson": True,
    })
    doc.setdefault("_meta", {})["map_count"] = len(doc["maps"])
    doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    maps_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def write_local_hugo_files(
    slug: str,
    title: str,
    description: str,
    airbnb_url: str,  # kept for callers; not used since layout is managed as a static file
    lat: float,       # kept for callers
    lon: float,       # kept for callers
    geojson: dict,
    categories: list[str],
) -> None:
    _ = airbnb_url, lat, lon  # layout managed as static Hugo file
    layout_name = _slug_layout_name(slug)
    generated = geojson.get("_meta", {}).get("generated")
    files: dict[Path, str] = {
        REPO_ROOT / "static" / slug / "locations.geojson": json.dumps(geojson, ensure_ascii=False, indent=2),
        REPO_ROOT / "content" / slug / "_index.md": _hugo_content(
            title, description, categories, layout=layout_name, generated=generated
        ),
    }
    print("\nWriting Hugo map files...", file=sys.stderr)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  ✓ {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    if "/" in slug:
        n_pois = len(geojson.get("features", []))
        _update_maps_json(slug, title, description, categories, n_pois)
        print("  ✓ data/maps.json", file=sys.stderr)
    else:
        index_script = REPO_ROOT / "scripts" / "generate_map_index.py"
        if index_script.exists():
            subprocess.run(["uv", "run", "--script", str(index_script)], check=False)
            print("  ✓ data/maps.json", file=sys.stderr)


# ---------------------------------------------------------------------------
# PR content builders  [UNCHANGED]
# ---------------------------------------------------------------------------

def _hugo_content(title: str, description: str, categories: list[str], layout: str | None = None, generated: str | None = None) -> str:
    tags = [f"{CATEGORIES[c]['icon']} {CATEGORIES[c]['label']}" for c in categories if c in CATEGORIES]
    layout_line = f'\nlayout: "{layout}"' if layout else ""
    lastmod_line = f'\nlastmod: "{generated}"' if generated else ""
    return f"""---
title: "{title}"
description: "{description}"
emoji: "🏠"
section: "airbnb"
weight: 55
accent_color: "#1a6b3c"
tags: {json.dumps(tags, ensure_ascii=False)}{layout_line}{lastmod_line}
---
"""


def _slug_layout_path(slug: str) -> str:
    parts = slug.split("/")
    if len(parts) > 1:
        return f"layouts/{'/'.join(parts[:-1])}/{parts[-1]}.html"
    return f"layouts/{slug}/list.html"


def _slug_layout_name(slug: str) -> str | None:
    parts = slug.split("/")
    return parts[-1] if len(parts) > 1 else None



# ---------------------------------------------------------------------------
# GitHub PR helpers  [UNCHANGED]
# ---------------------------------------------------------------------------

def _gh(method: str, path: str, token: str, **kwargs) -> requests.Response:
    return requests.request(
        method,
        f"{GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=20,
        **kwargs,
    )


def _ensure_fork(token: str, login: str) -> str:
    r = _gh("GET", f"/repos/{login}/travel-guide", token)
    if r.status_code == 200:
        return r.json()["full_name"]
    print("  Forking alx/travel-guide...", file=sys.stderr)
    r = _gh("POST", f"/repos/{UPSTREAM_REPO}/forks", token, json={"default_branch_only": True})
    r.raise_for_status()
    fork = r.json()["full_name"]
    for _ in range(20):
        time.sleep(3)
        if _gh("GET", f"/repos/{fork}", token).status_code == 200:
            return fork
    raise RuntimeError("Fork never became ready")


def _get_main_sha(token: str) -> str:
    r = _gh("GET", f"/repos/{UPSTREAM_REPO}/git/ref/heads/main", token)
    r.raise_for_status()
    return r.json()["object"]["sha"]


def _create_branch(token: str, repo: str, branch: str, sha: str) -> None:
    r = _gh("POST", f"/repos/{repo}/git/refs", token,
            json={"ref": f"refs/heads/{branch}", "sha": sha})
    if r.status_code not in (200, 201, 422):
        r.raise_for_status()


def _put_file(token: str, repo: str, path: str, content: str, branch: str, message: str) -> None:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    r = _gh("GET", f"/repos/{repo}/contents/{path}", token, params={"ref": branch})
    if r.status_code == 200:
        body["sha"] = r.json()["sha"]
    r = _gh("PUT", f"/repos/{repo}/contents/{path}", token, json=body)
    r.raise_for_status()


def _get_maps_json(token: str) -> tuple[dict, str]:
    r = _gh("GET", f"/repos/{UPSTREAM_REPO}/contents/data/maps.json", token)
    r.raise_for_status()
    data = r.json()
    return json.loads(base64.b64decode(data["content"]).decode()), data["sha"]


def build_pr(
    token: str,
    slug: str,
    title: str,
    description: str,
    airbnb_url: str,
    lat: float,
    lon: float,
    radius: float,
    geojson: dict,
    categories: list[str],
    force_update: bool = False,
    coord_confidence: str = "high",
) -> str:
    print("\nBuilding GitHub PR...", file=sys.stderr)

    r = _gh("GET", "/user", token)
    r.raise_for_status()
    login = r.json()["login"]
    print(f"  Authenticated as: {login}", file=sys.stderr)

    upstream_owner = UPSTREAM_REPO.split("/")[0]
    working_repo = UPSTREAM_REPO if login == upstream_owner else _ensure_fork(token, login)
    print(f"  Repo: {working_repo}", file=sys.stderr)

    branch = f"airbnb/{slug}"

    # B09 — detect and handle existing open PR
    r = _gh("GET", f"/repos/{UPSTREAM_REPO}/pulls", token,
            params={"head": f"{login}:{branch}", "state": "open"})
    if r.ok and r.json():
        existing_url = r.json()[0]["html_url"]
        if not force_update:
            print(f"  ⚠ Open PR already exists: {existing_url}", file=sys.stderr)
            print("  Use --force-update to replace it.", file=sys.stderr)
            return existing_url
        pr_number = r.json()[0]["number"]
        _gh("PATCH", f"/repos/{UPSTREAM_REPO}/pulls/{pr_number}", token,
            json={"state": "closed"})
        _gh("DELETE", f"/repos/{working_repo}/git/refs/heads/{branch}", token)
        print(f"  Closed existing PR and deleted branch for force update", file=sys.stderr)

    _create_branch(token, working_repo, branch, _get_main_sha(token))
    print(f"  Branch: {branch}", file=sys.stderr)

    geojson_path = f"static/{slug}/locations.geojson"
    _put_file(token, working_repo, geojson_path,
              json.dumps(geojson, ensure_ascii=False, indent=2),
              branch, f"feat({slug}): add locations.geojson from OSM Overpass")
    print(f"  ✓ {geojson_path}", file=sys.stderr)

    generated = geojson.get("_meta", {}).get("generated")
    content_path = f"content/{slug}/_index.md"
    _put_file(token, working_repo, content_path,
              _hugo_content(title, description, categories, layout=_slug_layout_name(slug), generated=generated),
              branch, f"feat({slug}): add Hugo content file")
    print(f"  ✓ {content_path}", file=sys.stderr)
    # Layout file is managed as a static Hugo template; not uploaded by this script

    maps_doc, maps_sha = _get_maps_json(token)
    n_pois = len(geojson["features"])
    cat_labels = sorted({CATEGORIES[c]["label"] for c in categories if c in CATEGORIES})
    maps_doc["maps"] = [m for m in maps_doc.get("maps", []) if m.get("slug") != slug]
    maps_doc["maps"].append({
        "slug": slug,
        "url": f"/{slug}/",
        "title": title,
        "description": description,
        "emoji": "🏠",
        "section": "airbnb",
        "weight": 55,
        "accent_color": "#1a6b3c",
        "tags": [f"{CATEGORIES[c]['icon']} {CATEGORIES[c]['label']}" for c in categories if c in CATEGORIES],
        "poi_count": n_pois,
        "categories": cat_labels,
        "has_geojson": True,
    })
    maps_doc["_meta"]["map_count"] = len(maps_doc["maps"])
    maps_doc["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    r = _gh("PUT", f"/repos/{working_repo}/contents/data/maps.json", token, json={
        "message": f"feat({slug}): update maps.json",
        "content": base64.b64encode(json.dumps(maps_doc, ensure_ascii=False, indent=2).encode()).decode(),
        "branch": branch,
        "sha": maps_sha,
    })
    r.raise_for_status()
    print("  ✓ data/maps.json", file=sys.stderr)

    by_cat = {}
    for f in geojson["features"]:
        cat = f["properties"]["category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    cat_table = "\n".join(
        f"| {CATEGORIES[c]['icon']} {CATEGORIES[c]['label']} | {by_cat.get(CATEGORIES[c]['label'], 0)} |"
        for c in categories if c in CATEGORIES
    )

    confidence_warning = "\n> ⚠ **Coordinate confidence: low** — verify coordinates before merging.\n" if coord_confidence == "low" else ""
    pr_body = f"""## {title}
{confidence_warning}
**Airbnb listing:** {airbnb_url}
**Coordinates:** {lat:.6f}°N, {lon:.6f}°E  (confidence: {coord_confidence})
**Radius:** {_fmt_dist(radius)}
**Total POIs:** {n_pois}
**Dedup radius:** {DEDUP_RADIUS_M}m (intra-category proximity)
**Source:** OSM Overpass

### POIs by category

| Category | Count |
|----------|-------|
{cat_table}

### Files added
- `static/{slug}/locations.geojson`
- `content/{slug}/_index.md`
- `data/maps.json` updated

🤖 Generated with [Claude Code](https://claude.com/claude-code) · `scripts/airbnb_env/airbnb_nearby.py`
"""

    head = branch if login == upstream_owner else f"{login}:{branch}"
    r = _gh("POST", f"/repos/{UPSTREAM_REPO}/pulls", token, json={
        "title": f"feat({slug}): add nearby POI map ({n_pois} places)",
        "head": head,
        "base": "main",
        "body": pr_body,
    })
    r.raise_for_status()
    return r.json()["html_url"]


# ---------------------------------------------------------------------------
# C04 — Batch mode
# ---------------------------------------------------------------------------

def _batch_mode(args: argparse.Namespace, cfg: "Config", radius: float, max_poi: int, dedup_radius: float) -> None:
    """Process multiple listings from a CSV file."""
    console = Console()
    rows: list[dict] = []
    with open(args.batch, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    results_summary = []
    for row in rows:
        slug = row.get("slug", "").strip()
        url  = row.get("url", "").strip()
        if not slug or not url:
            results_summary.append({"slug": slug or "?", "status": "skipped (missing url/slug)", "n_pois": 0, "pr": ""})
            continue
        print(f"\n--- Processing {slug} ---", file=sys.stderr)
        try:
            row_lat = float(row["lat"]) if row.get("lat") else None
            row_lon = float(row["lon"]) if row.get("lon") else None
            row_gmaps = row.get("gmaps_url", "").strip() or None
            confidence = "high"
            if row_lat is not None and row_lon is not None:
                lat, lon = row_lat, row_lon
            elif row_gmaps:
                lat, lon = coords_from_gmaps_url(row_gmaps)
            else:
                lat, lon, confidence = coords_from_airbnb_url(url)
            title = row.get("title", "").strip() or None
            location = reverse_geocode(lat, lon)
            if not title and location.get("neighbourhood"):
                title = f"{location['neighbourhood']}, {location['city']}"
            listing_id = listing_id_from_url(url)
            title = title or f"Airbnb — Nearby Places ({listing_id})"
            requested = [c.strip() for c in (args.categories or ",".join(cfg.default_categories)).split(",") if c.strip()]
            osm_results = query_overpass(requested, lat, lon, radius)
            api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
            google_results = query_google_nearby(api_key, requested, lat, lon, radius) if api_key and not args.no_google else None
            filtered = filter_and_limit(merge_results(osm_results, google_results), lat, lon,
                                        dedup_radius=dedup_radius, max_total=max_poi)
            n_pois = sum(len(v) for v in filtered.values())
            description = row.get("description", "") or f"{n_pois} POIs near the Airbnb. Source: OSM + Google."
            geojson = build_geojson(url, lat, lon, filtered, radius, slug,
                                    coord_confidence=confidence, location=location)
            cache_save(slug, lat, lon, requested, n_pois)
            pr_url = ""
            if args.pr:
                token = args.github_token or os.getenv("GITHUB_TOKEN", "")
                if token:
                    pr_url = build_pr(token=token, slug=slug, title=title, description=description,
                                      airbnb_url=url, lat=lat, lon=lon, radius=radius,
                                      geojson=geojson, categories=requested,
                                      force_update=getattr(args, 'force_update', False),
                                      coord_confidence=confidence)
            else:
                write_local_hugo_files(slug, title, description, url, lat, lon, geojson, requested)
            results_summary.append({"slug": slug, "status": "ok", "n_pois": n_pois, "pr": pr_url})
        except Exception as e:
            results_summary.append({"slug": slug, "status": f"error: {e}", "n_pois": 0, "pr": ""})
        time.sleep(5)  # Overpass rate limit buffer

    t = Table(title="Batch results", show_header=True, header_style="bold")
    t.add_column("Slug", style="cyan"); t.add_column("Status"); t.add_column("POIs", justify="right"); t.add_column("PR URL")
    for r in results_summary:
        t.add_row(r["slug"], r["status"], str(r["n_pois"]), r["pr"])
    console.print(t)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    load_env(args.env)

    # --- Load config (must happen before any config-dependent logic) ---
    cfg = load_config(Path(args.config) if args.config else None)

    # Wire module-level shims so functions that reference CATEGORIES etc. work
    global CATEGORIES, CAT_PRIORITY, DEFAULT_CATEGORIES
    global MAX_PER_CAT, MIN_RATING, MIN_REVIEWS, MAX_TOTAL_POIS, DEDUP_RADIUS_M, HARD_DIST_CAP_M
    CATEGORIES        = cfg.categories
    CAT_PRIORITY      = cfg.trim_priority
    DEFAULT_CATEGORIES = cfg.default_categories
    MAX_PER_CAT       = cfg.max_per_category
    MIN_RATING        = cfg.min_rating
    MIN_REVIEWS       = cfg.min_reviews
    MAX_TOTAL_POIS    = cfg.max_total_pois
    DEDUP_RADIUS_M    = cfg.dedup_radius_m
    HARD_DIST_CAP_M   = cfg.hard_dist_cap_m

    if not CATEGORIES:
        print("Error: no categories defined. Check your config file.", file=sys.stderr)
        sys.exit(1)

    # Resolve CLI args that default to config values
    radius      = args.radius      if args.radius      is not None else cfg.search_radius_m
    max_poi     = args.max_poi     if args.max_poi     is not None else cfg.max_total_pois
    dedup_radius = args.dedup_radius if args.dedup_radius is not None else cfg.dedup_radius_m
    categories_arg = args.categories or ",".join(DEFAULT_CATEGORIES)

    # C04 — batch mode
    if getattr(args, 'batch', None):
        _batch_mode(args, cfg, radius, max_poi, dedup_radius)
        return

    # --- Resolve coordinates (B10 — confidence scoring) ---
    confidence = "high"
    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        confidence = "high"
        print(f"Using coordinates from --lat/--lon: {lat}, {lon}", file=sys.stderr)
    elif args.gmaps:
        lat, lon = coords_from_gmaps_url(args.gmaps)
        confidence = "high"
        print(f"Coordinates from Google Maps URL: {lat}, {lon}", file=sys.stderr)
    else:
        lat, lon, confidence = coords_from_airbnb_url(args.airbnb_url)
        print(f"Coordinates from Airbnb page: {lat}, {lon} (confidence: {confidence})", file=sys.stderr)

    # --- Slug / title defaults (resolve early for coord sanity check query) ---
    listing_id = listing_id_from_url(args.airbnb_url)
    slug = args.slug or f"airbnb/{listing_id}"

    # B11 — reverse geocode for neighbourhood name and timezone
    print("  Reverse geocoding...", file=sys.stderr)
    location = reverse_geocode(lat, lon)
    if location.get("neighbourhood"):
        print(f"  Neighbourhood: {location['neighbourhood']}, {location.get('city', '')}", file=sys.stderr)

    # B10 / C03 — coord sanity check against city centroid
    # Only run when we have a meaningful place name to search; skip for raw slug/ID strings.
    _sanity_query = args.title or location.get("city")
    if confidence != "high" and not getattr(args, 'skip_coord_check', False) and _sanity_query:
        try:
            nom = requests.get(NOMINATIM_URL,
                               params={"q": _sanity_query, "format": "json", "limit": 1},
                               headers={"User-Agent": "travel-guide/1.0"}, timeout=10)
            if nom.ok and nom.json():
                city_lat, city_lon = float(nom.json()[0]["lat"]), float(nom.json()[0]["lon"])
                dist_from_city = haversine(lat, lon, city_lat, city_lon)
                if dist_from_city > 15_000:
                    print(f"  ⚠ Coords are {dist_from_city/1000:.1f}km from '{_sanity_query}' centroid", file=sys.stderr)
                    confidence = "low"
        except Exception:
            pass

    # B10 — block on low confidence unless explicitly allowed
    if confidence == "low" and not getattr(args, 'allow_low_accuracy', False):
        print("  Error: coordinate confidence is low. Use --allow-low-accuracy to proceed, or supply --lat/--lon.", file=sys.stderr)
        sys.exit(1)

    # --- Validate categories ---
    requested = [c.strip() for c in categories_arg.split(",") if c.strip()]
    unknown = [c for c in requested if c not in CATEGORIES]
    if unknown:
        print(f"Error: unknown categories: {', '.join(unknown)}", file=sys.stderr)
        print(f"  Valid: {', '.join(CATEGORIES.keys())}", file=sys.stderr)
        sys.exit(1)

    # B08 — check GeoJSON cache before hitting APIs
    cached_geojson = None
    if not getattr(args, 'force', False):
        cached_geojson = cache_load(slug, lat, lon, requested, cfg.cache_ttl_days)
        if cached_geojson:
            print("  Using cached GeoJSON (use --force to re-fetch)", file=sys.stderr)

    if cached_geojson is None:
        # --- Query OSM Overpass ---
        print(f"\nSearching within {_fmt_dist(radius)} of {lat:.5f}, {lon:.5f}...", file=sys.stderr)
        osm_results = query_overpass(requested, lat, lon, radius)

        # --- Optional Google Places fallback ---
        google_results = None
        api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if api_key and not args.no_google:
            print("  Querying Google Places...", file=sys.stderr)
            google_results = query_google_nearby(api_key, requested, lat, lon, radius)
        elif not api_key:
            print("  GOOGLE_MAPS_API_KEY not set — using OSM only", file=sys.stderr)

        results = filter_and_limit(
            merge_results(osm_results, google_results),
            lat, lon, dedup_radius=dedup_radius, max_total=max_poi,
        )
    else:
        results = {}  # will use cached_geojson directly

    title = args.title or (
        f"{location.get('neighbourhood')}, {location.get('city')}" if location.get("neighbourhood") else None
    ) or title_from_airbnb_url(args.airbnb_url) or f"Airbnb — Nearby Places ({listing_id})"
    n_pois = (len(cached_geojson["features"]) if cached_geojson
              else sum(len(v) for v in results.values()))
    description = (
        args.description
        or f"{n_pois} POIs within {_fmt_dist(radius)} of the Airbnb listing. Source: OSM Overpass + Google Places."
    )

    # A03 — dry run: print what would happen without doing it
    if getattr(args, 'dry_run', False):
        print("=== DRY RUN ===")
        print(f"Files that would be created:")
        print(f"  static/{slug}/locations.geojson")
        print(f"  content/{slug}/_index.md")
        print(f"  {_slug_layout_path(slug)}")
        print(f"\nPR title: feat({slug}): add nearby POI map ({n_pois} places)")
        print(f"Coord confidence: {confidence}")
        if location:
            print(f"Location: {location.get('neighbourhood')}, {location.get('city')}, {location.get('country')}")
        return

    def _make_geojson() -> dict:
        if cached_geojson:
            return cached_geojson
        gj = build_geojson(args.airbnb_url, lat, lon, results, radius, slug,
                           coord_confidence=confidence, location=location)
        cache_save(slug, lat, lon, requested, len(gj["features"]))
        return gj

    # --- PR mode ---
    if args.pr:
        token = args.github_token or os.getenv("GITHUB_TOKEN", "")
        if not token:
            print("Error: --pr requires a GitHub token (--github-token or GITHUB_TOKEN env var)", file=sys.stderr)
            sys.exit(1)
        geojson = _make_geojson()
        # C05 — generate preview image
        all_pois_flat = [p for pois in results.values() for p in pois]
        preview_path = REPO_ROOT / "static" / "images" / "map-previews" / f"{slug}.png"
        generate_preview(lat, lon, all_pois_flat, preview_path)
        pr_url = build_pr(
            token=token,
            slug=slug,
            title=title,
            description=description,
            airbnb_url=args.airbnb_url,
            lat=lat,
            lon=lon,
            radius=radius,
            geojson=geojson,
            categories=requested,
            force_update=getattr(args, 'force_update', False),
            coord_confidence=confidence,
        )
        print(f"\nPR created: {pr_url}", file=sys.stderr)
        return

    # --- Local output ---
    geojson = _make_geojson()
    if args.output == "table":
        output_table(args.airbnb_url, lat, lon, results, radius)
        write_local_hugo_files(slug, title, description, args.airbnb_url, lat, lon, geojson, requested)
        print(f"\nMap at: http://localhost:1313/{slug}/  (run: hugo serve)", file=sys.stderr)
    elif args.output == "json":
        output_json(args.airbnb_url, lat, lon, results, radius)
    else:
        print(json.dumps(geojson, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
