# Beads adoption + session search — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status note (2026-06-23):** Task 1 is COMPLETE. Tasks 2–3 are SUPERSEDED — session search is handled by CASS (`~/.local/bin/cass`), not a bespoke script. See br-lgk for the CASS configuration task and the updated spec at `docs/superpowers/specs/2026-06-23-beads-and-session-search-design.md`.

**Goal:** Adopt `br` (beads) as the agent-actionable issue queue for this repo, seeding it with four actionable issues including one to configure CASS for session search.

**Architecture:** Task 1 files four issues into the global `~/.beads` workspace via `br create` shell commands. ~~Tasks 2–3 build the session search script~~ (superseded — use CASS).

**Tech Stack:** `br` CLI (beads v0.2.15+); CASS (`cass`).

## Global Constraints

- Python ≥ 3.11 (type union syntax `X | Y` used throughout)
- No new pip dependencies — stdlib only for `session_search.py`
- `br` actor flag: `--actor claude` on all `br` write commands
- Session directory: `~/.claude/projects/-home-alx-code-lequartier/` (auto-derived from repo path — do not hard-code the slug, derive it at runtime)
- All `br create` commands: `--json` flag for machine-readable confirmation
- Tests run with: `uv run pytest tests/test_session_search.py -v`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `scripts/__init__.py` | Makes `scripts/` importable as a package for tests |
| Create | `scripts/session_search.py` | Session parser, filters, matching, CLI entry point |
| Create | `tests/test_session_search.py` | Unit + integration tests for all non-CLI functions |

No existing files are modified.

---

## Task 1: Beads — file four issues

**Files:** None (shell commands only)

**Interfaces:**
- Produces: four beads issue IDs printed to stdout (save them for reference)

- [ ] **Step 1: File issue — walking-time labels**

```bash
br create \
  --title "Walking-time labels on exported maps" \
  --type feature \
  --priority 3 \
  --description "Display walking time from the rental to each POI on the exported map PNG (e.g. '8 min' label on each marker). Formula: max(1, round(haversine(rental_lat, rental_lon, poi_lat, poi_lon) / 80)). Compute server-side in host_map_page(), pass as JSON array to template, render in Leaflet only when export_mode=True. Rental coords already in DB. GATED: ship Step 2 branded header first, then evaluate whether this is needed." \
  --labels "ready-for-human,gated" \
  --actor claude \
  --json
```

Expected: JSON with `"id": "br-..."`.

- [ ] **Step 2: File issue — 202 email fallback**

```bash
br create \
  --title "202 generating-page email fallback" \
  --type bug \
  --priority 2 \
  --description "Add fallback message to the HTML meta-refresh page returned when the PNG is missing on download. Current text: 'Your map is generating. This page will refresh in 30 seconds.' Add after the meta-refresh tag: 'If this page keeps refreshing after 2 minutes, email support@lequartier.co and we\\'ll send your map manually.' One-line addition in download_map_image() in src/web/routes/wizard.py. No new infrastructure." \
  --labels "ready-for-agent" \
  --actor claude \
  --json
```

Expected: JSON with `"id": "br-..."`.

- [ ] **Step 3: File issue — split wizard.py**

```bash
br create \
  --title "Split wizard.py into focused sub-modules" \
  --type task \
  --priority 3 \
  --description "src/web/routes/wizard.py is 1695 lines with 50+ functions spanning Airbnb routes, Zillow routes, export/download, Stripe/payment, host-map page, and shared helpers. Proposed split: routes/airbnb.py (Airbnb listing flow), routes/zillow.py (Zillow listing flow), routes/host_map.py (host_map_page, map_page, api_start_map, api_start_map_geo), routes/export.py (step2_continue, step3_create_pr, step3_notify, download_map_image, download_qr), routes/payment.py (api_checkout, stripe_webhook, _stripe_active). Shared helpers (_gh_headers, _gh_put_file, CATEGORY_ICONS, CATEGORY_COLORS, etc.) stay in a routes/shared.py. Blueprint registrations update in src/web/app.py." \
  --labels "ready-for-agent" \
  --actor claude \
  --json
```

Expected: JSON with `"id": "br-..."`.

- [x] **Step 4: File issue — configure CASS for session search**

```bash
br create \
  --title "Configure CASS (coding_agent_session_search) for this project" \
  --type feature \
  --priority 2 \
  --description "Stdlib-only script to search Claude Code session transcripts. Searches user messages, assistant messages, files touched (Read/Edit/Write/Bash tool calls), and date range. Default output: one line per session (date, short ID, first user message). --verbose: matching excerpts with ±1 message context. See docs/superpowers/specs/2026-06-23-beads-and-session-search-design.md for full spec." \
  --labels "ready-for-agent" \
  --actor claude \
  --json
```

Expected: JSON with `"id": "br-..."`.

- [ ] **Step 5: Confirm all four issues are in the tracker**

```bash
br list --json
```

Expected: `"total": 4` in the JSON output.

---

## Task 2: Session parser — `_parse_session`

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/session_search.py` (partial — `_parse_session` only)
- Create: `tests/test_session_search.py` (partial — parser tests only)

**Interfaces:**
- Produces:
  ```python
  def _parse_session(path: Path) -> dict | None:
      """Returns session dict or None if path unreadable or has no timestamp.

      Session dict keys:
        session_id: str          # filename stem (UUID)
        started_at: str          # ISO timestamp of first record
        first_user_message: str  # first user message, max 100 chars
        messages: list[dict]     # [{role, content, timestamp}]
        files_touched: set[str]  # absolute paths seen in tool calls
      """
  ```

- [ ] **Step 1: Create `scripts/__init__.py`**

```bash
touch scripts/__init__.py
```

- [ ] **Step 2: Write failing tests for `_parse_session`**

Create `tests/test_session_search.py`:

```python
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.session_search import _parse_session, _matches, _find_matching_messages


# ── Helpers ────────────────────────────────────────────────────────────────

def _user(content: str, ts: str = "2026-06-20T10:00:00.000Z") -> dict:
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": content}}


def _assistant(text: str = "", tools: list | None = None,
               ts: str = "2026-06-20T10:01:00.000Z") -> dict:
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for t in (tools or []):
        blocks.append({"type": "tool_use", "name": t["name"], "input": t["input"]})
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": blocks}}


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records))


# ── _parse_session tests ────────────────────────────────────────────────────

def test_parse_session_id_from_stem(tmp_path):
    p = tmp_path / "abc-123.jsonl"
    _write(p, [_user("hello")])
    s = _parse_session(p)
    assert s["session_id"] == "abc-123"


def test_parse_first_user_message(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("fix the overlay card"), _user("second message")])
    s = _parse_session(p)
    assert s["first_user_message"] == "fix the overlay card"


def test_parse_first_user_message_truncated_at_100(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("x" * 200)])
    s = _parse_session(p)
    assert len(s["first_user_message"]) == 100


def test_parse_messages_include_user_and_assistant(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("do X"), _assistant("I'll do X")])
    s = _parse_session(p)
    roles = [m["role"] for m in s["messages"]]
    assert roles == ["user", "assistant"]


def test_parse_files_touched_from_read_tool(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("read a file"),
        _assistant(tools=[{"name": "Read", "input": {"file_path": "/home/alx/code/lequartier/src/web/app.py"}}]),
    ])
    s = _parse_session(p)
    assert "/home/alx/code/lequartier/src/web/app.py" in s["files_touched"]


def test_parse_files_touched_from_edit_and_write(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("edit"),
        _assistant(tools=[
            {"name": "Edit", "input": {"file_path": "/home/alx/code/lequartier/src/web/routes/wizard.py"}},
            {"name": "Write", "input": {"file_path": "/home/alx/code/lequartier/scripts/new.py"}},
        ]),
    ])
    s = _parse_session(p)
    assert "/home/alx/code/lequartier/src/web/routes/wizard.py" in s["files_touched"]
    assert "/home/alx/code/lequartier/scripts/new.py" in s["files_touched"]


def test_parse_skips_malformed_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(_user("hello")) + "\nnot-json\n" + json.dumps(_assistant("world")) + "\n"
    )
    s = _parse_session(p)
    assert s is not None
    assert s["first_user_message"] == "hello"


def test_parse_returns_none_for_missing_file(tmp_path):
    assert _parse_session(tmp_path / "missing.jsonl") is None


def test_parse_returns_none_for_empty_file(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("")
    assert _parse_session(p) is None
```

- [ ] **Step 3: Run tests — confirm they all fail with ImportError**

```bash
uv run pytest tests/test_session_search.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name '_parse_session' from 'scripts.session_search'` (or `ModuleNotFoundError`).

- [ ] **Step 4: Implement `_parse_session` in `scripts/session_search.py`**

Create `scripts/session_search.py`:

```python
#!/usr/bin/env python3
"""Search Claude Code session transcripts for this project."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_slug = str(REPO_ROOT).replace("/", "-")
SESSION_DIR = Path.home() / ".claude" / "projects" / _slug


def _parse_session(path: Path) -> dict | None:
    started_at: str | None = None
    first_user_message: str | None = None
    messages: list[dict] = []
    files_touched: set[str] = set()

    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = r.get("timestamp")
            if ts and started_at is None:
                started_at = ts

            rtype = r.get("type")
            msg = r.get("message", {})

            if rtype == "user" and msg.get("role") == "user":
                content = msg.get("content", "")
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                text = text.strip()
                if text:
                    messages.append({"role": "user", "content": text, "timestamp": ts})
                    if first_user_message is None:
                        first_user_message = text

            elif rtype == "assistant" and msg.get("role") == "assistant":
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text", "").strip()
                        if text:
                            messages.append({"role": "assistant", "content": text, "timestamp": ts})
                    elif btype == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if name in ("Read", "Edit", "Write"):
                            fp = inp.get("file_path", "")
                            if fp:
                                files_touched.add(fp)
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            repo_str = str(REPO_ROOT)
                            for m in re.finditer(
                                rf"{re.escape(repo_str)}/[\w./\-]+", cmd
                            ):
                                files_touched.add(m.group())

    except OSError:
        return None

    if not started_at:
        return None

    return {
        "session_id": path.stem,
        "started_at": started_at,
        "first_user_message": (first_user_message or "")[:100],
        "messages": messages,
        "files_touched": files_touched,
    }


def _matches(
    session: dict,
    query: str | None,
    in_: str,
    file_: str | None,
    since: date | None,
    until: date | None,
) -> bool:
    raise NotImplementedError


def _find_matching_messages(session: dict, query: str, in_: str) -> list[dict]:
    raise NotImplementedError


def main() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run parser tests — all must pass**

```bash
uv run pytest tests/test_session_search.py -v -k "parse"
```

Expected: 8 tests PASSED, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/session_search.py tests/test_session_search.py
git commit -m "feat: add session_search parser skeleton with passing tests"
```

---

## Task 3: Filters, matching, and CLI

**Files:**
- Modify: `scripts/session_search.py` (implement `_matches`, `_find_matching_messages`, `main`)
- Modify: `tests/test_session_search.py` (add filter + integration tests)

**Interfaces:**
- Consumes: `_parse_session(path: Path) -> dict | None` from Task 2
- Produces:
  ```python
  def _matches(session: dict, query: str | None, in_: str,
               file_: str | None, since: date | None, until: date | None) -> bool: ...

  def _find_matching_messages(session: dict, query: str, in_: str) -> list[dict]:
      # Returns messages around first keyword match, ±1 neighbor context.
      # No duplicates. Preserves message order.
  ```

- [ ] **Step 1: Add failing tests for `_matches` and `_find_matching_messages`**

Append to `tests/test_session_search.py`:

```python
# ── _matches tests ──────────────────────────────────────────────────────────

def test_matches_keyword_in_user_message(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("fix the overlay card")])
    s = _parse_session(p)
    assert _matches(s, "overlay", "all", None, None, None)
    assert not _matches(s, "stripe", "all", None, None, None)


def test_matches_keyword_case_insensitive(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("Fix The Overlay Card")])
    s = _parse_session(p)
    assert _matches(s, "overlay", "all", None, None, None)


def test_matches_keyword_scoped_to_user(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("nothing"), _assistant("fix the overlay")])
    s = _parse_session(p)
    assert not _matches(s, "overlay", "user", None, None, None)
    assert _matches(s, "overlay", "assistant", None, None, None)


def test_matches_keyword_scoped_to_assistant(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("fix the overlay"), _assistant("ok")])
    s = _parse_session(p)
    assert not _matches(s, "overlay", "assistant", None, None, None)
    assert _matches(s, "overlay", "user", None, None, None)


def test_matches_file_filter_substring(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("edit"),
        _assistant(tools=[{"name": "Edit",
                           "input": {"file_path": "/home/alx/code/lequartier/src/web/app.py"}}]),
    ])
    s = _parse_session(p)
    assert _matches(s, None, "all", "app.py", None, None)
    assert _matches(s, None, "all", "src/web/app.py", None, None)
    assert not _matches(s, None, "all", "wizard.py", None, None)


def test_matches_since_filter(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("hello", ts="2026-06-15T10:00:00.000Z")])
    s = _parse_session(p)
    assert _matches(s, None, "all", None, date(2026, 6, 1), None)
    assert not _matches(s, None, "all", None, date(2026, 6, 20), None)


def test_matches_until_filter(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [_user("hello", ts="2026-06-15T10:00:00.000Z")])
    s = _parse_session(p)
    assert _matches(s, None, "all", None, None, date(2026, 6, 30))
    assert not _matches(s, None, "all", None, None, date(2026, 6, 10))


def test_matches_combined_filters(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("fix overlay", ts="2026-06-15T10:00:00.000Z"),
        _assistant(tools=[{"name": "Edit",
                           "input": {"file_path": "/home/alx/code/lequartier/src/web/app.py"}}]),
    ])
    s = _parse_session(p)
    assert _matches(s, "overlay", "all", "app.py", date(2026, 6, 1), date(2026, 6, 30))
    assert not _matches(s, "overlay", "all", "wizard.py", date(2026, 6, 1), date(2026, 6, 30))


# ── _find_matching_messages tests ───────────────────────────────────────────

def test_find_returns_match_with_neighbors(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("before"),
        _user("fix the overlay"),
        _assistant("I'll fix it"),
        _user("after"),
    ])
    s = _parse_session(p)
    excerpts = _find_matching_messages(s, "overlay", "all")
    contents = [m["content"] for m in excerpts]
    assert "fix the overlay" in contents
    assert "before" in contents
    assert "I'll fix it" in contents


def test_find_no_duplicates(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("overlay here"),
        _user("overlay again"),
    ])
    s = _parse_session(p)
    excerpts = _find_matching_messages(s, "overlay", "all")
    contents = [m["content"] for m in excerpts]
    assert len(contents) == len(set(contents))


def test_find_respects_in_scope(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("overlay in user"),
        _assistant("no match here"),
    ])
    s = _parse_session(p)
    # Searching only in assistant — user message won't trigger
    excerpts = _find_matching_messages(s, "overlay", "assistant")
    contents = [m["content"] for m in excerpts]
    assert "overlay in user" not in contents
```

- [ ] **Step 2: Run new tests — confirm they fail**

```bash
uv run pytest tests/test_session_search.py -v -k "matches or find"
```

Expected: failures with `NotImplementedError`.

- [ ] **Step 3: Implement `_matches` in `scripts/session_search.py`**

Replace the `_matches` stub:

```python
def _matches(
    session: dict,
    query: str | None,
    in_: str,
    file_: str | None,
    since: date | None,
    until: date | None,
) -> bool:
    ts = session["started_at"]
    if ts:
        session_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        if since and session_date < since:
            return False
        if until and session_date > until:
            return False

    if file_:
        if not any(file_ in p for p in session["files_touched"]):
            return False

    if query:
        q = query.lower()
        pool = [
            m["content"] for m in session["messages"]
            if in_ == "all" or m["role"] == in_
        ]
        if not any(q in text.lower() for text in pool):
            return False

    return True
```

- [ ] **Step 4: Implement `_find_matching_messages` in `scripts/session_search.py`**

Replace the `_find_matching_messages` stub:

```python
def _find_matching_messages(session: dict, query: str, in_: str) -> list[dict]:
    q = query.lower()
    messages = session["messages"]
    seen: set[int] = set()
    result: list[dict] = []

    for i, msg in enumerate(messages):
        if in_ != "all" and msg["role"] != in_:
            continue
        if q in msg["content"].lower():
            for j in range(max(0, i - 1), min(len(messages), i + 2)):
                if j not in seen:
                    seen.add(j)
                    result.append(messages[j])

    return result
```

- [ ] **Step 5: Run filter + match tests — all must pass**

```bash
uv run pytest tests/test_session_search.py -v -k "matches or find"
```

Expected: all PASSED.

- [ ] **Step 6: Implement `_fmt_date` and `main` in `scripts/session_search.py`**

Replace the `_fmt_date` placeholder (add before `main`) and `main` stub:

```python
def _fmt_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return "unknown"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Search Claude Code session transcripts"
    )
    p.add_argument("query", nargs="?", help="keyword(s) to search")
    p.add_argument(
        "--in", dest="in_",
        choices=["user", "assistant", "all"], default="all",
    )
    p.add_argument("--file", dest="file_", metavar="PATH",
                   help="only sessions that touched this file")
    p.add_argument("--since", metavar="YYYY-MM-DD", type=date.fromisoformat)
    p.add_argument("--until", metavar="YYYY-MM-DD", type=date.fromisoformat)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if not args.query and not args.file_ and not args.since and not args.until:
        p.error("provide QUERY or at least one filter (--file, --since, --until)")

    if not SESSION_DIR.exists():
        print(f"error: session directory not found: {SESSION_DIR}", file=sys.stderr)
        sys.exit(1)

    sessions: list[dict] = []
    for path in SESSION_DIR.glob("*.jsonl"):
        s = _parse_session(path)
        if s:
            sessions.append(s)

    sessions.sort(key=lambda s: s["started_at"], reverse=True)

    matched = [
        s for s in sessions
        if _matches(s, args.query, args.in_, args.file_, args.since, args.until)
    ]

    for s in matched:
        date_str = _fmt_date(s["started_at"])
        short_id = s["session_id"][:8]
        first_msg = s["first_user_message"][:80]
        print(f"{date_str}  {short_id}  {first_msg}")

        if args.verbose and args.query:
            excerpts = _find_matching_messages(s, args.query, args.in_)
            for msg in excerpts:
                role = msg["role"]
                content = msg["content"][:200].replace("\n", " ")
                print(f"  [{role:<9}] {content}")
            print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the full test suite**

```bash
uv run pytest tests/test_session_search.py -v
```

Expected: all tests PASSED, 0 failed.

- [ ] **Step 8: Smoke-test the CLI against real sessions**

```bash
uv run scripts/session_search.py "overlay"
```

Expected: one or more lines like:
```
2026-06-18  050620f0  restore airbnb overlay card on landing page...
```

```bash
uv run scripts/session_search.py "overlay" -v
```

Expected: same lines plus indented `[user]` / `[assistant]` excerpts.

```bash
uv run scripts/session_search.py --file wizard.py --since 2026-06-01
```

Expected: sessions that touched `wizard.py` after June 1.

- [ ] **Step 9: Run the full project test suite to check for regressions**

```bash
uv run pytest -v
```

Expected: all previously passing tests still PASS.

- [ ] **Step 10: Commit**

```bash
git add scripts/session_search.py tests/test_session_search.py
git commit -m "feat: add session_search CLI — keyword, file, and date filters"
```

---

## Self-Review

**Spec coverage:**
- ✅ Beads adoption: 4 issues filed (TODO-1, TODO-2, wizard split, session search)
- ✅ Session directory auto-derived from repo path
- ✅ Keyword search (`--in user|assistant|all`)
- ✅ File filter (`--file`)
- ✅ Date range (`--since`, `--until`)
- ✅ Default one-line output
- ✅ Verbose excerpts with `±1` context
- ✅ No new pip dependencies
- ✅ Missing file → `None`, malformed lines → skipped

**Placeholder scan:** No TBD/TODO in plan. All code steps are complete and runnable.

**Type consistency:** `_parse_session` returns `dict | None` with keys `session_id`, `started_at`, `first_user_message`, `messages`, `files_touched` — used identically in `_matches`, `_find_matching_messages`, and `main`.
