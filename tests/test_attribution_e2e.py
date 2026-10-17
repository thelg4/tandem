"""Scan → attribution → report on a repository where Claude Code and Codex logs cross."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tandem.cli import main
from tandem.errors import ExitCode

T0 = 1_790_000_000


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def git(repo: Path, t: int, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="dev",
        GIT_AUTHOR_EMAIL="d@example.invalid",
        GIT_COMMITTER_NAME="dev",
        GIT_COMMITTER_EMAIL="d@example.invalid",
        GIT_AUTHOR_DATE=f"@{t} +0000",
        GIT_COMMITTER_DATE=f"@{t} +0000",
    )
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def claude_write(path: str, content: str, t: int, n: int) -> list[dict[str, object]]:
    base = {
        "sessionId": "cc-1",
        "cwd": str(Path(path).parent),
        "version": "2.1.302",
        "isSidechain": False,
        "timestamp": iso(t),
    }
    call = {
        **base,
        "type": "assistant",
        "uuid": f"a{n}",
        "requestId": f"r{n}",
        "message": {
            "role": "assistant",
            "model": "claude-x",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"t{n}",
                    "name": "Write",
                    "input": {"file_path": path, "content": content},
                }
            ],
        },
    }
    result = {
        **base,
        "type": "user",
        "uuid": f"u{n}",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"t{n}", "content": "ok"}],
        },
        "toolUseResult": {
            "type": "create",
            "filePath": path,
            "content": content,
            "originalFile": None,
            "structuredPatch": [],
            "userModified": False,
        },
    }
    return [call, result]


def codex_change(repo: Path, rel: str, diff: str, t: int) -> list[dict[str, object]]:
    def rec(i: int, kind: str, payload: dict[str, object]) -> dict[str, object]:
        return {"timestamp": iso(t), "ordinal": i, "type": kind, "payload": payload}

    return [
        rec(0, "session_meta", {"id": "cx-1", "cwd": str(repo), "cli_version": "0.153.4"}),
        rec(
            1,
            "response_item",
            {
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": "c1",
                "name": "apply_patch",
                "input": "<patch>",
            },
        ),
        rec(
            2,
            "response_item",
            {
                "type": "custom_tool_call_output",
                "call_id": "c1",
                "output": json.dumps({"output": "ok", "metadata": {"exit_code": 0}}),
            },
        ),
        rec(
            3,
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "FileChange",
                    "id": "c1",
                    "status": "completed",
                    "changes": {str(repo / rel): {"type": "update", "unified_diff": diff}},
                },
            },
        ),
    ]


@pytest.fixture
def crossed(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, T0, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    git(repo, T0, "add", "-A")
    git(repo, T0, "commit", "-q", "-m", "init")

    # Claude writes m.py; committed.
    content = "def a():\n    return 1\n"
    (repo / "m.py").write_text(content)
    claude_log = claude_write(str(repo / "m.py"), content, T0 + 50, 1)
    git(repo, T0 + 100, "add", "-A")
    git(repo, T0 + 100, "commit", "-q", "-m", "claude")

    # Codex rewrites Claude's return line; committed.
    (repo / "m.py").write_text("def a():\n    return 2\n")
    codex_log = codex_change(
        repo, "m.py", "@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n", T0 + 150
    )
    git(repo, T0 + 200, "add", "-A")
    git(repo, T0 + 200, "commit", "-q", "-m", "codex")

    logs = tmp_path / "logs"
    (logs / "claude" / "p").mkdir(parents=True)
    (logs / "codex" / "2026").mkdir(parents=True)
    (logs / "claude" / "p" / "s.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in claude_log)
    )
    (logs / "codex" / "2026" / "r.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in codex_log)
    )
    return {
        "repo": repo,
        "claude": logs / "claude",
        "codex": logs / "codex",
        "db": tmp_path / "t.sqlite3",
    }


def test_cross_agent_rewrite_end_to_end(
    crossed: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db = ["--db", str(crossed["db"])]
    code = main(
        [
            "scan",
            str(crossed["repo"]),
            *db,
            "--claude-root",
            str(crossed["claude"]),
            "--codex-root",
            str(crossed["codex"]),
        ]
    )
    assert code == ExitCode.OK
    capsys.readouterr()
    assert main(["report", str(crossed["repo"]), *db, "--format", "json"]) == ExitCode.OK
    att = json.loads(capsys.readouterr().out)["coverage"]["attribution"]
    assert att["commits_analyzed"] == 3 and not att["history_truncated"]
    assert att["inserted_lines_in_range"] == 4  # README 1, Claude 2, Codex 1
    assert att["attributed_lines_by_class"] == {"verified": 3}
    claude, codex = att["agents"]["claude_code"], att["agents"]["codex"]
    assert claude["lines_committed"] == {"verified": 2}
    assert claude["lines_present_at_target"] == 1
    assert claude["lines_removed_later"] == {"other_agent": 1}
    assert codex["lines_present_at_target"] == 1
    assert codex["edits_by_outcome"] == {"committed": 1}

    capsys.readouterr()
    main(["report", str(crossed["repo"]), *db])
    text = capsys.readouterr().out
    assert text.index("Attribution coverage") < text.index("Attributed lines")
    assert "verified lines: 3 of 4 (75%)" in text
    assert "later removed, by: other_agent 1" in text


def test_attribution_reruns_from_cached_evidence(crossed: dict[str, Path]) -> None:
    db = ["--db", str(crossed["db"])]
    args = [
        "scan",
        str(crossed["repo"]),
        *db,
        "--claude-root",
        str(crossed["claude"]),
        "--codex-root",
        str(crossed["codex"]),
    ]
    assert main(args) == ExitCode.OK
    assert main(args) == ExitCode.OK  # unchanged logs: no import, attribution re-derived
    import sqlite3

    conn = sqlite3.connect(crossed["db"])
    runs = conn.execute("SELECT count(*) FROM run_attribution").fetchone()[0]
    per_run = conn.execute(
        "SELECT run_id, sum(line_count) FROM lineage GROUP BY run_id ORDER BY run_id"
    ).fetchall()
    assert runs == 2 and per_run[0][1] == per_run[1][1]
