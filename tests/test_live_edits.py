from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from conftest import GitRunner
from tandem.cli import main
from tandem.live.edits import file_edits, parse_patch_sections, parse_unified
from tandem.live.protocol import iso

NOW = time.time()


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _claude_result(
    repo: Path, at: float, result: dict[str, Any], *, error: bool = False
) -> dict[str, Any]:
    return {
        "type": "user",
        "cwd": str(repo),
        "timestamp": iso(at),
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": error}]},
        "toolUseResult": result,
    }


def _repo(git_repo: tuple[Path, GitRunner]) -> Path:
    repo, _ = git_repo
    (repo / "src").mkdir()
    return repo.resolve()


def test_parse_unified_and_patch_text() -> None:
    hunks = parse_unified("--- a\n+++ b\n@@ -3,2 +3,3 @@\n a\n-b\n+c\n+d\n")
    assert [(h.old_start, h.new_start, h.lines) for h in hunks] == [
        (3, 3, [" a", "-b", "+c", "+d"])
    ]
    patch = (
        "*** Begin Patch\n*** Update File: src/a.py\n@@ def f\n-x = 1\n+x = 2\n"
        "*** Add File: b.py\n+new\n*** End Patch\n"
    )
    sections = list(parse_patch_sections(patch))
    assert [(a, p, [h.lines for h in hs]) for a, p, hs in sections] == [
        ("Update", "src/a.py", [["-x = 1", "+x = 2"]]),
        ("Add", "b.py", [["+new"]]),
    ]


def test_claude_edits_keep_successes_newest_first(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    target = str(repo / "src" / "a.py")
    patch = [{"oldStart": 4, "oldLines": 1, "newStart": 4, "newLines": 1, "lines": ["-x", "+y"]}]
    records = [
        {"type": "ai-title", "aiTitle": "Tidy a.py"},
        _claude_result(
            repo,
            NOW - 300,
            {"type": "create", "filePath": target, "content": "x\n", "structuredPatch": []},
        ),
        _claude_result(
            repo, NOW - 200, {"filePath": target, "oldString": "x", "structuredPatch": patch}
        ),
        _claude_result(
            repo,
            NOW - 100,
            {"filePath": target, "oldString": "z", "structuredPatch": patch},
            error=True,
        ),
        _claude_result(
            repo, NOW - 50, {"filePath": str(repo / "src" / "other.py"), "structuredPatch": patch}
        ),
    ]
    _write(tmp_path / "claude" / "p" / "s1.jsonl", records)
    edits = file_edits(repo, "src/a.py", tmp_path / "claude", tmp_path / "none", now=NOW)
    assert [(e.tool, e.change) for e in edits] == [("Edit", "update"), ("Write", "create")]
    assert edits[0].session_id == "claude_code:s1"
    assert edits[0].title == "Tidy a.py"
    assert edits[0].hunks[0].old_start == 4
    assert edits[1].hunks[0].lines == ["+x"]


def test_codex_file_change_supersedes_patch_text(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    meta = {
        "type": "session_meta",
        "timestamp": iso(NOW - 500),
        "payload": {"id": "c1", "cwd": str(repo)},
    }
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch\n"

    def call(cid: str, at: float) -> dict[str, Any]:
        return {
            "type": "response_item",
            "timestamp": iso(at),
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": cid,
                "input": patch,
            },
        }

    def output(cid: str, at: float, text: str) -> dict[str, Any]:
        return {
            "type": "response_item",
            "timestamp": iso(at),
            "payload": {"type": "custom_tool_call_output", "call_id": cid, "output": text},
        }

    change = {
        "type": "event_msg",
        "timestamp": iso(NOW - 99),
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "FileChange",
                "id": "k2",
                "changes": {
                    str(repo / "src" / "a.py"): {
                        "type": "update",
                        "unified_diff": "@@ -7 +7 @@\n-x\n+y\n",
                    }
                },
            },
        },
    }
    records = [
        meta,
        call("k1", NOW - 300),
        output("k1", NOW - 299, "Exit code: 0\nSuccess."),  # patch text only
        call("k2", NOW - 100),
        output("k2", NOW - 99, "Exit code: 0"),
        change,  # answered by FileChange
        call("k3", NOW - 50),
        output("k3", NOW - 49, "Exit code: 1\nverification failed"),
    ]
    _write(tmp_path / "codex" / "2026" / "rollout-c1.jsonl", records)
    edits = file_edits(repo, "src/a.py", tmp_path / "none", tmp_path / "codex", now=NOW)
    assert [(e.session_id, e.hunks[0].old_start) for e in edits] == [
        ("codex:c1", 7),
        ("codex:c1", None),
    ]


def test_edits_cli_json(git_repo: tuple[Path, GitRunner], tmp_path: Path, capsys: Any) -> None:
    repo = _repo(git_repo)
    result = {
        "type": "create",
        "filePath": str(repo / "src" / "a.py"),
        "content": "a\nb\n",
        "structuredPatch": [],
    }
    _write(tmp_path / "claude" / "p" / "s1.jsonl", [_claude_result(repo, NOW, result)])
    code = main(
        [
            "edits",
            "src/a.py",
            "--repo",
            str(repo),
            "--format",
            "json",
            "--claude-root",
            str(tmp_path / "claude"),
            "--codex-root",
            str(tmp_path / "x"),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["path"] == "src/a.py"
    assert out["edits"][0]["hunks"] == [{"old_start": 0, "new_start": 1, "lines": ["+a", "+b"]}]
