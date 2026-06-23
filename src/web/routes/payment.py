from __future__ import annotations

import os

from flask import Blueprint, Response, abort, current_app, jsonify, request

from .. import maps_db

payment = Blueprint("payment", __name__)


def _stripe_active() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())


@payment.post("/api/checkout")
def api_checkout():
    """Create a Stripe Checkout session for a Host Map export."""
    import stripe

    map_uuid = request.args.get("uuid", "").strip()
    if not map_uuid:
        return jsonify({"error": "uuid required"}), 400

    rec = maps_db.get(map_uuid)
    if not rec:
        return jsonify({"error": "Map not found"}), 404

    if rec["unlocked"]:
        return jsonify({"error": "Already unlocked"}), 400

    secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret_key:
        return jsonify({"error": "Payments are not configured yet. Please try again later."}), 503

    stripe.api_key = secret_key
    base_url = os.environ.get("SITE_BASE_URL", request.host_url.rstrip("/"))

    try:
        checkout = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": "Le Quartier — Neighbourhood Map",
                        "description": "Downloadable map PNG, QR code, and permanent shareable link for your Airbnb listing.",
                    },
                    "unit_amount": 1900,
                },
                "quantity": 1,
            }],
            metadata={"map_uuid": map_uuid},
            success_url=f"{base_url}/p/{map_uuid}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/p/{map_uuid}",
        )
    except Exception as exc:
        current_app.logger.error("Stripe checkout error for %s: %s", map_uuid, exc)
        return jsonify({"error": "Could not create checkout session. Please try again."}), 502

    maps_db.set_stripe_session(map_uuid, checkout.id)
    return jsonify({"checkout_url": checkout.url})


@payment.post("/stripe/webhook")
def stripe_webhook():
    """Stripe webhook — marks Host Map as unlocked on payment completion."""
    import stripe

    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    secret  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    event = None
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        abort(400)
        return  # unreachable

    if event["type"] == "checkout.session.completed":
        session_id = event["data"]["object"]["id"]
        maps_db.unlock(session_id)

    return jsonify({"ok": True})
