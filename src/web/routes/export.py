from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests as http_requests

from flask import Blueprint, Response, abort, current_app, render_template, request, send_file, session

from .shared import (
    _GH_API, _GH_REPO, _CURATED_DIR,
    _SCRIPTS_DIR, _MAPS_DATA_DIR, _MAPS_IMG_DIR,
    _gh_headers, _gh_put_file,
)
from .payment import _stripe_active
from .. import maps_db
from .. import tasks as task_mod

export = Blueprint("export", __name__)


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


@export.post("/step2/continue")
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


@export.post("/step3/create-pr")
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


@export.post("/step3/notify")
def step3_notify():
    email      = request.form.get("email", "").strip()
    listing_id = request.form.get("listing_id", "").strip()
    if not email:
        return render_template("fragments/notify_result.html", email=None, error="Please enter an email address.")
    current_app.logger.info("Publish notification request: listing=%s email=%s", listing_id, email)
    return render_template("fragments/notify_result.html", email=email, error=None)


@export.get("/p/<map_uuid>/download/map")
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


@export.get("/p/<map_uuid>/download/qr")
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
