from __future__ import annotations

import json
from pathlib import Path

import pytest

from tandem.cli import main
from tandem.errors import ExitCode
from tandem.report.json_report import build_report, dumps, redact_path


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in ("doctor", "scan", "report", "session", "explain"):
        assert command in out


def test_unknown_option_is_invalid_input() -> None:
    assert main(["scan", "--no-such-flag"]) == ExitCode.INVALID_INPUT


def test_doctor_in_repo(
    repo_with_history: tuple[Path, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = repo_with_history
    assert main(["doctor", str(repo)]) == ExitCode.OK
    out = capsys.readouterr().out
    assert "repository" in out and "not found" in out  # isolated log roots are absent


def test_doctor_outside_repo(tmp_path: Path) -> None:
    assert main(["doctor", str(tmp_path)]) == ExitCode.UNAVAILABLE


def test_missing_path_is_invalid_input(tmp_path: Path) -> None:
    assert main(["doctor", str(tmp_path / "nope")]) == ExitCode.INVALID_INPUT


def test_scan_without_logs_is_partial_not_an_error(
    repo_with_history: tuple[Path, list[str]], tmp_path: Path
) -> None:
    repo, _ = repo_with_history
    dbfile = tmp_path / "scan.sqlite3"
    assert main(["scan", str(repo), "--db", str(dbfile)]) == ExitCode.OK
    assert dbfile.exists()
    assert main(["scan", str(repo), "--db", str(dbfile), "--strict"]) == ExitCode.UNAVAILABLE


def test_scan_rejects_unsafe_ref(repo_with_history: tuple[Path, list[str]]) -> None:
    repo, _ = repo_with_history
    assert main(["scan", str(repo), "--ref=--output=x"]) == ExitCode.INVALID_INPUT


def test_json_report_shape() -> None:
    report = build_report(
        repository={"path": redact_path(Path.home() / "code" / "x")},
        run=None,
        coverage={},
        metrics=[],
        interactions=[],
        filters={},
        caveats=[],
    )
    data = json.loads(dumps(report))
    assert data["format_version"]
    assert data["repository"]["path"] == "~/code/x"
