# GitHub Pages Static Map Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/build_static.py` — a script that renders all locally cached Host Map pages via the Flask test client and syncs them into the `gh-pages` branch under `/p/{uuid}/` and `/airbnb/{listing_id}/`.

**Architecture:** The script creates the Flask app in test mode (patching out heavy init), uses the test client with `SCRIPT_NAME=/lequartier` so `url_for()` generates GitHub Pages-correct paths, writes rendered HTML to a local `dist/` tree, copies static assets, then syncs three subdirectories (`p/`, `airbnb/`, `static/`) into `gh-pages` while preserving the existing `index.html` and `screenshot.png`.

**Tech Stack:** Python 3.11+, Flask test client, SQLite (`data/listings.db`), `shutil`, `subprocess`, `pytest`

## Global Constraints

- Run with: `uv run python scripts/build_static.py` (never `python` directly)
- `SCRIPT_NAME` value: `/lequartier` (exact — GitHub Pages serves from `https://alx.github.io/lequartier/`)
- DB path: `data/listings.db` relative to project root, table `maps`, columns: `uuid`, `listing_id`, `result_path`, `created_at`
- Only include listings where `result_path IS NOT NULL` AND the file at `result_path` exists on disk
- One canonical UUID per `listing_id`: most-recent `created_at` row wins
- Preserve on `gh-pages`: `index.html`, `screenshot.png` — never overwrite them
- Sync subdirs: `p/`, `airbnb/`, `static/` only
- Commit message on gh-pages: `chore: rebuild static map pages`
- Flask app factory: `src.web.app.create_app()` — patch `src.web.poi_engine.initialize` and `src.web.examples.seed_cache` to skip heavy init
- Redirect stub target format: `/lequartier/p/{uuid}/` (trailing slash, prefix required)
- `dist/` is a local scratch directory at project root — always safe to wipe and rebuild
- Tests live in `tests/test_build_static.py`; run with `uv run pytest tests/test_build_static.py -v`

---

### Task 1: Build script with unit tests

**Files:**
- Create: `scripts/__init__.py` (empty, makes scripts importable in tests)
- Create: `scripts/build_static.py`
- Create: `tests/test_build_static.py`

**Interfaces:**
- Produces:
  - `get_canonical_listings(db_path: Path) -> list[tuple[str, str]]` — returns `[(listing_id, uuid), …]` deduplicated by listing_id (most-recent uuid), skipping rows with missing/unreadable `result_path`
  - `write_redirect_stub(listing_id: str, uuid: str, dist: Path) -> None` — writes `dist/airbnb/{listing_id}/index.html`
  - `render_page(client, path: str) -> bytes` — GET via test client with `SCRIPT_NAME=/lequartier`, raises `RuntimeError` on non-200
  - `build() -> None` — orchestrator: create app, render all pages, copy static, sync gh-pages

- [ ] **Step 1: Create `scripts/__init__.py`**

```bash
touch /home/alx/code/lequartier/scripts/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_build_static.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/alx/code/lequartier && uv run pytest tests/test_build_static.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'scripts.build_static'`

- [ ] **Step 4: Create `scripts/build_static.py`**

```python
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

    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for row in rows:
        lid = row["listing_id"]
        if lid in seen:
            continue
        if not Path(row["result_path"]).exists():
            continue
        seen.add(lid)
        result.append((lid, row["uuid"]))
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

    subprocess.run(
        ["git", "checkout", "gh-pages"],
        cwd=PROJECT_ROOT, check=True,
    )

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

    subprocess.run(
        ["git", "checkout", current],
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
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /home/alx/code/lequartier && uv run pytest tests/test_build_static.py -v
```

Expected output (all green):
```
tests/test_build_static.py::test_get_canonical_listings_deduplicates PASSED
tests/test_build_static.py::test_get_canonical_listings_skips_null_result_path PASSED
tests/test_build_static.py::test_get_canonical_listings_skips_missing_file PASSED
tests/test_build_static.py::test_write_redirect_stub_creates_file PASSED
tests/test_build_static.py::test_write_redirect_stub_meta_refresh_url PASSED
tests/test_build_static.py::test_write_redirect_stub_canonical_link PASSED
tests/test_build_static.py::test_render_page_returns_bytes PASSED
tests/test_build_static.py::test_render_page_raises_on_404 PASSED
tests/test_build_static.py::test_render_page_sets_script_name PASSED
```

- [ ] **Step 6: Run the full build end-to-end and verify dist/ output**

```bash
cd /home/alx/code/lequartier && uv run python scripts/build_static.py
```

Expected console output (8 listings):
```
Rendering /p/a49e2136-...  (10349749)
  OK: airbnb/10349749/ → p/a49e2136-.../
Rendering /p/8346d8c5-...  (1136112938623890368)
  OK: airbnb/1136112938623890368/ → p/8346d8c5-.../
...
Copied static/ (…/src/web/static)
Pushed gh-pages.
```

Then verify the output structure:
```bash
find /home/alx/code/lequartier/dist -maxdepth 3 -type d | sort
```

Expected:
```
dist/
dist/airbnb/
dist/airbnb/10349749/
dist/airbnb/1136112938623890368/
...
dist/p/
dist/p/a49e2136-e5f0-4187-a257-1c5af63ab930/
...
dist/static/
dist/static/css/
dist/static/js/
dist/static/img/
```

Also verify a rendered page has prefixed static paths:
```bash
grep -o '/lequartier/static/[^"]*' /home/alx/code/lequartier/dist/p/83c49ad3-4a4f-47a0-a1a8-0299cb02cd0c/index.html | head -5
```

Expected: lines like `/lequartier/static/css/app.css`, `/lequartier/static/js/map-core.js`

And verify a redirect stub:
```bash
cat /home/alx/code/lequartier/dist/airbnb/688796991005765901/index.html
```

Expected:
```html
<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0;url=/lequartier/p/83c49ad3-4a4f-47a0-a1a8-0299cb02cd0c/"><link rel="canonical" href="/lequartier/p/83c49ad3-4a4f-47a0-a1a8-0299cb02cd0c/"></head><body><a href="/lequartier/p/83c49ad3-4a4f-47a0-a1a8-0299cb02cd0c/">Redirecting…</a></body></html>
```

- [ ] **Step 7: Verify gh-pages branch preserves index.html and screenshot.png**

```bash
git -C /home/alx/code/lequartier show gh-pages:index.html | head -3
git -C /home/alx/code/lequartier show gh-pages:screenshot.png | wc -c
```

Expected: `index.html` starts with `<!DOCTYPE html>` and `screenshot.png` is non-empty (3.5 MB).

- [ ] **Step 8: Commit on main**

```bash
cd /home/alx/code/lequartier
git add scripts/__init__.py scripts/build_static.py tests/test_build_static.py
git commit -m "feat: add build_static script to generate gh-pages map pages"
```
