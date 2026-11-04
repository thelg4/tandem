"""NDJSON protocol between ``tandem watch`` and the desktop app.

One JSON object per line, ``"v": 1``. Strings are stripped of control characters so a
transcript cannot drive the app's terminal or UI. No prompt text, diffs, or
command text are ever sent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

PROTOCOL_VERSION = 1
AGENTS = frozenset({"claude_code", "codex"})


def clean(s: str, limit: int = 200) -> str:
    return "".join(ch for ch in s if ch.isprintable())[:limit]


def iso(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return clean(value, limit=4096)
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def encode(msg: dict[str, object]) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, **_scrub(msg)}, separators=(",", ":"))


def _opt_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def decode_command(line: str) -> dict[str, object] | None:
    """Parse one app → watcher command; anything unexpected is ``None``."""
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("pane_id"), str):
        return None
    kind = value.get("type")
    if kind == "pane_started":
        pid = value.get("pid")
        if (
            value.get("agent") not in AGENTS
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or not _opt_str(value.get("claude_session_id"))
            or not _opt_str(value.get("spawned_at"))
        ):
            return None
        return value
    if kind in ("pane_exited", "pane_input"):
        return value if _opt_str(value.get("at")) else None
    return None
