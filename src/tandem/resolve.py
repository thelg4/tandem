"""Match agent events to the analyzed repository (spec §3 ING-02, ING-03).

Paths come from what the log records: absolute edit paths, or relative paths joined
to the event's effective working directory. Encoded folder names and substring
matches are never used. Each path gets a resolution status and, when it is not
resolved, a reason code.

Symlinked locations (macOS ``/tmp`` vs ``/private/tmp``) are handled by also comparing
against the canonical repository root. On a case-insensitive filesystem, comparison
is case-insensitive.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from tandem.model.events import Event, PathResolution


@dataclass(frozen=True, slots=True)
class WorktreeRef:
    worktree_id: str
    root: str
    """Normalized absolute path of the worktree's top level."""


@dataclass(frozen=True, slots=True)
class Resolution:
    status: PathResolution
    relative_path: str | None = None
    worktree_id: str | None = None
    reason: str | None = None


def worktree_id(path: Path) -> str:
    return "wt-" + hashlib.sha256(str(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _case_insensitive(path: Path) -> bool:
    """True when the filesystem holding ``path`` ignores case (default macOS APFS)."""
    name = path.name
    swapped = name.swapcase()
    if swapped == name:
        return False
    try:
        return path.with_name(swapped).exists() and path.exists()
    except OSError:
        return False


class RepoResolver:
    def __init__(
        self,
        repo_id: str,
        primary: Path,
        *,
        siblings: list[Path] | None = None,
        include_siblings: bool = False,
    ) -> None:
        self.repo_id = repo_id
        self.fold = _case_insensitive(primary)
        self.primary = self._ref(primary)
        self.siblings = [self._ref(p) for p in (siblings or []) if p != primary]
        self.include_siblings = include_siblings
        self._roots: list[tuple[str, WorktreeRef]] = []
        for ref in [self.primary, *self.siblings]:
            for variant in {ref.root, os.path.realpath(ref.root)}:
                self._roots.append((self._key(variant), ref))
        # Longest first, so a nested worktree wins over its parent directory.
        self._roots.sort(key=lambda item: len(item[0]), reverse=True)

    def _ref(self, path: Path) -> WorktreeRef:
        return WorktreeRef(worktree_id(path), os.path.normpath(str(path)))

    def _key(self, path: str) -> str:
        return path.casefold() if self.fold else path

    def _match(self, absolute: str) -> tuple[WorktreeRef, str] | None:
        candidates = {os.path.normpath(absolute)}
        parent = os.path.dirname(absolute)
        if os.path.isdir(parent):
            candidates.add(os.path.join(os.path.realpath(parent), os.path.basename(absolute)))
        for candidate in candidates:
            key = self._key(candidate)
            for root_key, ref in self._roots:
                if key == root_key:
                    return ref, ""
                if key.startswith(root_key + os.sep):
                    rel = candidate[len(root_key) + 1 :]
                    return ref, PurePosixPath(*Path(rel).parts).as_posix()
        return None

    def resolve(self, recorded: str | None, cwd: str | None) -> Resolution:
        if not recorded:
            return Resolution(PathResolution.UNRESOLVED, reason="no_path")
        if "\0" in recorded:
            return Resolution(PathResolution.UNRESOLVED, reason="invalid_path")
        if os.path.isabs(recorded):
            absolute = recorded
        elif cwd and os.path.isabs(cwd):
            absolute = os.path.join(cwd, recorded)
        else:
            return Resolution(PathResolution.UNRESOLVED, reason="relative_path_without_cwd")
        match = self._match(os.path.normpath(absolute))
        if match is None:
            return Resolution(PathResolution.OUTSIDE_REPO, reason="outside_repository")
        ref, rel = match
        if rel == "":
            return Resolution(
                PathResolution.UNRESOLVED, ref.worktree_id, reason="path_is_repo_root"
            )
        if rel.split("/", 1)[0] == ".git":
            return Resolution(PathResolution.OUTSIDE_REPO, reason="git_directory")
        if ref is not self.primary and not self.include_siblings:
            return Resolution(PathResolution.OUTSIDE_REPO, reason="sibling_worktree_not_selected")
        return Resolution(PathResolution.RESOLVED, rel, ref.worktree_id)

    def cwd_in_repo(self, cwd: str | None) -> WorktreeRef | None:
        if not cwd or not os.path.isabs(cwd):
            return None
        match = self._match(os.path.normpath(cwd))
        if match is None:
            return None
        ref = match[0]
        return ref if ref is self.primary or self.include_siblings else None

    def apply(self, event: Event) -> tuple[Event, Resolution | None]:
        """Return the event with repository scope filled in, plus the edit path resolution."""
        scope = event.scope
        cwd_ref = self.cwd_in_repo(scope.effective_cwd)
        resolution = None
        if event.edit is not None:
            resolution = self.resolve(event.edit.path, scope.effective_cwd)
            in_repo = resolution.status is PathResolution.RESOLVED
            new_scope = replace(
                scope,
                relative_path=resolution.relative_path,
                path_resolution=resolution.status,
                repo_id=self.repo_id if in_repo else None,
                worktree_id=resolution.worktree_id if in_repo else None,
            )
        elif cwd_ref is not None:
            new_scope = replace(scope, repo_id=self.repo_id, worktree_id=cwd_ref.worktree_id)
        else:
            return event, None
        return replace(event, scope=new_scope), resolution


@dataclass(frozen=True, slots=True)
class Segment:
    """A contiguous run of one session's events inside the repository (ING-03)."""

    session_id: str
    worktree_id: str | None
    first_sequence: int
    last_sequence: int


def segments(events: list[Event], resolver: RepoResolver) -> list[Segment]:
    """Group in-repo events into per-session contiguous segments, in source order.

    An event is in the repository when its edit resolved there or its working directory
    is inside it. A segment ends when the session's working directory is known to be
    outside the repository; events with no working directory neither extend nor end it.
    """
    out: list[Segment] = []
    current: dict[str, Segment] = {}
    for event in events:
        sid = event.actor.session_id
        cwd = event.scope.effective_cwd
        cwd_ref = resolver.cwd_in_repo(cwd)
        open_seg = current.get(sid)
        if event.scope.repo_id is not None or cwd_ref is not None:
            wt = event.scope.worktree_id or (cwd_ref.worktree_id if cwd_ref else None)
            seq = event.order.sequence
            if open_seg is not None and open_seg.worktree_id == wt:
                current[sid] = replace(open_seg, last_sequence=seq)
            else:
                if open_seg is not None:
                    out.append(open_seg)
                current[sid] = Segment(sid, wt, seq, seq)
        elif cwd is not None and open_seg is not None:
            out.append(open_seg)
            del current[sid]
    out.extend(current.values())
    out.sort(key=lambda s: (s.session_id, s.first_sequence))
    return out
