from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tandem.live.tail import Tail, claude_registry, peek_cwd, recent_logs
from tandem.model.events import AgentFamily


def test_tail_waits_for_complete_lines_and_detects_rewrite(tmp_path: Path) -> None:
    log = tmp_path / "s.jsonl"
    log.write_bytes(b'{"a": 1}\n{"b"')
    tail = Tail(log, AgentFamily.CLAUDE_CODE)
    records, rewritten = tail.read()
    assert records == [{"a": 1}] and not rewritten

    with log.open("ab") as fh:
        fh.write(b": 2}\n")
    records, rewritten = tail.read()
    assert records == [{"b": 2}] and not rewritten
    assert tail.read() == ([], False)

    log.write_bytes(b'{"c": 3}\n')
    records, rewritten = tail.read()
    assert records == [{"c": 3}] and rewritten


def test_tail_missing_file_is_empty(tmp_path: Path) -> None:
    assert Tail(tmp_path / "gone.jsonl", AgentFamily.CODEX).read() == ([], False)


def test_recent_logs_skips_old_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "p").mkdir(parents=True)
    fresh = root / "p" / "fresh.jsonl"
    fresh.write_text("{}\n")
    old = root / "p" / "old.jsonl"
    old.write_text("{}\n")
    past = time.time() - 3600
    os.utime(old, (past, past))
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    (root / "p" / "link.jsonl").symlink_to(outside)
    assert recent_logs(root, since=time.time() - 600) == [fresh.resolve()]
    assert recent_logs(tmp_path / "missing", since=0) == []


def test_peek_cwd(tmp_path: Path) -> None:
    claude = tmp_path / "c.jsonl"
    claude.write_text(
        json.dumps({"type": "summary"}) + "\n" + json.dumps({"type": "user", "cwd": "/r"}) + "\n"
    )
    assert peek_cwd(claude, AgentFamily.CLAUDE_CODE) == ("/r", None)

    codex = tmp_path / "x.jsonl"
    meta = {"id": "C", "cwd": "/q", "originator": "codex_cli_rs"}
    codex.write_text(json.dumps({"type": "session_meta", "payload": meta}) + "\n")
    assert peek_cwd(codex, AgentFamily.CODEX) == ("/q", meta)
    assert peek_cwd(tmp_path / "none.jsonl", AgentFamily.CODEX) == (None, None)


def test_claude_registry(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "12.json").write_text(json.dumps({"pid": 12, "sessionId": "u1", "cwd": "/r"}))
    (sessions / "13.json").write_text("{not json")
    (sessions / "14.abc.key").write_text("secret")
    (sessions / "15.json").write_text(json.dumps({"pid": "15", "sessionId": "u2"}))
    assert claude_registry(tmp_path) == {12: ("u1", "/r")}
    assert claude_registry(tmp_path / "nope") == {}


def test_peek_cwd_stops_at_its_byte_budget(tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    junk = json.dumps({"type": "user", "message": {"content": "x" * 5000}})
    log.write_text(junk + "\n" + json.dumps({"type": "user", "cwd": "/r"}) + "\n")
    assert peek_cwd(log, AgentFamily.CLAUDE_CODE, max_bytes=1000) == (None, None)
    assert peek_cwd(log, AgentFamily.CLAUDE_CODE) == ("/r", None)


def test_peek_cwd_skips_the_rest_of_an_overlong_line(tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    # The tail of the long line is itself valid JSON and must not be read as a record.
    long_line = "x" * 1000 + json.dumps({"type": "user", "cwd": "/wrong"})
    log.write_text(long_line + "\n" + json.dumps({"type": "user", "cwd": "/r"}) + "\n")
    assert peek_cwd(log, AgentFamily.CLAUDE_CODE, line_bytes=1000) == ("/r", None)
