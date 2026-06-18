import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs

from flask import Blueprint, current_app, request

logger = logging.getLogger(__name__)

webhook = Blueprint("webhook", __name__, url_prefix="/webhook")


@webhook.post("/github")
def github_webhook():
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        logger.error("WEBHOOK_SECRET env var is not set")
        return {"error": "Webhook secret not configured"}, 500

    payload_bytes = request.get_data()
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    provided = sig_header[len("sha256="):] if sig_header.startswith("sha256=") else ""
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, provided):
        logger.warning("Invalid or missing X-Hub-Signature-256")
        return {"error": "Invalid signature"}, 403

    event = request.headers.get("X-GitHub-Event", "")

    if event == "ping":
        return {"message": "pong"}, 200

    if event != "push":
        return {"message": f"Event '{event}' ignored"}, 200

    # GitHub can send either JSON or form-encoded (payload= field)
    content_type = request.content_type or ""
    if "application/x-www-form-urlencoded" in content_type:
        form = parse_qs(payload_bytes.decode("utf-8", errors="replace"))
        payload_str = form.get("payload", ["{}"])[0]
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            return {"error": "Malformed form payload"}, 400
    else:
        try:
            data = json.loads(payload_bytes)
        except json.JSONDecodeError:
            return {"error": "Malformed JSON payload"}, 400

    try:
        ref = data["ref"]
    except KeyError:
        return {"error": "Unexpected payload shape"}, 400

    if ref != "refs/heads/main":
        logger.info("Push to '%s' ignored", ref)
        return {"message": f"Push to '{ref}' ignored"}, 200

    project_root = current_app.config["PROJECT_ROOT"]
    logger.info("Push to main — scheduling git pull")

    def _pull():
        try:
            stash = subprocess.run(
                ["git", "stash", "--include-untracked"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            stashed = stash.returncode == 0 and "No local changes" not in stash.stdout

            r = subprocess.run(
                ["git", "pull", "--ff-only", "origin", "main"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode == 0:
                logger.info("git pull succeeded: %s", r.stdout.strip())
                if stashed:
                    subprocess.run(["git", "stash", "drop"], cwd=project_root, capture_output=True)
                _reload_gunicorn(project_root)
            else:
                logger.error("git pull failed (rc=%d): %s %s", r.returncode, r.stdout, r.stderr)
                if stashed:
                    subprocess.run(["git", "stash", "pop"], cwd=project_root, capture_output=True)
        except Exception as exc:
            logger.exception("git pull error: %s", exc)

    def _reload_gunicorn(cwd: str):
        pid_file = Path(cwd) / "gunicorn.pid"
        if not pid_file.exists():
            logger.warning("gunicorn.pid not found at %s — skipping reload", pid_file)
            return
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, __import__("signal").SIGHUP)
            logger.info("Sent SIGHUP to gunicorn master (pid %d)", pid)
        except Exception as exc:
            logger.warning("Could not reload gunicorn: %s", exc)

    threading.Thread(target=_pull, daemon=True).start()
    return {"message": "Pull scheduled"}, 200
