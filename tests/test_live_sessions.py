from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from tandem.live.protocol import iso
from tandem.live.sessions import history

NOW = time.time()


def _write(path: Path, records: list[dict[str, Any]], mtime: float = NOW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.utime(path, (mtime, mtime))


def _user(text: object, cwd: str, **extra: object) -> dict[str, Any]:
    return {"type": "user", "cwd": cwd, "message": {"role": "user", "content": text}, **extra}


def test_claude_sessions_titled_and_scoped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    claude = tmp_path / "claude"
    _write(
        claude / "p" / "a.jsonl",
        [
            _user("<command-name>/clear</command-name>", str(repo)),
            _user("fix the parser", str(repo)),
            {"type": "ai-title", "aiTitle": "Old title"},
            {"type": "ai-title", "aiTitle": "Parser fix"},
        ],
        NOW - 60,
    )
    # No AI title yet: the first real prompt stands in.
    _write(
        claude / "p" / "b.jsonl",
        [_user([{"type": "text", "text": "add   a\nsidebar"}], str(repo / "sub"))],
        NOW - 10,
    )
    # Opened and closed without a prompt; and a session in another repository.
    _write(claude / "p" / "c.jsonl", [_user("hi", str(repo), isMeta=True)])
    _write(claude / "q" / "d.jsonl", [_user("elsewhere", str(other))])
    # A subagent transcript is part of its parent session.
    _write(claude / "p" / "a" / "subagents" / "s.jsonl", [_user("sub task", str(repo))])

    found = history(repo, claude, tmp_path / "none", now=NOW)
    assert [(s.session_id, s.title) for s in found] == [
        ("claude_code:b", "add a sidebar"),
        ("claude_code:a", "Parser fix"),
    ]


def test_codex_sessions_use_thread_names_then_first_prompt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    codex = tmp_path / "codex" / "sessions"

    def rollout(tid: str, root: str, prompt: str, mtime: float) -> None:
        meta = {"id": tid, "session_id": root, "cwd": str(repo), "timestamp": iso(mtime - 30)}
        env = {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "<environment_context>x</environment_context>"}
            ],
        }
        _write(
            codex / "2026" / f"rollout-{tid}.jsonl",
            [
                {"timestamp": iso(mtime), "type": "session_meta", "payload": meta},
                {"timestamp": iso(mtime), "type": "response_item", "payload": env},
                {
                    "timestamp": iso(mtime),
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": prompt},
                },
            ],
            mtime,
        )

    rollout("T1", "T1", "review the diff", NOW - 100)
    rollout("T2", "T2", "write tests", NOW - 50)
    rollout("T3", "T1", "subagent work", NOW - 10)  # folds into T1
    (codex.parent / "session_index.jsonl").write_text(
        json.dumps({"id": "T1", "thread_name": "Review the diff", "updated_at": iso(NOW)}) + "\n"
    )
    found = history(repo, tmp_path / "none", codex, now=NOW)
    assert [(s.session_id, s.raw_id, s.title) for s in found] == [
        ("codex:T2", "T2", "write tests"),
        ("codex:T1", "T1", "Review the diff"),
    ]
    assert found[0].as_json()["agent"] == "codex"


def test_claude_session_with_a_live_process_is_running(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claude = tmp_path / "claude" / "projects"
    _write(claude / "p" / "live.jsonl", [_user("still going", str(repo))])
    _write(claude / "p" / "done.jsonl", [_user("finished", str(repo))], NOW - 5)
    reg = claude.parent / "sessions"
    reg.mkdir()
    (reg / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "live"}))
    found = {s.raw_id: s.running for s in history(repo, claude, tmp_path / "none", now=NOW)}
    assert found == {"live": True, "done": False}
