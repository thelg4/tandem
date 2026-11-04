"""Reconstruct an observed edit as a full before/after transition.

Given the file's current working state (the lines Tandem believes the file held when
the edit ran), produce the exact lines before and after the edit, or explain why that
is impossible.

- Claude Code edits carry the original file, so ``before`` comes from the log itself.
- Numbered hunks (Claude, Codex FileChange) are applied at their line numbers with every
  context and removed line checked. If the numbers do not fit, the hunk is located by
  its content instead.
- Codex patch text has no line numbers; each hunk is located by its content after its
  ``@@`` anchor, as Codex itself does.

A location found by content must be unique to count as exact. Several matching
locations make the transition non-unique, which caps its evidence at ``supported``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tandem.model.edits import ChangeType, Hunk, ObservedEdit


def to_lines(text: str) -> list[str]:
    """Split text into lines after normalizing line endings only."""
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


@dataclass(frozen=True, slots=True)
class Transition:
    before: list[str] | None
    """Lines before the edit; None when the file did not exist."""
    after: list[str] | None
    """Lines after the edit; None when the edit deleted the file."""
    unique: bool
    """Every hunk landed at exactly one possible location."""
    before_from_log: bool
    """The log recorded the prior content itself (Claude originalFile)."""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unplaceable:
    reason: str


class _NoMatch(Exception):
    pass


def _old_new(hunk: Hunk) -> tuple[list[str], list[str]]:
    old = [ln[1:] for ln in hunk.lines if ln[:1] in (" ", "-")]
    new = [ln[1:] for ln in hunk.lines if ln[:1] in (" ", "+")]
    return old, new


def apply_numbered(src: Sequence[str], hunks: Sequence[Hunk]) -> list[str]:
    """Apply numbered hunks at their stated lines, checking every old line exactly."""
    out: list[str] = []
    cursor = 0
    for hunk in hunks:
        if hunk.old_start is None:
            raise _NoMatch("unnumbered hunk")
        start = hunk.old_start if hunk.old_lines == 0 else hunk.old_start - 1
        old, new = _old_new(hunk)
        if (
            start < cursor
            or start + len(old) > len(src)
            or list(src[start : start + len(old)]) != old
        ):
            raise _NoMatch(f"context mismatch at line {hunk.old_start}")
        out.extend(src[cursor:start])
        out.extend(new)
        cursor = start + len(old)
    out.extend(src[cursor:])
    return out


def apply_located(src: Sequence[str], hunks: Sequence[Hunk]) -> tuple[list[str], bool]:
    """Apply hunks by locating their old lines by content. Returns (result, all_unique)."""
    out: list[str] = []
    cursor = 0
    unique = True
    for hunk in hunks:
        old, new = _old_new(hunk)
        search_from = cursor
        if hunk.anchor:
            anchor = hunk.anchor.strip()
            hits = [i for i in range(cursor, len(src)) if src[i].strip() == anchor]
            if not hits:
                raise _NoMatch("anchor not found")
            unique = unique and len(hits) == 1
            search_from = hits[0] + 1
        if not old:
            # Pure addition with no context: Codex appends after the anchor, or at the end.
            position = search_from if hunk.anchor else len(src)
        else:
            width = len(old)
            matches = [
                i
                for i in range(search_from, len(src) - width + 1)
                if list(src[i : i + width]) == old
            ]
            if not matches:
                raise _NoMatch("hunk content not found")
            unique = unique and len(matches) == 1
            position = matches[0]
        out.extend(src[cursor:position])
        out.extend(new)
        cursor = position + len(old)
    out.extend(src[cursor:])
    return out, unique


def reconstruct(edit: ObservedEdit, working: list[str] | None) -> Transition | Unplaceable:
    """Build the transition for ``edit`` given the file's working state (None = absent)."""
    if edit.change is ChangeType.DELETE:
        if working is None:
            return Unplaceable("delete_of_absent_file")
        return Transition(working, None, unique=True, before_from_log=False)

    if edit.change is ChangeType.ADD:
        if edit.after_text is None:
            return Unplaceable("add_without_content")
        over = ("add_over_existing_content",) if working else ()
        return Transition(
            working, to_lines(edit.after_text), unique=True, before_from_log=False, reasons=over
        )

    # UPDATE
    if edit.before_text is not None:
        before = to_lines(edit.before_text)
        if edit.after_text is not None:
            return Transition(before, to_lines(edit.after_text), unique=True, before_from_log=True)
        try:
            after = apply_numbered(before, edit.hunks)
        except _NoMatch:
            return Unplaceable("logged_patch_does_not_apply_to_logged_original")
        return Transition(before, after, unique=True, before_from_log=True)

    if working is None:
        return Unplaceable("update_of_absent_file")
    if not edit.hunks:
        return Unplaceable("update_without_hunks")
    if all(h.numbered for h in edit.hunks):
        try:
            after = apply_numbered(working, edit.hunks)
            return Transition(working, after, unique=True, before_from_log=False)
        except _NoMatch:
            pass  # fall through to a content search; numbers may be off by unseen changes
    try:
        located, unique = apply_located(working, edit.hunks)
    except _NoMatch as exc:
        return Unplaceable(f"hunks_do_not_match_working_state: {exc}")
    reasons: tuple[str, ...] = ("located_by_content",)
    if not unique:
        reasons += ("non_unique_location",)
    return Transition(working, located, unique=unique, before_from_log=False, reasons=reasons)
