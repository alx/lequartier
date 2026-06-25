"""Tests for scripts/build_static.py."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixture: minimal maps DB ───────────────────────────────────────────────────

@pytest.fixture
def tmp_result(tmp_path):
    """A readable result JSON file."""
    p = tmp_path / "result.json"
    p.write_text(json.dumps({
        "geojson": {"type": "FeatureCollection", "features": []},
        "location": {"city": "Bangkok", "country": "Thailand"},
        "n_pois": 0,
        "lat": 13.75,
        "lon": 100.5,
        "listing_id": "listing-111",
    }), encoding="utf-8")
    return p


@pytest.fixture
def tmp_db(tmp_path, tmp_result):
    """Maps DB with: two UUIDs for listing-111 (ccc is newer), listing-222 with no result_path."""
    db = tmp_path / "listings.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE maps (
            uuid TEXT PRIMARY KEY, listing_id TEXT NOT NULL,
            result_path TEXT, lat REAL, lon REAL,
            unlocked INTEGER DEFAULT 0, stripe_session_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.executemany("INSERT INTO maps VALUES (?,?,?,?,?,0,NULL,?)", [
        ("uuid-aaa", "listing-111", str(tmp_result), 13.75, 100.5, "2026-01-01T00:00:00"),
        ("uuid-ccc", "listing-111", str(tmp_result), 13.75, 100.5, "2026-01-03T00:00:00"),
        ("uuid-bbb", "listing-222", None,            13.75, 100.5, "2026-01-02T00:00:00"),
    ])
    conn.commit()
    conn.close()
    return db


# ── get_canonical_listings ─────────────────────────────────────────────────────

def test_get_canonical_listings_deduplicates(tmp_db):
    import scripts.build_static as bs
    listings = bs.get_canonical_listings(tmp_db)
    # listing-111: two rows, newest (uuid-ccc, 2026-01-03) wins
    assert len(listings) == 1
    assert listings[0] == ("listing-111", "uuid-ccc")


def test_get_canonical_listings_skips_null_result_path(tmp_db):
    import scripts.build_static as bs
    listings = bs.get_canonical_listings(tmp_db)
    listing_ids = [lid for lid, _ in listings]
    assert "listing-222" not in listing_ids


def test_get_canonical_listings_skips_missing_file(tmp_db, tmp_path):
    import scripts.build_static as bs
    # Point result_path at a file that does not exist
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("UPDATE maps SET result_path=? WHERE uuid=?",
                 (str(tmp_path / "gone.json"), "uuid-ccc"))
    conn.commit()
    conn.close()
    listings = bs.get_canonical_listings(tmp_db)
    assert listings == []


# ── write_redirect_stub ────────────────────────────────────────────────────────

def test_write_redirect_stub_creates_file(tmp_path):
    import scripts.build_static as bs
    bs.write_redirect_stub("listing-111", "uuid-aaa", tmp_path)
    stub = tmp_path / "airbnb" / "listing-111" / "index.html"
    assert stub.exists()


def test_write_redirect_stub_meta_refresh_url(tmp_path):
    import scripts.build_static as bs
    bs.write_redirect_stub("listing-111", "uuid-aaa", tmp_path)
    html = (tmp_path / "airbnb" / "listing-111" / "index.html").read_text()
    assert 'content="0;url=/lequartier/p/uuid-aaa/"' in html


def test_write_redirect_stub_canonical_link(tmp_path):
    import scripts.build_static as bs
    bs.write_redirect_stub("listing-111", "uuid-aaa", tmp_path)
    html = (tmp_path / "airbnb" / "listing-111" / "index.html").read_text()
    assert 'href="/lequartier/p/uuid-aaa/"' in html


# ── render_page ───────────────────────────────────────────────────────────────

def test_render_page_returns_bytes(app):
    """render_page returns HTML bytes for a known /p/{uuid} route."""
    import scripts.build_static as bs
    import tempfile

    result = {
        "geojson": {"type": "FeatureCollection", "features": []},
        "location": {"city": "Bangkok", "country": "Thailand"},
        "n_pois": 0, "lat": 13.75, "lon": 100.5,
        "listing_id": "12345",
    }
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w",
                                     delete=False, encoding="utf-8") as f:
        json.dump(result, f)
        result_path = f.name

    rec = {
        "uuid": "render-test-uuid", "listing_id": "12345",
        "lat": 13.75, "lon": 100.5,
        "result_path": result_path, "image_path": None, "qr_path": None,
        "unlocked": 0, "stripe_session_id": None, "created_at": "2026-01-01",
    }

    with patch("src.web.routes.host_map.maps_db.get", return_value=rec), \
         patch("src.web.routes.payment._stripe_active", return_value=False):
        with app.test_client() as client:
            html = bs.render_page(client, "/p/render-test-uuid")

    assert isinstance(html, bytes)
    assert b"<html" in html


def test_render_page_raises_on_404(app):
    import scripts.build_static as bs

    with patch("src.web.routes.host_map.maps_db.get", return_value=None):
        with app.test_client() as client:
            with pytest.raises(RuntimeError, match="404"):
                bs.render_page(client, "/p/no-such-uuid")


def test_render_page_sets_script_name(app):
    """Static asset URLs in rendered HTML are prefixed with /lequartier."""
    import scripts.build_static as bs
    import tempfile

    result = {
        "geojson": {"type": "FeatureCollection", "features": []},
        "location": {"city": "Test", "country": "Country"},
        "n_pois": 0, "lat": 0.0, "lon": 0.0, "listing_id": "99",
    }
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w",
                                     delete=False, encoding="utf-8") as f:
        json.dump(result, f)
        result_path = f.name

    rec = {
        "uuid": "script-name-uuid", "listing_id": "99",
        "lat": 0.0, "lon": 0.0, "result_path": result_path,
        "image_path": None, "qr_path": None, "unlocked": 0,
        "stripe_session_id": None, "created_at": "2026-01-01",
    }

    with patch("src.web.routes.host_map.maps_db.get", return_value=rec), \
         patch("src.web.routes.payment._stripe_active", return_value=False):
        with app.test_client() as client:
            html = bs.render_page(client, "/p/script-name-uuid")

    assert b"/lequartier/static/" in html
