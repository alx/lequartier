"""
SQLite index for curated listing metadata.

The curated JSON files are the source of truth. This index is a fast query
layer for /explore. It can be rebuilt at any time with rebuild().
"""
import json
import os
import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "listings.db"
_CURATED_DIR = Path(__file__).parent / "curated"
_ZILLOW_CURATED_DIR = Path(__file__).parent / "zillow_curated"

_DDL = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id  TEXT PRIMARY KEY,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    title       TEXT,
    city        TEXT,
    is_shared   INTEGER NOT NULL DEFAULT 0,
    n_pois      INTEGER NOT NULL DEFAULT 0,
    cached_at   TEXT
)
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    conn.commit()
    return conn


def upsert(listing_id: str, lat: float, lon: float, title: str | None,
           city: str | None, is_shared: bool, n_pois: int,
           cached_at: str | None = None) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT INTO listings (listing_id, lat, lon, title, city, is_shared, n_pois, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                lat=excluded.lat, lon=excluded.lon, title=excluded.title,
                city=excluded.city, is_shared=excluded.is_shared,
                n_pois=excluded.n_pois, cached_at=excluded.cached_at
        """, (listing_id, lat, lon, title, city, int(is_shared), n_pois, cached_at))


def set_shared(listing_id: str, shared: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE listings SET is_shared=? WHERE listing_id=?",
                     (int(shared), listing_id))


def shared_listings() -> list[dict]:
    """Return all is_shared=1 listings ordered newest first."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT listing_id, lat, lon, title, city, n_pois, cached_at
            FROM listings WHERE is_shared=1
            ORDER BY cached_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def rebuild() -> int:
    """Scan all curated files and repopulate the index. Returns row count."""
    rows: list[tuple] = []
    for curated_dir in [_CURATED_DIR, _ZILLOW_CURATED_DIR]:
        if not curated_dir.exists():
            continue
        for path in curated_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            result = data.get("result") or {}
            location = result.get("location") or {}
            title = result.get("custom_listing_title") or result.get("listing_title")
            city = location.get("city")
            rows.append((
                data.get("listing_id", path.stem),
                data.get("lat", 0.0),
                data.get("lon", 0.0),
                title,
                city,
                int(bool(data.get("is_shared", False))),
                data.get("result", {}).get("n_pois", 0),
                data.get("result", {}).get("cached_at"),
            ))

    with _connect() as conn:
        conn.execute("DELETE FROM listings")
        conn.executemany("""
            INSERT INTO listings (listing_id, lat, lon, title, city, is_shared, n_pois, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
    return len(rows)
