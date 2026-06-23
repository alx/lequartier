from __future__ import annotations

import base64
import json
import os
import re
import time
from functools import wraps
from pathlib import Path

import requests as http_requests
from flask import Response, current_app, request

# ── GitHub API ─────────────────────────────────────────────────────────────────
_GH_API  = "https://api.github.com"
_GH_REPO = "alx/travel-guide"

# ── Directory constants ────────────────────────────────────────────────────────
_CURATED_DIR        = Path(__file__).parent.parent / "curated"
_ZILLOW_CURATED_DIR = _CURATED_DIR / "zillow"

_MAPS_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "maps"
_MAPS_IMG_DIR  = Path(__file__).parent.parent / "static" / "img" / "maps"

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"

CATEGORY_ICONS: dict[str, str] = {
    "park": "fa-tree", "train_station": "fa-train", "transit": "fa-bus",
    "airport": "fa-plane", "museum": "fa-landmark", "monument": "fa-monument",
    "university": "fa-graduation-cap", "stadium": "fa-futbol",
    "market": "fa-store", "beach": "fa-umbrella-beach",
    "restaurant": "fa-utensils", "culture": "fa-masks-theater",
    "Park": "fa-tree", "Transit": "fa-bus", "Restaurant": "fa-utensils",
    "Market": "fa-store", "Supermarket": "fa-cart-shopping",
    "Bakery & Food": "fa-cookie-bite", "Bike Share": "fa-bicycle",
    "Health": "fa-kit-medical", "Playground": "fa-child-reaching",
    "Activity": "fa-person-running", "Culture": "fa-masks-theater",
    "Wellness": "fa-spa",
}

CATEGORY_COLORS: dict[str, str] = {
    "Park": "#16a34a", "Transit": "#1d4ed8", "Restaurant": "#b45309",
    "Market": "#b45309", "Supermarket": "#0f766e", "Bakery & Food": "#b45309",
    "Bike Share": "#0891b2", "Health": "#dc2626", "Playground": "#7c3aed",
    "Activity": "#0369a1", "Culture": "#be123c", "Wellness": "#059669",
    "park": "#16a34a", "transit": "#1d4ed8", "restaurant": "#b45309",
    "market": "#b45309", "museum": "#b45309", "culture": "#be123c",
}


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


def _require_edit_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if os.environ.get("EDIT_ENABLED", "").strip().lower() != "true":
            return Response("Not found.", 404)
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
