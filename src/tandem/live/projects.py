"""Recent projects: the Git worktrees where Claude Code and Codex sessions ran.

Both agents record each session's working directory, so the desktop app can open on
the repositories you actually use instead of a blank folder picker. Read-only: log
heads are peeked, and Git only resolves each directory's top level.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from tandem.errors import TandemError
from tandem.git_reader import GitReader
from tandem.live.protocol import iso
from tandem.live.tail import peek_cwd, recent_logs
from tandem.model.events import AgentFamily

DEFAULT_DAYS = 60
MAX_LOGS = 600
"""Most recent logs looked at per agent; older activity adds little to a recents list."""


@dataclass(slots=True)
class Project:
    path: Path
    last_active: float
    sessions: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "last_active": iso(self.last_active),
            "sessions": dict(self.sessions),
        }


class _TopLevels:
    """Git top level per directory, cached; None when it is not in a repository."""

    def __init__(self) -> None:
        self._cache: dict[str, Path | None] = {}

    def get(self, cwd: str) -> Path | None:
        if cwd not in self._cache:
            self._cache[cwd] = self._resolve(cwd)
        return self._cache[cwd]

    @staticmethod
    def _resolve(cwd: str) -> Path | None:
        path = Path(cwd)
        if not path.is_dir():
            return None
        try:
            return GitReader(path, timeout=10.0).discover().top_level
        except TandemError:
            return None


def _newest(root: Path, since: float) -> list[tuple[float, Path]]:
    found: list[tuple[float, Path]] = []
    for path in recent_logs(root, since):
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(reverse=True)
    return found[:MAX_LOGS]


def recent_projects(
    claude_root: Path,
    codex_root: Path,
    *,
    now: float | None = None,
    days: int = DEFAULT_DAYS,
    limit: int = 30,
) -> list[Project]:
    """Projects ordered by their latest agent activity, newest first."""
    since = (now if now is not None else time.time()) - days * 86400
    tops = _TopLevels()
    projects: dict[Path, Project] = {}
    # A Claude project folder holds one working directory's sessions, so one peek
    # per folder is enough; subagent transcripts belong to their parent session.
    claude_dirs: dict[Path, str | None] = {}
    sources = [
        (AgentFamily.CLAUDE_CODE, "claude_code", _newest(claude_root, since)),
        (AgentFamily.CODEX, "codex", _newest(codex_root, since)),
    ]
    for family, agent, logs in sources:
        for mtime, path in logs:
            if family is AgentFamily.CLAUDE_CODE:
                if path.parent.name == "subagents":
                    continue
                if path.parent not in claude_dirs:
                    claude_dirs[path.parent] = peek_cwd(path, family)[0]
                cwd = claude_dirs[path.parent] or peek_cwd(path, family)[0]
            else:
                cwd = peek_cwd(path, family)[0]
            top = tops.get(cwd) if cwd else None
            if top is None:
                continue
            project = projects.setdefault(top, Project(top, mtime))
            project.last_active = max(project.last_active, mtime)
            project.sessions[agent] = project.sessions.get(agent, 0) + 1
    ordered = sorted(projects.values(), key=lambda p: p.last_active, reverse=True)
    return ordered[:limit]
