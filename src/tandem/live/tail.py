"""Follow agent logs as they grow, and find the ones worth following.

Reading goes through ``sources.jsonl.read_incremental`` so an unfinished trailing line
waits for the next poll and a rewritten file is detected by its prefix fingerprint.
Symlinks out of a root are never followed (NFR-04). Nothing here writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tandem.model.events import AgentFamily
from tandem.sources.jsonl import Checkpoint, SourceState, read_incremental

PEEK_LINE_BYTES = 4 * 1024 * 1024


@dataclass(slots=True)
class Tail:
    path: Path
    agent: AgentFamily
    checkpoint: Checkpoint | None = None

    def read(self) -> tuple[list[dict[str, Any]], bool]:
        """Return new complete records and whether the file was rewritten since last read."""
        result = read_incremental(self.path, self.checkpoint)
        if result.state is SourceState.MISSING:
            return [], False
        rewritten = result.state is SourceState.REWRITTEN
        self.checkpoint = result.checkpoint
        return [r.value for r in result.records], rewritten


def recent_logs(root: Path, since: float) -> list[Path]:
    if not root.is_dir():
        return []
    base = root.resolve()
    found: list[Path] = []
    for path in base.rglob("*.jsonl"):
        if path.is_symlink():
            continue
        try:
            stat = path.stat()
            resolved = path.resolve()
        except OSError:
            continue
        if stat.st_mtime >= since and resolved.is_relative_to(base) and resolved.is_file():
            found.append(resolved)
    return sorted(found)


def peek_cwd(
    path: Path, agent: AgentFamily, max_lines: int = 200
) -> tuple[str | None, dict[str, Any] | None]:
    """Claude: the first recorded cwd. Codex: the session_meta cwd and payload."""
    try:
        fh = path.open("rb")
    except OSError:
        return None, None
    with fh:
        for _ in range(max_lines):
            line = fh.readline(PEEK_LINE_BYTES)
            if not line:
                break
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            if agent is AgentFamily.CODEX:
                payload = rec.get("payload")
                if rec.get("type") == "session_meta" and isinstance(payload, dict):
                    cwd = payload.get("cwd")
                    return (cwd if isinstance(cwd, str) else None), payload
            else:
                cwd = rec.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd, None
    return None, None


def claude_registry(config_dir: Path) -> dict[int, tuple[str, str | None]]:
    """pid -> (sessionId, cwd) from Claude Code's per-process files.

    ``<config>/sessions/<pid>.json`` is undocumented; anything unexpected is skipped.
    """
    sessions = config_dir / "sessions"
    out: dict[int, tuple[str, str | None]] = {}
    if not sessions.is_dir():
        return out
    for path in sessions.glob("*.json"):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pid, sid, cwd = data.get("pid"), data.get("sessionId"), data.get("cwd")
        if isinstance(pid, int) and not isinstance(pid, bool) and isinstance(sid, str) and sid:
            out[pid] = (sid, cwd if isinstance(cwd, str) else None)
    return out
