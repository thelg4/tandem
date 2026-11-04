"""Source discovery.

Discovery only lists candidate files. Matching a source to a repository uses
recorded working directories and file paths inside the records, resolved
against the Git root. Encoded folder names are never trusted.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tandem.model.events import AgentFamily
from tandem.paths import default_claude_root, default_codex_root


@dataclass(frozen=True, slots=True)
class SourceRoot:
    agent: AgentFamily
    path: Path
    explicit: bool

    @property
    def available(self) -> bool:
        return self.path.is_dir()


@dataclass(frozen=True, slots=True)
class SourceFile:
    agent: AgentFamily
    path: Path
    size: int


def resolve_roots(
    claude_root: Path | None = None, codex_root: Path | None = None
) -> list[SourceRoot]:
    return [
        SourceRoot(
            AgentFamily.CLAUDE_CODE, claude_root or default_claude_root(), claude_root is not None
        ),
        SourceRoot(AgentFamily.CODEX, codex_root or default_codex_root(), codex_root is not None),
    ]


def iter_source_files(root: SourceRoot) -> Iterator[SourceFile]:
    """Yield JSONL files under an approved root without following symlinks out of it."""
    if not root.available:
        return
    base = root.path.resolve()
    for path in sorted(base.rglob("*.jsonl")):
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(base) or not resolved.is_file():
            continue
        yield SourceFile(root.agent, resolved, resolved.stat().st_size)
