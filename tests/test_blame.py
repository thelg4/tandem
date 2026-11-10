"""tandem blame: per-line agent ownership of one file, aligned with the working tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tandem.cli import main
from tandem.errors import ExitCode
from test_attribution_e2e import _scan, crossed  # noqa: F401  (fixture)


def _blame(c: dict[str, Path], rel: str, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    capsys.readouterr()
    code = main(["blame", rel, "--repo", str(c["repo"]), "--db", str(c["db"]), "--format", "json"])
    assert code == ExitCode.OK
    out: dict[str, object] = json.loads(capsys.readouterr().out)
    return out


def _shape(blame: dict[str, object]) -> list[tuple[object, ...]]:
    spans = blame["spans"]
    assert isinstance(spans, list)
    return [(s["start"], s["count"], s["kind"], s["agent"], s["commit"] is not None) for s in spans]


def test_blame_names_each_lines_agent(
    crossed: dict[str, Path],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _scan(crossed)
    blame = _blame(crossed, "m.py", capsys)
    assert _shape(blame) == [
        (1, 1, "agent", "claude_code", True),
        (2, 1, "agent", "codex", True),
    ]
    spans = blame["spans"]
    assert isinstance(spans, list)
    assert {s["evidence"] for s in spans} == {"verified"}
    assert blame["edits"] == 2 and blame["scanned_at"]


def test_blame_marks_lines_no_log_explains(
    crossed: dict[str, Path],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _scan(crossed)
    m = crossed["repo"] / "m.py"
    m.write_text("# hand edit\n" + m.read_text())
    assert _shape(_blame(crossed, "m.py", capsys)) == [
        (1, 1, "changed", None, False),
        (2, 1, "agent", "claude_code", True),
        (3, 1, "agent", "codex", True),
    ]
    readme = _blame(crossed, str(crossed["repo"] / "README.md"), capsys)
    assert readme["note"] == "no_agent_edits"
    assert _shape(readme) == [(1, 1, "unknown", None, False)]


def test_blame_text_and_errors(
    crossed: dict[str, Path],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, db = str(crossed["repo"]), str(crossed["db"])
    assert main(["blame", "m.py", "--repo", repo, "--db", db]) == ExitCode.UNAVAILABLE
    _scan(crossed)
    capsys.readouterr()
    assert main(["blame", "m.py", "--repo", repo, "--db", db]) == ExitCode.OK
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Claude Code (verified)") and out[0].endswith("def a():")
    assert out[1].startswith("Codex (verified)")
    assert main(["blame", "../x", "--repo", repo, "--db", db]) == ExitCode.INVALID_INPUT
    assert main(["blame", "missing.py", "--repo", repo, "--db", db]) == ExitCode.INVALID_INPUT
