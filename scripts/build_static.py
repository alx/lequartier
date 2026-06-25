"""Build static gh-pages output from locally cached Host Map listings.

Usage:
    uv run python scripts/build_static.py
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_SRC   = PROJECT_ROOT / "src" / "web" / "static"
DIST_DIR     = PROJECT_ROOT / "dist"
DB_PATH      = PROJECT_ROOT / "data" / "listings.db"
SCRIPT_NAME  = "/lequartier"


def get_canonical_listings(db_path: Path = DB_PATH) -> list[tuple[str, str]]:
    """Return [(listing_id, uuid), …] — one entry per listing_id (newest uuid).

    Skips rows with NULL result_path or a result_path pointing to a missing file.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT uuid, listing_id, result_path
           FROM maps
           WHERE result_path IS NOT NULL
           ORDER BY listing_id, created_at DESC"""
    ).fetchall()
    conn.close()

    # First pass: pick the newest row per listing_id (first seen in DESC order)
    seen: set[str] = set()
    canonical: list[tuple[str, str, str]] = []  # (listing_id, uuid, result_path)
    for row in rows:
        lid = row["listing_id"]
        if lid in seen:
            continue
        seen.add(lid)
        canonical.append((lid, row["uuid"], row["result_path"]))

    # Second pass: filter out entries whose result file is missing
    result: list[tuple[str, str]] = []
    for lid, uuid, result_path in canonical:
        if not Path(result_path).exists():
            continue
        result.append((lid, uuid))
    return result


def render_page(client, path: str) -> bytes:
    """GET *path* via Flask test client with SCRIPT_NAME set for gh-pages.

    Raises RuntimeError if the response status is not 200.
    """
    response = client.get(
        path,
        environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"GET {path} returned {response.status_code}"
        )
    return response.data


def write_redirect_stub(listing_id: str, uuid: str, dist: Path) -> None:
    """Write dist/airbnb/{listing_id}/index.html with a meta-refresh to /p/{uuid}/."""
    target = f"{SCRIPT_NAME}/p/{uuid}/"
    html = (
        "<!DOCTYPE html>\n"
        "<html><head>"
        f'<meta http-equiv="refresh" content="0;url={target}">'
        f'<link rel="canonical" href="{target}">'
        "</head><body>"
        f'<a href="{target}">Redirecting…</a>'
        "</body></html>\n"
    )
    out = dist / "airbnb" / listing_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")


def _create_app():
    with patch("src.web.poi_engine.initialize"), \
         patch("src.web.examples.seed_cache"):
        from src.web.app import create_app  # noqa: PLC0415
        app = create_app()
        app.testing = True
    return app


def _copy_static(dist: Path) -> None:
    dst = dist / "static"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(str(STATIC_SRC), str(dst))


def _sync_to_gh_pages(dist: Path) -> None:
    current = subprocess.check_output(
        ["git", "branch", "--show-current"],
        cwd=PROJECT_ROOT, text=True,
    ).strip()

    # Stash tracked modifications so checkout can proceed cleanly
    stash_out = subprocess.check_output(
        ["git", "stash", "--quiet"],
        cwd=PROJECT_ROOT, text=True,
    ).strip()
    stashed = bool(stash_out)  # empty output means "No local changes to save"

    subprocess.run(
        ["git", "checkout", "gh-pages"],
        cwd=PROJECT_ROOT, check=True,
    )

    # Pull to integrate any remote changes before we commit
    subprocess.run(
        ["git", "pull", "--rebase", "origin", "gh-pages"],
        cwd=PROJECT_ROOT, check=True,
    )

    try:
        for subdir in ("p", "airbnb", "static"):
            src = dist / subdir
            dst = PROJECT_ROOT / subdir
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(str(src), str(dst))

        subprocess.run(
            ["git", "add", "p", "airbnb", "static"],
            cwd=PROJECT_ROOT, check=True,
        )

        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PROJECT_ROOT,
        )
        if changed.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: rebuild static map pages"],
                cwd=PROJECT_ROOT, check=True,
            )
            subprocess.run(
                ["git", "push", "origin", "gh-pages"],
                cwd=PROJECT_ROOT, check=True,
            )
            print("Pushed gh-pages.")
        else:
            print("No changes — gh-pages already up to date.")
    finally:
        subprocess.run(
            ["git", "checkout", current],
            cwd=PROJECT_ROOT, check=True,
        )

        if stashed:
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=PROJECT_ROOT, check=True,
            )


def build() -> None:
    listings = get_canonical_listings()
    if not listings:
        print("No listings with readable result_path found — nothing to build.")
        return

    DIST_DIR.mkdir(exist_ok=True)
    app = _create_app()

    with app.test_client() as client:
        for listing_id, uuid in listings:
            print(f"Rendering /p/{uuid}  ({listing_id})")
            try:
                html = render_page(client, f"/p/{uuid}")
            except RuntimeError as exc:
                print(f"  SKIP: {exc}")
                continue

            page_dir = DIST_DIR / "p" / uuid
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "index.html").write_bytes(html)

            write_redirect_stub(listing_id, uuid, DIST_DIR)
            print(f"  OK: airbnb/{listing_id}/ → p/{uuid}/")

    _copy_static(DIST_DIR)
    print(f"Copied static/ ({STATIC_SRC})")

    _sync_to_gh_pages(DIST_DIR)


if __name__ == "__main__":
    sys.exit(build())
