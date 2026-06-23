from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import os
import subprocess
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent.parent
GITHUB_REPO = "alx/travel-guide"


def _read_commit_info() -> tuple[str, datetime.datetime | None]:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%H %ci"],
            cwd=str(PROJECT_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        full_hash, date_part = out.split(" ", 1)
        dt = datetime.datetime.strptime(date_part[:19], "%Y-%m-%d %H:%M:%S")
        return full_hash, dt
    except Exception:
        return "", None


def _relative_time(dt: datetime.datetime) -> str:
    delta = datetime.datetime.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''} ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _configure_logging() -> None:
    log_file = os.environ.get("LOG_FILE") or str(PROJECT_ROOT / "logs" / "app.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if os.environ.get("FLASK_DEBUG") else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def create_app(config: dict | None = None) -> Flask:
    load_dotenv(PROJECT_ROOT / ".env")

    _configure_logging()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["GOOGLE_MAPS_API_KEY"] = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    app.config["GITHUB_TOKEN"] = os.environ.get("GITHUB_TOKEN", "")
    app.config["GA_MEASUREMENT_ID"] = os.environ.get("GA_MEASUREMENT_ID", "")
    app.config["PROJECT_ROOT"] = str(PROJECT_ROOT)

    top100_path = Path(__file__).parent / "static" / "data" / "top100.json"
    try:
        with open(top100_path, encoding="utf-8") as f:
            app.config["TOP100_CITIES"] = json.load(f)["cities"]
    except Exception:
        app.config["TOP100_CITIES"] = []

    app.jinja_env.filters["urlencode"] = quote_plus

    if config:
        app.config.update(config)

    from .poi_engine import initialize, get_cfg
    initialize(env_path=PROJECT_ROOT / ".env")

    from .examples import seed_cache
    seed_cache()

    commit_hash, commit_dt = _read_commit_info()
    app.config["IN_GIT_REPO"] = bool(commit_hash)

    @app.context_processor
    def inject_flags():
        commit_date = commit_dt.strftime("%-d %b %Y") if commit_dt else ""
        commit_time = commit_dt.strftime("%H:%M") if commit_dt else ""
        commit_ago = _relative_time(commit_dt) if commit_dt else ""
        cfg = get_cfg()
        return {
            "commit_hash": commit_hash,
            "commit_short": commit_hash[:7] if commit_hash else "",
            "commit_date": commit_date,
            "commit_time": commit_time,
            "commit_ago": commit_ago,
            "github_commit_url": f"https://github.com/{GITHUB_REPO}/commit/{commit_hash}" if commit_hash else "",
            "has_github_token": bool(app.config.get("GITHUB_TOKEN")),
            "can_write_local": False,
            "in_git_repo": bool(commit_hash),
            "categories": cfg.categories if cfg else {},
            "ga_measurement_id": app.config.get("GA_MEASUREMENT_ID", ""),
            "debug_mode": bool(os.environ.get("FLASK_DEBUG")),
        }

    from .routes.wizard import wizard
    app.register_blueprint(wizard)

    from .routes.payment import payment
    app.register_blueprint(payment)

    from .routes.export import export
    app.register_blueprint(export)

    from .routes.zillow import zillow
    app.register_blueprint(zillow)

    from .routes.webhook import webhook
    app.register_blueprint(webhook)

    from .routes.explore import explore
    app.register_blueprint(explore)

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    return app


def run_dev() -> None:
    app = create_app()
    app.run(debug=True, host="127.0.0.1", port=5010, threaded=True)


def run_prod() -> None:
    from gunicorn.app.base import BaseApplication

    class _App(BaseApplication):
        def __init__(self, application, options):
            self.options = options
            self.application = application
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key, value)

        def load(self):
            return self.application

    options = {
        "bind": "0.0.0.0:5010",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 4,
        "loglevel": "info",
        "pidfile": str(PROJECT_ROOT / "gunicorn.pid"),
    }
    _App(create_app(), options).run()
