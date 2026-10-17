from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tandem.cli import main
from tandem.errors import ExitCode


def _args(env: dict[str, Path]) -> list[str]:
    return [str(env["repo"]), "--db", str(env["db"])]


def _scan(env: dict[str, Path]) -> None:
    code = main(["scan", *_args(env), "--claude-root", str(env["claude"]),
                 "--codex-root", str(env["codex"])])  # fmt: skip
    assert code == ExitCode.OK


def test_report_before_scan_is_unavailable(env: dict[str, Path]) -> None:
    assert main(["report", *_args(env)]) == ExitCode.UNAVAILABLE


def test_text_report_order_and_content(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _scan(env)
    capsys.readouterr()
    assert main(["report", *_args(env)]) == ExitCode.OK
    out = capsys.readouterr().out
    # Coverage comes before per-agent numbers.
    assert out.index("Coverage") < out.index("Observed edits") < out.index("Notes")
    assert "claude_code: 1 log file(s)" in out
    assert "Files edited by both agents: 1" in out and "app.py" in out
    assert "linked to tool call by position only" in out
    assert "changed by the user during approval: 1" in out
    assert "stale" not in out


def test_json_report(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _scan(env)
    capsys.readouterr()
    assert main(["report", *_args(env), "--format", "json"]) == ExitCode.OK
    data = json.loads(capsys.readouterr().out)
    edits = data["coverage"]["edits"]
    assert edits["claude_code"]["in_repository"] == 3
    assert edits["claude_code"]["reconstruction"] == {"reconstructed": 3}
    assert edits["codex"]["evidence_source"]["codex.file_change"] == 4
    assert edits["codex"]["change_type"] == {"add": 1, "update": 3, "delete": 1}
    assert data["coverage"]["files_edited_by_both_agents"][0]["path"] == "app.py"
    assert data["coverage"]["usage"]["claude_code"]["input"] == 1200 + 5 * 40
    assert data["run"]["state"] == "complete" and data["caveats"]
    assert str(Path.home()) not in json.dumps(data)


def test_filters(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _scan(env)
    capsys.readouterr()
    main(["report", *_args(env), "--format", "json", "--agent", "codex", "--file", "app.py"])
    data = json.loads(capsys.readouterr().out)
    assert set(data["coverage"]["edits"]) == {"codex"}
    # Both the 0.98 patch-text session and the 0.144 FileChange session edit app.py.
    assert data["coverage"]["edits"]["codex"]["in_repository"] == 2
    assert data["filters"] == {"agent": "codex", "file": "app.py"}


def test_stale_report_is_flagged(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _scan(env)
    (env["repo"] / "new.txt").write_text("x\n")
    for cmd in (["add", "new.txt"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                     "commit", "-q", "-m", "more"]):  # fmt: skip
        subprocess.run(["git", *cmd], cwd=env["repo"], check=True)
    capsys.readouterr()
    main(["report", *_args(env)])
    assert "stale" in capsys.readouterr().out
