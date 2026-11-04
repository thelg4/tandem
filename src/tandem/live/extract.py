"""Incremental activity extraction for live watch.

These are deliberately separate from the scan adapters: adapters parse a whole source
generation and only produce edits from successful results, while live watch needs the
intent at call time ("Claude is about to edit X") from records as they arrive. Nothing
here feeds attribution or metrics.

Paths are returned absolute and normalized; the caller decides what is in scope.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from tandem.live.protocol import parse_iso

Kind = Literal["read", "edit", "shell", "prompt"]
Phase = Literal["start", "done", "failed"]


@dataclass(frozen=True, slots=True)
class Activity:
    kind: Kind
    phase: Phase
    paths: tuple[str, ...]
    tool: str
    at: float | None
    call_id: str


@dataclass(frozen=True, slots=True)
class Turn:
    working: bool
    at: float | None


@dataclass(frozen=True, slots=True)
class Meta:
    cwd: str | None = None
    model: str | None = None
    title: str | None = None
    root_session: str | None = None
    """Codex root thread id; subagent threads fold into it."""
    originator: str | None = None


Signal = Activity | Turn | Meta

CLAUDE_READ = frozenset({"Read", "Glob", "Grep", "NotebookRead", "LS"})
CLAUDE_EDIT = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
CLAUDE_SHELL = frozenset({"Bash"})
CODEX_SHELL = frozenset(
    {"exec", "exec_command", "shell", "shell_command", "local_shell", "container.exec"}
)
_PATCH_PATH = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$", re.M)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _abs(base: str | None, path: str) -> str:
    joined = path if os.path.isabs(path) or base is None else os.path.join(base, path)
    return os.path.normpath(joined)


@dataclass(slots=True)
class _Call:
    kind: Kind | None
    paths: tuple[str, ...]
    tool: str


class ClaudeExtractor:
    def __init__(self) -> None:
        self._calls: dict[str, _Call] = {}
        self._cwd: str | None = None
        self._model: str | None = None

    @property
    def pending(self) -> int:
        return len(self._calls)

    def feed(self, rec: dict[str, Any]) -> list[Signal]:
        kind = rec.get("type")
        at = parse_iso(rec.get("timestamp"))
        out: list[Signal] = []
        if kind == "ai-title":
            title = _str(rec.get("aiTitle"))
            return [Meta(title=title)] if title else []
        if kind == "system":
            if rec.get("subtype") == "turn_duration":
                out.append(Turn(False, at))
            return out
        if kind not in ("user", "assistant"):
            return out
        raw = rec.get("message")
        message: dict[str, Any] = raw if isinstance(raw, dict) else {}
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        meta = self._meta(_str(rec.get("cwd")), _str(message.get("model")))
        if kind == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use":
                    out.extend(self._use(block, at))
            if message.get("stop_reason") == "end_turn" and not self._calls:
                out.append(Turn(False, at))
        elif rec.get("isMeta") is not True:
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for block in results:
                out.extend(self._result(block, at))
            if isinstance(content, str) or (
                not results and any(b.get("type") == "text" for b in blocks)
            ):
                out.append(Turn(True, at))
                out.append(Activity("prompt", "start", (), "prompt", at, ""))
        out.extend(meta)
        return out

    def _meta(self, cwd: str | None, model: str | None) -> list[Signal]:
        changed_cwd = cwd if cwd and cwd != self._cwd else None
        changed_model = model if model and model != self._model else None
        self._cwd = cwd or self._cwd
        self._model = model or self._model
        if changed_cwd or changed_model:
            return [Meta(cwd=changed_cwd, model=changed_model)]
        return []

    def _use(self, block: dict[str, Any], at: float | None) -> list[Signal]:
        name = _str(block.get("name")) or "?"
        tool_id = _str(block.get("id")) or ""
        raw = block.get("input")
        inp: dict[str, Any] = raw if isinstance(raw, dict) else {}
        kind: Kind | None = None
        paths: tuple[str, ...] = ()
        target = (
            _str(inp.get("file_path")) or _str(inp.get("path")) or _str(inp.get("notebook_path"))
        )
        if name in CLAUDE_READ:
            kind = "read"
            paths = (_abs(self._cwd, target or "."),)
        elif name in CLAUDE_EDIT:
            kind = "edit"
            paths = (_abs(self._cwd, target),) if target else ()
        elif name in CLAUDE_SHELL:
            kind = "shell"
        if tool_id:
            self._calls[tool_id] = _Call(kind, paths, name)
        return [Activity(kind, "start", paths, name, at, tool_id)] if kind else []

    def _result(self, block: dict[str, Any], at: float | None) -> list[Signal]:
        call = self._calls.pop(_str(block.get("tool_use_id")) or "", None)
        if call is None or call.kind not in ("edit", "shell"):
            return []
        phase: Phase = "failed" if block.get("is_error") is True else "done"
        call_id = str(block.get("tool_use_id"))
        return [Activity(call.kind, phase, call.paths, call.tool, at, call_id)]


class CodexExtractor:
    def __init__(self) -> None:
        self._calls: dict[str, _Call] = {}
        self._cwd: str | None = None
        self.last_at: float | None = None

    @property
    def pending(self) -> int:
        return len(self._calls)

    def feed(self, rec: dict[str, Any]) -> list[Signal]:
        kind = rec.get("type")
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            return []
        at = parse_iso(rec.get("timestamp"))
        self.last_at = at
        if kind == "session_meta":
            self._cwd = _str(payload.get("cwd"))
            root = _str(payload.get("session_id")) or _str(payload.get("id"))
            return [
                Meta(cwd=self._cwd, root_session=root, originator=_str(payload.get("originator")))
            ]
        if kind == "turn_context":
            self._cwd = _str(payload.get("cwd")) or self._cwd
            return [Meta(cwd=_str(payload.get("cwd")), model=_str(payload.get("model")))]
        if kind == "response_item":
            return self._item(payload, at)
        if kind == "event_msg":
            return self._event(payload, at)
        return []

    def _item(self, p: dict[str, Any], at: float | None) -> list[Signal]:
        ptype = p.get("type")
        call_id = _str(p.get("call_id")) or _str(p.get("id")) or ""
        if ptype in ("function_call_output", "custom_tool_call_output"):
            call = self._calls.pop(call_id, None)
            if call is not None and call.kind in ("edit", "shell"):
                return [Activity(call.kind, "done", call.paths, call.tool, at, call_id)]
            return []
        if ptype not in ("function_call", "custom_tool_call", "local_shell_call"):
            return []
        name = _str(p.get("name")) or ("local_shell" if ptype == "local_shell_call" else "?")
        kind: Kind | None = None
        paths: tuple[str, ...] = ()
        if name == "apply_patch" or (ptype == "function_call" and self._is_patch_command(p)):
            patch, workdir = self._patch_input(p)
            base = workdir or self._cwd
            kind, paths = "edit", tuple(_abs(base, m) for m in _PATCH_PATH.findall(patch))
            name = "apply_patch"
        elif name in CODEX_SHELL:
            kind = "shell"
        if call_id:
            self._calls[call_id] = _Call(kind, paths, name)
        return [Activity(kind, "start", paths, name, at, call_id)] if kind else []

    @staticmethod
    def _args(p: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(p.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _is_patch_command(self, p: dict[str, Any]) -> bool:
        command = self._args(p).get("command")
        return isinstance(command, list) and bool(command) and command[0] == "apply_patch"

    def _patch_input(self, p: dict[str, Any]) -> tuple[str, str | None]:
        if p.get("type") == "custom_tool_call":
            return _str(p.get("input")) or "", None
        args = self._args(p)
        command = args.get("command")
        patch = _str(args.get("input")) or _str(args.get("patch"))
        if patch is None and isinstance(command, list) and len(command) > 1:
            patch = _str(command[1])
        return patch or "", _str(args.get("workdir"))

    def _event(self, p: dict[str, Any], at: float | None) -> list[Signal]:
        etype = p.get("type")
        if etype == "task_started":
            return [Turn(True, at)]
        if etype in ("task_complete", "turn_aborted"):
            return [Turn(False, at)]
        if etype == "user_message":
            return [Activity("prompt", "start", (), "prompt", at, "")]
        if etype == "item_completed":
            item = p.get("item")
            if isinstance(item, dict) and item.get("type") == "FileChange":
                changes = item.get("changes")
                item_id = _str(item.get("id")) or ""
                if isinstance(changes, dict):
                    return [
                        Activity(
                            "edit", "done", (_abs(self._cwd, path),), "FileChange", at, item_id
                        )
                        for path in changes
                        if isinstance(path, str) and path
                    ]
        return []
