from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from adapter_helpers import FIXTURES


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """core code makes zero outbound network requests. Any attempt fails the test."""

    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        if self.family == socket.AF_UNIX:
            return real_connect(self, address)  # type: ignore[arg-type]
        raise AssertionError(f"network access attempted: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANDEM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TANDEM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )
    return out.stdout.strip()


GitRunner = Callable[..., str]


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, GitRunner]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    def run(*args: str) -> str:
        return _git(repo, *args)

    return repo, run


@pytest.fixture
def repo_with_history(git_repo: tuple[Path, GitRunner]) -> tuple[Path, list[str]]:
    repo, git = git_repo
    commits = []
    for i in range(3):
        (repo / "app.py").write_text("".join(f"line {n}\n" for n in range(i + 2)))
        git("add", "app.py")
        git("commit", "-q", "-m", f"commit {i}")
        commits.append(git("rev-parse", "HEAD"))
    return repo, commits


def _copy_logs(dest: Path, agent_dir: str, repo: Path) -> None:
    """Copy fixture logs, rewriting the synthetic /work/demo working directory to ``repo``."""
    for src in (FIXTURES / agent_dir).rglob("*.jsonl"):
        out = dest / src.relative_to(FIXTURES / agent_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(src.read_text().replace("/work/demo", str(repo)))


@pytest.fixture
def env(repo_with_history: tuple[Path, list[str]], tmp_path: Path) -> dict[str, Path]:
    repo, _ = repo_with_history
    claude, codex = tmp_path / "logs" / "claude", tmp_path / "logs" / "codex"
    _copy_logs(claude, "claude_code", repo)
    _copy_logs(codex, "codex", repo)
    return {"repo": repo, "claude": claude, "codex": codex, "db": tmp_path / "t.sqlite3"}
