# Beads adoption + session search CLI

Date: 2026-06-23

## Overview

Two related pieces of work:

1. **Beads adoption** — adopt `br` (beads) as the agent-actionable issue queue for this repo, seeded with issues found during code review.
2. **Session search CLI** — `scripts/session_search.py`, a stdlib-only script that searches Claude Code session transcripts (`.jsonl` files) by keyword, file touched, and date range.

---

## Part 1: Beads adoption

### Workspace

Global `~/.beads` (already initialized). No per-project workspace needed at this scale.

### Issues to file

| Title | Source | Notes |
|---|---|---|
| Walking-time labels on exported maps | TODOS.md TODO-1 | Gated — ship Step 2 branded header first, then evaluate |
| 202 "generating" page email fallback | TODOS.md TODO-2 | One-line HTML addition, no new infrastructure |
| Split `wizard.py` into focused sub-modules | Code review | 1695 lines, 50+ functions; routes/airbnb.py, routes/zillow.py, routes/host_map.py, routes/export.py, routes/api.py |
| Session search CLI | New feature | This spec |

### What stays unchanged

The existing `.scratch/` markdown issue system described in CLAUDE.md remains. Beads is additive — it is the agent-actionable work queue; `.scratch/` holds PRDs and conversation history.

---

## Part 2: Session search CLI

### Location

`scripts/session_search.py`

### Session directory

Auto-detected: `~/.claude/projects/<slug>/` where `<slug>` is derived from the git root path (hyphens replacing slashes). For this repo: `~/.claude/projects/-home-alx-code-lequartier/`.

Each `.jsonl` file in that directory is one session. Filename stem is the session UUID.

### CLI interface

```
uv run scripts/session_search.py QUERY [options]

Arguments:
  QUERY                       keyword(s) to search

Options:
  --in {user,assistant,all}   where to search (default: all)
  --file PATH                 only sessions that touched this file
  --since YYYY-MM-DD          sessions on or after this date
  --until YYYY-MM-DD          sessions on or before this date
  -v, --verbose               show matching excerpts with ±2 message context
```

All options are combinable. With no QUERY and only `--file` or `--since`/`--until`, filters alone are sufficient (lists matching sessions).

### Data model (per session)

Parsed from the `.jsonl` file on every run — no index:

- `session_id` — filename stem (UUID)
- `started_at` — timestamp of the first message in the file
- `first_user_message` — content of the first `type=="user"` message, truncated to 100 chars
- `messages` — list of `{role, content, timestamp}` extracted from `type=="user"` and `type=="assistant"` records
- `files_touched` — set of file paths extracted from assistant tool-call arguments:
  - `Read`, `Edit`, `Write` tool calls: `file_path` argument
  - `Bash` tool calls: regex scan of the `command` argument for paths under the repo root (`/home/alx/code/lequartier/...`)

### Search logic

1. Load all `.jsonl` files in the session directory.
2. Parse each file into the session model above.
3. Apply filters in order:
   - `--since` / `--until`: drop sessions outside date range (compare `started_at`)
   - `--file PATH`: drop sessions where `PATH` is not in `files_touched` (substring match on the basename or relative path)
   - `QUERY` + `--in`: drop sessions where the keyword does not appear (case-insensitive) in the specified message pool
4. Sort remaining sessions by `started_at` descending (most recent first).
5. Output.

### Output format

**Default** (one line per matching session):
```
2026-06-18  050620f0  restore airbnb overlay card on landing page, move current...
2026-06-11  b5378fdf  fix: show bottom label on secondary markers when expanded...
```

Format: `{date}  {session_id[:8]}  {first_user_message[:80]}`

**Verbose** (`-v`, appended below each session line):
```
2026-06-18  050620f0  restore airbnb overlay card on landing page...
  [user]      restore airbnb overlay card on landing page, move current landing...
  [assistant] I'll restore the Airbnb overlay card. Let me read index.html first.
  [user]      (next message)
```
Shows up to 3 messages around the first match (the matching message ±1 neighbor on each side), truncated to 200 chars per message.

### Dependencies

Stdlib only: `json`, `argparse`, `pathlib`, `re`, `datetime`. No `pip install` required.

### Error handling

- Session directory not found: print a clear error with the expected path and exit 1.
- Malformed JSONL lines: skip silently (corrupt lines are not uncommon in partial sessions).
- No matches: exit 0 with no output (script-friendly).

---

## Out of scope

- Indexing / caching — add only if search over 500+ sessions becomes slow.
- Cross-project search — searches only this project's sessions.
- Web UI — CLI only.
