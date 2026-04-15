"""Parse Claude Code JSONL session files and extract metadata."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_session_jsonl(filepath: Path) -> dict:
    """Extract metadata from a Claude Code JSONL session file.

    Returns a dict with keys matching the sessions table columns.
    """
    stat = filepath.stat()
    meta = {
        "session_id": filepath.stem,
        "file_path": str(filepath),
        "file_size_kb": stat.st_size / 1024,
        "title": None,
        "model": None,
        "permission_mode": None,
        "message_count": 0,
        "user_messages": 0,
        "first_message": None,
    }

    # Derive project name from parent directory
    # e.g. "-Users-Pasha-PycharmProjects-cosmos" → "cosmos"
    project_dir = filepath.parent.name
    parts = project_dir.split("-")
    meta["project"] = parts[-1] if parts else project_dir

    # File timestamps — st_birthtime is macOS-specific, fall back to ctime
    if platform.system() == "Darwin":
        meta["created_at"] = datetime.fromtimestamp(
            stat.st_birthtime, tz=timezone.utc
        ).isoformat()
    else:
        meta["created_at"] = datetime.fromtimestamp(
            stat.st_ctime, tz=timezone.utc
        ).isoformat()
    meta["updated_at"] = datetime.fromtimestamp(
        stat.st_mtime, tz=timezone.utc
    ).isoformat()

    try:
        with open(filepath, "r") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = obj.get("type", "")

                if msg_type == "custom-title" and not meta["title"]:
                    meta["title"] = obj.get("customTitle")

                elif msg_type == "agent-name" and not meta["title"]:
                    meta["title"] = obj.get("agentName")

                elif msg_type == "permission-mode":
                    meta["permission_mode"] = obj.get("permissionMode")

                elif msg_type in ("user", "assistant"):
                    meta["message_count"] += 1

                    if msg_type == "user":
                        meta["user_messages"] += 1
                        if not meta["first_message"]:
                            content = obj.get("message", {}).get("content", "")
                            if isinstance(content, str) and content.strip():
                                meta["first_message"] = content[:500]
                            elif isinstance(content, list):
                                for block in content:
                                    if (
                                        isinstance(block, dict)
                                        and block.get("type") == "text"
                                    ):
                                        meta["first_message"] = block["text"][:500]
                                        break

                    elif msg_type == "assistant" and not meta["model"]:
                        meta["model"] = obj.get("message", {}).get("model")

    except Exception as e:
        print(
            f"  Warning: could not fully parse {filepath.name}: {e}",
            file=sys.stderr,
        )

    return meta
