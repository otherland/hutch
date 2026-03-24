<p align="center">
  <img src="hero.jpg" alt="Hutch — where agents talk to each other" width="100%">
</p>

# Hutch

*A small enclosure where agents talk to each other.*

[![Tests](https://github.com/otherland/hutch/actions/workflows/test.yml/badge.svg)](https://github.com/otherland/hutch/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/hutch)](https://pypi.org/project/hutch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[The problem](#the-problem) · [How it works](#how-it-works) · [Install](#install) · [Tools](#tools) · [Hooks](#hooks) · [Configuration](#configuration) · [MIT License](LICENSE)

---

## The problem

You're running four AI coding agents against the same repo. Claude Code, Codex, Copilot, Cursor — doesn't matter which. One is refactoring the auth module. Another is writing tests for it. A third is updating the API routes that import it. None of them know the others exist.

Session two overwrites session one's changes. Session three imports a function that session one just renamed. Session four force-pushes over all of it. You spend an hour untangling the merge.

This isn't a hypothetical. It's what happens every time you run parallel agents without coordination. The agents are fast, capable, and completely blind to each other.

**Hutch gives them eyes.**

## How it works

Hutch is an MCP server. You point your agents at it. They get:

**Identities.** Each agent registers with a unique name (`SilentOwl`, `GreenLake`). You can see who's working and what they're doing.

**Messaging.** Agents send markdown messages to each other with threads, importance levels, and acknowledgements. "I'm refactoring `auth.py`, stay clear." "Done, here's what changed."

**File reservations.** Advisory locks on paths and globs with TTL. Before editing `src/auth/*.py`, an agent checks if anyone else has claimed it. Conflicts are reported, not enforced — agents are adults.

**Shared context.** A key-value store for large blobs — schemas, plans, file contents. One agent stores it, others retrieve it by key.

**Full-text search.** FTS5 across all messages in a project. Find what was discussed, decided, or flagged.

Everything lives in a single SQLite database. No Redis, no Postgres, no Docker. One process, three dependencies, ~800 lines of Python.

## Install

```bash
pipx install hutch
hutch serve
# → Hutch MCP server on http://127.0.0.1:4718/mcp
```

Then point your agents at it. Hutch speaks MCP, so any agent that supports MCP servers can connect.

**Claude Code** — `.claude/settings.json`:
```json
{ "mcpServers": { "hutch": { "url": "http://127.0.0.1:4718/mcp" } } }
```

**Cursor** — `.cursor/mcp.json`:
```json
{ "mcpServers": { "hutch": { "url": "http://127.0.0.1:4718/mcp" } } }
```

**Codex / Copilot / any MCP client** — point it at `http://127.0.0.1:4718/mcp`.

Then either set up [hooks](#hooks) (recommended) or paste the [agent instructions](#agent-instructions) into your project's `CLAUDE.md`, `AGENTS.md`, or equivalent system prompt.

## Tools

| Tool | What it does |
|------|-------------|
| `ensure_project` | Create/get a project namespace (keyed by repo path) |
| `register_agent` | Register with a random AdjectiveNoun name |
| `send_message` | Send markdown to agents (threads, importance, broadcast) |
| `reply_message` | Reply to a message, preserving the thread |
| `fetch_inbox` | Read messages (peek mode available) |
| `acknowledge_message` | Explicitly ack a message |
| `search_messages` | FTS5 full-text search across all messages |
| `file_reservation_paths` | Claim advisory locks on paths/globs with TTL |
| `release_file_reservations` | Release your claims |
| `check_reservations` | Who holds reservations on these paths? |
| `list_file_reservations` | All active reservations in a project |
| `list_agents` | Who's registered and when were they last active? |
| `store_context` | Store a blob by key for other agents |
| `get_context` | Retrieve a stored blob |
| `list_context_keys` | List all stored keys |

## A typical session

```
Agent 1 (SilentOwl)                          Agent 2 (GreenLake)
───────────────────                          ───────────────────
ensure_project("/repo")                      ensure_project("/repo")
register_agent(program="claude-code")        register_agent(program="codex")

file_reservation_paths(                      fetch_inbox()
  paths=["src/auth/*.py"],                    → "SilentOwl is refactoring auth"
  reason="auth-refactor"
)                                            check_reservations(["src/auth/login.py"])
                                              → conflict: held by SilentOwl
send_message(
  to=["GreenLake"],                          # Works on something else instead
  subject="Refactoring auth",
  body_md="Stay clear of src/auth/"
)

# ... does the work ...

release_file_reservations()
send_message(
  to=["GreenLake"],
  subject="Auth done",
  body_md="Renamed login() to authenticate()"
)
                                             fetch_inbox()
                                              → "Auth done. Renamed login()..."
                                             # Now safe to update imports
```

## Configuration

```bash
hutch serve                        # defaults below
hutch serve --port 9000            # override via flag
export HUTCH_DB_PATH=./hutch.db    # default
export HUTCH_HOST=127.0.0.1        # default
export HUTCH_PORT=4718              # default
```

## Hooks

Hooks automate the coordination protocol — registration, file reservations, and cleanup happen automatically on session start/end and before file edits. No pasting instructions.

Hutch ships hook configs for Claude Code and GitHub Copilot in `examples/`. Copy the relevant one into your project:

**Claude Code** — merge into `.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "hutch hooks session-start", "timeout": 10 }] }],
    "PreToolUse": [{ "matcher": "Edit|Write", "hooks": [{ "type": "command", "command": "hutch hooks pre-edit", "timeout": 5 }] }],
    "SessionEnd": [{ "hooks": [{ "type": "command", "command": "hutch hooks session-end", "timeout": 10 }] }]
  }
}
```

**GitHub Copilot** — save as `.github/hooks/hooks.json`:
```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [{ "type": "command", "bash": "hutch hooks session-start", "timeoutSec": 10, "env": { "HUTCH_PROGRAM": "copilot" } }],
    "preToolUse": [{ "type": "command", "bash": "hutch hooks pre-edit", "timeoutSec": 5, "env": { "HUTCH_PROGRAM": "copilot" } }],
    "sessionEnd": [{ "type": "command", "bash": "hutch hooks session-end", "timeoutSec": 10, "env": { "HUTCH_PROGRAM": "copilot" } }]
  }
}
```

What the hooks do:
- **session-start**: registers the project and agent, checks inbox for unread messages
- **pre-edit**: checks file reservations before editing, warns on conflicts, claims the file
- **session-end**: releases all file reservations

With hooks, the only instructions you need are for the judgment calls hooks can't automate:

```markdown
## Hutch — agent coordination

While working:
- `send_message` to announce what you're doing (use thread_id to group related messages)
- `store_context` for schemas, plans, or large outputs other agents might need
- When done, `send_message` summarising what changed
```

## Agent instructions (without hooks)

If your agent doesn't support hooks, paste this into `CLAUDE.md`, `AGENTS.md`, or equivalent:

```markdown
## Hutch — agent coordination

On session start:
1. `ensure_project` with the repo's absolute path
2. `register_agent` with your program name and model

Before editing files:
1. `fetch_inbox` — check for messages from other agents
2. `check_reservations` on the files you plan to edit
3. `file_reservation_paths` to claim them

While working:
- `send_message` to announce what you're doing (use thread_id to group related messages)
- `store_context` for schemas, plans, or large outputs other agents might need

When done:
1. `release_file_reservations`
2. `send_message` summarising what changed
```

## Design

- **~800 lines.** The entire server. Not a framework, not a platform. A single coordination primitive.
- **3 dependencies.** FastMCP, aiosqlite, uvicorn. That's the full dependency tree.
- **SQLite everything.** WAL mode, FTS5 search, advisory locks. One file, zero ops.
- **No enforcement.** Reservations are advisory. Messages are optional. Agents coordinate because it helps, not because they're forced.

Inspired by [mcp_agent_mail](https://github.com/Dicklesworthstone/mcp_agent_mail) (30K LOC, 33 dependencies, 48 tools). Hutch keeps the coordination primitive and cuts everything else.

## License

MIT
