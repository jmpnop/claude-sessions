# claude-sessions

SQLite-backed CLI for indexing, searching, tagging, and managing Claude Code conversation sessions.

Claude Code stores sessions as UUID-named JSONL files under `~/.claude/projects/` with no searchable metadata. This tool builds a catalog on top of that, so you can find, tag, rename, and analyze sessions across all your projects.

## Install

```bash
# Install as a global CLI tool (recommended)
uv tool install git+https://github.com/jmpnop/claude-sessions.git

# Or from a local clone
uv pip install .

# With Parquet export support
uv pip install ".[parquet]"
```

## Quick start

```bash
claude-sessions sync           # Index all sessions
claude-sessions auto-name      # Name untitled sessions from first message
claude-sessions hook-install   # Auto-sync after every Claude Code session
```

## Usage

### Core commands

```bash
# Index all sessions from ~/.claude/projects
claude-sessions sync

# List sessions
claude-sessions ls
claude-sessions ls --project cosmos
claude-sessions ls --tag debug

# Full-text search
claude-sessions search "gemstone"

# Session details (prefix match or title match)
claude-sessions show 5a22
claude-sessions show cosmos
```

### Organize

```bash
# Rename (persists across syncs)
claude-sessions rename 5a22 "cosmos-3d-design"

# Auto-name all untitled sessions from their first message
claude-sessions auto-name
claude-sessions auto-name --dry-run   # Preview without writing

# Tag sessions
claude-sessions tag 5a22 3d-design active
claude-sessions untag 5a22 active

# Archive / unarchive
claude-sessions archive 5a22
```

### Maintain

```bash
# Garbage collection: find orphaned, stale, and empty sessions
claude-sessions gc
claude-sessions gc --days 14          # Custom stale threshold
claude-sessions gc --clean            # Remove orphaned DB entries

# Install/remove Claude Code auto-sync hook
claude-sessions hook-install          # Runs sync after every session
claude-sessions hook-uninstall
```

### Export

```bash
claude-sessions stats                 # Summary statistics
claude-sessions export --format json
claude-sessions export --format parquet
```

## Example output

```
ID       Project          Title                     Msgs     Size Tags             Updated
──────────────────────────────────────────────────────────────────────────────────────────
5a2263b2 cosmos           cosmos                     336    836KB 3d-design, activ 2026-04-15
bfd6b881 fpga             fpga                       206   1342KB active, fpga     2026-04-13
f2492206 blenderMCP       blenderMCP                8021 103568KB                  2026-04-13
```

## How it works

- Parses Claude Code JSONL session files to extract: title, project, model, message counts, first user message, timestamps
- Stores metadata in `~/.claude/session_manager.db` (SQLite with WAL mode)
- FTS5 full-text search across titles, projects, and first messages
- User renames (`claude-sessions rename`) are stored in a separate `title_user` column and survive `sync`
- `auto-name` generates slug-like names from first messages (strips conversational prefixes, truncates to 5 words)
- `gc` detects orphaned DB entries (JSONL deleted), stale sessions (no updates in N days), and empty sessions
- `hook-install` adds a Claude Code hook to `~/.claude/settings.json` that runs `sync` on every Stop event
- Session IDs resolve by prefix match (`5a22` finds `5a2263b2-...`) or title substring
- Schema auto-migrates on first open (safe to upgrade in place)

## License

MIT
