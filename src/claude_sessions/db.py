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
    title_user    TEXT,
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

MIGRATIONS = [
    # v0.1.1: add title_user column for user-set renames
    "ALTER TABLE sessions ADD COLUMN title_user TEXT",
]


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and auto-migrate) the session catalog database."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # Every Claude session's Stop hook runs `sync`, so many writers hit this one DB
    # at once. Give the lock a real wait budget instead of failing fast: the connect
    # `timeout` installs sqlite's busy handler, and `PRAGMA busy_timeout` is set
    # FIRST so even the WAL pragma and schema creation below wait for a contended
    # lock rather than raising "database is locked".
    db = sqlite3.connect(str(path), timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    _migrate(db)
    return db


def _migrate(db: sqlite3.Connection):
    """Run schema migrations that can't be expressed in CREATE IF NOT EXISTS."""
    for sql in MIGRATIONS:
        try:
            db.execute(sql)
            db.commit()
        except sqlite3.OperationalError:
            pass  # column/table already exists


def resolve_session_id(db: sqlite3.Connection, partial: str) -> str | None:
    """Resolve a partial session ID or title fragment to a full UUID."""
    rows = db.execute(
        "SELECT session_id FROM sessions WHERE session_id LIKE ?",
        (f"{partial}%",),
    ).fetchall()

    if len(rows) == 0:
        # Match against effective title (user override or auto)
        rows = db.execute(
            "SELECT session_id FROM sessions WHERE COALESCE(title_user, title) LIKE ?",
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
