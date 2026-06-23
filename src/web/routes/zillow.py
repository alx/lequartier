from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, render_template, request, session

from .shared import _ZILLOW_CURATED_DIR, _require_edit_auth, _og_image_url
from .. import cache as cache_mod
from .. import poi_engine
from .. import listing_index
from .. import tasks as task_mod

zillow = Blueprint("zillow", __name__)


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


@zillow.get("/zillow/<path:zillow_id>/edit")
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


@zillow.get("/zillow/<path:zillow_id>.geojson")
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


@zillow.get("/zillow/<path:zillow_id>")
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


@zillow.post("/zillow/<path:zillow_id>/save-curated")
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
