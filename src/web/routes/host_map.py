from __future__ import annotations

import json
import os
import uuid as uuid_mod
from pathlib import Path

from flask import (
    Blueprint, Response, abort, current_app, jsonify,
    render_template, request, session,
)

from .shared import CATEGORY_ICONS, CATEGORY_COLORS
from .airbnb import _fetch_task, _fetch_task_direct, _fetch_task_geo
from .payment import _stripe_active
from .. import cache as cache_mod
from .. import tasks as task_mod
from .. import poi_engine
from .. import maps_db
from ... import airbnb_nearby as lib

host_map = Blueprint("host_map", __name__)


def _active_result() -> dict | None:
    task_id = session.get("fetch_task_id")
    task    = task_mod.store.get(task_id) if task_id else None
    if task and task.status == task_mod.Status.DONE:
        return task.result
    return session.get("active_result")


def _render_map_page(r: dict, privacy_circle: bool, hide_poi_list: bool = False, hide_overlay: bool = False) -> str:
    cfg = poi_engine.get_cfg()
    return render_template(
        "airbnb.html",
        mode="map",
        readonly=True,
        embed=False,
        listing_id=f"{r['lat']:.4f},{r['lon']:.4f}",
        listing_id_prefix="map",
        lat=r["lat"],
        lon=r["lon"],
        confidence=r.get("confidence", "high"),
        location=r.get("location", {}),
        geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
        n_pois=r["n_pois"],
        airbnb_url="",
        from_cache=r.get("from_cache", False),
        categories=cfg.categories if cfg else {},
        listing_title=r.get("listing_title"),
        listing_photo=None,
        privacy_circle=privacy_circle,
        hide_poi_list=hide_poi_list,
        hide_overlay=hide_overlay,
    )


# ── /api/nearby ────────────────────────────────────────────────────────────────

def _api_nearby_response(geojson: dict) -> Response:
    return Response(json.dumps(geojson, ensure_ascii=False),
                    content_type="application/geo+json")


@host_map.route("/api/nearby", methods=["OPTIONS"])
def api_nearby_preflight():
    return Response("", status=204)


@host_map.post("/api/generate")
def api_generate():
    """Start a map-generation task (called by userscripts on 404). lat/lon are optional."""
    data       = request.get_json(force=True) or {}
    site       = data.get("site", "").strip()
    listing_id = data.get("listing_id", "").strip()

    if site not in ("airbnb", "zillow"):
        return jsonify({"error": "site must be 'airbnb' or 'zillow'"}), 400
    if not listing_id:
        return jsonify({"error": "listing_id is required"}), 400

    lat_raw = data.get("lat")
    lon_raw = data.get("lon")

    if lat_raw is not None and lon_raw is not None:
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "lat and lon must be numbers"}), 400
        task = task_mod.run_in_thread(_fetch_task_direct, site, listing_id, lat, lon)
    else:
        source_url = (f"https://www.airbnb.com/rooms/{listing_id}" if site == "airbnb"
                      else f"https://www.zillow.com/homedetails/{listing_id}")
        task = task_mod.run_in_thread(_fetch_task, source_url, None, None, None, False)

    return jsonify({"task_id": task.task_id})


@host_map.get("/api/nearby")
def api_nearby():
    try:
        lat = float(request.args.get("lat", ""))
        lon = float(request.args.get("lon", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon query params are required"}), 400

    radius    = int(request.args.get("radius", 1000))
    zillow_id = request.args.get("zillow_id", "").strip() or None
    cache_key = f"zillow/{zillow_id}" if zillow_id else None

    if cache_key:
        cached = cache_mod.get(cache_key)
        if cached:
            return _api_nearby_response(cached.get("geojson", {}))

    cfg  = poi_engine.get_cfg()
    cats = cfg.default_categories if cfg else []

    try:
        api_key  = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        osm      = lib.query_overpass(cats, lat, lon, radius)
        google   = lib.query_google_nearby(api_key, cats, lat, lon, radius) if api_key else None
        merged   = lib.merge_results(osm, google)
        filtered = lib.filter_and_limit(merged, lat, lon)
        location = lib.reverse_geocode(lat, lon)
        source_url = (f"https://www.zillow.com/homedetails/{zillow_id}"
                      if zillow_id else f"https://www.zillow.com/?ll={lat},{lon}")
        geojson  = lib.build_geojson(
            source_url, lat, lon, filtered, radius,
            slug=f"zillow/{zillow_id or f'{lat:.4f},{lon:.4f}'}",
            location=location,
        )
    except Exception as exc:
        current_app.logger.error("api_nearby error: %s", exc)
        return jsonify({"error": str(exc)}), 500

    if cache_key:
        result = {
            "lat": lat, "lon": lon, "geojson": geojson,
            "n_pois": len(filtered), "location": location,
            "listing_id": cache_key, "airbnb_url": source_url,
            "from_cache": False,
        }
        cache_mod.put(cache_key, lat, lon, cats, result)

    return _api_nearby_response(geojson)


# ── /map ───────────────────────────────────────────────────────────────────────

@host_map.get("/map")
def map_page():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, TypeError, ValueError):
        return render_template(
            "airbnb.html", mode="error", readonly=True,
            error="lat and lon query parameters are required (e.g. /map?lat=48.85&lon=2.35).",
        ), 400

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return render_template(
            "airbnb.html", mode="error", readonly=True,
            error="lat must be in [-90, 90] and lon in [-180, 180].",
        ), 400

    radius        = max(100, min(int(request.args.get("radius", 1000)), 5000))
    privacy       = request.args.get("privacy") == "1"
    hide_poi_list = request.args.get("no_pois") == "1"
    hide_overlay  = request.args.get("no_overlay") == "1"

    cache_key = f"map/{lat:.4f},{lon:.4f}"
    cfg  = poi_engine.get_cfg()
    cats = cfg.default_categories if cfg else []

    cached = cache_mod.get(cache_key, lat, lon, cats)
    if cached:
        return _render_map_page(
            {**cached, "from_cache": True},
            privacy_circle=privacy,
            hide_poi_list=hide_poi_list,
            hide_overlay=hide_overlay,
        )

    try:
        api_key  = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        osm      = lib.query_overpass(cats, lat, lon, radius)
        google   = lib.query_google_nearby(api_key, cats, lat, lon, radius) if api_key else None
        merged   = lib.merge_results(osm, google)
        filtered = lib.filter_and_limit(merged, lat, lon)
        location = lib.reverse_geocode(lat, lon)
        geojson  = lib.build_geojson(
            f"geo:{lat:.6f},{lon:.6f}", lat, lon, filtered, radius,
            slug=f"map/{lat:.4f},{lon:.4f}",
            location=location,
        )
    except Exception as exc:
        current_app.logger.error("map_page error: %s", exc)
        return render_template(
            "airbnb.html", mode="error", readonly=True,
            error=f"Could not fetch POIs: {exc}",
        ), 500

    result = {
        "lat": lat, "lon": lon, "geojson": geojson,
        "n_pois": len(filtered), "location": location,
        "airbnb_url": "", "from_cache": False,
    }
    cache_mod.put(cache_key, lat, lon, cats, result)

    return _render_map_page(
        result, privacy_circle=privacy,
        hide_poi_list=hide_poi_list, hide_overlay=hide_overlay,
    )


# ── Host Map: paid export flow ────────────────────────────────────────────────


@host_map.post("/api/start-map")
def api_start_map():
    """Start a background POI task for the landing page host flow.

    Returns {task_id, uuid, listing_id} so the frontend can poll
    /tasks/<task_id>/map-state and later initiate Stripe checkout.
    """
    airbnb_url = request.form.get("airbnb_url", "").strip()
    if not airbnb_url:
        return jsonify({"error": "airbnb_url required"}), 400
    try:
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
    except Exception:
        return jsonify({"error": "Could not parse an Airbnb listing ID from that URL."}), 400

    map_uuid = str(uuid_mod.uuid4())
    maps_db.create(map_uuid, listing_id, 0.0, 0.0)

    task = task_mod.run_in_thread(_fetch_task, airbnb_url, None, None, None, False, map_uuid)
    return jsonify({"task_id": task.task_id, "uuid": map_uuid, "listing_id": listing_id})


@host_map.post("/api/start-map-geo")
def api_start_map_geo():
    """Start a background POI task from GPS coordinates (landing page geo flow)."""
    data = request.get_json(force=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400

    listing_id = f"geo/{lat:.4f},{lon:.4f}"
    map_uuid   = str(uuid_mod.uuid4())
    maps_db.create(map_uuid, listing_id, lat, lon)

    task = task_mod.run_in_thread(_fetch_task_geo, listing_id, lat, lon, map_uuid)
    return jsonify({"task_id": task.task_id, "uuid": map_uuid})


@host_map.get("/p/<map_uuid>")
def host_map_page(map_uuid: str):
    """Shareable Host Map page — interactive map always visible, exports gated."""
    rec: dict | None = maps_db.get(map_uuid)
    if not rec:
        abort(404)
        return  # unreachable, satisfies type checker

    # Verify payment immediately on Stripe success redirect
    session_id_param = request.args.get("session_id", "").strip()
    if session_id_param and not rec["unlocked"]:
        try:
            import stripe
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            s = stripe.checkout.Session.retrieve(session_id_param)
            if s.payment_status == "paid":
                maps_db.set_stripe_session(map_uuid, session_id_param)
                maps_db.unlock(session_id_param)
                rec = maps_db.get(map_uuid) or rec
        except Exception:
            pass

    embed = request.args.get("embed") == "1"
    export_mode = request.args.get("export_mode") == "true"

    result: dict = {}
    if rec.get("result_path") and Path(rec["result_path"]).exists():
        try:
            result = json.loads(Path(rec["result_path"]).read_text(encoding="utf-8"))
        except Exception:
            pass

    geojson = result.get("geojson", {})
    location = result.get("location", {})
    listing_title = result.get("custom_listing_title") or result.get("listing_title")

    category_counts: dict[str, int] = {}
    secondary_counts: dict[str, int] = {}
    for f in geojson.get("features", []):
        props = f.get("properties", {})
        cat = props.get("category")
        if not cat:
            continue
        if props.get("status") == "secondary":
            secondary_counts[cat] = secondary_counts.get(cat, 0) + 1
        else:
            category_counts[cat] = category_counts.get(cat, 0) + 1
    n_pois = sum(category_counts.values())

    base_url  = os.environ.get("SITE_BASE_URL", request.host_url.rstrip("/"))
    share_url = f"{base_url}/p/{map_uuid}"

    airbnb_url = result.get("airbnb_url", "")
    if airbnb_url and rec.get("listing_id", "").startswith("geo/"):
        airbnb_url = ""

    return render_template(
        "p_uuid.html",
        uuid=map_uuid,
        lat=rec["lat"],
        lon=rec["lon"],
        geojson_json=json.dumps(geojson, ensure_ascii=False),
        unlocked=bool(rec["unlocked"]) or not _stripe_active(),
        location=location,
        listing_title=listing_title,
        n_pois=n_pois,
        category_counts=category_counts,
        secondary_counts=secondary_counts,
        category_icons=CATEGORY_ICONS,
        category_colors=CATEGORY_COLORS,
        share_url=share_url,
        embed=embed,
        export_mode=export_mode,
        airbnb_url=airbnb_url,
    )
