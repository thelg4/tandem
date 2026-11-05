from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from conftest import GitRunner
from tandem.cli import main
from tandem.live.projects import recent_projects
from tandem.live.protocol import iso
from tandem.live.usage import usage_summary

NOW = time.time()


def _write(path: Path, records: list[dict[str, Any]], mtime: float = NOW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.utime(path, (mtime, mtime))


def _claude_turn(request: str, at: float, *, inp: int, out: int, cwd: str = "/x") -> dict[str, Any]:
    return {
        "type": "assistant",
        "cwd": cwd,
        "requestId": request,
        "timestamp": iso(at),
        "message": {
            "id": f"msg_{request}",
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 100,
                "output_tokens": out,
            },
        },
    }


def _codex_count(at: float, total: int, inp: int, out: int, used: float | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "token_count",
        "info": {
            "total_token_usage": {"total_tokens": total},
            "last_token_usage": {"input_tokens": inp, "output_tokens": out},
        },
    }
    if used is not None:
        payload["rate_limits"] = {
            "primary": {"used_percent": used, "window_minutes": 300, "resets_at": int(at) + 3600},
            "secondary": {"used_percent": 3.0, "window_minutes": 10080, "resets_at": 1},
            "plan_type": "plus",
        }
    return {"timestamp": iso(at), "type": "event_msg", "payload": payload}


def test_claude_usage_counts_each_response_once(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    turn = _claude_turn("r1", NOW - 60, inp=5, out=7)
    old = _claude_turn("r0", NOW - 3 * 86400, inp=1, out=2)
    # One response split over two records repeats its usage; it counts once.
    _write(claude / "p" / "s.jsonl", [old, turn, turn])
    summary = usage_summary(claude, tmp_path / "none", now=NOW)
    usage = summary["claude_code"]
    assert isinstance(usage, dict)
    assert usage["today"] == {"input": 115, "output": 7}
    assert usage["week"] == {"input": 226, "output": 9}


def test_codex_usage_skips_repeated_counts_and_keeps_latest_limits(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    records = [
        _codex_count(NOW - 120, 100, 80, 20, used=10.0),
        _codex_count(NOW - 119, 100, 80, 20, used=10.0),  # the same call reported again
        _codex_count(NOW - 60, 150, 40, 10, used=12.5),
    ]
    _write(codex / "2026" / "rollout-a.jsonl", records)
    summary = usage_summary(tmp_path / "none", codex, now=NOW)
    usage = summary["codex"]
    assert isinstance(usage, dict)
    assert usage["today"] == {"input": 120, "output": 30}
    limits = usage["limits"]
    assert isinstance(limits, dict)
    assert limits["primary"]["used_percent"] == 12.5
    assert limits["primary"]["window_minutes"] == 300
    assert limits["plan"] == "plus"


def test_usage_without_logs_is_empty(tmp_path: Path) -> None:
    summary = usage_summary(tmp_path / "a", tmp_path / "b", now=NOW)
    assert summary["codex"] == {
        "today": {"input": 0, "output": 0},
        "week": {"input": 0, "output": 0},
        "last_at": None,
        "limits": None,
    }


def test_recent_projects_groups_sessions_by_repository(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo, _ = git_repo
    (repo / "pkg").mkdir()
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    # Two Claude sessions, one started in a subdirectory, and one Codex session.
    _write(
        claude / "a" / "s1.jsonl", [_claude_turn("r1", NOW, inp=1, out=1, cwd=str(repo))], NOW - 50
    )
    _write(
        claude / "b" / "s2.jsonl",
        [_claude_turn("r2", NOW, inp=1, out=1, cwd=str(repo / "pkg"))],
        NOW - 40,
    )
    meta = {
        "type": "session_meta",
        "timestamp": iso(NOW),
        "payload": {"id": "c1", "cwd": str(repo)},
    }
    _write(codex / "2026" / "rollout-c1.jsonl", [meta], NOW - 10)
    # Not a repository, and too old: both left out.
    _write(claude / "c" / "s3.jsonl", [_claude_turn("r3", NOW, inp=1, out=1, cwd=str(tmp_path))])
    _write(
        claude / "d" / "s4.jsonl",
        [_claude_turn("r4", NOW, inp=1, out=1, cwd=str(repo))],
        NOW - 90 * 86400,
    )

    projects = recent_projects(claude, codex, now=NOW)
    assert [p.path for p in projects] == [repo.resolve()]
    assert projects[0].sessions == {"claude_code": 2, "codex": 1}
    assert projects[0].last_active == NOW - 10


def test_projects_cli_json(git_repo: tuple[Path, GitRunner], tmp_path: Path, capsys: Any) -> None:
    repo, _ = git_repo
    claude = tmp_path / "claude"
    _write(claude / "a" / "s1.jsonl", [_claude_turn("r1", NOW, inp=1, out=1, cwd=str(repo))])
    code = main(
        [
            "projects",
            "--format",
            "json",
            "--claude-root",
            str(claude),
            "--codex-root",
            str(tmp_path / "x"),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["projects"][0]["name"] == repo.resolve().name
    assert out["projects"][0]["sessions"] == {"claude_code": 1}
