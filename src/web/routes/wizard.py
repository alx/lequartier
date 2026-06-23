from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
import uuid as uuid_mod
from pathlib import Path

import requests as http_requests

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .. import cache as cache_mod
from .. import tasks as task_mod
from .. import poi_engine
from .. import listing_index
from .. import maps_db
from ... import airbnb_nearby as lib

from .shared import (
    CATEGORY_ICONS, CATEGORY_COLORS,
    _GH_API, _GH_REPO, _CURATED_DIR, _ZILLOW_CURATED_DIR,
    _MAPS_DATA_DIR, _MAPS_IMG_DIR, _SCRIPTS_DIR,
    _gh_headers, _gh_put_file, _require_edit_auth,
)
from .payment import _stripe_active

wizard = Blueprint("wizard", __name__)


@wizard.after_request
def _allow_airbnb_framing(response):
    # Allow chrome-extension:// (and any) origin to embed the read-only Airbnb map.
    # Only applied to the listing read-only route, not edit/jpg/geojson.
    if re.match(r"^/airbnb/[^/]+$", request.path):
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        response.headers.pop("X-Frame-Options", None)
    return response


def _generate_exports(map_uuid: str, listing_id: str,
                      lat: float, lon: float, result: dict) -> None:
    """Persist the result JSON, generate a QR code PNG, and fire the Playwright
    map-image screenshot as a background subprocess (best-effort)."""
    try:
        import qrcode

        base_url  = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:5010").rstrip("/")
        share_url = f"{base_url}/p/{map_uuid}"

        _MAPS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        result_path = str(_MAPS_DATA_DIR / f"{map_uuid}.json")
        Path(result_path).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )

        _MAPS_IMG_DIR.mkdir(parents=True, exist_ok=True)
        qr_file = _MAPS_IMG_DIR / f"{map_uuid}_qr.png"
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(share_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1a6b3c", back_color="white")
        img.save(qr_file)  # type: ignore[arg-type]

        maps_db.update_coords(map_uuid, lat, lon)
        maps_db.set_paths(map_uuid, result_path, None, f"img/maps/{map_uuid}_qr.png")

        # Fire map image generation asynchronously — the download endpoint also
        # generates lazily on first request, so this is best-effort pre-warming.
        preview_url = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:5010")
        env = os.environ.copy()
        env["PREVIEW_BASE_URL"] = preview_url
        subprocess.Popen(
            ["node", str(_SCRIPTS_DIR / "generate-map-image.js"), map_uuid],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[maps] export generation failed for {map_uuid}: {exc}")


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


@wizard.get("/")
def index():
    all_cities = current_app.config.get("TOP100_CITIES", [])
    return render_template("index.html", bg_city=_random_city(), all_cities=all_cities, stripe_active=_stripe_active())


@wizard.get("/airbnb/")
def airbnb_index():
    return redirect(url_for("wizard.index"))


@wizard.get("/geo/")
def geo_index():
    all_cities = current_app.config.get("TOP100_CITIES", [])
    return render_template("landing.html", bg_city=_random_city(), all_cities=all_cities)


@wizard.get("/api/listing-preview")
def api_listing_preview():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url param required"}), 400
    try:
        preview = poi_engine.listing_preview(url)
        return jsonify(preview)
    except Exception:
        return jsonify({"title": None, "photo_url": None})


@wizard.post("/step1/submit")
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
    return redirect(url_for("wizard.airbnb_edit_page", listing_id=listing_id, task_id=task.task_id))


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


@wizard.get("/tasks/<task_id>/poll/fetch")
def poll_fetch(task_id: str):
    return _poll_task(task_id, readonly=False)


@wizard.get("/tasks/<task_id>/poll/view")
def poll_view(task_id: str):
    return _poll_task(task_id, readonly=True)


@wizard.get("/tasks/<task_id>/map-state")
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


@wizard.get("/airbnb/<listing_id>")
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
    return redirect(url_for("wizard.airbnb_page", listing_id=listing_id, task_id=task.task_id))


@wizard.get("/geo/<coords>")
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


@wizard.get("/airbnb/<listing_id>/edit")
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
    return redirect(url_for("wizard.airbnb_edit_page", listing_id=listing_id, task_id=task.task_id))


@wizard.get("/airbnb/<listing_id>.jpg")
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


@wizard.get("/airbnb/<listing_id>.geojson")
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


@wizard.post("/airbnb/<listing_id>/save-curated")
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


@wizard.post("/airbnb/<listing_id>/set-shared")
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


@wizard.post("/step2/continue")
def step2_continue():
    task_id = session.get("fetch_task_id")
    task    = task_mod.store.get(task_id) if task_id else None
    r       = (task.result if task and task.status == task_mod.Status.DONE
               else session.get("active_result"))

    if not r:
        return render_template("fragments/error_block.html",
                               error="Session expired — please start over.")

    active_ids    = set(request.form.getlist("active_ids"))
    secondary_ids = set(request.form.getlist("secondary_ids"))
    geojson       = r["geojson"]
    if active_ids:
        features = [f for f in geojson["features"] if f["id"] in active_ids]
        for f in features:
            f["properties"]["status"] = "secondary" if f["id"] in secondary_ids else "primary"
        geojson = {**geojson, "features": features}

    session["active_geojson"] = geojson
    n_active = len(geojson["features"])

    listing_id    = r["listing_id"]
    location      = r.get("location", {})
    city          = location.get("city") or ""
    neighbourhood = location.get("neighbourhood") or ""
    default_title = f"{neighbourhood}, {city}".strip(", ") or f"Airbnb {listing_id}"
    slug          = f"airbnb/{listing_id}"

    curated_file = _CURATED_DIR / f"{listing_id}.json"
    is_shared = False
    if curated_file.exists():
        try:
            is_shared = bool(json.loads(curated_file.read_text(encoding="utf-8")).get("is_shared", False))
        except Exception:
            pass

    return render_template(
        "fragments/step3_publish.html",
        listing_id=listing_id,
        location=location,
        n_pois=n_active,
        slug=slug,
        default_title=default_title,
        airbnb_url=r["airbnb_url"],
        geojson_json=json.dumps(geojson, ensure_ascii=False),
        has_github_token=bool(current_app.config.get("GITHUB_TOKEN")),
        can_write_local=False,
        in_git_repo=bool(current_app.config.get("IN_GIT_REPO")),
        edit_enabled=os.environ.get("EDIT_ENABLED", "").strip().lower() == "true",
        is_shared=is_shared,
    )


@wizard.post("/step3/create-pr")
def step3_create_pr():
    slug        = request.form.get("slug", "").strip()
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    email       = request.form.get("email", "").strip()

    if not email:
        return render_template("fragments/pr_result.html",
                               error="Please enter your email address.", pr_url=None, email="")

    geojson = session.get("active_geojson")
    result  = session.get("active_result") or {}

    if not geojson:
        return render_template("fragments/pr_result.html",
                               error="Session expired — please start over.", pr_url=None)

    token = current_app.config.get("GITHUB_TOKEN", "")
    if not token:
        return render_template("fragments/pr_result.html",
                               error="No GITHUB_TOKEN configured.", pr_url=None)

    geojson = {**geojson, "metadata": {
        "airbnb_id": result.get("listing_id", ""),
        "title":     title,
        "lat":       result.get("lat"),
        "lon":       result.get("lon"),
    }}

    hdrs         = _gh_headers(token)
    slug_safe    = re.sub(r"[^a-zA-Z0-9_-]", "-", slug)
    branch       = f"lequartier/{slug_safe}-{int(time.time())}"
    front_matter = (
        f"---\ntitle: {json.dumps(title)}\n"
        f"description: {json.dumps(description or '')}\n"
        f"layout: \"single\"\n---\n"
    )
    geojson_str = json.dumps(geojson, ensure_ascii=False, indent=2)

    try:
        resp = http_requests.get(
            f"{_GH_API}/repos/{_GH_REPO}/git/ref/heads/main",
            headers=hdrs, timeout=15,
        )
        resp.raise_for_status()
        base_sha = resp.json()["object"]["sha"]

        resp = http_requests.post(
            f"{_GH_API}/repos/{_GH_REPO}/git/refs",
            headers=hdrs,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            timeout=15,
        )
        resp.raise_for_status()

        _gh_put_file(hdrs, branch,
                     f"content/{slug}/_index.md", front_matter,
                     f"lequartier: add content for {slug}")
        _gh_put_file(hdrs, branch,
                     f"static/{slug}/locations.geojson", geojson_str,
                     f"lequartier: add geojson for {slug}")

        resp = http_requests.post(
            f"{_GH_API}/repos/{_GH_REPO}/pulls",
            headers=hdrs,
            json={
                "title": f"Map: {title}",
                "head":  branch,
                "base":  "main",
                "body":  (
                    f"Auto-generated by Le Quartier\n\n"
                    f"**Slug:** `{slug}`  \n"
                    f"**Title:** {title}  \n"
                    f"**Description:** {description}  \n"
                    f"**Notify:** {email}"
                ),
            },
            timeout=15,
        )
        resp.raise_for_status()
        pr_url = resp.json()["html_url"]

    except http_requests.HTTPError as exc:
        msg = ""
        try:
            msg = exc.response.json().get("message", "")
        except Exception:
            pass
        status = exc.response.status_code
        current_app.logger.error("GitHub PR error %s: %s", status, msg)
        hint = ""
        if status == 403:
            hint = " — ensure your token has Contents (read/write) and Pull requests (read/write) permissions (fine-grained PAT), or the 'repo' scope (classic PAT)."
        return render_template("fragments/pr_result.html",
                               error=f"GitHub API error {status}: {msg}{hint}",
                               pr_url=None, email=email)
    except Exception as exc:
        current_app.logger.error("GitHub PR error: %s", exc)
        return render_template("fragments/pr_result.html", error=str(exc), pr_url=None, email=email)

    return render_template("fragments/pr_result.html", error=None, pr_url=pr_url, email=email)


@wizard.post("/step3/notify")
def step3_notify():
    email      = request.form.get("email", "").strip()
    listing_id = request.form.get("listing_id", "").strip()
    if not email:
        return render_template("fragments/notify_result.html", email=None, error="Please enter an email address.")
    current_app.logger.info("Publish notification request: listing=%s email=%s", listing_id, email)
    return render_template("fragments/notify_result.html", email=email, error=None)


@wizard.get("/cache")
def cache_list():
    return render_template("cache.html", entries=cache_mod.stats())


@wizard.post("/cache/<listing_id>/invalidate")
def cache_invalidate(listing_id: str):
    cache_mod.invalidate(listing_id)
    return render_template("fragments/cache_invalidated.html", listing_id=listing_id)


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


# ── Zillow routes ─────────────────────────────────────────────────────────────
# Zillow IDs contain a slash (e.g. "23755-Clarendon-St.../19881430_zpid").
# Flask's <path:> converter accepts slashes. More-specific routes (/edit,
# .geojson) are registered before the catch-all so Werkzeug prefers them.

def _render_zillow_map(zillow_id: str, r: dict, readonly: bool) -> str:
    cfg = poi_engine.get_cfg()
    if not readonly:
        session["active_result"] = r
    return render_template(
        "airbnb.html",
        mode="map",
        readonly=readonly,
        embed=False,
        listing_id=zillow_id,
        listing_id_prefix="zillow",
        lat=r["lat"],
        lon=r["lon"],
        confidence=r.get("confidence", "high"),
        location=r.get("location", {}),
        geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
        n_pois=r["n_pois"],
        airbnb_url=r.get("airbnb_url", ""),
        from_cache=r.get("from_cache", False),
        categories=cfg.categories if cfg else {},
        listing_title=r.get("listing_title"),
        listing_photo=r.get("listing_photo"),
        og_image_url=_og_image_url(zillow_id),
    )


@wizard.get("/zillow/<path:zillow_id>/edit")
@_require_edit_auth
def zillow_edit_page(zillow_id: str):
    task_id = request.args.get("task_id")
    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            return _render_zillow_map(zillow_id, task.result, readonly=False)
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=zillow_id,
                                   listing_id_prefix="zillow", error=task.error, readonly=False)
        return render_template("airbnb.html", mode="loading", listing_id=zillow_id,
                               listing_id_prefix="zillow", task_id=task_id, readonly=False)

    cached = cache_mod.get(f"zillow/{zillow_id}")
    if not cached:
        return render_template(
            "airbnb.html", mode="error", listing_id=zillow_id, listing_id_prefix="zillow",
            readonly=False,
            error="No data cached for this listing yet. Open it on Zillow with the Le Quartier extension first.",
        )
    return _render_zillow_map(zillow_id, cached, readonly=False)


@wizard.get("/zillow/<path:zillow_id>.geojson")
def zillow_geojson(zillow_id: str):
    cached = cache_mod.get(f"zillow/{zillow_id}")
    if not cached:
        return jsonify({"error": "Not found"}), 404
    body = json.dumps(cached.get("geojson", {}), ensure_ascii=False, indent=2)
    headers: dict = {"Content-Type": "application/geo+json"}
    safe_name = zillow_id.replace("/", "--")
    if request.args.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}.geojson"'
    return Response(body, headers=headers)


@wizard.get("/zillow/<path:zillow_id>")
def zillow_page(zillow_id: str):
    task_id = request.args.get("task_id")
    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            return _render_zillow_map(zillow_id, task.result, readonly=True)
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=zillow_id,
                                   listing_id_prefix="zillow", error=task.error, readonly=True)
        return render_template("airbnb.html", mode="loading", listing_id=zillow_id,
                               listing_id_prefix="zillow", task_id=task_id, readonly=True)

    cached = cache_mod.get(f"zillow/{zillow_id}")
    if not cached:
        return render_template(
            "airbnb.html", mode="error", listing_id=zillow_id, listing_id_prefix="zillow",
            readonly=True,
            error="Not yet in Le Quartier. Open it on Zillow with the Le Quartier extension to generate the map.",
        )
    return _render_zillow_map(zillow_id, cached, readonly=True)


@wizard.post("/zillow/<path:zillow_id>/save-curated")
@_require_edit_auth
def zillow_save_curated(zillow_id: str):
    cache_key          = f"zillow/{zillow_id}"
    data               = request.get_json(force=True) or {}
    active_ids         = set(data.get("active_ids", []))
    secondary_ids      = set(data.get("secondary_ids", []))
    center_lat         = data.get("center_lat")
    center_lon         = data.get("center_lon")
    category_overrides = data.get("category_overrides") or {}

    cached = cache_mod.get(cache_key)
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
        features.append({**f, "properties": props})

    updated_geojson = {**cached["geojson"], "features": features}
    lat: float = center_lat if center_lat is not None else cached["lat"]
    lon: float = center_lon if center_lon is not None else cached["lon"]
    updated_result  = {**cached, "lat": lat, "lon": lon,
                       "geojson": updated_geojson, "n_pois": len(features)}

    safe_name     = zillow_id.replace("/", "--")
    curated_file  = _ZILLOW_CURATED_DIR / f"{safe_name}.json"
    categories: list[str] = []
    is_shared: bool = False
    if curated_file.exists():
        try:
            existing  = json.loads(curated_file.read_text(encoding="utf-8"))
            categories = existing.get("categories", [])
            is_shared  = bool(existing.get("is_shared", False))
        except Exception:
            pass
    if not categories:
        cfg = poi_engine.get_cfg()
        categories = sorted(cfg.default_categories if cfg else [])

    _ZILLOW_CURATED_DIR.mkdir(parents=True, exist_ok=True)
    seed_data = {"listing_id": cache_key, "lat": lat, "lon": lon,
                 "is_shared": is_shared, "categories": categories,
                 "result": updated_result}
    curated_file.write_text(json.dumps(seed_data, ensure_ascii=False, indent=2), encoding="utf-8")

    cache_mod.invalidate(cache_key)
    cache_mod.put(cache_key, lat, lon, categories, {**updated_result, "from_cache": False})

    zillow_location = updated_result.get("location") or {}
    listing_index.upsert(
        listing_id=cache_key, lat=lat, lon=lon,
        title=updated_result.get("custom_listing_title") or updated_result.get("listing_title"),
        city=zillow_location.get("city"),
        is_shared=is_shared,
        n_pois=len(features),
        cached_at=updated_result.get("cached_at"),
    )

    return jsonify({"ok": True, "n_pois": len(features)})


# ── /api/nearby ────────────────────────────────────────────────────────────────

def _api_nearby_response(geojson: dict) -> Response:
    return Response(json.dumps(geojson, ensure_ascii=False),
                    content_type="application/geo+json")


@wizard.route("/api/nearby", methods=["OPTIONS"])
def api_nearby_preflight():
    return Response("", status=204)


@wizard.post("/api/generate")
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


@wizard.get("/api/nearby")
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

@wizard.get("/map")
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


@wizard.post("/api/start-map")
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


@wizard.post("/api/start-map-geo")
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


@wizard.get("/p/<map_uuid>")
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


@wizard.get("/p/<map_uuid>/download/map")
def download_map_image(map_uuid: str):
    """Download the PNG map export — async pre-warm at payment time; 202 on cache miss."""
    rec = maps_db.get(map_uuid)
    if not rec or (not rec["unlocked"] and _stripe_active()):
        abort(403)

    img_path = _MAPS_IMG_DIR / f"{map_uuid}_map_v2.png"
    if img_path.exists():
        maps_db.set_paths(
            map_uuid,
            rec.get("result_path"),  # type: ignore[union-attr]
            f"img/maps/{map_uuid}_map_v2.png",
            rec.get("qr_path"),  # type: ignore[union-attr]
        )
        return send_file(
            img_path,
            as_attachment=True,
            download_name=f"lequartier-{map_uuid[:8]}.png",
            mimetype="image/png",
        )

    # PNG not ready — fire async generation and return a self-refreshing wait page.
    # Never block the gunicorn worker with a synchronous Playwright call.
    _MAPS_IMG_DIR.mkdir(parents=True, exist_ok=True)
    preview_url = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:5010")
    env = os.environ.copy()
    env["PREVIEW_BASE_URL"] = preview_url
    subprocess.Popen(
        ["node", str(_SCRIPTS_DIR / "generate-map-image.js"), map_uuid],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return Response(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='30'></head>"
        "<body style='font-family:sans-serif;padding:2rem;max-width:480px;margin:auto;'>"
        "<h2 style='color:#1a6b3c;'>Your map is being generated</h2>"
        "<p>This page will refresh automatically in 30 seconds.</p>"
        "<p style='color:#6b7280;font-size:0.9rem;'>If this page keeps refreshing after "
        "2 minutes, email <a href='mailto:support@lequartier.co'>support@lequartier.co</a> "
        "and we'll send your map manually.</p>"
        "</body></html>",
        status=202,
        mimetype="text/html",
    )


@wizard.get("/p/<map_uuid>/download/qr")
def download_qr(map_uuid: str):
    """Download the QR code PNG — pre-generated when task completed."""
    rec = maps_db.get(map_uuid)
    if not rec or (not rec["unlocked"] and _stripe_active()):
        abort(403)

    qr_path = _MAPS_IMG_DIR / f"{map_uuid}_qr.png"
    if not qr_path.exists():
        abort(404)

    return send_file(
        qr_path,
        as_attachment=True,
        download_name=f"lequartier-qr-{map_uuid[:8]}.png",
        mimetype="image/png",
    )
