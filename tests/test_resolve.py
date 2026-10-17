from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from adapter_helpers import of_type, parse_fixture
from tandem.adapters import ClaudeCodeAdapter
from tandem.model.events import EventType, PathResolution
from tandem.resolve import RepoResolver, segments


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "demo"
    (root / "src").mkdir(parents=True)
    return root


def test_absolute_path_inside_repo(repo: Path) -> None:
    r = RepoResolver("r1", repo).resolve(str(repo / "src" / "app.py"), None)
    assert r.status is PathResolution.RESOLVED and r.relative_path == "src/app.py"


def test_relative_path_uses_cwd(repo: Path) -> None:
    resolver = RepoResolver("r1", repo)
    assert resolver.resolve("app.py", str(repo / "src")).relative_path == "src/app.py"
    assert resolver.resolve("../demo/x.py", str(repo)).relative_path == "x.py"
    missing = resolver.resolve("app.py", None)
    assert missing.status is PathResolution.UNRESOLVED
    assert missing.reason == "relative_path_without_cwd"


def test_outside_and_traversal(repo: Path, tmp_path: Path) -> None:
    resolver = RepoResolver("r1", repo)
    assert resolver.resolve(str(tmp_path / "other.py"), None).status is PathResolution.OUTSIDE_REPO
    escaped = resolver.resolve("../../etc/passwd", str(repo))
    assert escaped.status is PathResolution.OUTSIDE_REPO
    # A sibling directory sharing a name prefix is not inside the repo.
    assert resolver.resolve(str(repo) + "-old/a.py", None).status is PathResolution.OUTSIDE_REPO


def test_git_directory_is_excluded(repo: Path) -> None:
    r = RepoResolver("r1", repo).resolve(str(repo / ".git" / "config"), None)
    assert r.status is PathResolution.OUTSIDE_REPO and r.reason == "git_directory"


def test_symlinked_location_resolves(repo: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(repo)
    resolver = RepoResolver("r1", repo)
    assert resolver.resolve(str(link / "src" / "app.py"), None).relative_path == "src/app.py"


def test_sibling_worktree_needs_opt_in(repo: Path, tmp_path: Path) -> None:
    sibling = tmp_path / "work" / "demo-feature"
    sibling.mkdir()
    path = str(sibling / "a.py")
    default = RepoResolver("r1", repo, siblings=[sibling]).resolve(path, None)
    assert default.status is PathResolution.OUTSIDE_REPO
    assert default.reason == "sibling_worktree_not_selected"
    opted = RepoResolver("r1", repo, siblings=[sibling], include_siblings=True).resolve(path, None)
    assert opted.status is PathResolution.RESOLVED and opted.relative_path == "a.py"
    assert opted.worktree_id != RepoResolver("r1", repo).primary.worktree_id


def test_case_insensitive_filesystem_detection(repo: Path) -> None:
    resolver = RepoResolver("r1", repo)
    upper = str(repo).upper() + "/SRC/App.py"
    status = resolver.resolve(upper, None).status
    expected = (
        PathResolution.RESOLVED
        if os.path.exists(str(repo).upper())
        else PathResolution.OUTSIDE_REPO
    )
    assert status is expected


def test_apply_and_segments_on_fixture() -> None:
    events, _ = parse_fixture(ClaudeCodeAdapter(), "claude_code/2.1/session.jsonl")
    resolver = RepoResolver("r1", Path("/work/demo"))
    applied = [resolver.apply(e)[0] for e in events]
    edits = of_type(applied, EventType.TEXT_EDIT)
    assert {e.scope.relative_path for e in edits} == {"app.py"}
    assert all(e.scope.repo_id == "r1" for e in applied)
    (segment,) = segments(applied, resolver)
    assert segment.first_sequence == applied[0].order.sequence


def test_segment_ends_when_cwd_leaves_repo() -> None:
    events, _ = parse_fixture(ClaudeCodeAdapter(), "claude_code/2.1/session.jsonl")
    resolver = RepoResolver("r1", Path("/work/demo"))
    moved = [
        replace(e, scope=replace(e.scope, effective_cwd="/elsewhere")) if i in (5, 6) else e
        for i, e in enumerate(events)
    ]
    applied = [resolver.apply(e)[0] for e in moved]
    assert len(segments(applied, resolver)) == 2
