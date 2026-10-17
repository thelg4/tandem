"""Parsers for the diff formats agent logs contain, plus exact hunk application.

- Unified diffs with numbered ``@@ -a,b +c,d @@`` headers (Codex FileChange).
- Claude Code ``structuredPatch`` entries.
- Codex ``*** Begin Patch`` text, whose hunks are anchored by context, not line numbers.

``apply_hunks`` places numbered hunks exactly and refuses on any context mismatch,
because a verified edit needs the expected before-content at a unique location (ALG-03).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tandem.model.edits import ChangeType, Hunk

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffParseError(ValueError):
    pass


class ApplyError(ValueError):
    pass


def parse_unified_diff(text: str) -> tuple[Hunk, ...]:
    """Parse numbered hunks; file header lines (---, +++, diff, index) are skipped."""
    hunks: list[Hunk] = []
    header: tuple[int, int, int, int] | None = None
    body: list[str] = []

    def flush() -> None:
        if header is not None:
            hunks.append(Hunk(header[0], header[1], header[2], header[3], tuple(body)))

    for line in text.splitlines():
        match = _HUNK_HEADER.match(line)
        if match:
            flush()
            old_start, old_len, new_start, new_len = match.groups()
            header = (
                int(old_start),
                int(old_len) if old_len is not None else 1,
                int(new_start),
                int(new_len) if new_len is not None else 1,
            )
            body = []
            continue
        if header is None:
            continue  # file headers before the first hunk
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line[:1] in (" ", "-", "+"):
            body.append(line)
        elif line == "":
            body.append(" ")  # some writers drop the space on empty context lines
        else:
            raise DiffParseError(f"unexpected line in hunk: {line[:40]!r}")
    flush()
    for hunk in hunks:
        _check_counts(hunk)
    return tuple(hunks)


def _check_counts(hunk: Hunk) -> None:
    old = sum(1 for ln in hunk.lines if ln[:1] in (" ", "-"))
    new = sum(1 for ln in hunk.lines if ln[:1] in (" ", "+"))
    if (old, new) != (hunk.old_lines, hunk.new_lines):
        raise DiffParseError(
            f"hunk at -{hunk.old_start} declares {hunk.old_lines}/{hunk.new_lines} lines "
            f"but has {old}/{new}"
        )


def parse_structured_patch(entries: Iterable[Mapping[str, Any]]) -> tuple[Hunk, ...]:
    """Claude Code ``toolUseResult.structuredPatch``."""
    hunks = []
    for entry in entries:
        try:
            hunk = Hunk(
                old_start=int(entry["oldStart"]),
                old_lines=int(entry["oldLines"]),
                new_start=int(entry["newStart"]),
                new_lines=int(entry["newLines"]),
                lines=tuple(str(x) for x in entry["lines"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DiffParseError(f"malformed structuredPatch entry: {exc}") from exc
        _check_counts(hunk)
        hunks.append(hunk)
    return tuple(hunks)


# -- Codex apply_patch text ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchFileOp:
    change: ChangeType
    path: str
    hunks: tuple[Hunk, ...] = ()
    added_content: str | None = None
    move_to: str | None = None


_OP = re.compile(r"^\*\*\* (Add File|Update File|Delete File): (.+)$")
_MOVE = re.compile(r"^\*\*\* Move to: (.+)$")


def parse_codex_patch(text: str) -> tuple[PatchFileOp, ...]:
    """Parse Codex ``*** Begin Patch`` text. Hunks carry no line numbers."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "*** Begin Patch")
    except StopIteration as exc:
        raise DiffParseError("no *** Begin Patch marker") from exc

    ops: list[PatchFileOp] = []
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "*** End Patch":
            return tuple(ops)
        match = _OP.match(line)
        if not match:
            raise DiffParseError(f"expected a file operation, got {line[:40]!r}")
        kind, path = match.group(1), match.group(2).strip()
        i += 1
        if kind == "Delete File":
            ops.append(PatchFileOp(ChangeType.DELETE, path))
            continue
        if kind == "Add File":
            added = []
            while i < len(lines) and lines[i].startswith("+"):
                added.append(lines[i][1:])
                i += 1
            content = "\n".join(added) + ("\n" if added else "")
            ops.append(PatchFileOp(ChangeType.ADD, path, added_content=content))
            continue
        move_to = None
        if i < len(lines) and (m := _MOVE.match(lines[i])):
            move_to = m.group(1).strip()
            i += 1
        hunks, i = _parse_update_hunks(lines, i)
        ops.append(PatchFileOp(ChangeType.UPDATE, path, hunks, move_to=move_to))
    raise DiffParseError("missing *** End Patch")


def _parse_update_hunks(lines: Sequence[str], i: int) -> tuple[tuple[Hunk, ...], int]:
    """Read context-anchored hunks until the next ``*** `` line. Returns (hunks, next index)."""
    hunks: list[Hunk] = []
    anchor: str | None = None
    body: list[str] = []
    while i < len(lines) and not lines[i].startswith("*** "):
        cur = lines[i]
        if cur.startswith("@@"):
            if body:
                hunks.append(Hunk(None, None, None, None, tuple(body), anchor))
            body = []
            anchor = cur[2:].strip() or None
        elif cur[:1] in (" ", "-", "+"):
            body.append(cur)
        elif cur == "":
            body.append(" ")
        else:
            raise DiffParseError(f"unexpected line in update hunk: {cur[:40]!r}")
        i += 1
    if body:
        hunks.append(Hunk(None, None, None, None, tuple(body), anchor))
    return tuple(hunks), i


# -- application ---------------------------------------------------------------------------


def split_lines(text: str) -> list[str]:
    """Split keeping no terminators; line endings are normalized (ALG-06)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def apply_hunks(before: str, hunks: Sequence[Hunk]) -> str:
    """Apply numbered hunks exactly. Any context or removal mismatch raises ApplyError."""
    src = split_lines(before)
    out: list[str] = []
    cursor = 0  # index into src
    for hunk in hunks:
        if not hunk.numbered or hunk.old_start is None:
            raise ApplyError("hunk has no line numbers")
        # A zero-length old range (pure insertion) names the line *before* the insertion.
        start = hunk.old_start if hunk.old_lines == 0 else hunk.old_start - 1
        if start < cursor or start > len(src):
            raise ApplyError(f"hunk at -{hunk.old_start} is out of order or out of range")
        out.extend(src[cursor:start])
        pos = start
        for line in hunk.lines:
            tag, body = line[:1], line[1:]
            if tag in (" ", "-"):
                if pos >= len(src) or src[pos] != body:
                    raise ApplyError(f"context mismatch at line {pos + 1}")
                if tag == " ":
                    out.append(body)
                pos += 1
            elif tag == "+":
                out.append(body)
        cursor = pos
    out.extend(src[cursor:])
    return "\n".join(out)
