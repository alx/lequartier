from __future__ import annotations

import os
from pathlib import Path

from .. import airbnb_nearby as lib

_cfg = None


def initialize(config_path: Path | None = None, env_path: Path | None = None) -> object:
    global _cfg
    from dotenv import load_dotenv

    if env_path:
        load_dotenv(env_path)
    else:
        candidate = Path(__file__).parent.parent.parent / ".env"
        if candidate.exists():
            load_dotenv(candidate)

    _cfg = lib.load_config(config_path)

    lib.CATEGORIES         = _cfg.categories
    lib.CAT_PRIORITY       = _cfg.trim_priority
    lib.DEFAULT_CATEGORIES = _cfg.default_categories
    lib.MAX_PER_CAT        = _cfg.max_per_category
    lib.MIN_RATING         = _cfg.min_rating
    lib.MIN_REVIEWS        = _cfg.min_reviews
    lib.MAX_TOTAL_POIS     = _cfg.max_total_pois
    lib.DEDUP_RADIUS_M     = _cfg.dedup_radius_m
    lib.HARD_DIST_CAP_M    = _cfg.hard_dist_cap_m

    return _cfg


def get_cfg():
    return _cfg


def resolve_coords(
    airbnb_url: str,
    gmaps_url: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[float, float, str]:
    if lat is not None and lon is not None:
        return float(lat), float(lon), "high"
    if gmaps_url:
        rlat, rlon = lib.coords_from_gmaps_url(gmaps_url)
        return rlat, rlon, "high"
    return lib.coords_from_airbnb_url(airbnb_url)


def fetch_all(
    airbnb_url: str,
    lat: float,
    lon: float,
    categories: list[str] | None = None,
    radius: float | None = None,
    progress_cb=None,
) -> tuple[dict, dict, dict, str]:
    cfg = lib.get_config()
    cats   = categories or cfg.default_categories
    radius = radius or cfg.search_radius_m

    def _prog(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    _prog(30, "Querying OSM Overpass…")
    osm = lib.query_overpass(cats, lat, lon, radius)

    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    google  = None
    if api_key:
        _prog(60, "Querying Google Places…")
        google = lib.query_google_nearby(api_key, cats, lat, lon, radius)
    else:
        _prog(60, "OSM only (no GOOGLE_MAPS_API_KEY set)")

    _prog(80, "Filtering and deduplicating…")
    merged   = lib.merge_results(osm, google)
    filtered = lib.filter_and_limit(merged, lat, lon)

    _prog(90, "Building GeoJSON…")
    location   = lib.reverse_geocode(lat, lon)
    listing_id = lib.listing_id_from_url(airbnb_url)
    slug       = f"airbnb/{listing_id}"
    geojson    = lib.build_geojson(airbnb_url, lat, lon, filtered, radius, slug,
                                   location=location)

    return filtered, geojson, location, listing_id


build_pr              = lib.build_pr
listing_id_from_url   = lib.listing_id_from_url
haversine             = lib.haversine
title_from_airbnb_url = lib.title_from_airbnb_url
photo_from_airbnb_url = lib.photo_from_airbnb_url


def listing_preview(url: str) -> dict:
    try:
        title = lib.title_from_airbnb_url(url)
    except Exception:
        title = None
    try:
        photo_url = lib.photo_from_airbnb_url(url)
    except Exception:
        photo_url = None
    return {"title": title, "photo_url": photo_url}


def apply_status_curation(features: list) -> None:
    lib._curate_statuses(features)
