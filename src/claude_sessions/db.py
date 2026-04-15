"""SQLite database layer for session catalog."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".claude" / "session_manager.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    project       TEXT,
    title         TEXT,
    model         TEXT,
    permission_mode TEXT,
    message_count INTEGER DEFAULT 0,
    user_messages INTEGER DEFAULT 0,
    file_size_kb  REAL DEFAULT 0,
    first_message TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    synced_at     TEXT,
    archived      INTEGER DEFAULT 0,
    file_path     TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    session_id TEXT NOT NULL,
    tag        TEXT NOT NULL,
    PRIMARY KEY (session_id, tag),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
    session_id,
    title,
    project,
    first_message,
    tokenize='porter unicode61'
);
"""


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and auto-migrate) the session catalog database."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def resolve_session_id(db: sqlite3.Connection, partial: str) -> str | None:
    """Resolve a partial session ID or title fragment to a full UUID."""
    rows = db.execute(
        "SELECT session_id FROM sessions WHERE session_id LIKE ?",
        (f"{partial}%",),
    ).fetchall()

    if len(rows) == 0:
        rows = db.execute(
            "SELECT session_id FROM sessions WHERE title LIKE ?",
            (f"%{partial}%",),
        ).fetchall()

    if len(rows) == 0:
        print(f"No session matching '{partial}'")
        return None
    elif len(rows) > 1:
        print(f"Ambiguous ID '{partial}', matches {len(rows)} sessions:")
        for r in rows:
            print(f"  {r['session_id']}")
        return None

    return rows[0]["session_id"]
