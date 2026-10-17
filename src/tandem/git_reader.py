"""Bounded, read-only Git access (spec §6 ALG-02, NFR-02, NFR-03).

Rules enforced here:
- Argument arrays only; revisions and paths are validated before use.
- No checkout, fetch, index writes, hooks, external diff drivers, or textconv.
- Every call has a timeout and an output size bound.
- Commit dates are metadata, not a causal clock.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tandem.errors import GitError, InvalidInputError

MIN_GIT_VERSION = (2, 31, 0)
"""Needs `rev-parse --path-format=absolute`. Validate on CI before release (SCOPE-03)."""

_SAFE_REV = re.compile(r"^[A-Za-z0-9_./@{}^~:+-]+$")

# Config overrides applied to every invocation. These keep Git from running
# repository-controlled programs or touching the network during a scan.
_HARDENING: tuple[str, ...] = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.allow=never",
    "-c",
    "core.pager=cat",
    "-c",
    "color.ui=false",
)


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",  # never refresh the index as a side effect
        GIT_PAGER="cat",
        GIT_NO_REPLACE_OBJECTS="1",
        LC_ALL="C",
    )
    return env


@dataclass(frozen=True, slots=True)
class GitResult:
    stdout: bytes
    stderr: bytes
    returncode: int


@dataclass(frozen=True, slots=True)
class RepoInfo:
    top_level: Path
    git_dir: Path
    common_dir: Path
    is_shallow: bool
    head: str | None
    """None for a repository with no commits yet."""

    @property
    def is_linked_worktree(self) -> bool:
        return self.git_dir != self.common_dir


class GitReader:
    def __init__(
        self,
        cwd: Path,
        *,
        git: str = "git",
        timeout: float = 60.0,
        max_output_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.cwd = cwd
        self.git = git
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    # -- low level -----------------------------------------------------------------

    def run(self, *args: str, check: bool = True) -> GitResult:
        cmd = [self.git, *_HARDENING, *args]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.cwd,
                env=_env(),
                capture_output=True,
                timeout=self.timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise GitError(f"git executable not found: {self.git}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {self.timeout}s") from exc
        if len(proc.stdout) > self.max_output_bytes:
            raise GitError(f"git {args[0]} output exceeded {self.max_output_bytes} bytes")
        if check and proc.returncode != 0:
            msg = proc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"git {args[0]} failed: {msg}")
        return GitResult(proc.stdout, proc.stderr, proc.returncode)

    def _text(self, *args: str) -> str:
        return self.run(*args).stdout.decode("utf-8", "surrogateescape").strip()

    # -- environment ---------------------------------------------------------------

    def version(self) -> tuple[int, int, int]:
        out = self._text("--version")
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
        if not match:
            raise GitError(f"could not parse git version from {out!r}")
        return (int(match[1]), int(match[2]), int(match[3] or 0))

    def check_version(self) -> tuple[int, int, int]:
        found = self.version()
        if found < MIN_GIT_VERSION:
            need = ".".join(map(str, MIN_GIT_VERSION))
            raise GitError(f"git {'.'.join(map(str, found))} is older than required {need}")
        return found

    # -- repository ----------------------------------------------------------------

    def discover(self) -> RepoInfo:
        res = self.run(
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--git-dir",
            "--git-common-dir",
            "--is-shallow-repository",
            check=False,
        )
        if res.returncode != 0:
            raise GitError(f"{self.cwd} is not inside a Git work tree")
        lines = res.stdout.decode("utf-8", "surrogateescape").splitlines()
        if len(lines) < 4:
            raise GitError("unexpected rev-parse output")
        head = self.run("rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False)
        return RepoInfo(
            top_level=Path(lines[0]).resolve(),
            git_dir=Path(lines[1]).resolve(),
            common_dir=Path(lines[2]).resolve(),
            is_shallow=lines[3].strip() == "true",
            head=head.stdout.decode().strip() or None if head.returncode == 0 else None,
        )

    @staticmethod
    def validate_revision(rev: str) -> str:
        if not rev or rev.startswith("-") or not _SAFE_REV.match(rev) or ".." in rev:
            raise InvalidInputError(f"unsafe or invalid revision: {rev!r}")
        return rev

    def resolve_commit(self, rev: str = "HEAD") -> str:
        """Resolve to an immutable object ID (ALG-01). IDs are opaque strings (DB-02)."""
        self.validate_revision(rev)
        res = self.run(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}", check=False
        )
        oid = res.stdout.decode().strip()
        if res.returncode != 0 or not oid:
            raise GitError(f"revision not found: {rev}")
        return oid

    def first_parent_chain(self, rev: str, *, max_count: int | None = None) -> list[str]:
        """Commits on the first-parent chain, oldest first (ALG-01)."""
        oid = self.resolve_commit(rev)
        args = ["rev-list", "--first-parent", "--reverse"]
        if max_count is not None:
            # rev-list applies --max-count before --reverse, so this keeps the newest N.
            args.append(f"--max-count={max_count}")
        args += ["--end-of-options", oid]
        out = self._text(*args)
        return out.split() if out else []

    def read_blob(self, oid: str, *, max_bytes: int) -> bytes | None:
        """Read a blob directly from the object store. Returns None when over the bound."""
        if not re.fullmatch(r"[0-9a-f]{4,128}", oid):
            raise InvalidInputError(f"invalid object id: {oid!r}")
        size = int(self._text("cat-file", "-s", oid))
        if size > max_bytes:
            return None
        return self.run("cat-file", "blob", oid).stdout

    def worktrees(self) -> list[Path]:
        out = self._text("worktree", "list", "--porcelain")
        return [
            Path(line[len("worktree ") :])
            for line in out.splitlines()
            if line.startswith("worktree ")
        ]
