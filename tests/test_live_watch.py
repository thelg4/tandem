from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from conftest import GitRunner
from tandem.cli import main
from tandem.live.protocol import iso
from tandem.live.watch import run_watch

UUID = "11111111-2222-3333-4444-555555555555"


def _repo(git_repo: tuple[Path, GitRunner]) -> Path:
    repo, git = git_repo
    (repo / "a.py").write_text("x = 1\n")
    git("add", "a.py")
    git("commit", "-q", "-m", "one")
    return repo


def _claude_edit_log(path: Path, repo: Path, *, sid: str = UUID) -> None:
    now = iso(time.time())
    use = {
        "type": "assistant",
        "sessionId": sid,
        "cwd": str(repo),
        "timestamp": now,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Edit",
                    "input": {"file_path": str(repo / "a.py")},
                }
            ]
        },
    }
    result = {
        "type": "user",
        "sessionId": sid,
        "cwd": str(repo),
        "timestamp": now,
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(use) + "\n" + json.dumps(result) + "\n")


def _codex_patch_log(path: Path, repo: Path, *, originator: str = "Codex Desktop") -> None:
    now = iso(time.time())
    meta = {
        "id": "CX",
        "session_id": "CX",
        "cwd": str(repo),
        "originator": originator,
        "timestamp": now,
    }
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch\n"
    call = {"type": "custom_tool_call", "call_id": "c1", "name": "apply_patch", "input": patch}
    lines = [
        {"timestamp": now, "type": "session_meta", "payload": meta},
        {"timestamp": now, "type": "response_item", "payload": call},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def _run(
    repo: Path, tmp_path: Path, commands: list[dict[str, Any]], loops: int = 40
) -> list[dict[str, Any]]:
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as w:
        for cmd in commands:
            w.write(json.dumps(cmd) + "\n")
    # The pipe's write end is closed: the watcher sees the commands, then EOF.
    out = io.StringIO()
    with os.fdopen(read_fd) as inp:
        code = run_watch(
            repo,
            out,
            inp,
            claude_root=tmp_path / "claude" / "projects",
            codex_root=tmp_path / "codex" / "sessions",
            claude_config=tmp_path / "claude",
            poll_s=0.01,
            max_loops=loops,
        )
    assert code == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_watch_end_to_end(git_repo: tuple[Path, GitRunner], tmp_path: Path) -> None:
    repo = _repo(git_repo)
    _claude_edit_log(tmp_path / "claude" / "projects" / "-x" / f"{UUID}.jsonl", repo)
    msgs = _run(
        repo,
        tmp_path,
        [
            {
                "type": "pane_started",
                "pane_id": "p1",
                "agent": "claude_code",
                "pid": 999999,
                "claude_session_id": UUID,
                "spawned_at": iso(time.time()),
            }
        ],
    )
    types = [m["type"] for m in msgs]
    assert types[:2] == ["hello", "tree"]
    assert all(m["v"] == 1 for m in msgs)
    assert msgs[1]["files"] == [{"path": "a.py", "size": 6}]
    sessions = [m for m in msgs if m["type"] == "session"]
    assert {m["session_id"] for m in sessions} == {f"claude_code:{UUID}"}
    assert sessions[-1]["pane_id"] == "p1" and sessions[-1]["external"] is False
    edits = [m for m in msgs if m["type"] == "activity" and m["kind"] == "edit"]
    assert [(e["phase"], e["paths"]) for e in edits] == [("start", ["a.py"]), ("done", ["a.py"])]


def test_external_codex_session_and_overlap(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    _claude_edit_log(tmp_path / "claude" / "projects" / "-x" / f"{UUID}.jsonl", repo)
    _codex_patch_log(tmp_path / "codex" / "sessions" / "2026" / "rollout-x.jsonl", repo)
    msgs = _run(repo, tmp_path, [])
    sessions = {m["session_id"]: m for m in msgs if m["type"] == "session"}
    assert sessions["codex:CX"]["external"] is True
    assert sessions[f"claude_code:{UUID}"]["external"] is True
    (overlap,) = [m for m in msgs if m["type"] == "overlap"]
    assert overlap["level"] == "active" and overlap["path"] == "a.py"
    assert overlap["sessions"] == ["claude_code:" + UUID, "codex:CX"]


def test_codex_cli_rollout_binds_to_pane(git_repo: tuple[Path, GitRunner], tmp_path: Path) -> None:
    repo = _repo(git_repo)
    spawned = iso(time.time() - 1)
    _codex_patch_log(tmp_path / "codex" / "sessions" / "r.jsonl", repo, originator="codex_cli_rs")
    msgs = _run(
        repo,
        tmp_path,
        [
            {
                "type": "pane_started",
                "pane_id": "p2",
                "agent": "codex",
                "pid": 5,
                "spawned_at": spawned,
            }
        ],
    )
    sessions = [m for m in msgs if m["type"] == "session" and m["session_id"] == "codex:CX"]
    assert sessions[-1]["pane_id"] == "p2" and sessions[-1]["external"] is False


def test_claude_pane_follows_registry(git_repo: tuple[Path, GitRunner], tmp_path: Path) -> None:
    repo = _repo(git_repo)
    new = "99999999-2222-3333-4444-555555555555"
    reg = tmp_path / "claude" / "sessions"
    reg.mkdir(parents=True)
    (reg / "4242.json").write_text(json.dumps({"pid": 4242, "sessionId": new, "cwd": str(repo)}))
    _claude_edit_log(tmp_path / "claude" / "projects" / "-x" / f"{new}.jsonl", repo, sid=new)
    msgs = _run(
        repo,
        tmp_path,
        [
            {
                "type": "pane_started",
                "pane_id": "p1",
                "agent": "claude_code",
                "pid": 4242,
                "claude_session_id": UUID,
                "spawned_at": iso(time.time()),
            }
        ],
    )
    sessions = [m for m in msgs if m["type"] == "session"]
    old = [m for m in sessions if m["session_id"] == f"claude_code:{UUID}"]
    assert old[-1]["ended"] is True
    current = [m for m in sessions if m["session_id"] == f"claude_code:{new}"]
    assert current[-1]["pane_id"] == "p1"


def test_out_of_scope_logs_ignored(git_repo: tuple[Path, GitRunner], tmp_path: Path) -> None:
    repo = _repo(git_repo)
    _claude_edit_log(tmp_path / "claude" / "projects" / "-y" / f"{UUID}.jsonl", tmp_path / "other")
    msgs = _run(repo, tmp_path, [])
    assert [m for m in msgs if m["type"] in ("session", "activity")] == []


def test_watch_exits_on_stdin_eof(git_repo: tuple[Path, GitRunner], tmp_path: Path) -> None:
    repo = _repo(git_repo)
    out = io.StringIO()
    done: list[int] = []
    thread = threading.Thread(
        target=lambda: done.append(
            run_watch(
                repo,
                out,
                io.StringIO(""),
                claude_root=tmp_path / "c",
                codex_root=tmp_path / "x",
                claude_config=tmp_path,
                poll_s=0.01,
            )
        )
    )
    thread.start()
    thread.join(timeout=5)
    assert done == [0]


def test_cli_watch_help() -> None:
    assert main(["watch", "--help"]) == 0


def test_coalesce_keeps_last_status_and_session_per_id() -> None:
    from tandem.live.watch import coalesce

    msgs: list[dict[str, object]] = [
        {"type": "status", "session_id": "A", "state": "working"},
        {"type": "activity", "session_id": "A", "id": "1"},
        {"type": "session", "session_id": "A", "title": None},
        {"type": "status", "session_id": "B", "state": "idle"},
        {"type": "status", "session_id": "A", "state": "idle"},
        {"type": "session", "session_id": "A", "title": "T"},
    ]
    assert coalesce(msgs) == [
        {"type": "activity", "session_id": "A", "id": "1"},
        {"type": "status", "session_id": "B", "state": "idle"},
        {"type": "status", "session_id": "A", "state": "idle"},
        {"type": "session", "session_id": "A", "title": "T"},
    ]


# -- final review fixes --------------------------------------------------------------


def _watcher(repo: Path, tmp_path: Path, clock: Any = time.time) -> Any:
    from tandem.git_reader import GitReader
    from tandem.live.watch import Watcher

    git = GitReader(repo)
    return Watcher(
        git.discover().top_level,
        io.StringIO(),
        tmp_path / "claude" / "projects",
        tmp_path / "codex" / "sessions",
        tmp_path / "claude",
        clock,
        git,
    )


def _out(w: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in w.out.getvalue().splitlines()]


def test_claude_log_without_cwd_yet_is_retried(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    log = tmp_path / "claude" / "projects" / "-x" / f"{UUID}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"type": "last-prompt", "sessionId": UUID}) + "\n")
    w = _watcher(repo, tmp_path)
    w.discover()
    _claude_edit_log(log, repo)
    w.discover()
    w.poll_tails()
    assert any(m["type"] == "activity" for m in _out(w))


def _codex_rollout(path: Path, repo: Path, sid: str, created: float) -> None:
    meta = {
        "id": sid,
        "session_id": sid,
        "cwd": str(repo),
        "originator": "codex_cli_rs",
        "timestamp": iso(created),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"timestamp": iso(created), "type": "session_meta", "payload": meta}) + "\n"
    )


def _start(w: Any, pane: str, spawned: float) -> None:
    w.handle(
        {
            "type": "pane_started",
            "pane_id": pane,
            "agent": "codex",
            "pid": 1,
            "spawned_at": iso(spawned),
        }
    )


def test_bound_codex_pane_keeps_session_when_input_is_stale(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    now = time.time()
    clock = {"t": now - 100}
    w = _watcher(repo, tmp_path, lambda: clock["t"])
    _start(w, "p1", now - 100)
    _codex_rollout(tmp_path / "codex" / "sessions" / "a.jsonl", repo, "A", now - 99)
    w.discover()
    w.handle({"type": "pane_input", "pane_id": "p1", "at": iso(now - 98)})
    clock["t"] = now
    _codex_rollout(tmp_path / "codex" / "sessions" / "b.jsonl", repo, "B", now)
    w.discover()
    sessions = {m["session_id"]: m for m in _out(w) if m["type"] == "session"}
    assert sessions["codex:A"]["pane_id"] == "p1" and sessions["codex:A"]["ended"] is False
    assert sessions["codex:B"]["external"] is True


def test_bound_codex_pane_follows_new_session_right_after_typing(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    now = time.time()
    clock = {"t": now - 100}
    w = _watcher(repo, tmp_path, lambda: clock["t"])
    _start(w, "p1", now - 100)
    _codex_rollout(tmp_path / "codex" / "sessions" / "a.jsonl", repo, "A", now - 99)
    w.discover()
    clock["t"] = now - 2
    w.handle({"type": "pane_input", "pane_id": "p1", "at": iso(now - 2)})
    clock["t"] = now
    _codex_rollout(tmp_path / "codex" / "sessions" / "b.jsonl", repo, "B", now)
    w.discover()
    sessions = {m["session_id"]: m for m in _out(w) if m["type"] == "session"}
    assert sessions["codex:B"]["pane_id"] == "p1"


def test_codex_binds_to_the_pane_typed_into(
    git_repo: tuple[Path, GitRunner], tmp_path: Path
) -> None:
    repo = _repo(git_repo)
    now = time.time()
    clock = {"t": now - 10}
    w = _watcher(repo, tmp_path, lambda: clock["t"])
    _start(w, "pA", now - 10)
    _start(w, "pB", now - 9)
    clock["t"] = now - 1
    w.handle({"type": "pane_input", "pane_id": "pB", "at": iso(now - 1)})
    clock["t"] = now
    _codex_rollout(tmp_path / "codex" / "sessions" / "b.jsonl", repo, "B", now)
    w.discover()
    sessions = {m["session_id"]: m for m in _out(w) if m["type"] == "session"}
    assert sessions["codex:B"]["pane_id"] == "pB"


def test_unreadable_log_does_not_kill_the_watcher(
    git_repo: tuple[Path, GitRunner], tmp_path: Path, monkeypatch: Any
) -> None:
    from tandem.live.tail import Tail

    repo = _repo(git_repo)
    _claude_edit_log(tmp_path / "claude" / "projects" / "-x" / f"{UUID}.jsonl", repo)
    w = _watcher(repo, tmp_path)
    w.discover()

    def boom(self: Tail) -> tuple[list[dict[str, Any]], bool]:
        raise PermissionError("gone")

    monkeypatch.setattr(Tail, "read", boom)
    w.poll_tails()
    assert any(m["type"] == "diagnostic" and m["code"] == "log_read_failed" for m in _out(w))
