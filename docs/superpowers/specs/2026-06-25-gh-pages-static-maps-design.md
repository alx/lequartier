# GitHub Pages Static Map Pages — Design Spec

**Date:** 2026-06-25
**Status:** Approved

## Overview

Extend the existing `gh-pages` branch with pre-rendered Host Map pages for all locally cached listings. A build script on `main` uses the Flask test client to render `/p/{uuid}` pages with correct subdirectory-relative paths, writes redirect stubs for `/airbnb/{listing_id}`, copies static assets, then syncs the output into `gh-pages`.

The existing fallback `index.html` and `screenshot.png` on `gh-pages` are preserved.

## Routes Exposed on gh-pages

| URL pattern | What it serves |
|---|---|
| `/lequartier/` | Existing fallback page (unchanged) |
| `/lequartier/p/{uuid}/` | Pre-rendered Host Map (interactive Leaflet map, embedded GeoJSON) |
| `/lequartier/airbnb/{listing_id}/` | HTML meta-refresh redirect → `/lequartier/p/{uuid}/` |
| `/lequartier/static/…` | Flask static assets (CSS, JS, images) |

## Listings Covered

All listings present in `data/listings.db` that have a `result_path` pointing to a readable JSON file. At time of writing, 8 listings resolve cleanly:

| listing_id | uuid |
|---|---|
| `10349749` | `a49e2136-e5f0-4187-a257-1c5af63ab930` |
| `1136112938623890368` | `8346d8c5-63db-4d07-a1f6-71585cb884c9` |
| `1143906909159344434` | `c8eabcaa-5d04-47bd-9a7f-99619b5df072` |
| `686559818391956388` | `9ce82d41-7909-4ecc-85ab-72e8ed1ecfab` |
| `688796991005765901` | `83c49ad3-4a4f-47a0-a1a8-0299cb02cd0c` |
| `800518541496219083` | `886a7606-e6d5-48dd-a613-79b6c0df1b6c` |
| `898250921519049619` | `3052220e-be62-46ec-a84e-ba0b50ca7884` |
| `geo__9.4648,100.0457` | `b4641f2d-b7d2-40cf-902a-ee0ad7ed5ec5` |

Listings without a readable `result_path` (e.g. `938967549723131802`) are silently skipped.

## Build Script

**Location:** `scripts/build_static.py` (on `main`)

**Invocation:** `uv run python scripts/build_static.py`

**Flow:**

1. Create Flask app in test mode; set `SCRIPT_NAME=/lequartier` via `environ_overrides` on every test-client request so `url_for()` generates `/lequartier/static/…` paths (correct for GitHub Pages subdirectory)
2. Query `data/listings.db` → all rows with a readable `result_path`
3. For each `(uuid, listing_id)`:
   - `GET /p/{uuid}` via test client → write HTML to `dist/p/{uuid}/index.html`
   - Write `dist/airbnb/{listing_id}/index.html` with `<meta http-equiv="refresh" content="0;url=/lequartier/p/{uuid}/">`
4. Copy `src/web/static/` → `dist/static/` (full recursive copy, overwrite)
5. Switch to `gh-pages` branch
6. Copy `dist/` contents into repo root on `gh-pages` (preserve `index.html` and `screenshot.png`)
7. `git add -A`, commit `chore: rebuild static map pages`, push `origin gh-pages`
8. Switch back to `main`

## Key Technical Detail: SCRIPT_NAME

GitHub Pages for `alx/lequartier` serves from `https://alx.github.io/lequartier/`. Flask's `url_for('static', filename='css/app.css')` normally generates `/static/css/app.css` (absolute from domain root), which would resolve to `https://alx.github.io/static/css/app.css` — wrong.

Passing `environ_overrides={'SCRIPT_NAME': '/lequartier'}` to each test-client request causes Flask to prefix all `url_for()` outputs with `/lequartier`, producing `/lequartier/static/css/app.css` — correct.

## dist/ Directory Structure

```
dist/
  static/           ← copy of src/web/static/
  p/
    {uuid}/
      index.html    ← rendered Host Map HTML
  airbnb/
    {listing_id}/
      index.html    ← meta-refresh redirect stub
```

## gh-pages Branch After Sync

```
index.html            ← preserved fallback page
screenshot.png        ← preserved screenshot
static/               ← Flask static assets
p/{uuid}/index.html   ← one per listing
airbnb/{id}/index.html ← one per listing
```

## Out of Scope

- No Zillow routes (Zillow listings did not resolve to UUIDs locally)
- No `/explore` page
- No payment/Stripe flow (export gating stripped — map always visible)
- No automatic rebuild on cache changes (manual `uv run python scripts/build_static.py`)
- No CI/CD pipeline for gh-pages updates
