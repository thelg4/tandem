"""Generate the synthetic agent-log fixtures.

The record shapes mirror what docs/log-audit.md found in real logs; every value
here is invented. Re-run after changing a scenario:

    python tests/fixtures/build_fixtures.py

Each scenario's expected normalized result is asserted in tests/test_adapters_*.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
CWD = "/work/demo"
APP = f"{CWD}/app.py"


def write(rel: str, records: list[dict[str, Any]]) -> None:
    path = HERE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


# -- Claude Code 2.1 ---------------------------------------------------------------------


def claude_code_2_1() -> list[dict[str, Any]]:
    session = "cc-session-0001"
    n = 0

    def base(kind: str, parent: str | None) -> dict[str, Any]:
        nonlocal n
        n += 1
        return {
            "type": kind,
            "uuid": f"u{n:03d}",
            "parentUuid": parent,
            "sessionId": session,
            "timestamp": f"2026-10-01T12:00:{n:02d}.000Z",
            "cwd": CWD,
            "gitBranch": "main",
            "version": "2.1.302",
            "isSidechain": False,
            "userType": "external",
        }

    def assistant(parent: str, request: str, blocks: list[dict[str, Any]], usage: dict[str, int]):
        out = []
        for block in blocks:  # one record per content block, each repeating the usage
            rec = base("assistant", parent)
            rec["requestId"] = request
            rec["message"] = {
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [block],
                "usage": dict(usage),
            }
            out.append(rec)
            parent = rec["uuid"]
        return out

    def result(parent: str, tool_id: str, tur: Any, *, error: bool = False) -> dict[str, Any]:
        rec = base("user", parent)
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}
        if error:
            block["is_error"] = True
            block["content"] = "String to replace not found in file."
        rec["message"] = {"role": "user", "content": [block]}
        rec["toolUseResult"] = tur
        return rec

    u1 = {"input_tokens": 1200, "cache_creation_input_tokens": 300,
          "cache_read_input_tokens": 0, "output_tokens": 80}  # fmt: skip
    u2 = {"input_tokens": 40, "cache_creation_input_tokens": 0,
          "cache_read_input_tokens": 1500, "output_tokens": 60}  # fmt: skip

    records: list[dict[str, Any]] = []
    prompt = base("user", None)
    prompt["message"] = {"role": "user", "content": "<synthetic prompt>"}
    records.append(prompt)

    created = "def run():\n    return 1\n"
    records += assistant(
        prompt["uuid"],
        "req_001",
        [
            {"type": "text", "text": "<synthetic reply>"},
            {"type": "tool_use", "id": "toolu_001", "name": "Write",
             "input": {"file_path": APP, "content": created}},
        ],
        u1,
    )  # fmt: skip
    records.append(
        result(records[-1]["uuid"], "toolu_001", {
            "type": "create", "filePath": APP, "content": created,
            "originalFile": None, "structuredPatch": [], "userModified": False,
        })
    )  # fmt: skip

    records += assistant(
        records[-1]["uuid"],
        "req_002",
        [{"type": "tool_use", "id": "toolu_002", "name": "Edit",
          "input": {"file_path": APP, "old_string": "    return 1", "new_string": "    return 2",
                    "replace_all": False}}],
        u2,
    )  # fmt: skip
    records.append(
        result(records[-1]["uuid"], "toolu_002", {
            "filePath": APP, "oldString": "    return 1", "newString": "    return 2",
            "originalFile": created, "replaceAll": False, "userModified": False,
            "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2,
                                 "lines": [" def run():", "-    return 1", "+    return 2"]}],
        })
    )  # fmt: skip

    records += assistant(
        records[-1]["uuid"],
        "req_003",
        [{"type": "tool_use", "id": "toolu_003", "name": "Edit",
          "input": {"file_path": APP, "old_string": "missing", "new_string": "x"}}],
        u2,
    )  # fmt: skip
    records.append(
        result(records[-1]["uuid"], "toolu_003",
               "Error: String to replace not found in file.", error=True)
    )  # fmt: skip

    records += assistant(
        records[-1]["uuid"],
        "req_004",
        [{"type": "tool_use", "id": "toolu_004", "name": "Bash",
          "input": {"command": "pytest -q", "description": "Run tests"}}],
        u2,
    )  # fmt: skip
    records.append(
        result(records[-1]["uuid"], "toolu_004",
               {"stdout": "1 passed", "stderr": "", "interrupted": False, "isImage": False})
    )  # fmt: skip

    # Edit the user changed during approval.
    records += assistant(
        records[-1]["uuid"],
        "req_005",
        [{"type": "tool_use", "id": "toolu_005", "name": "Edit",
          "input": {"file_path": APP, "old_string": "    return 2", "new_string": "    return 3"}}],
        u2,
    )  # fmt: skip
    records.append(
        result(records[-1]["uuid"], "toolu_005", {
            "filePath": APP, "oldString": "    return 2", "newString": "    return 30",
            "originalFile": "def run():\n    return 2\n", "replaceAll": False,
            "userModified": True,
            "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2,
                                 "lines": [" def run():", "-    return 2", "+    return 30"]}],
        })
    )  # fmt: skip

    # Call with no result: the session ended before it completed.
    records += assistant(
        records[-1]["uuid"],
        "req_006",
        [{"type": "tool_use", "id": "toolu_006", "name": "Write",
          "input": {"file_path": f"{CWD}/never.py", "content": "x = 1\n"}}],
        u2,
    )  # fmt: skip

    records.append({"type": "last-prompt", "sessionId": session, "lastPrompt": "<synthetic>",
                    "leafUuid": records[-1]["uuid"]})  # fmt: skip
    return records


# -- Codex ---------------------------------------------------------------------------------


class Rollout:
    def __init__(self, thread: str, start_ordinal: int = 0) -> None:
        self.thread = thread
        self.records: list[dict[str, Any]] = []
        self.ordinal = start_ordinal

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append({
            "timestamp": f"2026-10-02T09:{self.ordinal // 60:02d}:{self.ordinal % 60:02d}.000Z",
            "ordinal": self.ordinal,
            "type": kind,
            "payload": payload,
        })  # fmt: skip
        self.ordinal += 1

    def meta(self, version: str, **extra: Any) -> None:
        self.add(
            "session_meta",
            {
                "id": self.thread,
                "session_id": "cx-session-0001",
                "timestamp": "2026-10-02T09:00:00Z",
                "cwd": CWD,
                "originator": "codex_cli_rs",
                "cli_version": version,
                "source": "cli",
                "model_provider": "openai",
                "base_instructions": None,
                "history_mode": "full",
                "git": {},
                **extra,
            },
        )

    def turn(self, turn_id: str, model: str = "gpt-5-codex") -> None:
        self.add(
            "turn_context",
            {
                "cwd": CWD,
                "model": model,
                "turn_id": turn_id,
                "approval_policy": "on-request",
                "sandbox_policy": {},
            },
        )

    def tokens(self, total: dict[str, int], last: dict[str, int]) -> None:
        self.add(
            "event_msg",
            {
                "type": "token_count",
                "rate_limits": None,
                "info": {
                    "total_token_usage": total,
                    "last_token_usage": last,
                    "model_context_window": 200000,
                },
            },
        )

    def file_change(self, item_id: str, changes: dict[str, Any]) -> None:
        self.add("event_msg", {"type": "item_completed", "thread_id": self.thread,
                               "turn_id": "t1", "completed_at_ms": 0, "item": {
                                   "type": "FileChange", "id": item_id, "changes": changes,
                                   "status": "completed", "stdout": ""}})  # fmt: skip


def usage(inp: int, cached: int, out: int) -> dict[str, int]:
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": out,
        "reasoning_output_tokens": 0,
        "total_tokens": inp + out,
    }


PATCH_OK = """*** Begin Patch
*** Update File: app.py
@@ def run():
-    return 2
+    return 3
*** End Patch
"""

PATCH_BAD = """*** Begin Patch
*** Update File: app.py
@@
-    return 99
+    return 100
*** End Patch
"""


def codex_0_98() -> list[dict[str, Any]]:
    r = Rollout("cx-thread-0098")
    r.meta("0.98.0")
    r.turn("t1")
    r.add(
        "response_item",
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "<synthetic prompt>"}],
        },
    )
    r.add("response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "call_p1",
                            "name": "apply_patch", "input": PATCH_OK})  # fmt: skip
    r.add(
        "response_item",
        {
            "type": "custom_tool_call_output",
            "call_id": "call_p1",
            "output": "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nSuccess.",
        },
    )
    r.tokens(usage(1000, 200, 50), usage(1000, 200, 50))
    r.tokens(usage(1000, 200, 50), usage(1000, 200, 50))  # repeated observation: no new usage
    r.add("response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "call_p2",
                            "name": "apply_patch", "input": PATCH_BAD})  # fmt: skip
    r.add("response_item", {"type": "custom_tool_call_output", "call_id": "call_p2",
                            "output": "apply_patch verification failed: Failed to find expected "
                                      "lines in app.py"})  # fmt: skip
    r.add(
        "response_item",
        {
            "type": "function_call",
            "name": "shell_command",
            "call_id": "call_s1",
            "arguments": json.dumps({"command": "pytest -q", "workdir": CWD}),
        },
    )
    r.add(
        "response_item",
        {
            "type": "function_call_output",
            "call_id": "call_s1",
            "output": "Exit code: 1\nWall time: 1.0 seconds\nOutput:\n1 failed",
        },
    )
    r.tokens(usage(1600, 900, 120), usage(600, 700, 70))
    return r.records


def codex_0_144() -> list[dict[str, Any]]:
    r = Rollout("cx-thread-0144")
    r.meta("0.144.0-alpha.4")
    r.turn("t1")
    r.add("response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "call_a1",
                            "name": "apply_patch", "input": PATCH_OK})  # fmt: skip
    r.add("response_item", {"type": "custom_tool_call_output", "call_id": "call_a1", "output":
                            json.dumps({"output": "Success.", "metadata": {"exit_code": 0,
                                        "duration_seconds": 0.1}})})  # fmt: skip
    r.file_change("call_a1", {APP: {"type": "update", "move_path": None, "unified_diff":
                  "@@ -1,2 +1,2 @@\n def run():\n-    return 2\n+    return 3\n"}})  # fmt: skip
    # An exec code cell that runs apply_patch internally: the FileChange id matches no call.
    r.add("response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "call_e1",
                            "name": "exec", "input": "await tools.apply_patch(`*** Begin Patch\n"
                            "*** Add File: docs/notes.md\n+# Notes\n*** End Patch`)"})  # fmt: skip
    r.file_change("item-77", {f"{CWD}/docs/notes.md": {"type": "add", "content": "# Notes\n"}})
    r.add(
        "response_item",
        {
            "type": "custom_tool_call_output",
            "call_id": "call_e1",
            "output": [{"type": "input_text", "text": "done"}],
        },
    )
    r.tokens(usage(2000, 1500, 90), usage(2000, 1500, 90))
    r.tokens(usage(500, 100, 20), usage(500, 100, 20))  # counter reset: starts a new segment
    return r.records


def codex_0_153_subagent() -> list[dict[str, Any]]:
    r = Rollout("cx-thread-0153")
    r.meta("0.153.4", parent_thread_id="cx-thread-parent", subagent_history_start_ordinal=4)
    # Ordinals 1-3 are history copied from the parent thread; they must not count again.
    r.turn("t0")
    r.file_change("item-parent", {APP: {"type": "update", "move_path": None, "unified_diff":
                  "@@ -1,2 +1,2 @@\n def run():\n-    return 3\n+    return 4\n"}})  # fmt: skip
    r.tokens(usage(5000, 0, 400), usage(5000, 0, 400))
    # Subagent's own work starts at ordinal 4.
    r.turn("t1")
    r.add("response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "call_x1",
                            "name": "exec", "input": "<synthetic code cell>"})  # fmt: skip
    r.file_change(
        "item-88",
        {
            f"{CWD}/old.txt": {"type": "delete"},
            f"{CWD}/lib/util.py": {
                "type": "update",
                "move_path": None,
                "unified_diff": "@@ -0,0 +1,1 @@\n+X = 1\n",
            },
        },
    )
    r.add(
        "response_item",
        {
            "type": "custom_tool_call_output",
            "call_id": "call_x1",
            "output": [{"type": "input_text", "text": "done"}],
        },
    )
    r.tokens(usage(5300, 4000, 450), usage(300, 4000, 50))
    return r.records


def main() -> None:
    write("claude_code/2.1/session.jsonl", claude_code_2_1())
    write("codex/0.98-patch-text/rollout.jsonl", codex_0_98())
    write("codex/0.144-file-change/rollout.jsonl", codex_0_144())
    write("codex/0.153-subagent/rollout.jsonl", codex_0_153_subagent())


if __name__ == "__main__":
    main()
