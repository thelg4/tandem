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


def _json_report(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    capsys.readouterr()
    assert main(["report", *_args(env), "--format", "json"]) == ExitCode.OK
    report: dict[str, object] = json.loads(capsys.readouterr().out)
    return report


def test_logs_newer_than_history_are_flagged(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    # The fixture history is dated 2026-01-01 and its logs start in October: the shape
    # of a repository whose older Claude Code transcripts were deleted.
    _scan(env)
    capsys.readouterr()
    assert main(["report", *_args(env)]) == ExitCode.OK
    out = capsys.readouterr().out
    assert "! claude_code logs start 2026-10-01, after the last analyzed commit (2026-01-01)" in out
    assert "cleanupPeriodDays" in out
    gaps = _json_report(env, capsys)["coverage"]["log_gaps"]  # type: ignore[index]
    assert [g["agent"] for g in gaps] == ["claude_code", "codex"]
    assert all(g["kind"] == "all_history" for g in gaps)


def test_no_gap_when_logs_cover_the_history(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    for agent_dir in (env["claude"], env["codex"]):
        for f in agent_dir.rglob("*.jsonl"):
            f.write_text(f.read_text().replace('"2026-10-0', '"2025-10-0'))
    _scan(env)
    gaps = _json_report(env, capsys)["coverage"]["log_gaps"]  # type: ignore[index]
    assert gaps == []


def test_sources_from_older_scans_are_reread_for_record_times(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    _scan(env)
    before = _json_report(env, capsys)["coverage"]["edits"]  # type: ignore[index]
    conn = sqlite3.connect(env["db"])
    conn.execute("UPDATE sources SET first_record_at = NULL, last_record_at = NULL")
    conn.commit()
    conn.close()
    _scan(env)
    conn = sqlite3.connect(env["db"])
    missing = conn.execute("SELECT count(*) FROM sources WHERE first_record_at IS NULL")
    assert missing.fetchone()[0] == 0
    conn.close()
    after = _json_report(env, capsys)["coverage"]["edits"]  # type: ignore[index]
    assert after == before  # re-reading a source does not duplicate its edits
    assert after["claude_code"]["in_repository"] > 0
