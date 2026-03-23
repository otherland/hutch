"""SQLite database: schema, connections, and retry logic."""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import random
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("HUTCH_DB_PATH", "./hutch.db")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    NOT NULL UNIQUE,
    human_key   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS agents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    name             TEXT    NOT NULL,
    program          TEXT    NOT NULL,
    model            TEXT    NOT NULL,
    task_description TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_active_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(project_id, name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    sender_id    INTEGER NOT NULL REFERENCES agents(id),
    thread_id    TEXT,
    subject      TEXT    NOT NULL,
    body_md      TEXT    NOT NULL,
    importance   TEXT    NOT NULL DEFAULT 'normal',
    ack_required INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS message_recipients (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    kind       TEXT    NOT NULL DEFAULT 'to',
    read_at    TEXT,
    acked_at   TEXT,
    PRIMARY KEY (message_id, agent_id)
);
CREATE TABLE IF NOT EXISTS file_reservations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    agent_id     INTEGER NOT NULL REFERENCES agents(id),
    path_pattern TEXT    NOT NULL,
    exclusive    INTEGER NOT NULL DEFAULT 1,
    reason       TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at   TEXT    NOT NULL,
    released_at  TEXT
);
CREATE TABLE IF NOT EXISTS context_store (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    stored_by  INTEGER NOT NULL REFERENCES agents(id),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(project_id, key)
);
"""

_FTS = """\
CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages USING fts5(
    message_id UNINDEXED, subject, body
);
CREATE TRIGGER IF NOT EXISTS fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO fts_messages(rowid, message_id, subject, body)
    VALUES (new.id, new.id, new.subject, new.body_md);
END;
CREATE TRIGGER IF NOT EXISTS fts_ad AFTER DELETE ON messages BEGIN
    DELETE FROM fts_messages WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS fts_au AFTER UPDATE ON messages BEGIN
    DELETE FROM fts_messages WHERE rowid = old.id;
    INSERT INTO fts_messages(rowid, message_id, subject, body)
    VALUES (new.id, new.id, new.subject, new.body_md);
END;
"""

_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_agents_project      ON agents(project_id);
CREATE INDEX IF NOT EXISTS idx_messages_project_ts  ON messages(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_thread      ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender_ts   ON messages(sender_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recipients_agent     ON message_recipients(agent_id, message_id);
CREATE INDEX IF NOT EXISTS idx_reservations_active  ON file_reservations(project_id, released_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_reservations_agent   ON file_reservations(project_id, agent_id, released_at);
"""

_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous  = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA cache_size   = -8192",
    "PRAGMA temp_store   = MEMORY",
]

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_db():
    """Async context manager: one connection with pragmas set."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        for p in _PRAGMAS:
            await conn.execute(p)
        yield conn
    finally:
        await conn.close()


async def init_schema() -> None:
    """Create tables, FTS, indexes. Idempotent."""
    async with get_db() as conn:
        await conn.executescript(_SCHEMA)
        await conn.executescript(_FTS)
        await conn.executescript(_INDEXES)
        await conn.commit()


def retry_on_lock(fn):
    """Decorator: retry up to 3x on SQLite lock with exponential backoff."""
    @functools.wraps(fn)
    async def wrapper(*a: Any, **kw: Any) -> Any:
        for attempt in range(4):
            try:
                return await fn(*a, **kw)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 3:
                    delay = 0.1 * (2 ** attempt) + random.uniform(0, 0.05)
                    log.warning("db locked, retry %d in %.2fs", attempt + 1, delay)
                    await asyncio.sleep(delay)
                else:
                    raise
    return wrapper
