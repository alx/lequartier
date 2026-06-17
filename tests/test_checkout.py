"""End-to-end tests for the Stripe checkout flow.

Covers: api_checkout, stripe_webhook, host_map_page unlock-on-redirect,
and the download endpoints.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_UUID = "11111111-2222-3333-4444-555555555555"
_SESSION_ID = "cs_test_abc123"

_BASE_REC = {
    "uuid": _UUID,
    "listing_id": "12345",
    "lat": 51.5,
    "lon": -0.1,
    "result_path": None,
    "image_path": None,
    "qr_path": None,
    "unlocked": 0,
    "stripe_session_id": None,
    "created_at": "2026-01-01T00:00:00",
}


# ── /api/checkout ──────────────────────────────────────────────────────────────

def test_checkout_missing_uuid(client):
    resp = client.post("/api/checkout")
    assert resp.status_code == 400
    assert b"uuid required" in resp.data


def test_checkout_map_not_found(client):
    with patch("src.web.routes.wizard.maps_db.get", return_value=None):
        resp = client.post("/api/checkout?uuid=" + _UUID)
    assert resp.status_code == 404
    assert b"not found" in resp.data.lower()


def test_checkout_already_unlocked(client):
    with patch("src.web.routes.wizard.maps_db.get",
               return_value={**_BASE_REC, "unlocked": 1}):
        resp = client.post("/api/checkout?uuid=" + _UUID)
    assert resp.status_code == 400
    assert b"Already unlocked" in resp.data


def test_checkout_missing_stripe_key(client):
    with patch("src.web.routes.wizard.maps_db.get", return_value=dict(_BASE_REC)), \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": ""}):
        resp = client.post("/api/checkout?uuid=" + _UUID)
    assert resp.status_code == 503
    assert b"not configured" in resp.data


def test_checkout_creates_session_and_returns_url(client):
    fake_session = SimpleNamespace(id=_SESSION_ID, url="https://checkout.stripe.com/pay/test")

    with patch("src.web.routes.wizard.maps_db.get", return_value=dict(_BASE_REC)), \
         patch("src.web.routes.wizard.maps_db.set_stripe_session") as mock_set, \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake"}), \
         patch("stripe.checkout.Session.create", return_value=fake_session):
        resp = client.post("/api/checkout?uuid=" + _UUID)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["checkout_url"] == "https://checkout.stripe.com/pay/test"
    mock_set.assert_called_once_with(_UUID, _SESSION_ID)


def test_checkout_stripe_error_returns_502(client):
    import stripe as _stripe

    with patch("src.web.routes.wizard.maps_db.get", return_value=dict(_BASE_REC)), \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake"}), \
         patch("stripe.checkout.Session.create",
               side_effect=_stripe.StripeError("card_error")):
        resp = client.post("/api/checkout?uuid=" + _UUID)

    assert resp.status_code == 502
    assert b"checkout session" in resp.data


# ── /stripe/webhook ────────────────────────────────────────────────────────────

def _make_event(session_id: str) -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id}},
    }


def test_webhook_bad_signature_returns_400(client):
    import stripe as _stripe
    with patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": "whsec_fake"}), \
         patch("stripe.Webhook.construct_event",
               side_effect=_stripe.error.SignatureVerificationError("bad sig", "hdr")):
        resp = client.post(
            "/stripe/webhook",
            data=b"{}",
            headers={"Stripe-Signature": "t=1,v1=bad"},
        )
    assert resp.status_code == 400


def test_webhook_unlocks_map(client):
    event = _make_event(_SESSION_ID)
    with patch("stripe.Webhook.construct_event", return_value=event), \
         patch("src.web.routes.wizard.maps_db.unlock") as mock_unlock, \
         patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": "whsec_fake"}):
        resp = client.post(
            "/stripe/webhook",
            data=json.dumps(event).encode(),
            headers={"Stripe-Signature": "t=1,v1=ok", "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_unlock.assert_called_once_with(_SESSION_ID)


def test_webhook_ignores_non_checkout_events(client):
    event = {"type": "payment_intent.created", "data": {"object": {}}}
    with patch("stripe.Webhook.construct_event", return_value=event), \
         patch("src.web.routes.wizard.maps_db.unlock") as mock_unlock, \
         patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": "whsec_fake"}):
        resp = client.post(
            "/stripe/webhook",
            data=json.dumps(event).encode(),
            headers={"Stripe-Signature": "t=1,v1=ok", "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_unlock.assert_not_called()


# ── /p/<uuid> unlock-on-redirect ──────────────────────────────────────────────

def _make_rec_with_result(tmp_path):
    result = {
        "location": {"city": "London", "country": "UK", "neighbourhood": "Shoreditch", "address": ""},
        "geojson": {"type": "FeatureCollection", "features": []},
        "listing_title": "Test",
    }
    p = tmp_path / "result.json"
    p.write_text(json.dumps(result))
    return {**_BASE_REC, "result_path": str(p)}


def test_host_map_page_locked_shows_checkout_button(client, tmp_path):
    rec = _make_rec_with_result(tmp_path)
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{_UUID}")
    assert resp.status_code == 200
    assert b"btn-checkout" in resp.data
    assert b"Unlock exports" in resp.data


def test_host_map_page_unlocked_shows_downloads(client, tmp_path):
    rec = {**_make_rec_with_result(tmp_path), "unlocked": 1}
    with patch("src.web.routes.wizard.maps_db.get", return_value=rec):
        resp = client.get(f"/p/{_UUID}")
    assert resp.status_code == 200
    assert b"download/map" in resp.data
    assert b"download/qr" in resp.data


def test_host_map_page_verifies_stripe_on_session_id(client, tmp_path):
    """When session_id param is present and map is locked, verify with Stripe and unlock."""
    rec = _make_rec_with_result(tmp_path)
    paid_session = SimpleNamespace(payment_status="paid")

    with patch("src.web.routes.wizard.maps_db.get") as mock_get, \
         patch("src.web.routes.wizard.maps_db.set_stripe_session") as mock_set_session, \
         patch("src.web.routes.wizard.maps_db.unlock") as mock_unlock, \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake"}), \
         patch("stripe.checkout.Session.retrieve", return_value=paid_session):

        unlocked_rec = {**rec, "unlocked": 1}
        mock_get.side_effect = [rec, unlocked_rec]

        resp = client.get(f"/p/{_UUID}?session_id={_SESSION_ID}")

    assert resp.status_code == 200
    mock_set_session.assert_called_once_with(_UUID, _SESSION_ID)
    mock_unlock.assert_called_once_with(_SESSION_ID)
    assert b"download/map" in resp.data


def test_host_map_page_session_id_not_paid_stays_locked(client, tmp_path):
    rec = _make_rec_with_result(tmp_path)
    unpaid_session = SimpleNamespace(payment_status="unpaid")

    with patch("src.web.routes.wizard.maps_db.get", return_value=rec), \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake"}), \
         patch("stripe.checkout.Session.retrieve", return_value=unpaid_session):
        resp = client.get(f"/p/{_UUID}?session_id={_SESSION_ID}")

    assert resp.status_code == 200
    assert b"btn-checkout" in resp.data


# ── download endpoints ─────────────────────────────────────────────────────────

def test_download_map_requires_unlock(client):
    locked_rec = {**_BASE_REC, "unlocked": 0}
    with patch("src.web.routes.wizard.maps_db.get", return_value=locked_rec):
        resp = client.get(f"/p/{_UUID}/download/map")
    assert resp.status_code == 403


def test_download_qr_requires_unlock(client):
    locked_rec = {**_BASE_REC, "unlocked": 0}
    with patch("src.web.routes.wizard.maps_db.get", return_value=locked_rec):
        resp = client.get(f"/p/{_UUID}/download/qr")
    assert resp.status_code == 403


def test_download_map_serves_file_when_ready(client, tmp_path):
    img = tmp_path / f"{_UUID}_map_v2.png"
    img.write_bytes(b"\x89PNG\r\n")
    rec = {**_BASE_REC, "unlocked": 1, "result_path": None, "qr_path": None}

    with patch("src.web.routes.wizard.maps_db.get", return_value=rec), \
         patch("src.web.routes.wizard.maps_db.set_paths"), \
         patch("src.web.routes.wizard._MAPS_IMG_DIR", tmp_path):
        resp = client.get(f"/p/{_UUID}/download/map")

    assert resp.status_code == 200
    assert resp.mimetype == "image/png"


def test_download_qr_serves_file_when_ready(client, tmp_path):
    qr = tmp_path / f"{_UUID}_qr.png"
    qr.write_bytes(b"\x89PNG\r\n")
    rec = {**_BASE_REC, "unlocked": 1}

    with patch("src.web.routes.wizard.maps_db.get", return_value=rec), \
         patch("src.web.routes.wizard._MAPS_IMG_DIR", tmp_path):
        resp = client.get(f"/p/{_UUID}/download/qr")

    assert resp.status_code == 200
    assert resp.mimetype == "image/png"


def test_download_map_returns_202_when_not_ready(client, tmp_path):
    rec = {**_BASE_REC, "unlocked": 1}

    with patch("src.web.routes.wizard.maps_db.get", return_value=rec), \
         patch("src.web.routes.wizard._MAPS_IMG_DIR", tmp_path), \
         patch("src.web.routes.wizard._SCRIPTS_DIR", tmp_path), \
         patch("subprocess.Popen"):
        resp = client.get(f"/p/{_UUID}/download/map")

    assert resp.status_code == 202
    assert b"being generated" in resp.data
