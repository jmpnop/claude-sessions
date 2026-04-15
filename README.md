# claude-sessions

SQLite-backed CLI for indexing, searching, tagging, and managing Claude Code conversation sessions.

Claude Code stores sessions as UUID-named JSONL files under `~/.claude/projects/` with no searchable metadata. This tool builds a catalog on top of that, so you can find, tag, rename, and analyze sessions across all your projects.

## Install

```bash
# From source
uv pip install .

# With Parquet export support
uv pip install ".[parquet]"

# macOS .pkg (installs to /usr/local/bin)
sudo installer -pkg dist/claude-sessions-0.1.0.pkg -target /
```

## Usage

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

# Rename (persists outside Claude Code)
claude-sessions rename 5a22 "cosmos-3d-design"

# Tag sessions
claude-sessions tag 5a22 3d-design active
claude-sessions untag 5a22 active

# Archive / unarchive
claude-sessions archive 5a22

# Statistics
claude-sessions stats

# Export catalog
claude-sessions export --format json
claude-sessions export --format parquet
```

## Example output

```
ID       Project          Title                     Msgs     Size Tags             Updated
──────────────────────────────────────────────────────────────────────────────────────────
5a2263b2 cosmos           cosmos                     133    362KB 3d-design, activ 2026-04-15
bfd6b881 fpga             fpga                       206   1342KB active, fpga     2026-04-13
f2492206 blenderMCP       blender-mcp-megasession   8021 103568KB                  2026-04-13
```

## How it works

- Parses Claude Code JSONL session files to extract: title, project, model, message counts, first user message, timestamps
- Stores metadata in `~/.claude/session_manager.db` (SQLite with WAL mode)
- FTS5 full-text search across titles, projects, and first messages
- Session IDs resolve by prefix match (`5a22` finds `5a2263b2-...`) or title substring

## Build .pkg

```bash
./scripts/build-pkg.sh
# Output: dist/claude-sessions-<version>.pkg
```

## License

MIT
