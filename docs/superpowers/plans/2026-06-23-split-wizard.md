# Split wizard.py into focused sub-modules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `src/web/routes/wizard.py` (1695 lines, 50+ functions) into six focused modules without changing any route behaviour, URL, or response.

**Architecture:** Six new files are created incrementally. At each task the new module is registered in `app.py` and the moved code is removed from `wizard.py`, keeping the app functional after every commit. Blueprints are renamed from `wizard` to per-module names; `url_for` references are updated in the same commit as the move. `wizard.py` is deleted in the final task once it is empty.

**Tech Stack:** Python 3.11+, Flask Blueprints. No new dependencies.

## Global Constraints

- Python ≥ 3.11
- No new pip dependencies
- Every route URL and HTTP method stays identical — zero behaviour change
- After every task: `uv run pytest -v` must show exactly **19 passed, 5 failed** (the 5 pre-existing `test_checkout.py` failures are unrelated to this refactor)
- Blueprint names: `airbnb`, `payment`, `export`, `zillow`, `host_map` (exact strings, used in `url_for`)
- All `url_for("wizard.*")` references must be updated **in the same commit** as the function move

---

## File Map

| File | Blueprint | Responsibility |
|---|---|---|
| `src/web/routes/shared.py` | none | Constants, path constants, GH API helpers, `_require_edit_auth` |
| `src/web/routes/payment.py` | `payment` | `_stripe_active`, `/api/checkout`, `/stripe/webhook` |
| `src/web/routes/export.py` | `export` | `_generate_exports`, `/step2/continue`, `/step3/create-pr`, `/step3/notify`, `/p/<uuid>/download/*` |
| `src/web/routes/zillow.py` | `zillow` | All `/zillow/<path:*>` routes and `_render_zillow_map` |
| `src/web/routes/airbnb.py` | `airbnb` | All `/airbnb/*`, `/geo/*`, `/step1/*`, `/tasks/*`, `/cache/*`, `/`, and the three fetch-task runners |
| `src/web/routes/host_map.py` | `host_map` | `/map`, `/api/generate`, `/api/nearby`, `/api/start-map*`, `/p/<uuid>` |
| `src/web/routes/wizard.py` | _(deleted in Task 6)_ | |
| `src/web/app.py` | — | Register all 5 new blueprints; updated incrementally |

### Dependency order (no circular imports)
```
shared  →  (no intra-package deps)
payment →  maps_db
export  →  shared, payment._stripe_active, maps_db, task_mod
zillow  →  shared, cache_mod, poi_engine, listing_index, task_mod
airbnb  →  shared, export._generate_exports, payment._stripe_active, cache_mod, poi_engine, listing_index, maps_db, task_mod
host_map → shared, airbnb._fetch_task/_fetch_task_direct/_fetch_task_geo, payment._stripe_active, cache_mod, poi_engine, maps_db, task_mod
```

### `url_for` rename map
When a function moves to a new blueprint, `url_for("wizard.fn")` becomes `url_for("<new_bp>.fn")`. Update in the **same task** as the move.

| Old | New | Updated in |
|---|---|---|
| `wizard.index` | `airbnb.index` | Task 5 |
| `wizard.airbnb_edit_page` | `airbnb.airbnb_edit_page` | Task 5 |
| `wizard.airbnb_page` | `airbnb.airbnb_page` | Task 5 |
| `wizard.geo_page` | `airbnb.geo_page` | Task 5 |
| `wizard.host_map_page` | `host_map.host_map_page` | Task 6 |

---

## Task 1: Create `shared.py` — constants and utilities

**Files:**
- Create: `src/web/routes/shared.py`
- Modify: `src/web/routes/wizard.py` (replace inline definitions with imports)

**Interfaces:**
- Produces (all public, imported by later tasks):
  ```python
  CATEGORY_ICONS: dict[str, str]
  CATEGORY_COLORS: dict[str, str]
  _GH_API: str
  _GH_REPO: str
  _CURATED_DIR: Path
  _ZILLOW_CURATED_DIR: Path
  _MAPS_DATA_DIR: Path
  _MAPS_IMG_DIR: Path
  _SCRIPTS_DIR: Path
  def _gh_headers(token: str) -> dict: ...
  def _gh_put_file(hdrs, branch, path, content, message) -> None: ...
  def _require_edit_auth(f): ...   # decorator
  ```

- [ ] **Step 1: Create `src/web/routes/shared.py`**

  Copy the following blocks verbatim from `wizard.py` into the new file, in this order:

  ```python
  from __future__ import annotations

  import base64
  import json
  import os
  import re
  import time
  from functools import wraps
  from pathlib import Path

  import requests as http_requests
  from flask import Response, current_app, request

  # ── GitHub API ─────────────────────────────────────────────────────────────────
  # Lines 38-40 of wizard.py
  _GH_API  = "https://api.github.com"
  _GH_REPO = "alx/travel-guide"

  # ── Directory constants ────────────────────────────────────────────────────────
  # Lines 40-41 of wizard.py
  _CURATED_DIR        = Path(__file__).parent.parent / "curated"
  _ZILLOW_CURATED_DIR = _CURATED_DIR / "zillow"

  # Lines 127-128 of wizard.py
  _MAPS_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "maps"
  _MAPS_IMG_DIR  = Path(__file__).parent.parent / "static" / "img" / "maps"

  # Line 644 of wizard.py
  _SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"
  ```

  Then copy `CATEGORY_ICONS` and `CATEGORY_COLORS` dicts verbatim from wizard.py lines 43-64.

  Then copy `_gh_headers` (wizard.py lines 67-73), `_gh_put_file` (lines 75-93), and `_require_edit_auth` (lines 107-124) verbatim.

- [ ] **Step 2: Update `wizard.py` to import from shared.py**

  Replace the constants/functions in `wizard.py` with imports. At the top of `wizard.py`, after the existing stdlib/flask imports, add:

  ```python
  from .shared import (
      CATEGORY_ICONS, CATEGORY_COLORS,
      _GH_API, _GH_REPO, _CURATED_DIR, _ZILLOW_CURATED_DIR,
      _MAPS_DATA_DIR, _MAPS_IMG_DIR, _SCRIPTS_DIR,
      _gh_headers, _gh_put_file, _require_edit_auth,
  )
  ```

  Remove the inline definitions of all the above from wizard.py (the originals at lines 38-64, 67-93, 107-128, and 644). Also remove the now-redundant imports that shared.py covers (`base64`, `re`, `time`, `http_requests`) **only if** wizard.py no longer uses them directly — check before removing.

- [ ] **Step 3: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed` — same as baseline.

- [ ] **Step 4: Commit**

  ```bash
  git add src/web/routes/shared.py src/web/routes/wizard.py
  git commit -m "refactor: extract shared constants and utilities to routes/shared.py"
  ```

---

## Task 2: Create `payment.py`

**Files:**
- Create: `src/web/routes/payment.py`
- Modify: `src/web/routes/wizard.py` (remove moved functions)
- Modify: `src/web/app.py` (register `payment` blueprint)

**Interfaces:**
- Consumes: `maps_db` module
- Produces:
  ```python
  payment: Blueprint          # Blueprint("payment", __name__)
  def _stripe_active() -> bool: ...
  ```

- [ ] **Step 1: Create `src/web/routes/payment.py`**

  ```python
  from __future__ import annotations

  import os

  from flask import Blueprint, Response, abort, current_app, jsonify, request

  from .. import maps_db

  payment = Blueprint("payment", __name__)
  ```

  Then copy these functions verbatim from `wizard.py`:
  - `_stripe_active` (wizard.py line 1437)
  - `api_checkout` (wizard.py lines 1558–1604) — change decorator from `@wizard.post` to `@payment.post`
  - `stripe_webhook` (wizard.py lines 1607–1627) — change decorator from `@wizard.post` to `@payment.post`

- [ ] **Step 2: Register `payment` blueprint in `app.py`**

  In `src/web/app.py`, after the existing blueprint registrations (around line 132), add:

  ```python
  from .routes.payment import payment
  app.register_blueprint(payment)
  ```

- [ ] **Step 3: Update `wizard.py`**

  - Add import at the top: `from .payment import _stripe_active`
  - Remove `_stripe_active`, `api_checkout`, `stripe_webhook` function bodies and their decorators from `wizard.py`

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed`.

- [ ] **Step 5: Commit**

  ```bash
  git add src/web/routes/payment.py src/web/routes/wizard.py src/web/app.py
  git commit -m "refactor: extract payment routes to routes/payment.py"
  ```

---

## Task 3: Create `export.py`

**Files:**
- Create: `src/web/routes/export.py`
- Modify: `src/web/routes/wizard.py`
- Modify: `src/web/app.py`

**Interfaces:**
- Consumes: `shared._GH_API`, `shared._GH_REPO`, `shared._CURATED_DIR`, `shared._SCRIPTS_DIR`, `shared._MAPS_DATA_DIR`, `shared._MAPS_IMG_DIR`, `shared._gh_headers`, `shared._gh_put_file`, `payment._stripe_active`, `maps_db`, `tasks as task_mod`
- Produces:
  ```python
  export: Blueprint           # Blueprint("export", __name__)
  def _generate_exports(map_uuid: str, listing_id: str, lat: float, lon: float, result: dict) -> None: ...
  ```

- [ ] **Step 1: Create `src/web/routes/export.py`**

  ```python
  from __future__ import annotations

  import json
  import os
  import subprocess
  from pathlib import Path

  from flask import Blueprint, Response, abort, current_app, render_template, request, send_file, session

  from .shared import (
      _GH_API, _GH_REPO, _CURATED_DIR,
      _SCRIPTS_DIR, _MAPS_DATA_DIR, _MAPS_IMG_DIR,
      _gh_headers, _gh_put_file,
  )
  from .payment import _stripe_active
  from .. import maps_db
  from .. import tasks as task_mod

  export = Blueprint("export", __name__)
  ```

  Then copy these functions verbatim from `wizard.py`, changing `@wizard.` decorators to `@export.`:
  - `_generate_exports` (wizard.py lines 131–172) — no decorator, copy as-is
  - `step2_continue` (wizard.py lines 899–951)
  - `step3_create_pr` (wizard.py lines 954–1055) — also needs `import re, time, http_requests` at module level (add to export.py imports)
  - `step3_notify` (wizard.py lines 1058–1065)
  - `download_map_image` (wizard.py lines 1630–1676)
  - `download_qr` (wizard.py lines 1679–1695)

  Add the extra imports to export.py (used by step3_create_pr):
  ```python
  import re
  import time
  import requests as http_requests
  ```

- [ ] **Step 2: Register `export` blueprint in `app.py`**

  ```python
  from .routes.export import export
  app.register_blueprint(export)
  ```

- [ ] **Step 3: Update `wizard.py`**

  - Add: `from .export import _generate_exports`
  - Remove: `_generate_exports`, `step2_continue`, `step3_create_pr`, `step3_notify`, `download_map_image`, `download_qr` from wizard.py

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed`.

- [ ] **Step 5: Commit**

  ```bash
  git add src/web/routes/export.py src/web/routes/wizard.py src/web/app.py
  git commit -m "refactor: extract export/step routes to routes/export.py"
  ```

---

## Task 4: Create `zillow.py`

**Files:**
- Create: `src/web/routes/zillow.py`
- Modify: `src/web/routes/wizard.py`
- Modify: `src/web/app.py`

**Interfaces:**
- Consumes: `shared._ZILLOW_CURATED_DIR`, `shared._require_edit_auth`, `cache_mod`, `poi_engine`, `listing_index`, `task_mod`

- [ ] **Step 1: Create `src/web/routes/zillow.py`**

  ```python
  from __future__ import annotations

  import json

  from flask import Blueprint, Response, jsonify, render_template, request, session

  from .shared import _ZILLOW_CURATED_DIR, _require_edit_auth
  from .. import cache as cache_mod
  from .. import poi_engine
  from .. import listing_index
  from .. import tasks as task_mod

  zillow = Blueprint("zillow", __name__)
  ```

  Then copy these functions verbatim from `wizard.py`, changing `@wizard.` to `@zillow.`:
  - `_render_zillow_map` (wizard.py lines 1118–1143) — no decorator
  - `zillow_edit_page` (wizard.py lines 1144–1165) — note: keep both `@zillow.get(...)` AND `@_require_edit_auth` decorators, in that order
  - `zillow_geojson` (wizard.py lines 1168–1178)
  - `zillow_page` (wizard.py lines 1181–1201)
  - `zillow_save_curated` (wizard.py lines 1204–1271) — keep both `@zillow.post(...)` AND `@_require_edit_auth`

  **Important:** `zillow_save_curated` uses `poi_engine.get_cfg()` and `cache_mod`. Both are imported above.

- [ ] **Step 2: Register `zillow` blueprint in `app.py`**

  ```python
  from .routes.zillow import zillow
  app.register_blueprint(zillow)
  ```

- [ ] **Step 3: Update `wizard.py`**

  Remove: `_render_zillow_map`, `zillow_edit_page`, `zillow_geojson`, `zillow_page`, `zillow_save_curated` from wizard.py (and their comment header `# ── Zillow routes`).

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed`.

- [ ] **Step 5: Commit**

  ```bash
  git add src/web/routes/zillow.py src/web/routes/wizard.py src/web/app.py
  git commit -m "refactor: extract Zillow routes to routes/zillow.py"
  ```

---

## Task 5: Create `airbnb.py`

**Files:**
- Create: `src/web/routes/airbnb.py`
- Modify: `src/web/routes/wizard.py`
- Modify: `src/web/app.py`

**Interfaces:**
- Consumes: `shared.*`, `export._generate_exports`, `payment._stripe_active`, `cache_mod`, `poi_engine`, `listing_index`, `maps_db`, `task_mod`, `airbnb_nearby as lib`
- Produces (called by host_map.py in Task 6):
  ```python
  airbnb: Blueprint
  def _fetch_task(task, airbnb_url, gmaps_url, lat, lon, force=False, map_uuid=None) -> None: ...
  def _fetch_task_direct(task, site, listing_id, lat, lon, map_uuid=None) -> None: ...
  def _fetch_task_geo(task, listing_id, lat, lon, map_uuid=None) -> None: ...
  ```

- [ ] **Step 1: Create `src/web/routes/airbnb.py`**

  ```python
  from __future__ import annotations

  import json
  import os
  import random
  import uuid as uuid_mod
  from pathlib import Path

  from flask import (
      Blueprint, Response, abort, current_app, jsonify,
      make_response, redirect, render_template, request, send_file, session, url_for,
  )

  from .shared import (
      CATEGORY_ICONS, CATEGORY_COLORS,
      _CURATED_DIR, _SCRIPTS_DIR, _MAPS_IMG_DIR,
      _require_edit_auth,
  )
  from .export import _generate_exports
  from .payment import _stripe_active
  from .. import cache as cache_mod
  from .. import tasks as task_mod
  from .. import poi_engine
  from .. import listing_index
  from .. import maps_db
  from ... import airbnb_nearby as lib

  airbnb = Blueprint("airbnb", __name__)
  ```

  Copy these functions verbatim from `wizard.py`, changing `@wizard.` to `@airbnb.`:
  - `_allow_airbnb_framing` (wizard.py lines 97–105) — change to `@airbnb.after_request`
  - `_fetch_task` (wizard.py lines 175–281) — no decorator
  - `_fetch_task_direct` (wizard.py lines 283–406) — no decorator
  - `_fetch_task_geo` (wizard.py lines 407–477) — no decorator
  - `_random_city` (wizard.py lines 480–482) — no decorator
  - `index` (wizard.py lines 485–488)
  - `airbnb_index` (wizard.py lines 491–493)
  - `geo_index` (wizard.py lines 496–499)
  - `api_listing_preview` (wizard.py lines 502–511)
  - `step1_submit` (wizard.py lines 514–534)
  - `_get_or_create_host_map` (wizard.py lines 537–549) — no decorator
  - `_poll_task` (wizard.py lines 551–600) — no decorator
  - `poll_fetch` (wizard.py lines 601–603)
  - `poll_view` (wizard.py lines 606–608)
  - `task_map_state` (wizard.py lines 611–645)
  - `_og_image_url` (wizard.py lines 647–652) — no decorator
  - `_render_airbnb_map` (wizard.py lines 654–682) — no decorator
  - `airbnb_page` (wizard.py lines 683–713)
  - `geo_page` (wizard.py lines 714–747)
  - `airbnb_edit_page` (wizard.py lines 749–776) — keep both `@airbnb.get(...)` AND `@_require_edit_auth`
  - `airbnb_preview_jpg` (wizard.py lines 777–794)
  - `airbnb_geojson` (wizard.py lines 796–808)
  - `save_curated` (wizard.py lines 809–881) — keep both `@airbnb.post(...)` AND `@_require_edit_auth`
  - `set_shared` (wizard.py lines 881–897) — keep both decorators
  - `cache_list` (wizard.py lines 1068–1071)
  - `cache_invalidate` (wizard.py lines 1073–1077)

  **Update `url_for` references in airbnb.py** (change these exact strings):

  | Old string | New string | Location in airbnb.py |
  |---|---|---|
  | `"wizard.index"` | `"airbnb.index"` | `airbnb_index` |
  | `"wizard.airbnb_edit_page"` | `"airbnb.airbnb_edit_page"` | `step1_submit`, `airbnb_preview_jpg` |
  | `"wizard.airbnb_page"` | `"airbnb.airbnb_page"` | `airbnb_page` (task poll redirect) |
  | `"wizard.geo_page"` | `"airbnb.geo_page"` | `geo_page` |
  | `"wizard.host_map_page"` | **keep as `"wizard.host_map_page"`** | `_get_or_create_host_map`, `airbnb_page`, `geo_page` — host_map_page is still on the wizard Blueprint until Task 6 |

- [ ] **Step 2: Register `airbnb` blueprint in `app.py`**

  ```python
  from .routes.airbnb import airbnb
  app.register_blueprint(airbnb)
  ```

- [ ] **Step 3: Update `wizard.py`**

  Remove all functions listed above from wizard.py. Also remove:
  - `from .export import _generate_exports` (no longer needed in wizard.py)
  - `from .payment import _stripe_active` (no longer needed in wizard.py)

  After this, wizard.py should contain only:
  - The module-level imports (slim down to only what remains)
  - `_active_result` (wizard.py line 1079)
  - `_render_map_page` (wizard.py lines 1087–1110)
  - `_api_nearby_response` (wizard.py lines 1276–1278)
  - `api_nearby_preflight` (wizard.py lines 1281–1283)
  - `api_generate` (wizard.py lines 1286–1313)
  - `api_nearby` (wizard.py lines 1316–1363)
  - `map_page` (wizard.py lines 1368–1432)
  - `api_start_map` (wizard.py lines 1441–1460)
  - `api_start_map_geo` (wizard.py lines 1463–1478)
  - `host_map_page` (wizard.py lines 1481–1555)

  And the Blueprint creation: `wizard = Blueprint("wizard", __name__)`.

  Update wizard.py's imports to only what those remaining functions need:
  ```python
  from .airbnb import _fetch_task, _fetch_task_direct, _fetch_task_geo
  from .payment import _stripe_active
  from .shared import CATEGORY_ICONS, CATEGORY_COLORS, _MAPS_IMG_DIR, _SCRIPTS_DIR
  ```

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed`.

- [ ] **Step 5: Commit**

  ```bash
  git add src/web/routes/airbnb.py src/web/routes/wizard.py src/web/app.py
  git commit -m "refactor: extract Airbnb/geo routes to routes/airbnb.py"
  ```

---

## Task 6: Create `host_map.py`, delete `wizard.py`

**Files:**
- Create: `src/web/routes/host_map.py`
- Modify: `src/web/routes/airbnb.py` (update remaining `url_for("wizard.host_map_page")` → `"host_map.host_map_page"`)
- Modify: `src/web/app.py` (register `host_map`, remove `wizard`)
- Delete: `src/web/routes/wizard.py`

**Interfaces:**
- Consumes: `shared.CATEGORY_ICONS`, `shared.CATEGORY_COLORS`, `shared._MAPS_IMG_DIR`, `shared._SCRIPTS_DIR`, `airbnb._fetch_task`, `airbnb._fetch_task_direct`, `airbnb._fetch_task_geo`, `payment._stripe_active`, `cache_mod`, `poi_engine`, `maps_db`, `task_mod`, `airbnb_nearby as lib`

- [ ] **Step 1: Create `src/web/routes/host_map.py`**

  ```python
  from __future__ import annotations

  import json
  import os
  import uuid as uuid_mod
  from pathlib import Path

  from flask import (
      Blueprint, Response, abort, current_app, jsonify,
      render_template, request, session,
  )

  from .shared import CATEGORY_ICONS, CATEGORY_COLORS, _MAPS_IMG_DIR, _SCRIPTS_DIR
  from .airbnb import _fetch_task, _fetch_task_direct, _fetch_task_geo
  from .payment import _stripe_active
  from .. import cache as cache_mod
  from .. import tasks as task_mod
  from .. import poi_engine
  from .. import maps_db
  from ... import airbnb_nearby as lib

  host_map = Blueprint("host_map", __name__)
  ```

  Copy these functions verbatim from `wizard.py`, changing `@wizard.` to `@host_map.`:
  - `_active_result` (wizard.py line 1079) — no decorator
  - `_render_map_page` (wizard.py lines 1087–1110) — no decorator
  - `_api_nearby_response` (wizard.py lines 1276–1278) — no decorator
  - `api_nearby_preflight` (wizard.py lines 1281–1283)
  - `api_generate` (wizard.py lines 1286–1313)
  - `api_nearby` (wizard.py lines 1316–1363)
  - `map_page` (wizard.py lines 1368–1432)
  - `api_start_map` (wizard.py lines 1441–1460)
  - `api_start_map_geo` (wizard.py lines 1463–1478)
  - `host_map_page` (wizard.py lines 1481–1555)

  No `url_for` references to other blueprints exist in these functions — no url_for updates needed in host_map.py.

- [ ] **Step 2: Update `url_for("wizard.host_map_page")` in `airbnb.py`**

  In `src/web/routes/airbnb.py`, find every occurrence of `"wizard.host_map_page"` (there are 5, in `_get_or_create_host_map`, `airbnb_page` ×2, `geo_page` ×2) and replace with `"host_map.host_map_page"`.

- [ ] **Step 3: Update `app.py`**

  Add `host_map` registration and remove `wizard`:

  ```python
  # Add:
  from .routes.host_map import host_map
  app.register_blueprint(host_map)

  # Remove these two lines:
  from .routes.wizard import wizard
  app.register_blueprint(wizard)
  ```

- [ ] **Step 4: Delete `wizard.py`**

  ```bash
  rm src/web/routes/wizard.py
  ```

- [ ] **Step 5: Run tests**

  ```bash
  uv run pytest -v
  ```

  Expected: `19 passed, 5 failed`.

- [ ] **Step 6: Commit**

  ```bash
  git add src/web/routes/host_map.py src/web/routes/airbnb.py src/web/app.py
  git rm src/web/routes/wizard.py
  git commit -m "refactor: extract host-map routes to routes/host_map.py, delete wizard.py"
  ```

---

## Self-Review

**Spec coverage:**
- ✅ wizard.py deleted
- ✅ routes/airbnb.py, routes/zillow.py, routes/host_map.py, routes/export.py, routes/payment.py, routes/shared.py all created
- ✅ Blueprint registrations updated in app.py
- ✅ No route URL changes
- ✅ url_for references updated in same commit as function moves

**Placeholder scan:** All steps contain exact function lists, exact import blocks, exact url_for rename tables. No TBDs.

**Type consistency:** `_fetch_task`, `_fetch_task_direct`, `_fetch_task_geo` produced by Task 5 (airbnb.py) and consumed by Task 6 (host_map.py) under the exact same names.

**Incremental safety:** After each task, the app is fully functional:
- Tasks 1–4: wizard.py still has the routes being served
- Task 5: airbnb routes move to `airbnb` Blueprint; wizard.py retains host_map routes; `url_for("wizard.host_map_page")` still resolves
- Task 6: host_map routes move to `host_map` Blueprint; wizard.py deleted; `url_for("wizard.host_map_page")` updated to `"host_map.host_map_page"` in the same commit
