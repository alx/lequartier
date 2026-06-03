from __future__ import annotations

import base64
import json
import re
import time

import requests as http_requests
from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)

from .. import cache as cache_mod
from .. import tasks as task_mod
from .. import poi_engine

_GH_API  = "https://api.github.com"
_GH_REPO = "alx/travel-guide"


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

        task_mod.store.update(task.task_id, progress="Reverse geocoding…", progress_pct=22)

        _filtered, geojson, location, listing_id = poi_engine.fetch_all(
            airbnb_url, rlat, rlon, progress_cb=_prog
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


@wizard.get("/")
def index():
    return render_template("index.html")


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
        return render_template("fragments/error_block.html", error="Please enter an Airbnb URL.")

    if not force and lat is not None and lon is not None:
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
        cfg        = poi_engine.get_cfg()
        cats       = cfg.default_categories if cfg else []
        cached     = cache_mod.get(listing_id, lat, lon, cats,
                                   ttl_days=cfg.cache_ttl_days if cfg else 7)
        if cached:
            session["fetch_task_id"] = None
            session["airbnb_url"]    = airbnb_url
            session["active_result"] = cached
            listing_title = (cached.get("listing_title")
                             or request.form.get("listing_title") or None)
            listing_photo = (cached.get("listing_photo")
                             or request.form.get("listing_photo") or None)
            return render_template(
                "fragments/step2_map.html",
                task_id=None,
                lat=cached["lat"],
                lon=cached["lon"],
                confidence=cached.get("confidence", "high"),
                listing_id=cached["listing_id"],
                location=cached.get("location", {}),
                geojson_json=json.dumps(cached["geojson"], ensure_ascii=False),
                n_pois=cached["n_pois"],
                airbnb_url=cached["airbnb_url"],
                from_cache=True,
                categories=cfg.categories if cfg else {},
                listing_title=listing_title,
                listing_photo=listing_photo,
            )

    task = task_mod.run_in_thread(_fetch_task, airbnb_url, gmaps_url, lat, lon, force)
    session["fetch_task_id"] = task.task_id
    session["airbnb_url"]    = airbnb_url

    return render_template("fragments/loading_fetch.html",
                           task_id=task.task_id, pct=5, progress="Starting…", error=False)


@wizard.get("/tasks/<task_id>/poll/fetch")
def poll_fetch(task_id: str):
    task = task_mod.store.get(task_id)
    if not task:
        return render_template("fragments/error_block.html", error="Task not found.")

    if task.status == task_mod.Status.ERROR:
        return render_template("fragments/loading_fetch.html",
                               task_id=task_id, pct=100,
                               progress=task.error, error=True)

    if task.status == task_mod.Status.DONE:
        r   = task.result
        cfg = poi_engine.get_cfg()
        session["active_result"] = r
        return render_template(
            "fragments/step2_map.html",
            task_id=task_id,
            lat=r["lat"],
            lon=r["lon"],
            confidence=r["confidence"],
            listing_id=r["listing_id"],
            location=r.get("location", {}),
            geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
            n_pois=r["n_pois"],
            airbnb_url=r["airbnb_url"],
            from_cache=r.get("from_cache", False),
            categories=cfg.categories if cfg else {},
            listing_title=r.get("listing_title"),
            listing_photo=r.get("listing_photo"),
        )

    return render_template("fragments/loading_fetch.html",
                           task_id=task_id,
                           pct=task.progress_pct,
                           progress=task.progress,
                           error=False)


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
