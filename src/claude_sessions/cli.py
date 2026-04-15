"""CLI entrypoint for claude-sessions."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_sessions.db import get_db, resolve_session_id
from claude_sessions.parser import parse_session_jsonl

CLAUDE_DIR = Path.home() / ".claude" / "projects"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

HOOK_ENTRY = {
    "type": "command",
    "command": "claude-sessions sync",
}
HOOK_EVENT = "Stop"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _upsert_fts(db: sqlite3.Connection, session_id: str, title: str, project: str, first_message: str):
    """Insert or replace an FTS entry for a session."""
    db.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
    db.execute(
        "INSERT INTO session_fts (session_id, title, project, first_message) VALUES (?,?,?,?)",
        (session_id, title or "", project or "", first_message or ""),
    )


# ── Commands ────────────────────────────────────────────────────────────────


def cmd_sync(args):
    """Index all sessions from ~/.claude/projects into SQLite."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    count_new, count_updated = 0, 0

    if not CLAUDE_DIR.exists():
        print(f"Claude projects directory not found: {CLAUDE_DIR}")
        return

    for project_dir in sorted(CLAUDE_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            meta = parse_session_jsonl(jsonl)
            meta["synced_at"] = now

            existing = db.execute(
                "SELECT synced_at, updated_at, title_user FROM sessions WHERE session_id = ?",
                (meta["session_id"],),
            ).fetchone()

            # Effective title for FTS: user override wins
            effective_title = (existing["title_user"] if existing and existing["title_user"] else meta["title"])

            if existing:
                db.execute(
                    """UPDATE sessions SET
                        project=?, title=?, model=?, permission_mode=?,
                        message_count=?, user_messages=?, file_size_kb=?,
                        first_message=?, updated_at=?, synced_at=?, file_path=?
                    WHERE session_id=?""",
                    (
                        meta["project"], meta["title"], meta["model"],
                        meta["permission_mode"], meta["message_count"],
                        meta["user_messages"], meta["file_size_kb"],
                        meta["first_message"], meta["updated_at"], now,
                        meta["file_path"], meta["session_id"],
                    ),
                )
                _upsert_fts(db, meta["session_id"], effective_title, meta["project"], meta["first_message"])
                count_updated += 1
            else:
                db.execute(
                    """INSERT INTO sessions
                        (session_id, project, title, model, permission_mode,
                         message_count, user_messages, file_size_kb,
                         first_message, created_at, updated_at, synced_at, file_path)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        meta["session_id"], meta["project"], meta["title"],
                        meta["model"], meta["permission_mode"],
                        meta["message_count"], meta["user_messages"],
                        meta["file_size_kb"], meta["first_message"],
                        meta["created_at"], meta["updated_at"], now,
                        meta["file_path"],
                    ),
                )
                _upsert_fts(db, meta["session_id"], meta["title"], meta["project"], meta["first_message"])
                count_new += 1

    db.commit()
    db.close()
    print(f"Synced: {count_new} new, {count_updated} updated")


def cmd_ls(args):
    """List sessions with optional filters."""
    db = get_db()

    query = """SELECT s.*, COALESCE(s.title_user, s.title) as effective_title,
               GROUP_CONCAT(t.tag, ', ') as tags
               FROM sessions s LEFT JOIN tags t ON s.session_id = t.session_id"""
    conditions, params = [], []

    if args.project:
        conditions.append("s.project LIKE ?")
        params.append(f"%{args.project}%")
    if args.tag:
        conditions.append("s.session_id IN (SELECT session_id FROM tags WHERE tag = ?)")
        params.append(args.tag)
    if not args.all:
        conditions.append("s.archived = 0")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " GROUP BY s.session_id ORDER BY s.updated_at DESC"

    rows = db.execute(query, params).fetchall()
    if not rows:
        print("No sessions found. Run `claude-sessions sync` first.")
        return

    print(f"{'ID':8} {'Project':16} {'Title':24} {'Msgs':>5} {'Size':>8} {'Tags':16} {'Updated'}")
    print("\u2500" * 110)
    for r in rows:
        sid = r["session_id"][:8]
        proj = (r["project"] or "?")[:16]
        title = (r["effective_title"] or "(untitled)")[:24]
        msgs = r["message_count"] or 0
        size = f"{r['file_size_kb']:.0f}KB"
        tags = (r["tags"] or "")[:16]
        updated = (r["updated_at"] or "")[:10]
        print(f"{sid:8} {proj:16} {title:24} {msgs:>5} {size:>8} {tags:16} {updated}")

    print(f"\n{len(rows)} session(s)")
    db.close()


def cmd_search(args):
    """Full-text search across session titles and first messages."""
    db = get_db()

    # Quote each term so FTS5 doesn't interpret hyphens/operators
    safe_query = " ".join(f'"{term}"' for term in args.query.split())

    rows = db.execute(
        """SELECT s.*, COALESCE(s.title_user, s.title) as effective_title,
                  GROUP_CONCAT(t.tag, ', ') as tags
           FROM session_fts f
           JOIN sessions s ON f.session_id = s.session_id
           LEFT JOIN tags t ON s.session_id = t.session_id
           WHERE session_fts MATCH ?
           GROUP BY s.session_id
           ORDER BY rank""",
        (safe_query,),
    ).fetchall()

    if not rows:
        print(f"No sessions matching '{args.query}'")
        return

    for r in rows:
        sid = r["session_id"][:8]
        title = r["effective_title"] or "(untitled)"
        proj = r["project"] or "?"
        preview = (r["first_message"] or "")[:120].replace("\n", " ")
        print(f"\n  {sid}  {title} [{proj}]")
        if preview:
            print(f"         {preview}")

    print(f"\n{len(rows)} result(s)")
    db.close()


def cmd_tag(args):
    """Add tags to a session."""
    db = get_db()
    session_id = resolve_session_id(db, args.id)
    if not session_id:
        return

    for tag in args.tags:
        db.execute(
            "INSERT OR IGNORE INTO tags (session_id, tag) VALUES (?, ?)",
            (session_id, tag.lower()),
        )
    db.commit()
    print(f"Tagged {session_id[:8]} with: {', '.join(args.tags)}")
    db.close()


def cmd_untag(args):
    """Remove a tag from a session."""
    db = get_db()
    session_id = resolve_session_id(db, args.id)
    if not session_id:
        return

    db.execute("DELETE FROM tags WHERE session_id = ? AND tag = ?", (session_id, args.tag.lower()))
    db.commit()
    print(f"Removed tag '{args.tag}' from {session_id[:8]}")
    db.close()


def cmd_show(args):
    """Show detailed info about a session."""
    db = get_db()
    session_id = resolve_session_id(db, args.id)
    if not session_id:
        return

    r = db.execute("SELECT *, COALESCE(title_user, title) as effective_title FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    tags = db.execute("SELECT tag FROM tags WHERE session_id = ?", (session_id,)).fetchall()

    print(f"Session:    {r['session_id']}")
    print(f"Title:      {r['effective_title'] or '(untitled)'}")
    if r["title_user"] and r["title"]:
        print(f"  (auto):   {r['title']}")
    print(f"Project:    {r['project']}")
    print(f"Model:      {r['model'] or '?'}")
    print(f"Messages:   {r['message_count']} total, {r['user_messages']} from user")
    print(f"Size:       {r['file_size_kb']:.0f} KB")
    print(f"Created:    {r['created_at']}")
    print(f"Updated:    {r['updated_at']}")
    print(f"Archived:   {'Yes' if r['archived'] else 'No'}")
    print(f"Tags:       {', '.join(t['tag'] for t in tags) if tags else '(none)'}")
    print(f"File:       {r['file_path']}")
    if r["first_message"]:
        print(f"\nFirst message:\n{textwrap.indent(r['first_message'][:300], '  ')}")

    db.close()


def cmd_rename(args):
    """Rename a session (persists across syncs)."""
    db = get_db()
    session_id = resolve_session_id(db, args.id)
    if not session_id:
        return

    db.execute("UPDATE sessions SET title_user = ? WHERE session_id = ?", (args.name, session_id))

    # Update FTS with new name
    r = db.execute("SELECT project, first_message FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    _upsert_fts(db, session_id, args.name, r["project"], r["first_message"])

    db.commit()
    print(f"Renamed {session_id[:8]} \u2192 '{args.name}'")
    db.close()


def cmd_archive(args):
    """Toggle archive status on a session."""
    db = get_db()
    session_id = resolve_session_id(db, args.id)
    if not session_id:
        return

    current = db.execute("SELECT archived FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    new_val = 0 if current["archived"] else 1
    db.execute("UPDATE sessions SET archived = ? WHERE session_id = ?", (new_val, session_id))
    db.commit()
    status = "archived" if new_val else "unarchived"
    print(f"Session {session_id[:8]} {status}")
    db.close()


def cmd_stats(args):
    """Show summary statistics."""
    db = get_db()

    total = db.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
    archived = db.execute("SELECT COUNT(*) as c FROM sessions WHERE archived=1").fetchone()["c"]
    total_size = db.execute("SELECT SUM(file_size_kb) as s FROM sessions").fetchone()["s"] or 0
    total_msgs = db.execute("SELECT SUM(message_count) as s FROM sessions").fetchone()["s"] or 0

    print(f"Total sessions:  {total} ({archived} archived)")
    print(f"Total size:      {total_size / 1024:.1f} MB")
    print(f"Total messages:  {total_msgs}")

    print("\nBy project:")
    for r in db.execute(
        "SELECT project, COUNT(*) as c, SUM(file_size_kb) as sz FROM sessions GROUP BY project ORDER BY sz DESC"
    ):
        print(f"  {r['project']:20} {r['c']:3} sessions  {r['sz'] / 1024:.1f} MB")

    print("\nTop tags:")
    for r in db.execute("SELECT tag, COUNT(*) as c FROM tags GROUP BY tag ORDER BY c DESC LIMIT 10"):
        print(f"  #{r['tag']:16} {r['c']} sessions")

    db.close()


def cmd_export(args):
    """Export session catalog to Parquet or JSON."""
    db = get_db()
    rows = db.execute(
        """SELECT s.*, COALESCE(s.title_user, s.title) as effective_title,
                  GROUP_CONCAT(t.tag, ',') as tags
           FROM sessions s LEFT JOIN tags t ON s.session_id = t.session_id
           GROUP BY s.session_id"""
    ).fetchall()

    records = [dict(r) for r in rows]

    if args.format == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            print("Install with: uv pip install claude-sessions[parquet]")
            return

        table = pa.Table.from_pylist(records)
        out = Path("sessions_export.parquet")
        pq.write_table(table, str(out))
        print(f"Exported {len(records)} sessions \u2192 {out}")

    else:
        out = Path("sessions_export.json")
        out.write_text(json.dumps(records, indent=2, default=str))
        print(f"Exported {len(records)} sessions \u2192 {out}")

    db.close()


_STRIP_PREFIXES = re.compile(
    r"^(can you|could you|please|i want to|i need to|i'd like to|help me|let's|let me|i want you to)\b\s*",
    re.IGNORECASE,
)


def _slug_from_message(message: str) -> str:
    """Generate a short slug-like name (3-5 words) from a first message."""
    if not message:
        return ""
    # Take the first ~60 chars, cut at last word boundary
    text = message[:60].split("\n")[0].strip()
    if len(message) > 60:
        text = text.rsplit(" ", 1)[0] if " " in text else text

    # Strip common conversational prefixes
    text = _STRIP_PREFIXES.sub("", text).strip()

    # Lowercase, replace non-alphanum with hyphens, collapse
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    # Truncate to 3-5 words (hyphen-separated)
    parts = slug.split("-")
    slug = "-".join(parts[:5])

    return slug


def cmd_auto_name(args):
    """Automatically name all untitled sessions from their first message."""
    db = get_db()
    dry_run = args.dry_run

    rows = db.execute(
        "SELECT session_id, first_message, project FROM sessions "
        "WHERE title IS NULL AND title_user IS NULL AND first_message IS NOT NULL"
    ).fetchall()

    if not rows:
        print("No untitled sessions to name.")
        db.close()
        return

    named = 0
    for r in rows:
        slug = _slug_from_message(r["first_message"])
        if not slug:
            continue

        if dry_run:
            print(f"  {r['session_id'][:8]}  ->  {slug}")
        else:
            db.execute(
                "UPDATE sessions SET title_user = ? WHERE session_id = ?",
                (slug, r["session_id"]),
            )
            _upsert_fts(db, r["session_id"], slug, r["project"], r["first_message"])
            print(f"  {r['session_id'][:8]}  ->  {slug}")
        named += 1

    if not dry_run:
        db.commit()

    label = "Would name" if dry_run else "Named"
    print(f"\n{label} {named} session(s)")
    db.close()


def cmd_gc(args):
    """Garbage collection: detect orphaned, stale, and empty sessions."""
    db = get_db()
    stale_days = args.days
    clean = args.clean
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()

    rows = db.execute(
        "SELECT session_id, file_path, updated_at, message_count, file_size_kb, "
        "COALESCE(title_user, title) as effective_title, project "
        "FROM sessions"
    ).fetchall()

    orphaned = []
    stale = []
    empty = []

    for r in rows:
        sid = r["session_id"]
        fp = r["file_path"]
        label = f"{sid[:8]}  {(r['effective_title'] or '(untitled)')[:30]:30}  [{r['project'] or '?'}]"

        # Orphaned: file_path no longer exists on disk
        if fp and not Path(fp).exists():
            orphaned.append((sid, label, fp))
            continue  # no point checking stale/empty if file is gone

        # Empty: 0 messages or file < 1KB
        msg_count = r["message_count"] or 0
        size_kb = r["file_size_kb"] or 0
        if msg_count == 0 or size_kb < 1:
            empty.append((sid, label, msg_count, size_kb))

        # Stale: not updated in more than N days
        updated = r["updated_at"] or ""
        if updated and updated < cutoff:
            stale.append((sid, label, updated))

    # ── Report ──
    print(f"Session garbage collection report (stale threshold: {stale_days} days)\n")

    print(f"Orphaned DB entries (file deleted): {len(orphaned)}")
    for sid, label, fp in orphaned:
        print(f"  {label}")
        print(f"         missing: {fp}")

    print(f"\nStale sessions (no update in {stale_days}+ days): {len(stale)}")
    for sid, label, updated in stale:
        print(f"  {label}  last: {updated[:10]}")

    print(f"\nEmpty sessions (0 messages or <1KB): {len(empty)}")
    for sid, label, msgs, size in empty:
        print(f"  {label}  msgs={msgs} size={size:.0f}KB")

    total_issues = len(orphaned) + len(stale) + len(empty)
    if total_issues == 0:
        print("\nAll clean — no issues found.")
        db.close()
        return

    # ── Clean mode ──
    if clean:
        if orphaned:
            removed = 0
            for sid, _label, _fp in orphaned:
                db.execute("DELETE FROM tags WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM session_fts WHERE session_id = ?", (sid,))
                db.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
                removed += 1
            db.commit()
            print(f"\nCleaned: removed {removed} orphaned DB entry(ies).")

        if stale:
            print(f"\nStale sessions are not auto-deleted.")
            print(f"Consider archiving them with:")
            for sid, _label, _updated in stale:
                print(f"  claude-sessions archive {sid[:8]}")
    else:
        if orphaned:
            print(f"\nRun with --clean to remove {len(orphaned)} orphaned entry(ies).")
        if stale:
            print(f"Stale sessions can be archived individually with `claude-sessions archive <id>`.")

    print(f"\nSummary: {len(orphaned)} orphaned, {len(stale)} stale, {len(empty)} empty")
    db.close()


def cmd_hook_install(args):
    """Install a Claude Code hook that runs 'claude-sessions sync' after every response."""
    settings = {}
    if CLAUDE_SETTINGS.exists():
        settings = json.loads(CLAUDE_SETTINGS.read_text())

    hooks = settings.setdefault("hooks", {})
    event_hooks = hooks.setdefault(HOOK_EVENT, [])

    # Check if already installed
    for h in event_hooks:
        if h.get("command") == HOOK_ENTRY["command"]:
            print("Hook already installed.")
            return

    event_hooks.append(HOOK_ENTRY)

    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"Installed Claude Code hook: '{HOOK_ENTRY['command']}' on {HOOK_EVENT} event")
    print(f"Settings written to {CLAUDE_SETTINGS}")


def cmd_hook_uninstall(args):
    """Remove the claude-sessions sync hook from Claude Code settings."""
    if not CLAUDE_SETTINGS.exists():
        print("No settings file found — nothing to uninstall.")
        return

    settings = json.loads(CLAUDE_SETTINGS.read_text())
    hooks = settings.get("hooks", {})
    event_hooks = hooks.get(HOOK_EVENT, [])

    original_len = len(event_hooks)
    event_hooks = [h for h in event_hooks if h.get("command") != HOOK_ENTRY["command"]]

    if len(event_hooks) == original_len:
        print("Hook not found — nothing to uninstall.")
        return

    # Clean up empty structures
    if event_hooks:
        hooks[HOOK_EVENT] = event_hooks
    else:
        hooks.pop(HOOK_EVENT, None)
    if not hooks:
        settings.pop("hooks", None)

    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"Removed Claude Code hook: '{HOOK_ENTRY['command']}'")
    print(f"Settings written to {CLAUDE_SETTINGS}")


# ── CLI entrypoint ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        prog="claude-sessions",
        description="Claude Code Session Manager \u2014 index, search, tag, and manage sessions",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("sync", help="Index sessions from ~/.claude/projects")

    ls_p = sub.add_parser("ls", help="List sessions")
    ls_p.add_argument("--project", "-p", help="Filter by project name")
    ls_p.add_argument("--tag", "-t", help="Filter by tag")
    ls_p.add_argument("--all", "-a", action="store_true", help="Include archived")

    search_p = sub.add_parser("search", help="Full-text search sessions")
    search_p.add_argument("query", help="Search query")

    tag_p = sub.add_parser("tag", help="Add tags to a session")
    tag_p.add_argument("id", help="Session ID (prefix or title)")
    tag_p.add_argument("tags", nargs="+", help="Tags to add")

    untag_p = sub.add_parser("untag", help="Remove a tag")
    untag_p.add_argument("id", help="Session ID (prefix or title)")
    untag_p.add_argument("tag", help="Tag to remove")

    show_p = sub.add_parser("show", help="Show session details")
    show_p.add_argument("id", help="Session ID (prefix or title)")

    rename_p = sub.add_parser("rename", help="Rename a session (persists across syncs)")
    rename_p.add_argument("id", help="Session ID (prefix or title)")
    rename_p.add_argument("name", help="New name")

    archive_p = sub.add_parser("archive", help="Toggle archive status")
    archive_p.add_argument("id", help="Session ID (prefix or title)")

    sub.add_parser("stats", help="Show summary statistics")

    export_p = sub.add_parser("export", help="Export catalog")
    export_p.add_argument("--format", "-f", choices=["json", "parquet"], default="json")

    auto_name_p = sub.add_parser("auto-name", help="Auto-name untitled sessions from first message")
    auto_name_p.add_argument("--dry-run", action="store_true", help="Show what would be named without writing")

    gc_p = sub.add_parser("gc", help="Garbage collection: find orphaned, stale, and empty sessions")
    gc_p.add_argument("--days", type=int, default=30, help="Stale threshold in days (default: 30)")
    gc_p.add_argument("--clean", action="store_true", help="Remove orphaned DB entries (dry-run by default)")

    sub.add_parser("hook-install", help="Install Claude Code hook to auto-sync after every session")
    sub.add_parser("hook-uninstall", help="Remove the Claude Code auto-sync hook")

    args = parser.parse_args()

    commands = {
        "sync": cmd_sync,
        "ls": cmd_ls,
        "search": cmd_search,
        "tag": cmd_tag,
        "untag": cmd_untag,
        "show": cmd_show,
        "rename": cmd_rename,
        "archive": cmd_archive,
        "stats": cmd_stats,
        "export": cmd_export,
        "auto-name": cmd_auto_name,
        "gc": cmd_gc,
        "hook-install": cmd_hook_install,
        "hook-uninstall": cmd_hook_uninstall,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()
