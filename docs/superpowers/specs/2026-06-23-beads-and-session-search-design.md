# Beads adoption + session search

Date: 2026-06-23

## Overview

Two related pieces of work:

1. **Beads adoption** — adopt `br` (beads) as the agent-actionable issue queue for this repo, seeded with issues found during code review.
2. **Session search** — use [CASS](https://github.com/Dicklesworthstone/coding_agent_session_search) (`~/.local/bin/cass`), already installed. Indexes Claude Code sessions with BM25 + optional semantic search. Agent-friendly: `cass search "query" --robot`. Configure via `~/.config/cass/sources.toml`.

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
| Configure CASS for session search | New feature | Use existing CASS install; configure sources.toml |

### What stays unchanged

The existing `.scratch/` markdown issue system described in CLAUDE.md remains. Beads is additive — it is the agent-actionable work queue; `.scratch/` holds PRDs and conversation history.

---

## Part 2: Session search via CASS

CASS (`~/.local/bin/cass`) is already installed. It indexes Claude Code session `.jsonl` files with BM25 full-text search and optional local semantic embeddings.

### Configuration

Add this project's session directory to `~/.config/cass/sources.toml`:

```toml
[[sources]]
name = "lequartier"
kind = "claude_code"
path = "~/.claude/projects/-home-alx-code-lequartier"
```

Then run `cass index` to build the initial index.

### Usage

```bash
cass search "overlay card"           # lexical search
cass search "overlay card" --robot   # JSON output for agent use
cass sessions --current --json       # list sessions
```

### Agent integration

`cass search "query" --robot` returns structured JSON — suitable for use in agent prompts to retrieve past decisions, code patterns, or conversation context from prior sessions on this project.
