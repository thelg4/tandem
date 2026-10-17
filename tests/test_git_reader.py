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


def test_history_and_deltas(git_repo: tuple[Path, object]) -> None:
    repo, git = git_repo
    run = git  # type: ignore[assignment]
    (repo / "a.py").write_text("one\ntwo\n")
    (repo / "img.bin").write_bytes(b"\x00\x01\x02")
    run("add", ".")
    run("commit", "-q", "-m", "c1")
    run("mv", "a.py", "b.py")
    run("commit", "-q", "-m", "rename")
    (repo / "b.py").write_text("one\ntwo\nthree\n")
    (repo / "c.py").write_text("x\n")
    run("rm", "-q", "img.bin")
    run("add", ".")
    run("commit", "-q", "-m", "c3")
    reader = GitReader(repo)
    history = reader.history(reader.resolve_commit("HEAD"), max_count=10)
    assert len(history) == 3 and history[0].parents == ()
    assert [d.status for d in reader.file_deltas(history[0])] == ["A", "A"]
    (rename,) = reader.file_deltas(history[1])
    assert (rename.status, rename.old_path, rename.path) == ("R", "a.py", "b.py")
    assert rename.old_blob == rename.new_blob
    third = {d.path: d for d in reader.file_deltas(history[2])}
    assert third["b.py"].status == "M" and third["img.bin"].new_blob is None
    assert reader.added_lines(history[2], lambda p: True) == 2
    assert reader.added_lines(history[2], lambda p: p != "c.py") == 1
    assert reader.added_lines(history[0], lambda p: True) == 2  # binary not counted
    assert len(reader.history(history[-1].oid, max_count=2)) == 2


def test_added_lines_by_commit_matches_per_commit(
    repo_with_history: tuple[Path, list[str]],
) -> None:
    repo, commits = repo_with_history
    reader = GitReader(repo)
    history = reader.history(commits[-1], max_count=10)
    bulk = reader.added_lines_by_commit(commits[-1], max_count=10, include=lambda p: True)
    assert bulk == {c.oid: reader.added_lines(c, lambda p: True) for c in history}
    assert bulk[commits[0]] == 2
