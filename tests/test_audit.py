from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from adapter_helpers import FIXTURES
from tandem.cli import main
from tandem.errors import ExitCode


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    shutil.copytree(FIXTURES / "claude_code", claude)
    shutil.copytree(FIXTURES / "codex", codex)
    return claude, codex


def test_audit_json_counts(roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    claude, codex = roots
    code = main(
        ["audit", "--claude-root", str(claude), "--codex-root", str(codex), "--format", "json"]
    )
    assert code == ExitCode.OK
    data = {a["agent"]: a for a in json.loads(capsys.readouterr().out)["audit"]}
    assert data["claude_code"]["files"] == 1
    assert data["claude_code"]["reconstruction"] == {"exact": 2}
    assert data["claude_code"]["failed_edit_calls"] == 1
    assert data["codex"]["files"] == 3
    assert data["codex"]["failed_edit_calls"] == 1
    assert data["codex"]["versions"]["0.98.0"] == 1


def test_audit_never_prints_content_or_paths(
    roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    claude, codex = roots
    main(["audit", "--claude-root", str(claude), "--codex-root", str(codex)])
    out = capsys.readouterr().out
    for secret in ("synthetic prompt", "return 1", "/work/demo", str(claude), "# Notes"):
        assert secret not in out


def test_audit_without_logs_is_unavailable(tmp_path: Path) -> None:
    code = main(
        ["audit", "--claude-root", str(tmp_path / "x"), "--codex-root", str(tmp_path / "y")]
    )
    assert code == ExitCode.UNAVAILABLE
