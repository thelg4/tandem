from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tandem.errors import GitError, InvalidInputError
from tandem.git_reader import GitReader


def _snapshot(repo: Path) -> dict[str, bytes]:
    """Every file under .git plus the worktree, to prove reads mutate nothing."""
    return {
        str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*")) if p.is_file()
    }


def test_discover(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, commits = repo_with_history
    info = GitReader(repo).discover()
    assert info.top_level == repo.resolve()
    assert info.head == commits[-1]
    assert not info.is_shallow
    assert not info.is_linked_worktree


def test_first_parent_chain_oldest_first(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, commits = repo_with_history
    assert GitReader(repo).first_parent_chain("HEAD") == commits


def test_read_blob_respects_bound(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, _ = repo_with_history
    git = GitReader(repo)
    oid = git.run("rev-parse", "HEAD:app.py").stdout.decode().strip()
    assert git.read_blob(oid, max_bytes=1024) == (repo / "app.py").read_bytes()
    assert git.read_blob(oid, max_bytes=2) is None


@pytest.mark.parametrize("rev", ["--output=/tmp/x", "HEAD;rm", "a..b", "", "$(id)"])
def test_unsafe_revisions_rejected(rev: str) -> None:
    with pytest.raises(InvalidInputError):
        GitReader.validate_revision(rev)


def test_unknown_revision(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, _ = repo_with_history
    with pytest.raises(GitError):
        GitReader(repo).resolve_commit("does-not-exist")


def test_not_a_repository(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        GitReader(tmp_path).discover()


def test_reads_do_not_mutate_repository(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, _ = repo_with_history
    (repo / "app.py").write_text("dirty worktree\n")  # index is now stale
    before = _snapshot(repo)
    git = GitReader(repo)
    git.discover()
    git.first_parent_chain("HEAD")
    git.worktrees()
    assert _snapshot(repo) == before


def test_hooks_never_run(repo_with_history: tuple[Path, list[str]], tmp_path: Path) -> None:
    repo, _ = repo_with_history
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", f"touch {marker}"], cwd=repo, check=True)
    git = GitReader(repo)
    git.discover()
    git.first_parent_chain("HEAD")
    assert not marker.exists()
