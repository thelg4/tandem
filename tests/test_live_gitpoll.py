from __future__ import annotations

import os
from pathlib import Path

from conftest import GitRunner
from tandem.git_reader import GitReader
from tandem.live.gitpoll import DirtyWatcher, head, repo_tree


def test_tree_dirty_and_head(git_repo: tuple[Path, GitRunner]) -> None:
    repo, git = git_repo
    reader = GitReader(repo)
    assert head(reader) is None
    (repo / "f.txt").write_text("hello\n")
    git("add", "f.txt")
    git("commit", "-q", "-m", "one")
    assert head(reader) == git("rev-parse", "HEAD")

    files, truncated = repo_tree(reader, repo)
    assert files == [{"path": "f.txt", "size": 6}] and not truncated

    watcher = DirtyWatcher(reader, repo)
    assert watcher.poll() == []
    (repo / "f.txt").write_text("changed\n")
    assert watcher.poll() == ["f.txt"]
    assert watcher.poll() == []
    later = os.stat(repo / "f.txt").st_mtime + 5
    os.utime(repo / "f.txt", (later, later))
    assert watcher.poll() == ["f.txt"]

    (repo / "new dir").mkdir()
    (repo / "new dir" / "n.py").write_text("x")
    assert watcher.poll() == ["new dir/n.py"]
    files, _ = repo_tree(reader, repo)
    assert {f["path"] for f in files} == {"f.txt", "new dir/n.py"}


def test_tree_cap(git_repo: tuple[Path, GitRunner]) -> None:
    repo, _ = git_repo
    for i in range(5):
        (repo / f"{i}.txt").write_text("x")
    files, truncated = repo_tree(GitReader(repo), repo, cap=3)
    assert len(files) == 3 and truncated
