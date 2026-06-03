from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent.parent / "cache"
DEFAULT_TTL_DAYS = 7


def _cache_path(listing_id: str) -> Path:
    safe = listing_id.replace("/", "__")
    return CACHE_DIR / f"{safe}.json"


def _age_days(iso_ts: str) -> float:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _coords_close(lat1: float, lon1: float, lat2: float, lon2: float, threshold_m: float = 100) -> bool:
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    d = r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return d <= threshold_m


def get(
    listing_id: str,
    lat: float | None = None,
    lon: float | None = None,
    categories: list[str] | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict | None:
    path = _cache_path(listing_id)
    if not path.exists():
        return None
    try:
        records: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    best: dict | None = None
    best_ts: str = ""

    for rec in records:
        ts = rec.get("cached_at", "")
        if not ts:
            continue
        try:
            age = _age_days(ts)
        except Exception:
            continue
        if age >= ttl_days:
            continue

        if lat is not None and lon is not None:
            rlat = rec.get("lat")
            rlon = rec.get("lon")
            if rlat is None or rlon is None:
                continue
            if not _coords_close(lat, lon, rlat, rlon):
                continue

        if categories is not None:
            if sorted(categories) != sorted(rec.get("categories", [])):
                continue

        if not best_ts or ts > best_ts:
            best    = rec
            best_ts = ts

    return best["result"] if best else None


def put(
    listing_id: str,
    lat: float,
    lon: float,
    categories: list[str],
    result: dict,
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(listing_id)

    try:
        records: list[dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        records = []

    records = [
        r for r in records
        if not (
            _coords_close(lat, lon, r.get("lat", 0), r.get("lon", 0))
            and sorted(categories) == sorted(r.get("categories", []))
        )
    ]

    records.append({
        "cached_at":  datetime.now(timezone.utc).isoformat(),
        "listing_id": listing_id,
        "lat":        lat,
        "lon":        lon,
        "categories": sorted(categories),
        "result":     result,
    })

    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def invalidate(listing_id: str) -> None:
    path = _cache_path(listing_id)
    if path.exists():
        path.unlink()


def stats() -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        try:
            records: list[dict] = json.loads(p.read_text(encoding="utf-8"))
            for rec in records:
                age = round(_age_days(rec.get("cached_at", "")), 1) if rec.get("cached_at") else None
                out.append({
                    "listing_id": rec.get("listing_id", p.stem),
                    "cached_at":  rec.get("cached_at", ""),
                    "age_days":   age,
                    "n_pois":     rec.get("result", {}).get("n_pois", "?"),
                    "city":       rec.get("result", {}).get("location", {}).get("city", ""),
                    "categories": rec.get("categories", []),
                })
        except Exception:
            out.append({"listing_id": p.stem, "error": "unreadable"})
    return out
