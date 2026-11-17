"""Conversation history: the Claude Code and Codex sessions that ran in one worktree.

The desktop app lists these in its sidebar, like the chat list of a chat app, and can
resume one in a new pane. Read-only: titles come from Claude Code's ``ai-title`` records,
Codex's ``session_index.jsonl`` thread names, or else the first prompt. Only lines that
can carry a title or a prompt are parsed, so long transcripts read quickly.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tandem.live.protocol import clean, iso, parse_iso
from tandem.live.scope import Scope
from tandem.live.tail import claude_registry, peek_cwd, recent_logs
from tandem.model.events import AgentFamily

DEFAULT_DAYS = 30
TITLE_CHARS = 80
# Text the agents add to the first user turn on their own; never a title.
_NOT_A_PROMPT = (
    "<environment_context",
    "<user_instructions",
    "<permissions",
    "# AGENTS.md",
    "<command-",
    "<local-command",
    "Caveat:",
    "<system-reminder",
)


@dataclass(slots=True)
class HistorySession:
    agent: str
    raw_id: str
    """The agent's own session id, which its resume command takes."""
    title: str | None
    started: float
    last_active: float
    running: bool = False
    """A live process has it open right now (known for Claude Code only)."""

    @property
    def session_id(self) -> str:
        return f"{self.agent}:{self.raw_id}"

    def as_json(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "raw_id": self.raw_id,
            "title": self.title,
            "started": iso(self.started),
            "last_active": iso(self.last_active),
            "running": self.running,
        }


def _prompt_title(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or text.startswith(_NOT_A_PROMPT):
        return None
    return clean(" ".join(text.split()), TITLE_CHARS) or None


def _records(path: Path, markers: tuple[bytes, ...]) -> list[dict[str, Any]]:
    """Records on lines that contain any of ``markers``."""
    out: list[dict[str, Any]] = []
    try:
        fh = path.open("rb")
    except OSError:
        return out
    with fh:
        for line in fh:
            if not any(m in line for m in markers):
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _claude_title(path: Path) -> tuple[str | None, bool]:
    """The latest AI title, else the first prompt; and whether the user ever typed."""
    title: str | None = None
    prompt: str | None = None
    prompted = False
    for rec in _records(path, (b'"ai-title"', b'"type":"user"', b'"type": "user"')):
        kind = rec.get("type")
        if kind == "ai-title":
            raw = rec.get("aiTitle")
            if isinstance(raw, str) and raw:
                title = clean(raw, TITLE_CHARS)
        elif kind == "user" and rec.get("isMeta") is not True and not rec.get("isSidechain"):
            message = rec.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            texts: list[object] = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                blocks = [b for b in content if isinstance(b, dict)]
                if not any(b.get("type") == "tool_result" for b in blocks):
                    texts = [b.get("text") for b in blocks if b.get("type") == "text"]
            for text in texts:
                found = _prompt_title(text)
                if found:
                    prompted = True
                    prompt = prompt or found
    return title or prompt, prompted


def _codex_first_prompt(path: Path) -> str | None:
    for rec in _records(path, (b'"user_message"', b'"role":"user"')):
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "user_message":
            found = _prompt_title(payload.get("message"))
        elif payload.get("type") == "message" and payload.get("role") == "user":
            content = payload.get("content")
            blocks = content if isinstance(content, list) else []
            texts = [b.get("text") for b in blocks if isinstance(b, dict)]
            found = next((t for t in map(_prompt_title, texts) if t), None)
        else:
            continue
        if found:
            return found
    return None


def codex_thread_names(codex_root: Path) -> dict[str, str]:
    """Codex thread id -> the name Codex gave it, from ``session_index.jsonl``."""
    names: dict[str, str] = {}
    for rec in _records(codex_root.parent / "session_index.jsonl", (b'"thread_name"',)):
        tid, name = rec.get("id"), rec.get("thread_name")
        if isinstance(tid, str) and isinstance(name, str) and name:
            names[tid] = clean(name, TITLE_CHARS)
    return names


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def running_claude_sessions(claude_root: Path) -> set[str]:
    """Claude Code session ids whose process is still alive, from its per-process files."""
    registry = claude_registry(claude_root.parent)
    return {sid for pid, (sid, _cwd) in registry.items() if pid > 0 and _alive(pid)}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def history(
    top: Path,
    claude_root: Path,
    codex_root: Path,
    *,
    now: float | None = None,
    days: int = DEFAULT_DAYS,
    limit: int = 200,
) -> list[HistorySession]:
    """Sessions whose working directory is inside ``top``, most recently active first."""
    since = (now if now is not None else time.time()) - days * 86400
    scope = Scope(top)
    found: dict[str, HistorySession] = {}

    def add(s: HistorySession) -> None:
        prev = found.get(s.session_id)
        if prev is None:
            found[s.session_id] = s
            return
        prev.started = min(prev.started, s.started)
        prev.last_active = max(prev.last_active, s.last_active)
        prev.title = prev.title or s.title

    running = running_claude_sessions(claude_root)
    for path in recent_logs(claude_root, since):
        if path.parent.name == "subagents":
            continue
        mtime = _mtime(path)
        cwd = peek_cwd(path, AgentFamily.CLAUDE_CODE)[0]
        if mtime is None or cwd is None or not scope.contains(cwd):
            continue
        title, prompted = _claude_title(path)
        if not prompted:
            continue  # opened and closed without a word; nothing to go back to
        birth = getattr(path.stat(), "st_birthtime", mtime)
        started = min(birth, mtime)
        add(HistorySession("claude_code", path.stem, title, started, mtime, path.stem in running))

    names = codex_thread_names(codex_root)
    for path in recent_logs(codex_root, since):
        mtime = _mtime(path)
        cwd, meta = peek_cwd(path, AgentFamily.CODEX)
        if mtime is None or meta is None or cwd is None or not scope.contains(cwd):
            continue
        root = meta.get("session_id") or meta.get("id")
        raw = root if isinstance(root, str) and root else path.stem
        own = meta.get("id")
        if isinstance(own, str) and own != raw:
            continue  # a subagent thread; it folds into its root session
        prompt = _codex_first_prompt(path)
        title = names.get(raw) or prompt
        if title is None:
            continue
        started = parse_iso(meta.get("timestamp")) or mtime
        add(HistorySession("codex", raw, title, started, mtime))

    ordered = sorted(found.values(), key=lambda s: s.last_active, reverse=True)
    return ordered[:limit]
