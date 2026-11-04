from __future__ import annotations

import json
from typing import Any

from tandem.live.extract import Activity, ClaudeExtractor, CodexExtractor, Meta, Turn

TS = "2026-11-04T17:00:00.000Z"


def _claude_use(name: str, tool_id: str, **inp: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "sessionId": "s1",
        "cwd": "/r",
        "timestamp": TS,
        "message": {
            "model": "claude-x",
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}],
        },
    }


def _claude_result(tool_id: str, *, error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": "s1",
        "cwd": "/r",
        "timestamp": TS,
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error}]
        },
    }


def _acts(signals: list[Any]) -> list[Activity]:
    return [s for s in signals if isinstance(s, Activity)]


def test_claude_read_and_edit_lifecycle() -> None:
    ex = ClaudeExtractor()
    (read,) = _acts(ex.feed(_claude_use("Read", "t1", file_path="/r/src/a.py")))
    assert (read.kind, read.phase, read.paths, read.tool) == (
        "read",
        "start",
        ("/r/src/a.py",),
        "Read",
    )
    assert read.at is not None
    ex.feed(_claude_result("t1"))

    (edit,) = _acts(ex.feed(_claude_use("Edit", "t2", file_path="src/b.py")))
    assert (edit.kind, edit.phase, edit.paths) == ("edit", "start", ("/r/src/b.py",))
    assert ex.pending == 1
    (done,) = _acts(ex.feed(_claude_result("t2")))
    assert (done.kind, done.phase, done.paths, done.call_id) == (
        "edit",
        "done",
        ("/r/src/b.py",),
        "t2",
    )
    assert ex.pending == 0

    ex.feed(_claude_use("Write", "t3", file_path="/r/c.py"))
    (failed,) = _acts(ex.feed(_claude_result("t3", error=True)))
    assert failed.phase == "failed"


def test_claude_glob_without_path_reads_cwd() -> None:
    ex = ClaudeExtractor()
    (act,) = _acts(ex.feed(_claude_use("Grep", "t1", pattern="x")))
    assert act.paths == ("/r",)


def test_claude_turn_and_title() -> None:
    ex = ClaudeExtractor()
    sigs = ex.feed(
        {
            "type": "user",
            "sessionId": "s1",
            "cwd": "/r",
            "timestamp": TS,
            "message": {"content": "hello"},
        }
    )
    assert Turn(True, sigs[0].at) in sigs  # type: ignore[union-attr]
    assert [a.kind for a in _acts(sigs)] == ["prompt"]
    assert any(isinstance(s, Meta) and s.cwd == "/r" for s in sigs)

    sigs = ex.feed({"type": "system", "subtype": "turn_duration", "timestamp": TS})
    assert [s for s in sigs if isinstance(s, Turn)] == [Turn(False, sigs[0].at)]  # type: ignore[union-attr]

    sigs = ex.feed({"type": "ai-title", "aiTitle": "Fix the parser", "sessionId": "s1"})
    assert sigs == [Meta(title="Fix the parser")]


def test_claude_end_turn_without_pending_closes_turn() -> None:
    ex = ClaudeExtractor()
    rec = {
        "type": "assistant",
        "sessionId": "s1",
        "cwd": "/r",
        "timestamp": TS,
        "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]},
    }
    assert any(isinstance(s, Turn) and not s.working for s in ex.feed(rec))


def _codex(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": TS, "type": kind, "payload": payload}


def _meta(cwd: str = "/r/a") -> dict[str, Any]:
    return _codex(
        "session_meta",
        {"id": "C", "session_id": "S", "cwd": cwd, "originator": "codex_cli_rs"},
    )


def test_codex_meta_root_session() -> None:
    ex = CodexExtractor()
    sigs = ex.feed(_meta())
    assert sigs == [Meta(cwd="/r/a", root_session="S", originator="codex_cli_rs")]


def test_codex_relative_patch_path() -> None:
    ex = CodexExtractor()
    ex.feed(_meta("/r/a"))
    patch = "*** Begin Patch\n*** Update File: ../b/x.ts\n@@\n-a\n+b\n*** End Patch\n"
    (act,) = _acts(
        ex.feed(
            _codex(
                "response_item",
                {
                    "type": "custom_tool_call",
                    "call_id": "c1",
                    "name": "apply_patch",
                    "input": patch,
                },
            )
        )
    )
    assert (act.kind, act.phase, act.paths) == ("edit", "start", ("/r/b/x.ts",))
    assert ex.pending == 1
    (done,) = _acts(
        ex.feed(
            _codex(
                "response_item",
                {"type": "custom_tool_call_output", "call_id": "c1", "output": "ok"},
            )
        )
    )
    assert (done.phase, done.paths) == ("done", ("/r/b/x.ts",))
    assert ex.pending == 0


def test_codex_function_call_patch_uses_workdir_and_moves() -> None:
    ex = CodexExtractor()
    ex.feed(_meta("/r"))
    patch = "*** Begin Patch\n*** Add File: n.py\n+x\n*** Update File: o.py\n*** Move to: p.py\n"
    args = json.dumps({"input": patch, "workdir": "/r/sub"})
    (act,) = _acts(
        ex.feed(
            _codex(
                "response_item",
                {
                    "type": "function_call",
                    "call_id": "c2",
                    "name": "apply_patch",
                    "arguments": args,
                },
            )
        )
    )
    assert act.paths == ("/r/sub/n.py", "/r/sub/o.py", "/r/sub/p.py")


def test_codex_file_change_and_turns() -> None:
    ex = CodexExtractor()
    ex.feed(_meta("/r"))
    assert ex.feed(_codex("event_msg", {"type": "task_started"})) == [Turn(True, ex.last_at)]
    item = {
        "type": "FileChange",
        "id": "exec-1",
        "changes": {"/r/x.py": {"type": "update"}, "/r/y.py": {"type": "add"}},
    }
    acts = _acts(ex.feed(_codex("event_msg", {"type": "item_completed", "item": item})))
    assert [(a.kind, a.phase, a.paths) for a in acts] == [
        ("edit", "done", ("/r/x.py",)),
        ("edit", "done", ("/r/y.py",)),
    ]
    assert ex.feed(_codex("event_msg", {"type": "task_complete"})) == [Turn(False, ex.last_at)]
    assert any(
        isinstance(s, Turn) and not s.working
        for s in ex.feed(_codex("event_msg", {"type": "turn_aborted"}))
    )
    assert [a.kind for a in _acts(ex.feed(_codex("event_msg", {"type": "user_message"})))] == [
        "prompt"
    ]


def test_codex_turn_context_updates_cwd_and_model() -> None:
    ex = CodexExtractor()
    ex.feed(_meta("/r"))
    sigs = ex.feed(_codex("turn_context", {"cwd": "/r/z", "model": "gpt-x"}))
    assert sigs == [Meta(cwd="/r/z", model="gpt-x")]


def test_shell_tools() -> None:
    claude = ClaudeExtractor()
    (sh,) = _acts(claude.feed(_claude_use("Bash", "t1", command="ls")))
    assert (sh.kind, sh.phase, sh.paths) == ("shell", "start", ())

    codex = CodexExtractor()
    codex.feed(_meta("/r"))
    for name, kind in (("exec", "custom_tool_call"), ("exec_command", "function_call")):
        payload: dict[str, Any] = {"type": kind, "call_id": f"x-{name}", "name": name}
        payload["input" if kind == "custom_tool_call" else "arguments"] = "{}"
        (act,) = _acts(codex.feed(_codex("response_item", payload)))
        assert (act.kind, act.tool) == ("shell", name)
    assert codex.pending == 2
