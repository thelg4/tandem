"""Per-edit diffs for one file, read on demand from Claude Code and Codex logs.

The desktop app asks for these when you open a file; they are never part of the live
watch stream. Only edits that succeeded are returned:

- Claude Code: the ``toolUseResult`` of an ``Edit``/``Write``/``MultiEdit`` call that
  did not error. Its ``structuredPatch`` carries numbered hunks; a created file has
  none, so its content is shown as added lines.
- Codex: ``FileChange`` items (numbered unified diffs, or the content of an added
  file). Older versions log only ``apply_patch`` text, whose hunks carry no line
  numbers; such a call counts when its output reports success and no ``FileChange``
  answered it.

Read-only and offline. Log lines that do not mention the file's name are skipped
before parsing, so a week of logs reads quickly.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tandem.adapters.codex import _output_status
from tandem.live.protocol import clean, iso, parse_iso
from tandem.live.scope import Scope
from tandem.live.tail import peek_cwd, recent_logs
from tandem.model.events import ActionStatus, AgentFamily

DEFAULT_DAYS = 7
MAX_LINES_PER_EDIT = 400
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")


@dataclass(slots=True)
class Hunk:
    old_start: int | None
    new_start: int | None
    lines: list[str] = field(default_factory=list)
    """Each line starts with ' ', '-' or '+'."""


@dataclass(slots=True)
class FileEdit:
    agent: str
    session_id: str
    at: float
    tool: str
    change: str
    """update, create or delete."""
    hunks: list[Hunk]
    title: str | None = None
    user_modified: bool = False
    truncated: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "title": self.title,
            "at": iso(self.at),
            "tool": self.tool,
            "change": self.change,
            "user_modified": self.user_modified,
            "truncated": self.truncated,
            "hunks": [
                {"old_start": h.old_start, "new_start": h.new_start, "lines": h.lines}
                for h in self.hunks
            ],
        }


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cap(hunks: list[Hunk]) -> tuple[list[Hunk], bool]:
    """At most MAX_LINES_PER_EDIT lines in total; long lines are shortened for display."""
    out: list[Hunk] = []
    budget = MAX_LINES_PER_EDIT
    for h in hunks:
        if budget <= 0:
            return out, True
        lines = [line[:2000] for line in h.lines[:budget]]
        budget -= len(lines)
        out.append(Hunk(h.old_start, h.new_start, lines))
        if len(lines) < len(h.lines):
            return out, True
    return out, False


def _added(content: str) -> list[Hunk]:
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [Hunk(0, 1, ["+" + line for line in lines])]


def parse_unified(diff: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for line in diff.split("\n"):
        m = _HUNK.match(line)
        if m:
            hunks.append(Hunk(int(m[1]), int(m[3])))
        elif hunks and line[:1] in (" ", "-", "+"):
            hunks[-1].lines.append(line)
    return hunks


def parse_patch_sections(patch: str) -> Iterator[tuple[str, str, list[Hunk]]]:
    """(action, path, hunks) per file in ``*** Begin Patch`` text; hunks have no numbers."""
    action: str | None = None
    path = ""
    hunks: list[Hunk] = []
    for line in patch.split("\n"):
        m = _PATCH_FILE.match(line)
        if m or line.startswith("*** End Patch"):
            if action is not None:
                yield action, path, hunks
            action, path, hunks = (m[1], m[2], []) if m else (None, "", [])
            continue
        if action is None or line.startswith("*** "):
            continue
        if line.startswith("@@"):
            hunks.append(Hunk(None, None))
        elif line[:1] in (" ", "-", "+"):
            if not hunks:
                hunks.append(Hunk(None, None))
            hunks[-1].lines.append(line)
    if action is not None:
        yield action, path, hunks


class _Target:
    """The file being looked up, matched however a log spells its path."""

    def __init__(self, top: Path, rel: str) -> None:
        self.scope = Scope(top)
        self.rel = rel
        self.needle = os.path.basename(rel).encode()

    def matches(self, path: str, base: str | None) -> bool:
        joined = path if os.path.isabs(path) or base is None else os.path.join(base, path)
        return self.scope.relative(os.path.normpath(joined)) == self.rel


def _claude_edits(path: Path, sid: str, target: _Target) -> list[FileEdit]:
    edits: list[FileEdit] = []
    title: str | None = None
    try:
        fh = path.open("rb")
    except OSError:
        return edits
    with fh:
        for line in fh:
            if b'"ai-title"' in line:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                raw = rec.get("aiTitle") if isinstance(rec, dict) else None
                title = clean(raw, 80) if isinstance(raw, str) and raw else title
                continue
            if target.needle not in line or b'"toolUseResult"' not in line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict) or rec.get("type") != "user":
                continue
            result = _dict(rec.get("toolUseResult"))
            file_path = result.get("filePath")
            at = parse_iso(rec.get("timestamp"))
            if not isinstance(file_path, str) or at is None:
                continue
            if not target.matches(
                file_path, rec.get("cwd") if isinstance(rec.get("cwd"), str) else None
            ):
                continue
            content = _dict(rec.get("message")).get("content")
            blocks = (
                [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            )
            if any(b.get("type") == "tool_result" and b.get("is_error") is True for b in blocks):
                continue
            patch = result.get("structuredPatch")
            hunks = [
                Hunk(
                    h.get("oldStart") if isinstance(h.get("oldStart"), int) else None,
                    h.get("newStart") if isinstance(h.get("newStart"), int) else None,
                    [s for s in h.get("lines", []) if isinstance(s, str)],
                )
                for h in (patch if isinstance(patch, list) else [])
                if isinstance(h, dict)
            ]
            created = result.get("type") == "create"
            if created and not hunks and isinstance(result.get("content"), str):
                hunks = _added(result["content"])
            if not hunks:
                continue
            tool = (
                "Edit" if "oldString" in result else "MultiEdit" if "edits" in result else "Write"
            )
            hunks, truncated = _cap(hunks)
            edits.append(
                FileEdit(
                    "claude_code",
                    sid,
                    at,
                    tool,
                    "create" if created else "update",
                    hunks,
                    user_modified=result.get("userModified") is True,
                    truncated=truncated,
                )
            )
    for edit in edits:
        edit.title = title
    return edits


def _codex_edits(path: Path, sid: str, cwd: str | None, target: _Target) -> list[FileEdit]:
    edits: list[FileEdit] = []
    patches: dict[str, tuple[float, list[tuple[str, list[Hunk]]]]] = {}
    """apply_patch call id -> (time, matching file sections), awaiting its output."""
    patch_only: list[tuple[str, FileEdit]] = []
    """Edits known only from patch text, by call id; dropped if a FileChange answered."""
    answered: set[str] = set()
    try:
        fh = path.open("rb")
    except OSError:
        return edits
    with fh:
        for line in fh:
            is_output = b"_call_output" in line
            if target.needle not in line and not is_output and b'"turn_context"' not in line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            payload = _dict(rec.get("payload")) if isinstance(rec, dict) else {}
            at = parse_iso(rec.get("timestamp")) if isinstance(rec, dict) else None
            kind = rec.get("type") if isinstance(rec, dict) else None
            if kind == "turn_context":
                cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else cwd
                continue
            if at is None:
                continue
            ptype = payload.get("type")
            if kind == "event_msg" and ptype == "item_completed":
                item = _dict(payload.get("item"))
                if item.get("type") != "FileChange":
                    continue
                if isinstance(item.get("id"), str):
                    answered.add(item["id"])
                for file_path, change in _dict(item.get("changes")).items():
                    change = _dict(change)
                    moved = change.get("move_path")
                    hit = target.matches(file_path, cwd) or (
                        isinstance(moved, str) and target.matches(moved, cwd)
                    )
                    if not isinstance(file_path, str) or not hit:
                        continue
                    ctype = change.get("type")
                    if ctype == "add" and isinstance(change.get("content"), str):
                        hunks, label = _added(change["content"]), "create"
                    elif ctype == "delete":
                        hunks, label = [], "delete"
                    else:
                        diff = change.get("unified_diff")
                        hunks, label = (
                            parse_unified(diff if isinstance(diff, str) else ""),
                            "update",
                        )
                    hunks, truncated = _cap(hunks)
                    edits.append(
                        FileEdit("codex", sid, at, "apply_patch", label, hunks, truncated=truncated)
                    )
            elif kind == "response_item" and ptype in ("custom_tool_call", "function_call"):
                if payload.get("name") != "apply_patch":
                    continue
                call_id = payload.get("call_id")
                text, workdir = _patch_text(payload)
                sections = [
                    (action, hunks)
                    for action, file_path, hunks in parse_patch_sections(text)
                    if target.matches(file_path, workdir or cwd)
                ]
                if isinstance(call_id, str) and sections:
                    patches[call_id] = (at, sections)
            elif kind == "response_item" and ptype in (
                "custom_tool_call_output",
                "function_call_output",
            ):
                call_id = payload.get("call_id")
                if not isinstance(call_id, str):
                    continue
                pending = patches.pop(call_id, None)
                if (
                    pending is None
                    or _output_status(payload.get("output")) is not ActionStatus.SUCCEEDED
                ):
                    continue
                call_at, sections = pending
                for action, hunks in sections:
                    label = {"Add": "create", "Delete": "delete"}.get(action, "update")
                    capped, truncated = _cap(hunks)
                    edit = FileEdit(
                        "codex", sid, call_at, "apply_patch", label, capped, truncated=truncated
                    )
                    patch_only.append((call_id, edit))
    return edits + [edit for call_id, edit in patch_only if call_id not in answered]


def _patch_text(payload: dict[str, Any]) -> tuple[str, str | None]:
    if payload.get("type") == "custom_tool_call":
        raw = payload.get("input")
        return (raw if isinstance(raw, str) else ""), None
    try:
        args = json.loads(payload.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError):
        return "", None
    if not isinstance(args, dict):
        return "", None
    text = args.get("input") or args.get("patch")
    workdir = args.get("workdir")
    return (text if isinstance(text, str) else ""), (workdir if isinstance(workdir, str) else None)


def file_edits(
    top: Path,
    rel: str,
    claude_root: Path,
    codex_root: Path,
    *,
    now: float | None = None,
    days: int = DEFAULT_DAYS,
    limit: int = 50,
) -> list[FileEdit]:
    """Successful agent edits to ``rel`` (relative to the worktree ``top``), newest first."""
    since = (now if now is not None else time.time()) - days * 86400
    target = _Target(top, rel)
    edits: list[FileEdit] = []
    for path in recent_logs(claude_root, since):
        cwd = peek_cwd(path, AgentFamily.CLAUDE_CODE)[0]
        if cwd is None or not target.scope.contains(cwd):
            continue
        # A subagent transcript lives in <session>/subagents/ and counts as its session.
        raw = path.parent.parent.name if path.parent.name == "subagents" else path.stem
        edits.extend(_claude_edits(path, f"claude_code:{raw}", target))
    for path in recent_logs(codex_root, since):
        cwd, meta = peek_cwd(path, AgentFamily.CODEX)
        if meta is None or cwd is None or not target.scope.contains(cwd):
            continue
        root = meta.get("session_id") or meta.get("id")
        sid = f"codex:{root if isinstance(root, str) and root else path.stem}"
        edits.extend(_codex_edits(path, sid, cwd, target))
    edits = [e for e in edits if e.at >= since]
    edits.sort(key=lambda e: e.at, reverse=True)
    return edits[:limit]
