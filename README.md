# mcp-agent-mail-lite

The coordination primitive from [mcp_agent_mail](https://github.com/Dicklesworthstone/mcp_agent_mail) (30K LOC), distilled to 869 lines.

Gives coding agents **identities**, **inboxes**, **file reservations**, **search**, and a **context store** over a single SQLite database. Designed to pair with [Beads Rust](https://github.com/Dicklesworthstone/beads_rust) for task planning.

## Install & run

```bash
pip install fastmcp aiosqlite uvicorn
python -m mcp_agent_mail_lite
# → FastMCP server on http://127.0.0.1:8765
```

Configure with environment variables:

```bash
export MCP_AGENT_MAIL_DB_PATH=./agent_mail.db  # default
export MCP_AGENT_MAIL_HOST=127.0.0.1           # default
export MCP_AGENT_MAIL_PORT=8765                 # default
export MCP_AGENT_MAIL_TOKEN=                    # optional bearer token
```

## Wire up your agents

Add to your `.claude/settings.json`, `.cursor/mcp.json`, or equivalent:

```json
{
  "mcpServers": {
    "agent-mail": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Then paste the [AGENTS.md blurb](#agentsmd-blurb) into your project's `CLAUDE.md` or `AGENTS.md`.

## Tools (13)

| # | Tool | What it does |
|---|------|-------------|
| 1 | `ensure_project` | Create/get a project namespace (keyed by repo path) |
| 2 | `register_agent` | Register an identity with a random AdjectiveNoun name |
| 3 | `send_message` | Send markdown to other agents (with threads, importance, broadcast) |
| 4 | `reply_message` | Reply to a message, preserving the thread |
| 5 | `fetch_inbox` | Read messages (marks them read as side-effect) |
| 6 | `acknowledge_message` | Explicitly ack a message |
| 7 | `search_messages` | FTS5 full-text search across all project messages |
| 8 | `file_reservation_paths` | Claim advisory locks on paths/globs with TTL |
| 9 | `release_file_reservations` | Release your file claims |
| 10 | `list_agents` | Who's registered in this project? |
| 11 | `check_reservations` | Read-only: who holds reservations on these paths? |
| 12 | `store_context` | Store a large blob by key (schema, plan, file) |
| 13 | `get_context` | Retrieve a stored blob by key |

## Beads integration

This server handles **coordination** (messaging + file reservations). [Beads Rust](https://github.com/Dicklesworthstone/beads_rust) handles **task planning** (issues, priorities, dependencies). They connect through one convention:

**Use the Beads issue ID as `thread_id` in messages and `reason` in file reservations.**

```
br ready --json                              # Pick a task
br update bd-7f3a2c --status in_progress     # Claim it
file_reservation_paths(..., reason="bd-7f3a2c")  # Lock files
send_message(..., thread_id="bd-7f3a2c")     # Tell the team
# ... do the work ...
br close bd-7f3a2c                           # Complete task
release_file_reservations(...)               # Free files
send_message(..., thread_id="bd-7f3a2c")     # Report done
```

## AGENTS.md blurb

Paste this into your project's `CLAUDE.md` or `AGENTS.md`:

```markdown
## Agent Coordination

You have two coordination systems. Use both.

### Beads (`br` CLI) — task planning
- `br ready --json` → what's unblocked and ready to work on
- `br show bd-XXXX --json` → full task details
- `br update bd-XXXX --status in_progress` → claim a task
- `br close bd-XXXX --reason "Done"` → complete a task

### MCP Agent Mail — messaging & file reservations
- `ensure_project` + `register_agent` on session start
- `fetch_inbox` to check for messages from other agents
- `list_agents` to see who else is working

### Full workflow:
1. `br ready --json` → pick highest-priority unblocked task (e.g. `bd-7f3a2c`)
2. `br update bd-7f3a2c --status in_progress`
3. `check_reservations` on the files you plan to edit
4. `file_reservation_paths` with `reason: "bd-7f3a2c"` on those files
5. `send_message` to relevant agents with `thread_id: "bd-7f3a2c"` announcing what you're doing
6. Do the work
7. `br close bd-7f3a2c --reason "Implemented"`
8. `release_file_reservations`
9. `send_message` in same thread: done, here's what changed

### Sharing large context:
- `store_context` a schema, plan, or file contents by key
- Reference it in messages: "see context key `api-schema-v2`"
- Other agents retrieve with `get_context`
```

## What was cut

This is a 97% reduction of the original. See [SPEC.md](SPEC.md) for the full analysis, but in short: Git mirroring (3,384 LOC), web UI (13,665 LOC), CLI (5,134 LOC), static export (2,217 LOC), contact policies, window identities, product grouping, build slots, macros, LLM summarisation, 23 resource endpoints, circuit breakers, query tracking, and Rich logging panels were all removed. What remains is the coordination primitive.

## License

MIT
