"""Smoke tests for export_mode template flag.

Regression guard: placeRentalMarker() must appear in normal interactive maps
and must be absent when export_mode=true. A Jinja syntax error in the guard
would remove the marker for ALL visitors, not just export mode.
"""
import json
from unittest.mock import patch

TEST_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_BASE_REC = {
    "uuid": TEST_UUID,
    "listing_id": "test",
    "lat": 51.522,
    "lon": -0.087,
    "result_path": None,
    "image_path": None,
    "qr_path": None,
    "unlocked": 1,
    "stripe_session_id": None,
    "created_at": "2026-01-01T00:00:00",
}

_RESULT = {
    "location": {"city": "London", "neighbourhood": "Shoreditch", "country": "UK", "address": ""},
    "geojson": {"type": "FeatureCollection", "features": []},
    "listing_title": "Test Listing",
}


def _make_rec(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(_RESULT))
    return {**_BASE_REC, "result_path": str(result_file)}


def test_interactive_map_includes_marker_call(client, tmp_path):
    """Normal embed view must include placeRentalMarker() call."""
    rec = _make_rec(tmp_path)
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{TEST_UUID}?embed=1")
    assert resp.status_code == 200
    assert b"placeRentalMarker(LAT, LON)" in resp.data


def test_export_mode_suppresses_marker_call(client, tmp_path):
    """export_mode=true must remove the placeRentalMarker() call from rendered JS."""
    rec = _make_rec(tmp_path)
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{TEST_UUID}?embed=1&export_mode=true")
    assert resp.status_code == 200
    assert b"placeRentalMarker(LAT, LON)" not in resp.data


def test_export_mode_renders_header(client, tmp_path):
    """export_mode=true must include the export-header div with city name."""
    rec = _make_rec(tmp_path)
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{TEST_UUID}?embed=1&export_mode=true")
    assert resp.status_code == 200
    assert b"export-header" in resp.data
    assert b"Shoreditch" in resp.data


def test_no_export_mode_no_header(client, tmp_path):
    """Without export_mode, export-header must not appear."""
    rec = _make_rec(tmp_path)
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{TEST_UUID}?embed=1")
    assert resp.status_code == 200
    assert b"export-header" not in resp.data
