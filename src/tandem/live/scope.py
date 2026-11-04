"""Decide whether a path is inside the watched worktree, with symlinks resolved.

Agents record the paths they were given, which may go through a symlinked alias of the
repository, or through a symlink inside it that points elsewhere. Both sides are compared
as real paths, so an alias counts as inside and an escaping link counts as outside.
"""

from __future__ import annotations

import os
from pathlib import Path


class Scope:
    def __init__(self, top: Path) -> None:
        self._top = os.path.realpath(top)

    def relative(self, path: str) -> str | None:
        """The path relative to the top level, or None when it is the top or outside it."""
        real = os.path.realpath(path)
        if not real.startswith(self._top + os.sep):
            return None
        return os.path.relpath(real, self._top)

    def contains(self, path: str) -> bool:
        """True for the top level itself and anything under it."""
        return os.path.realpath(path) == self._top or self.relative(path) is not None
