from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, abort, render_template

from .. import listing_index

explore = Blueprint("explore", __name__)

_CURATED_DIR = Path(__file__).parent.parent / "curated"


def _curated_path(listing_id: str) -> Path:
    if listing_id.startswith("zillow/"):
        safe = listing_id[len("zillow/"):].replace("/", "--")
        return _CURATED_DIR / "zillow" / f"{safe}.json"
    return _CURATED_DIR / f"{listing_id}.json"


@explore.get("/explore")
def explore_page():
    listings = listing_index.shared_listings()
    return render_template("explore.html", shared_listings=listings)


@explore.get("/api/listing/<path:listing_id>/geojson")
def listing_geojson(listing_id: str):
    path = _curated_path(listing_id)
    if not path.exists():
        abort(404)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        abort(500)
    geojson = (data.get("result") or {}).get("geojson")
    if not geojson:
        abort(404)
    return Response(
        json.dumps(geojson, ensure_ascii=False),
        content_type="application/geo+json",
    )
