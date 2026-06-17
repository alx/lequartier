"""SQLite store for Host Maps (paid neighbourhood map exports)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "listings.db"

_DDL = """
CREATE TABLE IF NOT EXISTS maps (
    uuid              TEXT PRIMARY KEY,
    listing_id        TEXT NOT NULL,
    lat               REAL NOT NULL,
    lon               REAL NOT NULL,
    result_path       TEXT,
    image_path        TEXT,
    qr_path           TEXT,
    unlocked          INTEGER NOT NULL DEFAULT 0,
    stripe_session_id TEXT,
    created_at        TEXT NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    conn.commit()
    return conn


def create(map_uuid: str, listing_id: str, lat: float, lon: float) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO maps
               (uuid, listing_id, lat, lon, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (map_uuid, listing_id, lat, lon,
             datetime.now(timezone.utc).isoformat()),
        )


def update_coords(map_uuid: str, lat: float, lon: float) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE maps SET lat=?, lon=? WHERE uuid=?",
            (lat, lon, map_uuid),
        )


def set_paths(map_uuid: str, result_path: str | None,
              image_path: str | None, qr_path: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE maps SET result_path=?, image_path=?, qr_path=? WHERE uuid=?",
            (result_path, image_path, qr_path, map_uuid),
        )


def set_stripe_session(map_uuid: str, stripe_session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE maps SET stripe_session_id=? WHERE uuid=?",
            (stripe_session_id, map_uuid),
        )


def unlock(stripe_session_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE maps SET unlocked=1 WHERE stripe_session_id=? AND unlocked=0",
            (stripe_session_id,),
        )
        return cur.rowcount > 0


def get(map_uuid: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM maps WHERE uuid=?", (map_uuid,)
        ).fetchone()
    return dict(row) if row else None


def get_by_listing_id(listing_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM maps WHERE listing_id=? ORDER BY created_at DESC LIMIT 1",
            (listing_id,),
        ).fetchone()
    return dict(row) if row else None
