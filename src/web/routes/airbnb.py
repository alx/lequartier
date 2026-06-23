from __future__ import annotations

import json
import os
import random
import subprocess
import uuid as uuid_mod
from pathlib import Path

from flask import (
    Blueprint, Response, abort, current_app, jsonify,
    make_response, redirect, render_template, request, send_file, session, url_for,
)

from .shared import (
    CATEGORY_ICONS, CATEGORY_COLORS,
    _CURATED_DIR, _SCRIPTS_DIR, _MAPS_IMG_DIR,
    _require_edit_auth,
)
from .export import _generate_exports
from .payment import _stripe_active
from .. import cache as cache_mod
from .. import tasks as task_mod
from .. import poi_engine
from .. import listing_index
from .. import maps_db
from ... import airbnb_nearby as lib

airbnb = Blueprint("airbnb", __name__)


@airbnb.after_request
def _allow_airbnb_framing(response):
    import re
    # Allow chrome-extension:// (and any) origin to embed the read-only Airbnb map.
    # Only applied to the listing read-only route, not edit/jpg/geojson.
    if re.match(r"^/airbnb/[^/]+$", request.path):
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        response.headers.pop("X-Frame-Options", None)
    return response


def _fetch_task(
    task: task_mod.TaskState,
    airbnb_url: str,
    gmaps_url: str | None,
    lat: float | None,
    lon: float | None,
    force: bool = False,
    map_uuid: str | None = None,
) -> None:
    try:
        task_mod.store.update(task.task_id, status=task_mod.Status.RUNNING,
                              progress="Checking cache…", progress_pct=10)

        listing_id = poi_engine.listing_id_from_url(airbnb_url)
        cfg        = poi_engine.get_cfg()
        categories = cfg.default_categories if cfg else []

        if not force:
            cached = cache_mod.get(listing_id, lat, lon, categories,
                                   ttl_days=cfg.cache_ttl_days if cfg else 7)
            if cached:
                rlat = cached.get("lat") or lat or 0.0
                rlon = cached.get("lon") or lon or 0.0
                confidence = cached.get("confidence", "high")
                task_mod.store.update(task.task_id,
                                      partial_lat=rlat, partial_lon=rlon,
                                      partial_confidence=confidence)
                features = cached.get("geojson", {}).get("features", [])
                if features and "status" not in features[0].get("properties", {}):
                    poi_engine.apply_status_curation(features)
                cached_result = {**cached, "from_cache": True}
                task_mod.store.update(
                    task.task_id,
                    status=task_mod.Status.DONE,
                    progress="Loaded from cache",
                    progress_pct=100,
                    result=cached_result,
                )
                if map_uuid:
                    _generate_exports(map_uuid, listing_id, rlat, rlon, cached_result)
                return

        task_mod.store.update(task.task_id, progress="Resolving coordinates…", progress_pct=18)
        rlat, rlon, confidence = poi_engine.resolve_coords(airbnb_url, gmaps_url, lat, lon)
        task_mod.store.update(task.task_id,
                              partial_lat=rlat, partial_lon=rlon, partial_confidence=confidence)

        def _prog(pct, msg):
            task_mod.store.update(task.task_id, progress=msg, progress_pct=pct)

        def _partial(partial_gj):
            task_mod.store.update(task.task_id, partial_geojson=partial_gj)

        def _log(msg):
            task_mod.store.update(task.task_id, progress=msg)

        task_mod.store.update(task.task_id, progress="Reverse geocoding…", progress_pct=22)

        _filtered, geojson, location, listing_id = poi_engine.fetch_all(
            airbnb_url, rlat, rlon, progress_cb=_prog, partial_cb=_partial, log_cb=_log
        )

        task_mod.store.update(task.task_id, progress="Compressing GeoJSON…", progress_pct=93)

        n_pois = len(geojson.get("features", []))
        try:
            listing_title = poi_engine.title_from_airbnb_url(airbnb_url)
        except Exception:
            listing_title = None
        try:
            listing_photo = poi_engine.photo_from_airbnb_url(airbnb_url)
        except Exception:
            listing_photo = None

        result = {
            "lat":           rlat,
            "lon":           rlon,
            "confidence":    confidence,
            "listing_id":    listing_id,
            "location":      location,
            "geojson":       geojson,
            "n_pois":        n_pois,
            "airbnb_url":    airbnb_url,
            "from_cache":    False,
            "listing_title": listing_title,
            "listing_photo": listing_photo,
        }

        cache_mod.put(listing_id, rlat, rlon, categories, result)

        task_mod.store.update(
            task.task_id,
            status=task_mod.Status.DONE,
            progress="Done!",
            progress_pct=100,
            result=result,
        )
        if map_uuid:
            _generate_exports(map_uuid, listing_id, rlat, rlon, result)
    except SystemExit:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error="Could not extract coordinates — paste the Google Maps URL too.",
                              progress_pct=100)
    except Exception as exc:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error=str(exc), progress_pct=100)


def _fetch_task_direct(
    task: task_mod.TaskState,
    site: str,
    listing_id: str,
    lat: float,
    lon: float,
    map_uuid: str | None = None,
) -> None:
    """Task runner for userscript-triggered generation — coordinates already known."""
    try:
        cache_key  = f"zillow/{listing_id}" if site == "zillow" else listing_id
        source_url = (f"https://www.airbnb.com/rooms/{listing_id}" if site == "airbnb"
                      else f"https://www.zillow.com/homedetails/{listing_id}")

        task_mod.store.update(
            task.task_id, status=task_mod.Status.RUNNING,
            progress="Checking cache…", progress_pct=15,
            partial_lat=lat, partial_lon=lon, partial_confidence="high",
        )

        cfg        = poi_engine.get_cfg()
        categories = cfg.default_categories if cfg else []
        cached     = cache_mod.get(cache_key)
        if cached:
            features = cached.get("geojson", {}).get("features", [])
            if features and "status" not in features[0].get("properties", {}):
                poi_engine.apply_status_curation(features)
            task_mod.store.update(
                task.task_id,
                status=task_mod.Status.DONE,
                progress="Loaded from cache",
                progress_pct=100,
                result={**cached, "from_cache": True},
            )
            return

        radius = cfg.search_radius_m if cfg else 1000

        def _prog(pct, msg):
            task_mod.store.update(task.task_id, progress=msg, progress_pct=pct)

        def _partial(partial_gj):
            task_mod.store.update(task.task_id, partial_geojson=partial_gj)

        def _log(msg):
            task_mod.store.update(task.task_id, progress=msg)

        _prog(22, "Reverse geocoding…")
        location = lib.reverse_geocode(lat, lon)

        def _per_cat(cat_key):
            label = lib.CATEGORIES.get(cat_key, {}).get("label", cat_key)
            _log(f"Querying OSM for {label}…")

        _prog(30, "Querying OSM…")
        osm = lib.query_overpass(categories, lat, lon, radius, per_cat_cb=_per_cat)

        if osm:
            osm_filtered = lib.filter_and_limit(lib.merge_results(osm, None), lat, lon)
            slug         = f"{site}/{listing_id}"
            partial_gj   = lib.build_geojson(source_url, lat, lon, osm_filtered, radius, slug,
                                             location=location)
            _partial(partial_gj)

        api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        google  = None
        if api_key:
            _log("Querying Google Places…")
            _prog(60, "Querying Google Places…")
            google = lib.query_google_nearby(api_key, categories, lat, lon, radius)
        else:
            _prog(60, "OSM only (no Google Maps API key)")

        _prog(80, "Filtering and deduplicating…")
        merged   = lib.merge_results(osm, google)
        filtered = lib.filter_and_limit(merged, lat, lon)

        _prog(90, "Building GeoJSON…")
        slug    = f"{site}/{listing_id}"
        geojson = lib.build_geojson(source_url, lat, lon, filtered, radius, slug,
                                    location=location)

        _prog(93, "Compressing GeoJSON…")

        listing_title = None
        listing_photo = None
        if site == "airbnb":
            try:
                listing_title = poi_engine.title_from_airbnb_url(source_url)
            except Exception:
                pass
            try:
                listing_photo = poi_engine.photo_from_airbnb_url(source_url)
            except Exception:
                pass

        n_pois = len(filtered)
        result = {
            "lat":           lat,
            "lon":           lon,
            "confidence":    "high",
            "listing_id":    cache_key,
            "location":      location,
            "geojson":       geojson,
            "n_pois":        n_pois,
            "airbnb_url":    source_url,
            "from_cache":    False,
            "listing_title": listing_title,
            "listing_photo": listing_photo,
        }

        cache_mod.put(cache_key, lat, lon, categories, result)

        task_mod.store.update(
            task.task_id,
            status=task_mod.Status.DONE,
            progress="Done!",
            progress_pct=100,
            result=result,
        )
    except Exception as exc:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error=str(exc), progress_pct=100)


def _fetch_task_geo(
    task: task_mod.TaskState,
    listing_id: str,
    lat: float,
    lon: float,
    map_uuid: str | None = None,
) -> None:
    """Task runner for coordinate-based map generation — no Airbnb URL involved."""
    try:
        task_mod.store.update(task.task_id, status=task_mod.Status.RUNNING,
                              progress="Checking cache…", progress_pct=10,
                              partial_lat=lat, partial_lon=lon, partial_confidence="high")

        cfg        = poi_engine.get_cfg()
        categories = cfg.default_categories if cfg else []

        cached = cache_mod.get(listing_id, lat, lon, categories,
                               ttl_days=cfg.cache_ttl_days if cfg else 7)
        if cached:
            features = cached.get("geojson", {}).get("features", [])
            if features and "status" not in features[0].get("properties", {}):
                poi_engine.apply_status_curation(features)
            task_mod.store.update(task.task_id,
                                  status=task_mod.Status.DONE,
                                  progress="Loaded from cache",
                                  progress_pct=100,
                                  result={**cached, "from_cache": True})
            return

        def _prog(pct, msg):
            task_mod.store.update(task.task_id, progress=msg, progress_pct=pct)

        def _partial(partial_gj):
            task_mod.store.update(task.task_id, partial_geojson=partial_gj)

        def _log(msg):
            task_mod.store.update(task.task_id, progress=msg)

        _filtered, geojson, location, _ = poi_engine.fetch_all(
            lat=lat, lon=lon,
            listing_id=listing_id,
            progress_cb=_prog, partial_cb=_partial, log_cb=_log,
        )

        n_pois = len(geojson.get("features", []))
        result = {
            "lat":           lat,
            "lon":           lon,
            "confidence":    "high",
            "listing_id":    listing_id,
            "location":      location,
            "geojson":       geojson,
            "n_pois":        n_pois,
            "airbnb_url":    "",
            "from_cache":    False,
            "listing_title": None,
            "listing_photo": None,
        }

        cache_mod.put(listing_id, lat, lon, categories, result)

        task_mod.store.update(task.task_id,
                              status=task_mod.Status.DONE,
                              progress="Done!",
                              progress_pct=100,
                              result=result)
        if map_uuid:
            _generate_exports(map_uuid, listing_id, lat, lon, result)
    except Exception as exc:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error=str(exc), progress_pct=100)


def _random_city() -> dict:
    cities = current_app.config.get("TOP100_CITIES", [])
    return random.choice(cities) if cities else {"lat": 48.8566, "lon": 2.3522, "name": "Paris", "country": "FR"}


@airbnb.get("/")
def index():
    all_cities = current_app.config.get("TOP100_CITIES", [])
    return render_template("index.html", bg_city=_random_city(), all_cities=all_cities, stripe_active=_stripe_active())


@airbnb.get("/airbnb/")
def airbnb_index():
    return redirect(url_for("airbnb.index"))


@airbnb.get("/geo/")
def geo_index():
    all_cities = current_app.config.get("TOP100_CITIES", [])
    return render_template("landing.html", bg_city=_random_city(), all_cities=all_cities)


@airbnb.get("/api/listing-preview")
def api_listing_preview():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url param required"}), 400
    try:
        preview = poi_engine.listing_preview(url)
        return jsonify(preview)
    except Exception:
        return jsonify({"title": None, "photo_url": None})


@airbnb.post("/step1/submit")
def step1_submit():
    airbnb_url = request.form.get("airbnb_url", "").strip()
    gmaps_url  = request.form.get("gmaps_url", "").strip() or None
    lat_s      = request.form.get("lat", "").strip() or None
    lon_s      = request.form.get("lon", "").strip() or None
    force      = request.form.get("force_refresh") == "1"

    lat = float(lat_s) if lat_s else None
    lon = float(lon_s) if lon_s else None

    if not airbnb_url:
        return render_template("index.html", error="Please enter an Airbnb URL.", bg_city=_random_city(), all_cities=current_app.config.get("TOP100_CITIES", []))

    try:
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
    except Exception:
        return render_template("index.html", error="Could not parse an Airbnb listing ID from that URL.", bg_city=_random_city(), all_cities=current_app.config.get("TOP100_CITIES", []))

    task = task_mod.run_in_thread(_fetch_task, airbnb_url, gmaps_url, lat, lon, force)
    return redirect(url_for("airbnb.airbnb_edit_page", listing_id=listing_id, task_id=task.task_id))


def _get_or_create_host_map(listing_id: str, lat: float, lon: float, result: dict) -> str:
    """Return the stable UUID for listing_id, creating one if absent."""
    existing = maps_db.get_by_listing_id(listing_id)
    if existing:
        map_uuid = existing["uuid"]
        if not existing.get("result_path") or not Path(existing["result_path"]).exists():
            _generate_exports(map_uuid, listing_id, lat, lon, result)
        return map_uuid
    map_uuid = str(uuid_mod.uuid4())
    maps_db.create(map_uuid, listing_id, lat, lon)
    _generate_exports(map_uuid, listing_id, lat, lon, result)
    return map_uuid


def _poll_task(task_id: str, readonly: bool = False):
    task = task_mod.store.get(task_id)
    if not task:
        return render_template("fragments/error_block.html", error="Task not found.")

    if task.status == task_mod.Status.ERROR:
        return render_template("fragments/loading_fetch.html",
                               task_id=task_id, pct=100,
                               progress=task.error, error=True, readonly=readonly)

    if task.status == task_mod.Status.DONE:
        r = task.result
        if readonly:
            map_uuid = _get_or_create_host_map(
                r["listing_id"], r["lat"], r["lon"], r
            )
            resp = make_response("")
            resp.headers["HX-Redirect"] = url_for("wizard.host_map_page", map_uuid=map_uuid)
            return resp
        cfg = poi_engine.get_cfg()
        session["active_result"] = r
        location = dict(r.get("location", {}))
        if r.get("custom_neighbourhood"):
            location["neighbourhood"] = r["custom_neighbourhood"]
        listing_title = r.get("custom_listing_title") or r.get("listing_title")
        return render_template(
            "fragments/step2_map.html",
            task_id=task_id,
            lat=r["lat"],
            lon=r["lon"],
            confidence=r["confidence"],
            listing_id=r["listing_id"],
            location=location,
            geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
            n_pois=r["n_pois"],
            airbnb_url=r["airbnb_url"],
            from_cache=r.get("from_cache", False),
            categories=cfg.categories if cfg else {},
            listing_title=listing_title,
            listing_photo=r.get("listing_photo"),
            readonly=False,
        )

    return render_template("fragments/loading_fetch.html",
                           task_id=task_id,
                           pct=task.progress_pct,
                           progress=task.progress,
                           error=False, readonly=readonly)


@airbnb.get("/tasks/<task_id>/poll/fetch")
def poll_fetch(task_id: str):
    return _poll_task(task_id, readonly=False)


@airbnb.get("/tasks/<task_id>/poll/view")
def poll_view(task_id: str):
    return _poll_task(task_id, readonly=True)


@airbnb.get("/tasks/<task_id>/map-state")
def task_map_state(task_id: str):
    task = task_mod.store.get(task_id)
    if not task:
        return jsonify({}), 404
    location = {}
    listing_title = None
    if task.status == task_mod.Status.DONE and task.result:
        location      = task.result.get("location", {})
        listing_title = (task.result.get("custom_listing_title")
                         or task.result.get("listing_title"))
    return jsonify({
        "lat":          task.partial_lat,
        "lon":          task.partial_lon,
        "confidence":   task.partial_confidence,
        "features":     (
            task.partial_geojson.get("features", [])
            if task.partial_geojson
            else (task.result.get("geojson", {}).get("features", [])
                  if task.status == task_mod.Status.DONE and task.result
                  else [])
        ),
        "progress_pct": task.progress_pct,
        "progress":     task.progress,
        "done":         task.status == task_mod.Status.DONE,
        "error":        task.error if task.status == task_mod.Status.ERROR else None,
        "location":     location,
        "listing_title": listing_title,
    })


_PREVIEWS_DIR = Path(__file__).parent.parent / "static" / "img" / "previews"
_OG_IMAGES_DIR = Path(__file__).parent.parent / "static" / "img" / "og"


def _og_image_url(listing_id: str) -> str | None:
    """Return an absolute URL to the pre-generated OG PNG, or None if absent."""
    if (_OG_IMAGES_DIR / f"{listing_id}.png").exists():
        return url_for("static", filename=f"img/og/{listing_id}.png", _external=True)
    return None


def _render_airbnb_map(r: dict, readonly: bool = False, embed: bool = False) -> str:
    cfg = poi_engine.get_cfg()
    if not readonly:
        session["active_result"] = r
    location = dict(r.get("location", {}))
    if r.get("custom_neighbourhood"):
        location["neighbourhood"] = r["custom_neighbourhood"]
    listing_title = r.get("custom_listing_title") or r.get("listing_title")
    return render_template(
        "airbnb.html",
        mode="map",
        readonly=readonly,
        embed=embed,
        listing_id=r["listing_id"],
        lat=r["lat"],
        lon=r["lon"],
        confidence=r.get("confidence", "high"),
        location=location,
        geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
        n_pois=r["n_pois"],
        airbnb_url=r["airbnb_url"],
        from_cache=r.get("from_cache", False),
        categories=cfg.categories if cfg else {},
        listing_title=listing_title,
        listing_photo=r.get("listing_photo"),
        og_image_url=_og_image_url(r["listing_id"]),
    )


@airbnb.get("/airbnb/<listing_id>")
def airbnb_page(listing_id: str):
    """Loading gate — redirects to /p/<uuid> once data is ready."""
    task_id = request.args.get("task_id")
    refresh = request.args.get("refresh") == "1"

    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            r = task.result
            map_uuid = _get_or_create_host_map(r["listing_id"], r["lat"], r["lon"], r)
            return redirect(url_for("wizard.host_map_page", map_uuid=map_uuid))
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=listing_id,
                                   listing_id_prefix="airbnb", error=task.error, readonly=True)
        return render_template("airbnb.html", mode="loading", listing_id=listing_id,
                               listing_id_prefix="airbnb", task_id=task_id, readonly=True)

    if not refresh:
        cached = cache_mod.get(listing_id)
        if cached:
            map_uuid = _get_or_create_host_map(
                cached["listing_id"], cached["lat"], cached["lon"], cached
            )
            return redirect(url_for("wizard.host_map_page", map_uuid=map_uuid))

    airbnb_url = f"https://www.airbnb.com/rooms/{listing_id}"
    task = task_mod.run_in_thread(_fetch_task, airbnb_url, None, None, None, refresh)
    return redirect(url_for("airbnb.airbnb_page", listing_id=listing_id, task_id=task.task_id))


@airbnb.get("/geo/<coords>")
def geo_page(coords: str):
    """Loading gate for coordinate-based maps — /geo/9.4781,100.0472 → /p/<uuid>."""
    try:
        p1, p2 = coords.split(",", 1)
        lat = round(float(p1), 4)
        lon = round(float(p2), 4)
    except (ValueError, AttributeError):
        abort(400)
        return

    listing_id = f"geo/{lat},{lon}"
    task_id    = request.args.get("task_id")

    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            r = task.result
            map_uuid = _get_or_create_host_map(r["listing_id"], r["lat"], r["lon"], r)
            return redirect(url_for("wizard.host_map_page", map_uuid=map_uuid))
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=coords,
                                   listing_id_prefix="geo", error=task.error, readonly=True)
        return render_template("airbnb.html", mode="loading", listing_id=coords,
                               listing_id_prefix="geo", task_id=task_id, readonly=True)

    cached = cache_mod.get(listing_id, lat, lon)
    if cached:
        map_uuid = _get_or_create_host_map(listing_id, cached["lat"], cached["lon"], cached)
        return redirect(url_for("wizard.host_map_page", map_uuid=map_uuid))

    task = task_mod.run_in_thread(_fetch_task_geo, listing_id, lat, lon)
    return redirect(url_for("wizard.geo_page", coords=coords, task_id=task.task_id))


@airbnb.get("/airbnb/<listing_id>/edit")
@_require_edit_auth
def airbnb_edit_page(listing_id: str):
    """Full interactive editor."""
    task_id = request.args.get("task_id")
    embed   = request.args.get("embed") == "1"

    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            return _render_airbnb_map(task.result, readonly=False, embed=embed)
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=listing_id,
                                   listing_id_prefix="airbnb", error=task.error,
                                   readonly=False, embed=embed)
        return render_template("airbnb.html", mode="loading", listing_id=listing_id,
                               listing_id_prefix="airbnb", task_id=task_id,
                               readonly=False, embed=embed)

    cached = cache_mod.get(listing_id)
    if cached:
        return _render_airbnb_map(cached, readonly=False, embed=embed)

    airbnb_url = f"https://www.airbnb.com/rooms/{listing_id}"
    task = task_mod.run_in_thread(_fetch_task, airbnb_url, None, None, None, False)
    return redirect(url_for("airbnb.airbnb_edit_page", listing_id=listing_id, task_id=task.task_id))


@airbnb.get("/airbnb/<listing_id>.jpg")
def airbnb_preview_jpg(listing_id: str):
    """Static map preview — generated on first request via Playwright."""
    _PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = _PREVIEWS_DIR / f"{listing_id}.jpg"
    if not preview_path.exists():
        script = _SCRIPTS_DIR / "generate-preview.js"
        if not script.exists():
            abort(404)
        result = subprocess.run(
            ["node", str(script), listing_id],
            capture_output=True, timeout=90,
            cwd=str(_SCRIPTS_DIR.parent),
        )
        if result.returncode != 0 or not preview_path.exists():
            abort(404)
    return send_file(preview_path, mimetype="image/jpeg")


@airbnb.get("/airbnb/<listing_id>.geojson")
def airbnb_geojson(listing_id: str):
    cached = cache_mod.get(listing_id)
    if not cached:
        return jsonify({"error": "Not found"}), 404
    geojson = cached.get("geojson", {})
    body = json.dumps(geojson, ensure_ascii=False, indent=2)
    headers = {"Content-Type": "application/geo+json"}
    if request.args.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{listing_id}.geojson"'
    return Response(body, headers=headers)


@airbnb.post("/airbnb/<listing_id>/save-curated")
@_require_edit_auth
def save_curated(listing_id: str):
    data               = request.get_json(force=True) or {}
    active_ids         = set(data.get("active_ids", []))
    secondary_ids      = set(data.get("secondary_ids", []))
    center_lat         = data.get("center_lat")
    center_lon         = data.get("center_lon")
    category_overrides = data.get("category_overrides") or {}

    cached = cache_mod.get(listing_id)
    if not cached:
        return jsonify({"ok": False, "error": "Listing not in cache"}), 404

    all_features = cached.get("geojson", {}).get("features", [])
    known_cats   = {f["properties"].get("category") for f in all_features if f.get("properties")}
    features = []
    for f in all_features:
        if f.get("id") not in active_ids:
            continue
        props = {**f["properties"],
                 "status": "secondary" if f["id"] in secondary_ids else "primary"}
        if f["id"] in category_overrides and category_overrides[f["id"]] in known_cats:
            props["category"] = category_overrides[f["id"]]
        f = {**f, "properties": props}
        features.append(f)

    updated_geojson = {**cached["geojson"], "features": features}
    lat: float = center_lat if center_lat is not None else cached["lat"]
    lon: float = center_lon if center_lon is not None else cached["lon"]
    updated_result  = {**cached, "lat": lat, "lon": lon,
                       "geojson": updated_geojson, "n_pois": len(features)}

    curated_file = _CURATED_DIR / f"{listing_id}.json"
    categories: list[str] = []
    is_shared: bool = False
    if curated_file.exists():
        try:
            existing = json.loads(curated_file.read_text(encoding="utf-8"))
            categories = existing.get("categories", [])
            is_shared  = bool(existing.get("is_shared", False))
        except Exception:
            pass
    if not categories:
        cfg = poi_engine.get_cfg()
        categories = sorted(cfg.default_categories if cfg else [])

    _CURATED_DIR.mkdir(exist_ok=True)
    seed_data = {"listing_id": listing_id, "lat": lat, "lon": lon,
                 "is_shared": is_shared, "categories": categories,
                 "result": updated_result}
    curated_file.write_text(
        json.dumps(seed_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cache_mod.invalidate(listing_id)
    cache_mod.put(listing_id, lat, lon, categories,
                  {**updated_result, "from_cache": False})

    location = updated_result.get("location") or {}
    listing_index.upsert(
        listing_id=listing_id, lat=lat, lon=lon,
        title=updated_result.get("custom_listing_title") or updated_result.get("listing_title"),
        city=location.get("city"),
        is_shared=is_shared,
        n_pois=len(features),
        cached_at=updated_result.get("cached_at"),
    )

    return jsonify({"ok": True, "n_pois": len(features)})


@airbnb.post("/airbnb/<listing_id>/set-shared")
@_require_edit_auth
def set_shared(listing_id: str):
    data = request.get_json(force=True) or {}
    shared = bool(data.get("shared", False))
    curated_file = _CURATED_DIR / f"{listing_id}.json"
    if not curated_file.exists():
        return jsonify({"ok": False, "error": "Not curated yet"}), 404
    try:
        curated = json.loads(curated_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"ok": False, "error": "Corrupt curated file"}), 500
    curated["is_shared"] = shared
    curated_file.write_text(json.dumps(curated, ensure_ascii=False, indent=2), encoding="utf-8")
    listing_index.set_shared(listing_id, shared)
    return jsonify({"ok": True, "is_shared": shared})


@airbnb.get("/cache")
def cache_list():
    return render_template("cache.html", entries=cache_mod.stats())


@airbnb.post("/cache/<listing_id>/invalidate")
def cache_invalidate(listing_id: str):
    cache_mod.invalidate(listing_id)
    return render_template("fragments/cache_invalidated.html", listing_id=listing_id)
