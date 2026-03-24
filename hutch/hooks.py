"""Hook handlers for Hutch agent coordination.

Called by Claude Code and GitHub Copilot hooks to automate the
coordination protocol (register, reserve files, release on exit).

Usage:  hutch hooks <session-start|pre-edit|session-end>

Reads hook context JSON from stdin. Calls the existing server
functions directly — no duplication of logic.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from . import db
from .server import (
    ensure_project,
    register_agent,
    check_reservations,
    file_reservation_paths,
    release_file_reservations,
    fetch_inbox,
)


def _name_file(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"hutch-{session_id}.name")


def _read_agent_name(session_id: str) -> str | None:
    path = _name_file(session_id)
    if os.path.exists(path):
        return open(path).read().strip() or None
    return None


async def _session_start(ctx: dict) -> None:
    await db.init_schema()
    cwd = ctx.get("cwd", os.getcwd())
    session_id = ctx.get("session_id", f"pid-{os.getpid()}")

    await ensure_project(project_key=cwd)

    result = await register_agent(
        project_key=cwd,
        program=os.environ.get("HUTCH_PROGRAM", "claude-code"),
        model=os.environ.get("HUTCH_MODEL", "unknown"),
    )

    if isinstance(result, dict) and "name" in result:
        with open(_name_file(session_id), "w") as f:
            f.write(result["name"])

        # Persist agent name as env var for subsequent hooks (Claude Code)
        env_file = os.environ.get("CLAUDE_ENV_FILE")
        if env_file:
            with open(env_file, "a") as f:
                f.write(f"export HUTCH_AGENT_NAME={result['name']}\n")
                f.write(f"export HUTCH_SESSION_ID={session_id}\n")

    # Check inbox for unread messages
    if isinstance(result, dict) and "name" in result:
        inbox = await fetch_inbox(
            project_key=cwd,
            agent_name=result["name"],
            unread_only=True,
            limit=10,
        )
        if isinstance(inbox, dict) and inbox.get("count", 0) > 0:
            msgs = inbox["messages"]
            summary = "\n".join(
                f"- [{m['sender']}] {m['subject']}: {m['body_md'][:200]}"
                for m in msgs
            )
            _output({"additionalContext": f"Hutch: registered as {result['name']}. {len(msgs)} unread message(s):\n{summary}"})
        else:
            _output({"additionalContext": f"Hutch: registered as {result['name']}. No unread messages."})


async def _pre_edit(ctx: dict) -> None:
    await db.init_schema()
    cwd = ctx.get("cwd", os.getcwd())
    session_id = ctx.get("session_id", os.environ.get("HUTCH_SESSION_ID", f"pid-{os.getpid()}"))

    file_path = ctx.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    agent_name = (
        os.environ.get("HUTCH_AGENT_NAME")
        or _read_agent_name(session_id)
    )
    if not agent_name:
        return

    result = await check_reservations(project_key=cwd, paths=[file_path])
    if isinstance(result, dict):
        reservations = result.get("reservations", [])
        conflicts = [r for r in reservations if r["held_by"] != agent_name]
        if conflicts:
            holders = ", ".join(
                f"{c['held_by']} ({c['path_pattern']})" for c in conflicts
            )
            _output({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"Hutch: {file_path} is reserved by {holders}. "
                        "Coordinate with them before editing."
                    ),
                }
            })
            return

    await file_reservation_paths(
        project_key=cwd,
        agent_name=agent_name,
        paths=[file_path],
    )


async def _session_end(ctx: dict) -> None:
    await db.init_schema()
    cwd = ctx.get("cwd", os.getcwd())
    session_id = ctx.get("session_id", os.environ.get("HUTCH_SESSION_ID", f"pid-{os.getpid()}"))

    agent_name = (
        os.environ.get("HUTCH_AGENT_NAME")
        or _read_agent_name(session_id)
    )
    if not agent_name:
        return

    await release_file_reservations(project_key=cwd, agent_name=agent_name)

    path = _name_file(session_id)
    if os.path.exists(path):
        os.unlink(path)


def _output(data: dict) -> None:
    print(json.dumps(data), flush=True)


def main() -> None:
    ctx = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    handlers = {
        "session-start": _session_start,
        "pre-edit": _pre_edit,
        "session-end": _session_end,
    }

    handler = handlers.get(cmd)
    if handler:
        asyncio.run(handler(ctx))
    else:
        print(f"Usage: hutch hooks <{'|'.join(handlers)}>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
