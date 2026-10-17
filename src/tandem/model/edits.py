"""Observed edits: what an agent's log says it changed in one file (spec §4 Content, §6 ALG-03).

An ``ObservedEdit`` is evidence, not attribution. Attribution later decides whether
the edit can be placed against a Git snapshot and what evidence class it earns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChangeType(StrEnum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


class EditSource(StrEnum):
    """Where in the provider log the edit evidence came from."""

    CLAUDE_TOOL_USE_RESULT = "claude_code.tool_use_result"
    """Original file text plus a numbered structured patch."""
    CODEX_FILE_CHANGE = "codex.file_change"
    """Numbered unified diff per file, emitted on success."""
    CODEX_PATCH_TEXT = "codex.patch_text"
    """apply_patch input only: context-anchored hunks without line numbers."""


class CallLink(StrEnum):
    """How the edit was tied to the tool call that produced it."""

    CALL_ID = "call_id"
    SOURCE_ORDER = "source_order"
    """Inferred from position in the log (e.g. Codex exec cells). Recorded, never hidden."""
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Hunk:
    """One hunk. ``lines`` keep their diff prefix: ' ' context, '-' removed, '+' added.

    Starts and counts are None when the source has no line numbers (Codex patch text);
    ``anchor`` then carries the patch's ``@@`` context line, if any.
    """

    old_start: int | None
    old_lines: int | None
    new_start: int | None
    new_lines: int | None
    lines: tuple[str, ...]
    anchor: str | None = None

    @property
    def numbered(self) -> bool:
        return self.old_start is not None and self.new_start is not None

    @property
    def removed(self) -> int:
        return sum(1 for line in self.lines if line.startswith("-"))

    @property
    def added(self) -> int:
        return sum(1 for line in self.lines if line.startswith("+"))


@dataclass(frozen=True, slots=True)
class ObservedEdit:
    path: str
    """Path as recorded by the agent (absolute or relative to ``cwd``)."""
    change: ChangeType
    source: EditSource
    hunks: tuple[Hunk, ...] = ()
    before_text: str | None = None
    """Full prior content when the log records it (Claude Code originalFile)."""
    after_text: str | None = None
    """Full resulting content when the log records it (writes and adds)."""
    move_to: str | None = None
    call_id: str | None = None
    call_link: CallLink = CallLink.NONE
    user_modified: bool = False
    """The user changed the proposed edit before it applied; do not credit it unchanged."""

    @property
    def lines_added(self) -> int:
        if self.hunks:
            return sum(h.added for h in self.hunks)
        return 0 if self.after_text is None else len(self.after_text.splitlines())

    @property
    def lines_removed(self) -> int:
        return sum(h.removed for h in self.hunks)
