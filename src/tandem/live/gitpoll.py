"""Git polling for live watch: the file tree, HEAD, and newly changed paths.

Runs only through ``GitReader`` (hooks, fsmonitor and textconv disabled, optional locks
off), so polling never writes to the repository. Content is not hashed; a dirty path
fires again when its mtime changes between polls.
"""

from __future__ import annotations

from pathlib import Path

from tandem.git_reader import GitReader


def head(git: GitReader) -> str | None:
    result = git.run("rev-parse", "--verify", "-q", "HEAD", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode("ascii", "replace").strip() or None


def _split(out: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def repo_tree(git: GitReader, top: Path, cap: int = 20_000) -> tuple[list[dict[str, object]], bool]:
    out = git.run("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    files: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in _split(out):
        if path in seen:
            continue
        seen.add(path)
        try:
            size = (top / path).stat().st_size
        except OSError:
            continue  # deleted in the worktree
        if len(files) >= cap:
            return files, True
        files.append({"path": path, "size": size})
    return files, False


def _dirty(git: GitReader) -> list[str]:
    out = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    entries = out.split(b"\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:].decode("utf-8", "surrogateescape"))
        if entry[:1] in (b"R", b"C"):
            i += 1  # the rename source follows as its own entry
    return paths


class DirtyWatcher:
    def __init__(self, git: GitReader, top: Path) -> None:
        self._git = git
        self._top = top
        self._seen = self._snapshot()

    def _snapshot(self) -> dict[str, float]:
        snap: dict[str, float] = {}
        for path in _dirty(self._git):
            try:
                snap[path] = (self._top / path).stat().st_mtime
            except OSError:
                snap[path] = -1.0
        return snap

    def poll(self) -> list[str]:
        current = self._snapshot()
        changed = sorted(p for p, m in current.items() if self._seen.get(p) != m)
        self._seen = current
        return changed
