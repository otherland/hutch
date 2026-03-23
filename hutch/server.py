"""FastMCP server: 16 tools for multi-agent coordination."""
from __future__ import annotations

import fnmatch
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import db, names

log = logging.getLogger(__name__)

_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
_IMPORTANCE_LEVELS = frozenset({"low", "normal", "high", "urgent"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime(_UTC_FMT)


def _err(code: str, message: str, **extra: Any) -> dict:
    return {"error": code, "message": message, "recoverable": True, **extra}


async def _agent_names(conn, project_id: int) -> list[str]:
    cur = await conn.execute(
        "SELECT name FROM agents WHERE project_id = ? ORDER BY last_active_at DESC LIMIT 20",
        (project_id,),
    )
    return [r["name"] for r in await cur.fetchall()]


async def _get_project(conn, key: str) -> dict | None:
    resolved = str(Path(key).resolve()) if Path(key).is_absolute() else key
    slug = names.slugify(resolved)
    cur = await conn.execute(
        "SELECT * FROM projects WHERE slug = ? OR human_key = ?", (slug, resolved),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _require_project(conn, key: str) -> dict:
    p = await _get_project(conn, key)
    if not p:
        raise ValueError(f"PROJECT_NOT_FOUND: No project for '{key}'. Call ensure_project first.")
    return p


async def _require_agent(conn, project_id: int, name: str) -> dict:
    cur = await conn.execute(
        "SELECT * FROM agents WHERE project_id = ? AND name = ? COLLATE NOCASE",
        (project_id, name),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)
    existing = await _agent_names(conn, project_id)
    hint = f" Registered agents: {', '.join(existing)}" if existing else " No agents registered yet."
    raise ValueError(f"AGENT_NOT_FOUND: No agent '{name}' in this project.{hint}")


async def _bump(conn, agent_id: int) -> None:
    await conn.execute("UPDATE agents SET last_active_at = ? WHERE id = ?", (_utcnow(), agent_id))


def _fnmatch_overlap(a: str, b: str) -> bool:
    return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


async def _expire_reservations(conn, project_id: int) -> None:
    now = _utcnow()
    await conn.execute(
        "UPDATE file_reservations SET released_at = ? "
        "WHERE project_id = ? AND released_at IS NULL AND expires_at < ?",
        (now, project_id, now),
    )


async def _active_reservations(conn, project_id: int) -> list[dict]:
    now = _utcnow()
    cur = await conn.execute(
        "SELECT fr.*, a.name AS agent_name FROM file_reservations fr "
        "JOIN agents a ON fr.agent_id = a.id "
        "WHERE fr.project_id = ? AND fr.released_at IS NULL AND fr.expires_at > ?",
        (project_id, now),
    )
    return [dict(r) for r in await cur.fetchall()]


def _format_reservation(r: dict) -> dict:
    return {
        "path_pattern": r["path_pattern"], "held_by": r["agent_name"],
        "exclusive": bool(r["exclusive"]), "reason": r["reason"],
        "expires_at": r["expires_at"],
    }


@asynccontextmanager
async def _ctx(project_key: str, agent_name: str | None = None):
    """Open connection, resolve project (+ optional agent), auto-commit on clean exit."""
    async with db.get_db() as conn:
        project = await _require_project(conn, project_key)
        if agent_name:
            agent = await _require_agent(conn, project["id"], agent_name)
            yield conn, project, agent
        else:
            yield conn, project
        await conn.commit()


@asynccontextmanager
async def _lifespan(app):
    await db.init_schema()
    log.info("Schema ready, db=%s", db.DB_PATH)
    yield


mcp = FastMCP("Hutch", lifespan=_lifespan)


def _tool(fn):
    """Register as MCP tool with retry-on-lock; keep fn callable for tests."""
    wrapped = db.retry_on_lock(fn)
    mcp.tool()(wrapped)
    return wrapped


@_tool
async def ensure_project(project_key: str) -> dict:
    """Idempotently create a project namespace. project_key must be an absolute repo path."""
    if not Path(project_key).is_absolute():
        return _err("INVALID_ARGUMENT", f"project_key must be an absolute path, got: '{project_key}'")
    resolved = str(Path(project_key).resolve())
    slug = names.slugify(resolved)
    async with db.get_db() as conn:
        existing = await _get_project(conn, project_key)
        if existing:
            return existing
        try:
            await conn.execute(
                "INSERT INTO projects (slug, human_key) VALUES (?, ?)", (slug, resolved)
            )
            await conn.commit()
        except sqlite3.IntegrityError:
            pass
        return dict(await (await conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,))).fetchone())


@_tool
async def register_agent(
    project_key: str, program: str, model: str,
    name: str | None = None, task_description: str = "",
) -> dict:
    """Register or update an agent identity. Omit name to auto-generate."""
    async with _ctx(project_key) as (conn, project):
        pid = project["id"]
        if name:
            if not names.is_valid_name(name):
                return _err("INVALID_AGENT_NAME",
                    f"'{name}' is not a valid AdjectiveNoun name. "
                    f"Omit the name parameter to auto-generate one, or use a combination "
                    f"like 'GreenLake' or 'SilentOwl'.")
        else:
            for _ in range(20):
                candidate = names.generate()
                cur = await conn.execute(
                    "SELECT id FROM agents WHERE project_id = ? AND name = ? COLLATE NOCASE",
                    (pid, candidate),
                )
                if not await cur.fetchone():
                    name = candidate
                    break
            else:
                return _err("INVALID_ARGUMENT", "Failed to generate unique name after 20 attempts.")

        now = _utcnow()
        await conn.execute(
            "INSERT INTO agents (project_id, name, program, model, task_description, last_active_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(project_id, name) DO UPDATE SET "
            "program=excluded.program, model=excluded.model, "
            "task_description=excluded.task_description, last_active_at=excluded.last_active_at",
            (pid, name, program, model, task_description, now),
        )
        cur = await conn.execute(
            "SELECT * FROM agents WHERE project_id = ? AND name = ? COLLATE NOCASE", (pid, name)
        )
        return dict(await cur.fetchone())


@_tool
async def send_message(
    project_key: str, sender_name: str, to: list[str], subject: str, body_md: str,
    cc: list[str] | None = None, importance: str = "normal",
    ack_required: bool = False, thread_id: str | None = None,
    broadcast: bool = False,
) -> dict:
    """Send a markdown message to one or more agents."""
    async with _ctx(project_key, sender_name) as (conn, project, sender):
        pid = project["id"]

        if broadcast:
            if to and any(t.strip() for t in to):
                return _err("INVALID_ARGUMENT", "broadcast=true with explicit 'to' is not allowed.")
            cur = await conn.execute(
                "SELECT name FROM agents WHERE project_id = ? AND id != ?", (pid, sender["id"])
            )
            to = [r["name"] for r in await cur.fetchall()]
            if not to:
                return _err("EMPTY_RECIPIENTS", "Broadcast: no other agents registered.")

        if not to and not (cc or []):
            return _err("EMPTY_RECIPIENTS", "No recipients. Provide 'to', 'cc', or set broadcast=true.")

        if importance not in _IMPORTANCE_LEVELS:
            return _err("INVALID_ARGUMENT",
                f"importance must be one of {sorted(_IMPORTANCE_LEVELS)}, got: '{importance}'")

        if thread_id and not names.is_valid_thread_id(thread_id):
            return _err("INVALID_ARGUMENT", f"thread_id '{thread_id}' must be alphanumeric/._- and max 128 chars.")

        all_recipients: list[tuple[dict, str]] = []
        for n in (to or []):
            all_recipients.append((await _require_agent(conn, pid, n), "to"))
        for n in (cc or []):
            all_recipients.append((await _require_agent(conn, pid, n), "cc"))

        now = _utcnow()
        cur = await conn.execute(
            "INSERT INTO messages (project_id, sender_id, thread_id, subject, body_md, importance, ack_required, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, sender["id"], thread_id, subject[:512], body_md, importance, int(ack_required), now),
        )
        msg_id = cur.lastrowid

        seen: set[int] = set()
        for agent, kind in all_recipients:
            if agent["id"] not in seen:
                await conn.execute(
                    "INSERT INTO message_recipients (message_id, agent_id, kind) VALUES (?,?,?)",
                    (msg_id, agent["id"], kind),
                )
                seen.add(agent["id"])

        await _bump(conn, sender["id"])
        return {
            "message_id": msg_id, "thread_id": thread_id,
            "recipients": [a["name"] for a, _ in all_recipients], "created_at": now,
        }


@_tool
async def reply_message(
    project_key: str, message_id: int, sender_name: str, body_md: str,
    importance: str = "normal", ack_required: bool = False,
) -> dict:
    """Reply to a message, preserving the thread."""
    async with _ctx(project_key, sender_name) as (conn, project, _sender):
        pid = project["id"]
        cur = await conn.execute("SELECT * FROM messages WHERE id = ? AND project_id = ?", (message_id, pid))
        original = await cur.fetchone()
        if not original:
            return _err("MESSAGE_NOT_FOUND", f"Message {message_id} not found in this project.")
        original = dict(original)

        tid = original.get("thread_id") or str(original["id"])

        cur = await conn.execute(
            "SELECT a.name FROM message_recipients mr JOIN agents a ON mr.agent_id = a.id WHERE mr.message_id = ?",
            (message_id,),
        )
        orig_recipients = [r["name"] for r in await cur.fetchall()]
        cur = await conn.execute("SELECT name FROM agents WHERE id = ?", (original["sender_id"],))
        orig_sender = (await cur.fetchone())["name"]

        to_names = set(orig_recipients + [orig_sender]) - {sender_name}
        if not to_names:
            to_names = {orig_sender}

        subj = re.sub(r"^(Re:\s*)+", "", original["subject"], flags=re.IGNORECASE)
        subj = f"Re: {subj}"[:512]

    return await send_message(
        project_key=project_key, sender_name=sender_name,
        to=list(to_names), subject=subj, body_md=body_md,
        importance=importance, ack_required=ack_required, thread_id=tid,
    )


@_tool
async def fetch_inbox(
    project_key: str, agent_name: str,
    since: str | None = None, thread_id: str | None = None,
    urgent_only: bool = False, unread_only: bool = False,
    limit: int = 50, mark_read: bool = True,
) -> dict:
    """Read messages addressed to an agent. Set mark_read=false to peek without marking read."""
    limit = min(max(1, limit), 200)
    async with _ctx(project_key, agent_name) as (conn, project, agent):
        clauses = ["mr.agent_id = ?"]
        params: list[Any] = [agent["id"]]
        if since:
            clauses.append("m.created_at > ?")
            params.append(since)
        if thread_id:
            clauses.append("m.thread_id = ?")
            params.append(thread_id)
        if urgent_only:
            clauses.append("m.importance IN ('high','urgent')")
        if unread_only:
            clauses.append("mr.read_at IS NULL")

        cur = await conn.execute(
            f"SELECT m.*, mr.read_at, mr.acked_at, a.name AS sender_name "
            f"FROM messages m "
            f"JOIN message_recipients mr ON m.id = mr.message_id "
            f"JOIN agents a ON m.sender_id = a.id "
            f"WHERE {' AND '.join(clauses)} ORDER BY m.created_at DESC LIMIT ?",
            params + [limit],
        )
        rows = [dict(r) for r in await cur.fetchall()]

        now = _utcnow()
        if mark_read:
            unread_ids = [r["id"] for r in rows if not r.get("read_at")]
            if unread_ids:
                ph = ",".join("?" * len(unread_ids))
                await conn.execute(
                    f"UPDATE message_recipients SET read_at = ? "
                    f"WHERE agent_id = ? AND read_at IS NULL AND message_id IN ({ph})",
                    [now, agent["id"]] + unread_ids,
                )

        await _bump(conn, agent["id"])
        return {"messages": [
            {
                "id": r["id"], "sender": r["sender_name"], "subject": r["subject"],
                "body_md": r["body_md"], "importance": r["importance"],
                "ack_required": bool(r["ack_required"]), "thread_id": r["thread_id"],
                "created_at": r["created_at"], "read": r["read_at"] is not None,
                "acked": bool(r["acked_at"]),
            }
            for r in rows
        ], "count": len(rows)}


@_tool
async def acknowledge_message(project_key: str, agent_name: str, message_id: int) -> dict:
    """Acknowledge a message (separate from reading)."""
    async with _ctx(project_key, agent_name) as (conn, project, agent):
        cur = await conn.execute(
            "SELECT 1 FROM message_recipients WHERE message_id = ? AND agent_id = ?",
            (message_id, agent["id"]),
        )
        if not await cur.fetchone():
            return _err("MESSAGE_NOT_FOUND",
                f"Message {message_id} not found or {agent_name} is not a recipient.")

        now = _utcnow()
        await conn.execute(
            "UPDATE message_recipients SET acked_at = ?, read_at = COALESCE(read_at, ?) "
            "WHERE message_id = ? AND agent_id = ?",
            (now, now, message_id, agent["id"]),
        )
        return {"acknowledged": True, "message_id": message_id, "acked_at": now}


@_tool
async def search_messages(project_key: str, query: str, limit: int = 20) -> dict:
    """Full-text search across messages in a project."""
    limit = min(max(1, limit), 100)
    async with _ctx(project_key) as (conn, project):
        pid = project["id"]
        try:
            if not query.strip():
                raise sqlite3.OperationalError("empty query")
            cur = await conn.execute(
                "SELECT m.id, m.subject, m.thread_id, m.created_at, a.name AS sender, "
                "snippet(fts_messages, 2, '\u00bb', '\u00ab', '\u2026', 32) AS snippet "
                "FROM fts_messages fts "
                "JOIN messages m ON fts.message_id = m.id "
                "JOIN agents a ON m.sender_id = a.id "
                "WHERE m.project_id = ? AND fts_messages MATCH ? "
                "ORDER BY bm25(fts_messages) LIMIT ?",
                (pid, query.replace('"', '""'), limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        except sqlite3.OperationalError:
            pattern = f"%{query}%"
            cur = await conn.execute(
                "SELECT m.id, m.subject, m.thread_id, m.created_at, a.name AS sender, "
                "SUBSTR(m.body_md, 1, 200) AS snippet "
                "FROM messages m JOIN agents a ON m.sender_id = a.id "
                "WHERE m.project_id = ? AND (m.subject LIKE ? OR m.body_md LIKE ?) "
                "ORDER BY m.created_at DESC LIMIT ?",
                (pid, pattern, pattern, limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        return {"results": rows, "count": len(rows)}


@_tool
async def file_reservation_paths(
    project_key: str, agent_name: str, paths: list[str],
    ttl_seconds: int = 3600, exclusive: bool = True, reason: str = "",
) -> dict:
    """Request advisory file reservations on paths/globs. Always grants; reports conflicts."""
    if not paths:
        return _err("INVALID_ARGUMENT", "paths must be non-empty.")
    ttl_seconds = max(60, ttl_seconds)

    async with _ctx(project_key, agent_name) as (conn, project, agent):
        pid = project["id"]
        await _expire_reservations(conn, pid)
        active = await _active_reservations(conn, pid)

        now = _utcnow()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).strftime(_UTC_FMT)

        granted, conflicts = [], {}
        for path in paths:
            if exclusive:
                for res in active:
                    if (res["agent_id"] != agent["id"] and res["exclusive"]
                            and _fnmatch_overlap(path, res["path_pattern"])):
                        key = (res["path_pattern"], res["agent_name"])
                        conflicts[key] = {
                            "path_pattern": res["path_pattern"],
                            "held_by": res["agent_name"],
                            "expires_at": res["expires_at"],
                        }

            cur = await conn.execute(
                "INSERT INTO file_reservations (project_id, agent_id, path_pattern, exclusive, reason, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (pid, agent["id"], path, int(exclusive), reason, now, expires),
            )
            granted.append({
                "id": cur.lastrowid, "path_pattern": path,
                "exclusive": exclusive, "reason": reason, "expires_at": expires,
            })

        await _bump(conn, agent["id"])
        return {"granted": granted, "conflicts": list(conflicts.values())}


@_tool
async def release_file_reservations(
    project_key: str, agent_name: str, paths: list[str] | None = None,
) -> dict:
    """Release file reservations. If paths is null, release all for this agent."""
    async with _ctx(project_key, agent_name) as (conn, project, agent):
        now, pid, aid = _utcnow(), project["id"], agent["id"]
        base = ("UPDATE file_reservations SET released_at = ? "
                "WHERE project_id = ? AND agent_id = ? AND released_at IS NULL AND expires_at > ?")
        if paths:
            ph = ",".join("?" * len(paths))
            cur = await conn.execute(
                f"{base} AND path_pattern IN ({ph})", (now, pid, aid, now, *paths),
            )
        else:
            cur = await conn.execute(base, (now, pid, aid, now))
        return {"released": cur.rowcount}


@_tool
async def list_agents(project_key: str, active_since: str | None = None) -> dict:
    """List registered agents in a project."""
    async with _ctx(project_key) as (conn, project):
        sql = ("SELECT name, program, model, task_description, last_active_at "
               "FROM agents WHERE project_id = ?")
        params: list[Any] = [project["id"]]
        if active_since:
            sql += " AND last_active_at > ?"
            params.append(active_since)
        cur = await conn.execute(sql + " ORDER BY last_active_at DESC", params)
        return {"agents": [dict(r) for r in await cur.fetchall()]}


@_tool
async def check_reservations(project_key: str, paths: list[str]) -> dict:
    """Read-only: who holds reservations on these paths? Does not create any."""
    if not paths:
        return _err("INVALID_ARGUMENT", "paths must be non-empty.")
    async with _ctx(project_key) as (conn, project):
        await _expire_reservations(conn, project["id"])
        active = await _active_reservations(conn, project["id"])
        return {"reservations": [
            _format_reservation(res) for requested in paths
            for res in active if _fnmatch_overlap(requested, res["path_pattern"])
        ]}


@_tool
async def list_file_reservations(project_key: str) -> dict:
    """List all active file reservations in a project."""
    async with _ctx(project_key) as (conn, project):
        await _expire_reservations(conn, project["id"])
        active = await _active_reservations(conn, project["id"])
        return {"reservations": [_format_reservation(r) for r in active]}


@_tool
async def store_context(project_key: str, agent_name: str, key: str, value: str) -> dict:
    """Store a large blob by key for other agents to retrieve."""
    if not names.is_valid_context_key(key):
        return _err("INVALID_ARGUMENT",
            f"key '{key}' must be alphanumeric/hyphens/underscores, 1-128 chars.")
    async with _ctx(project_key, agent_name) as (conn, project, agent):
        now = _utcnow()
        await conn.execute(
            "INSERT INTO context_store (project_id, key, value, stored_by, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET "
            "value=excluded.value, stored_by=excluded.stored_by, created_at=excluded.created_at",
            (project["id"], key, value, agent["id"], now),
        )
        return {"key": key, "stored_by": agent_name, "created_at": now, "size_bytes": len(value.encode())}


@_tool
async def get_context(project_key: str, key: str) -> dict:
    """Retrieve a stored context blob by key."""
    async with _ctx(project_key) as (conn, project):
        cur = await conn.execute(
            "SELECT cs.key, cs.value, a.name AS stored_by, cs.created_at "
            "FROM context_store cs JOIN agents a ON cs.stored_by = a.id "
            "WHERE cs.project_id = ? AND cs.key = ?",
            (project["id"], key),
        )
        row = await cur.fetchone()
        if not row:
            return _err("CONTEXT_NOT_FOUND", f"No context stored under key '{key}' in this project.")
        return dict(row)


@_tool
async def list_context_keys(project_key: str) -> dict:
    """List all context keys stored in a project."""
    async with _ctx(project_key) as (conn, project):
        cur = await conn.execute(
            "SELECT cs.key, a.name AS stored_by, cs.created_at, LENGTH(cs.value) AS size_bytes "
            "FROM context_store cs JOIN agents a ON cs.stored_by = a.id "
            "WHERE cs.project_id = ? ORDER BY cs.key",
            (project["id"],),
        )
        return {"keys": [dict(r) for r in await cur.fetchall()]}
