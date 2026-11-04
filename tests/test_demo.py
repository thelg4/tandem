"""The demo runs the real pipeline on its synthetic repository and logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from tandem.demo import run


def test_demo_finds_its_scripted_interactions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path / "demo") == 0
    out = capsys.readouterr().out
    assert "Codex changed Claude Code's lines: exact_revert 1, partial_revert 1, rewrite 1" in out
    assert "survival at 5: 33 / 43 = 76.7%" in out
    assert "exact revert rate: 1 / 4 = 25.0%" in out
    assert "the later edit removed exactly the lines the earlier edit added" in out
    # Every edit is placed and every agent line is verified.
    assert "inserted lines verified: 61 of 70 (87%)" in out


def test_demo_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("keep me")
    assert run(tmp_path) == 2
    assert (tmp_path / "x").read_text() == "keep me"
