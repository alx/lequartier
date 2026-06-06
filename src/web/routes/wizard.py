from __future__ import annotations

import base64
import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

import requests as http_requests
from functools import wraps

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
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
from ... import airbnb_nearby as lib

_GH_API             = "https://api.github.com"
_GH_REPO            = "alx/travel-guide"
_CURATED_DIR        = Path(__file__).parent.parent / "curated"
_ZILLOW_CURATED_DIR = _CURATED_DIR / "zillow"


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_put_file(hdrs: dict, branch: str, path: str, content: str, message: str) -> None:
    encoded = base64.b64encode(content.encode()).decode()
    body: dict = {"message": message, "content": encoded, "branch": branch}
    existing = http_requests.get(
        f"{_GH_API}/repos/{_GH_REPO}/contents/{path}",
        headers=hdrs,
        params={"ref": branch},
        timeout=15,
    )
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
    resp = http_requests.put(
        f"{_GH_API}/repos/{_GH_REPO}/contents/{path}",
        headers=hdrs,
        json=body,
        timeout=20,
    )
    resp.raise_for_status()

wizard = Blueprint("wizard", __name__)


def _require_edit_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        username = os.environ.get("EDIT_USERNAME", "")
        password = os.environ.get("EDIT_PASSWORD", "")
        if not username or not password:
            return f(*args, **kwargs)
        auth = request.authorization
        if auth and auth.username == username and auth.password == password:
            return f(*args, **kwargs)
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Edit"'},
        )
    return decorated


def _fetch_task(
    task: task_mod.TaskState,
    airbnb_url: str,
    gmaps_url: str | None,
    lat: float | None,
    lon: float | None,
    force: bool = False,
) -> None:
    try:
        task_mod.store.update(task.task_id, status=task_mod.Status.RUNNING,
                              progress="Resolving coordinates…", progress_pct=10)

        rlat, rlon, confidence = poi_engine.resolve_coords(airbnb_url, gmaps_url, lat, lon)
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
        cfg        = poi_engine.get_cfg()
        categories = cfg.default_categories if cfg else []

        task_mod.store.update(task.task_id,
                              partial_lat=rlat, partial_lon=rlon, partial_confidence=confidence)

        if not force:
            task_mod.store.update(task.task_id, progress="Checking cache…", progress_pct=18)
            cached = cache_mod.get(listing_id, rlat, rlon, categories,
                                   ttl_days=cfg.cache_ttl_days if cfg else 7)
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
    except SystemExit:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error="Could not extract coordinates — paste the Google Maps URL too.",
                              progress_pct=100)
    except Exception as exc:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error=str(exc), progress_pct=100)


def _random_city() -> dict:
    cities = current_app.config.get("TOP100_CITIES", [])
    return random.choice(cities) if cities else {"lat": 48.8566, "lon": 2.3522, "name": "Paris", "country": "FR"}


@wizard.get("/")
def index():
    return render_template("index.html", bg_city=_random_city())


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
        return render_template("index.html", error="Please enter an Airbnb URL.", bg_city=_random_city())

    try:
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
    except Exception:
        return render_template("index.html", error="Could not parse an Airbnb listing ID from that URL.", bg_city=_random_city())

    task = task_mod.run_in_thread(_fetch_task, airbnb_url, gmaps_url, lat, lon, force)
    return redirect(url_for("wizard.airbnb_edit_page", listing_id=listing_id, task_id=task.task_id))


def _poll_task(task_id: str, readonly: bool = False):
    task = task_mod.store.get(task_id)
    if not task:
        return render_template("fragments/error_block.html", error="Task not found.")

    if task.status == task_mod.Status.ERROR:
        return render_template("fragments/loading_fetch.html",
                               task_id=task_id, pct=100,
                               progress=task.error, error=True, readonly=readonly)

    if task.status == task_mod.Status.DONE:
        r   = task.result
        cfg = poi_engine.get_cfg()
        if not readonly:
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
            readonly=readonly,
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
    return jsonify({
        "lat":         task.partial_lat,
        "lon":         task.partial_lon,
        "confidence":  task.partial_confidence,
        "features":    task.partial_geojson.get("features", []) if task.partial_geojson else [],
        "progress_pct": task.progress_pct,
        "progress":    task.progress,
        "done":        task.status == task_mod.Status.DONE,
        "error":       task.error if task.status == task_mod.Status.ERROR else None,
    })


_PREVIEWS_DIR = Path(__file__).parent.parent / "static" / "img" / "previews"
_SCRIPTS_DIR  = Path(__file__).parent.parent.parent.parent / "scripts"


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
    )


@wizard.get("/airbnb/<listing_id>")
def airbnb_page(listing_id: str):
    """Read-only map view — no editing UI."""
    task_id = request.args.get("task_id")
    refresh = request.args.get("refresh") == "1"

    if task_id:
        task = task_mod.store.get(task_id)
        if task and task.status == task_mod.Status.DONE:
            return _render_airbnb_map(task.result, readonly=True)
        if task and task.status == task_mod.Status.ERROR:
            return render_template("airbnb.html", mode="error", listing_id=listing_id,
                                   error=task.error, readonly=True)
        return render_template("airbnb.html", mode="loading", listing_id=listing_id,
                               task_id=task_id, readonly=True)

    if not refresh:
        cached = cache_mod.get(listing_id)
        if cached:
            return _render_airbnb_map(cached, readonly=True)

    airbnb_url = f"https://www.airbnb.com/rooms/{listing_id}"
    task = task_mod.run_in_thread(_fetch_task, airbnb_url, None, None, None, refresh)
    return redirect(url_for("wizard.airbnb_page", listing_id=listing_id, task_id=task.task_id))


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
                                   error=task.error, readonly=False, embed=embed)
        return render_template("airbnb.html", mode="loading", listing_id=listing_id,
                               task_id=task_id, readonly=False, embed=embed)

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
    if curated_file.exists():
        try:
            categories = json.loads(curated_file.read_text(encoding="utf-8")).get("categories", [])
        except Exception:
            pass
    if not categories:
        cfg = poi_engine.get_cfg()
        categories = sorted(cfg.default_categories if cfg else [])

    _CURATED_DIR.mkdir(exist_ok=True)
    seed_data = {"listing_id": listing_id, "lat": lat, "lon": lon,
                 "categories": categories, "result": updated_result}
    curated_file.write_text(
        json.dumps(seed_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cache_mod.invalidate(listing_id)
    cache_mod.put(listing_id, lat, lon, categories,
                  {**updated_result, "from_cache": False})

    return jsonify({"ok": True, "n_pois": len(features)})


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
    )


@wizard.get("/zillow/<path:zillow_id>/edit")
@_require_edit_auth
def zillow_edit_page(zillow_id: str):
    cached = cache_mod.get(f"zillow/{zillow_id}")
    if not cached:
        return render_template(
            "airbnb.html", mode="error", readonly=False,
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
    cached = cache_mod.get(f"zillow/{zillow_id}")
    if not cached:
        return render_template(
            "airbnb.html", mode="error", readonly=True,
            error="No data cached for this listing yet. Open it on Zillow with the Le Quartier extension first.",
        )
    return _render_zillow_map(zillow_id, cached, readonly=True)


@wizard.post("/zillow/<path:zillow_id>/save-curated")
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
    if curated_file.exists():
        try:
            categories = json.loads(curated_file.read_text(encoding="utf-8")).get("categories", [])
        except Exception:
            pass
    if not categories:
        cfg = poi_engine.get_cfg()
        categories = sorted(cfg.default_categories if cfg else [])

    _ZILLOW_CURATED_DIR.mkdir(parents=True, exist_ok=True)
    seed_data = {"listing_id": cache_key, "lat": lat, "lon": lon,
                 "categories": categories, "result": updated_result}
    curated_file.write_text(json.dumps(seed_data, ensure_ascii=False, indent=2), encoding="utf-8")

    cache_mod.invalidate(cache_key)
    cache_mod.put(cache_key, lat, lon, categories, {**updated_result, "from_cache": False})

    return jsonify({"ok": True, "n_pois": len(features)})


# ── /api/nearby ────────────────────────────────────────────────────────────────

def _api_nearby_response(geojson: dict) -> Response:
    resp = Response(json.dumps(geojson, ensure_ascii=False),
                    content_type="application/geo+json")
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://") or current_app.debug:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@wizard.route("/api/nearby", methods=["OPTIONS"])
def api_nearby_preflight():
    resp = Response("", status=204)
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://") or current_app.debug:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


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
