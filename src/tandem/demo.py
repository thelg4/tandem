"""``tandem demo``: run the real pipeline on a synthetic repository.

Builds a throwaway Git repository and matching Claude Code and Codex logs in a temporary
directory, then scans it and prints the report and one interaction's evidence. Nothing
is read from or written to the user's own logs, repositories, or Tandem database.

Every log record and commit here is invented. The record shapes follow
docs/log-audit.md, so the adapters, attribution, interactions, and metrics run exactly
as they do on real logs. The story:

1. Claude Code writes a token-bucket rate limiter and its tests.
2. Codex rewrites the limiter's refill logic and adds a README.
3. A second Claude Code session adds a ``retry_after`` helper.
4. Codex removes that helper exactly (an exact revert) and adds a config module.
5. Claude Code raises the default capacity and documents it in one edit; Codex restores
   the old capacity but keeps the docs, so that edit is only partly reverted.
6. Someone edits a Claude line by hand (an unattributed change), and later commits
   let the earlier lines mature.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

START = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)

LIMITER = '''"""Token-bucket rate limiting."""

import time


class TokenBucket:
    """Allow bursts up to capacity, refilling at rate tokens per second."""

    def __init__(self, rate: float, capacity: int = 10) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.updated = time.monotonic()

    def allow(self, cost: int = 1) -> bool:
        now = time.monotonic()
        self.tokens += (now - self.updated) * self.rate
        if self.tokens > self.capacity:
            self.tokens = self.capacity
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
'''

TESTS = """from limiter import TokenBucket


def test_allows_a_burst_up_to_capacity() -> None:
    bucket = TokenBucket(rate=1.0, capacity=3)
    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]


def test_cost_larger_than_capacity_is_refused() -> None:
    assert not TokenBucket(rate=1.0, capacity=2).allow(cost=5)
"""

README = """# limiter

A small token-bucket rate limiter.

```python
bucket = TokenBucket(rate=5.0, capacity=10)
if bucket.allow():
    handle(request)
```
"""

CONFIG = '''"""Limits per endpoint, in requests per second."""

LIMITS = {
    "search": 5.0,
    "upload": 0.5,
}
'''


def _sub(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


class _Demo:
    def __init__(self, root: Path) -> None:
        self.repo = root / "limiter"
        self.claude_dir = root / "logs" / "claude"
        self.codex_dir = root / "logs" / "codex"
        self.db = root / "tandem.sqlite3"
        self.clock = START
        self.files: dict[str, str] = {}
        self.claude: dict[str, list[dict[str, Any]]] = {}
        self.codex: dict[str, list[dict[str, Any]]] = {}
        self.codex_totals: dict[str, dict[str, int]] = {}
        self.repo.mkdir(parents=True)
        self._git("init", "-q", "-b", "main")

    # -- time and git ------------------------------------------------------------------

    def _tick(self, minutes: int = 3) -> str:
        self.clock += timedelta(minutes=minutes)
        return self.clock.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _git(self, *args: str) -> None:
        when = self.clock.strftime("%Y-%m-%dT%H:%M:%SZ")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.repo),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Demo",
            "GIT_AUTHOR_EMAIL": "demo@example.invalid",
            "GIT_COMMITTER_NAME": "Demo",
            "GIT_COMMITTER_EMAIL": "demo@example.invalid",
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        }
        git = shutil.which("git") or "git"
        # Fixed arguments, run only on the demo's own temporary repository.
        subprocess.run([git, *args], cwd=self.repo, env=env, check=True, capture_output=True)  # noqa: S603

    def commit(self, message: str) -> None:
        self._tick(5)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def _write(self, rel: str, text: str) -> None:
        self.files[rel] = text
        (self.repo / rel).write_text(text, encoding="utf-8")

    def hand_edit(self, rel: str, old: str, new: str) -> None:
        self._write(rel, _sub(self.files[rel], old, new))

    # -- Claude Code -------------------------------------------------------------------

    def _claude_record(self, session: str, kind: str, **fields: Any) -> dict[str, Any]:
        records = self.claude.setdefault(session, [])
        rec: dict[str, Any] = {
            "type": kind,
            "uuid": f"{session}-{len(records):03d}",
            "parentUuid": records[-1]["uuid"] if records else None,
            "sessionId": session,
            "timestamp": self._tick(1),
            "cwd": str(self.repo),
            "gitBranch": "main",
            "version": "2.1.302",
            "isSidechain": False,
            "userType": "external",
            **fields,
        }
        records.append(rec)
        return rec

    def _claude_call(self, session: str, tool: str, inp: dict[str, Any], result: Any) -> None:
        records = self.claude.setdefault(session, [])
        if not records:
            self._claude_record(
                session, "user", message={"role": "user", "content": "<synthetic prompt>"}
            )
        n = len(records)
        tool_id = f"toolu_{session}_{n:03d}"
        usage = {
            "input_tokens": 900 + 40 * n,
            "cache_creation_input_tokens": 1200,
            "cache_read_input_tokens": 6000 + 500 * n,
            "output_tokens": 350,
        }
        self._claude_record(
            session,
            "assistant",
            requestId=f"req_{session}_{n:03d}",
            message={
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "tool_use", "id": tool_id, "name": tool, "input": inp}],
                "usage": usage,
            },
        )
        self._claude_record(
            session,
            "user",
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
            },
            toolUseResult=result,
        )

    def claude_write(self, session: str, rel: str, text: str) -> None:
        path = str(self.repo / rel)
        self._claude_call(
            session,
            "Write",
            {"file_path": path, "content": text},
            {"type": "create", "filePath": path, "content": text, "originalFile": None,
             "structuredPatch": [], "userModified": False},
        )  # fmt: skip
        self._write(rel, text)

    def claude_edit(self, session: str, rel: str, old: str, new: str) -> None:
        path, before = str(self.repo / rel), self.files[rel]
        after = _sub(before, old, new)
        self._claude_call(
            session,
            "Edit",
            {"file_path": path, "old_string": old, "new_string": new, "replace_all": False},
            {"filePath": path, "oldString": old, "newString": new, "originalFile": before,
             "replaceAll": False, "userModified": False,
             "structuredPatch": _structured_patch(before, after)},
        )  # fmt: skip
        self._write(rel, after)

    def claude_rewrite(self, session: str, rel: str, text: str) -> None:
        """A Write over an existing file: one edit that may change several places."""
        path, before = str(self.repo / rel), self.files[rel]
        self._claude_call(
            session,
            "Write",
            {"file_path": path, "content": text},
            {"type": "update", "filePath": path, "content": text, "originalFile": before,
             "structuredPatch": _structured_patch(before, text), "userModified": False},
        )  # fmt: skip
        self._write(rel, text)

    # -- Codex -------------------------------------------------------------------------

    def _codex_record(self, thread: str, kind: str, payload: dict[str, Any]) -> None:
        records = self.codex.setdefault(thread, [])
        if not records:
            records.append(self._codex_line(thread, 0, "session_meta", {
                "id": thread, "session_id": thread, "timestamp": self._tick(1)[:19] + "Z",
                "cwd": str(self.repo), "originator": "codex_cli_rs", "cli_version": "0.153.4",
                "source": "cli", "model_provider": "openai", "base_instructions": None,
                "history_mode": "full", "git": {},
            }))  # fmt: skip
            records.append(self._codex_line(thread, 1, "turn_context", {
                "cwd": str(self.repo), "model": "gpt-5-codex", "turn_id": "t1",
                "approval_policy": "on-request", "sandbox_policy": {},
            }))  # fmt: skip
        records.append(self._codex_line(thread, len(records), kind, payload))

    def _codex_line(self, thread: str, ordinal: int, kind: str, payload: Any) -> dict[str, Any]:
        return {"timestamp": self._tick(1), "ordinal": ordinal, "type": kind, "payload": payload}

    def codex_change(self, thread: str, changes: dict[str, str | None]) -> None:
        """Apply one Codex patch: ``{path: new text}``; None deletes the file."""
        n = len(self.codex.get(thread, []))
        call = f"call_{thread}_{n:03d}"
        self._codex_record(thread, "response_item", {
            "type": "custom_tool_call", "status": "completed", "call_id": call,
            "name": "apply_patch", "input": "*** Begin Patch\n<synthetic patch>\n*** End Patch\n",
        })  # fmt: skip
        self._codex_record(thread, "response_item", {
            "type": "custom_tool_call_output", "call_id": call,
            "output": json.dumps({"output": "Success.", "metadata": {"exit_code": 0}}),
        })  # fmt: skip
        file_changes: dict[str, Any] = {}
        for rel, text in changes.items():
            path = str(self.repo / rel)
            if rel not in self.files:
                assert text is not None
                file_changes[path] = {"type": "add", "content": text}
                self._write(rel, text)
            elif text is None:
                file_changes[path] = {"type": "delete"}
                (self.repo / rel).unlink()
                del self.files[rel]
            else:
                diff = _unified(self.files[rel], text)
                file_changes[path] = {"type": "update", "move_path": None, "unified_diff": diff}
                self._write(rel, text)
        self._codex_record(thread, "event_msg", {
            "type": "item_completed", "thread_id": thread, "turn_id": "t1",
            "completed_at_ms": 0, "item": {"type": "FileChange", "id": call,
                                           "changes": file_changes, "status": "completed"},
        })  # fmt: skip
        last = {"input_tokens": 14000, "cached_input_tokens": 11000,
                "cache_write_input_tokens": 0, "output_tokens": 600,
                "reasoning_output_tokens": 250, "total_tokens": 14600}  # fmt: skip
        total = self.codex_totals.setdefault(thread, dict.fromkeys(last, 0))
        for k, v in last.items():
            total[k] += v
        self._codex_record(thread, "event_msg", {
            "type": "token_count", "rate_limits": None,
            "info": {"total_token_usage": dict(total), "last_token_usage": last,
                     "model_context_window": 272000},
        })  # fmt: skip

    def save_logs(self) -> None:
        for session, records in self.claude.items():
            _jsonl(self.claude_dir / "projects" / "-demo-limiter" / f"{session}.jsonl", records)
        for thread, records in self.codex.items():
            name = f"rollout-{START:%Y-%m-%d}T09-00-00-{thread}.jsonl"
            _jsonl(self.codex_dir / "sessions" / f"{START:%Y/%m/%d}" / name, records)


def _jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def _unified(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), n=3
    )
    return "".join(list(lines)[2:])


_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _structured_patch(before: str, after: str) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    for line in _unified(before, after).splitlines():
        m = _HEADER.match(line)
        if m:
            a, b, c, d = m.groups()
            hunks.append({"oldStart": int(a), "oldLines": int(b or 1), "newStart": int(c),
                          "newLines": int(d or 1), "lines": []})  # fmt: skip
        elif hunks and not line.startswith("\\"):
            hunks[-1]["lines"].append(line)
    return hunks


def build(root: Path) -> _Demo:
    """Create the demo repository and logs under ``root``."""
    d = _Demo(root)
    claude1, claude2, codex1, codex2 = "c1a7e5d0", "c2b8f6e1", "x9d3a1c4", "x8e4b2d5"

    d.claude_write(claude1, "limiter.py", LIMITER)
    d.commit("Add token-bucket limiter")
    d.claude_write(claude1, "test_limiter.py", TESTS)
    d.commit("Test burst and oversized cost")

    refill_old = (
        "        self.tokens += (now - self.updated) * self.rate\n"
        "        if self.tokens > self.capacity:\n"
        "            self.tokens = self.capacity\n"
    )
    refill_new = (
        "        elapsed = max(0.0, now - self.updated)\n"
        "        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)\n"
    )
    d.codex_change(
        codex1,
        {"limiter.py": _sub(d.files["limiter.py"], refill_old, refill_new), "README.md": README},
    )
    d.commit("Clamp refill and document usage")

    helper = (
        "\n"
        "    def retry_after(self, cost: int = 1) -> float:\n"
        "        missing = max(0.0, cost - self.tokens)\n"
        "        return missing / self.rate\n"
    )
    d.claude_edit(claude2, "limiter.py", "            return True\n        return False\n",
                  "            return True\n        return False\n" + helper)  # fmt: skip
    d.commit("Add retry_after")
    d.codex_change(
        codex2,
        {
            "limiter.py": _sub(d.files["limiter.py"], helper, ""),
            "config.py": CONFIG,
        },
    )
    d.commit("Move limits to config")

    doc_old = '    """Allow bursts up to capacity, refilling at rate tokens per second."""\n'
    doc_new = (
        '    """Allow bursts up to capacity, refilling at rate tokens per second.\n'
        "\n"
        "    The bucket starts full, so the first burst is never delayed.\n"
        '    """\n'
    )
    raised = _sub(d.files["limiter.py"], "capacity: int = 10", "capacity: int = 20")
    d.claude_rewrite(claude2, "limiter.py", _sub(raised, doc_old, doc_new))
    d.commit("Raise default capacity; document initial state")
    restored = _sub(d.files["limiter.py"], "capacity: int = 20", "capacity: int = 10")
    d.codex_change(codex2, {"limiter.py": restored})
    d.commit("Restore default capacity")

    d.hand_edit("test_limiter.py", "rate=1.0, capacity=2", "rate=2.0, capacity=2")
    d.commit("Tweak test rate")
    for i, line in enumerate(
        ["\n## Limits\n", "\nSee `config.py`.\n", "\n## License\n", "\nMIT\n"]
    ):
        d.hand_edit("README.md", d.files["README.md"], d.files["README.md"] + line)
        d.commit(f"Docs {i + 1}")
    d.save_logs()
    return d


@contextlib.contextmanager
def _isolated_config(root: Path) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in ("TANDEM_CONFIG_DIR", "TANDEM_DATA_DIR")}
    os.environ["TANDEM_CONFIG_DIR"] = str(root / "config")
    os.environ["TANDEM_DATA_DIR"] = str(root / "data")
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run(keep: Path | None = None, *, explain: bool = True) -> int:
    from tandem.cli import main  # imported here: cli imports this module

    with contextlib.ExitStack() as stack:
        if keep is None:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="tandem-demo-")))
        else:
            root = keep.expanduser().resolve()
            if root.exists() and any(root.iterdir()):
                print(f"tandem: {root} is not empty", file=sys.stderr)
                return 2
        stack.enter_context(_isolated_config(root))
        d = build(root)
        print("tandem demo: synthetic repository and logs (every record is invented)\n",
              file=sys.stderr)  # fmt: skip
        common = [str(d.repo), "--db", str(d.db)]
        roots = ["--claude-root", str(d.claude_dir), "--codex-root", str(d.codex_dir)]
        code = main(["scan", *common, *roots])
        if code:
            return code
        print(file=sys.stderr)
        code = main(["report", *common])
        if code or not explain:
            return code
        top = _top_interaction(d.db)
        if top:
            print(f"\n$ tandem explain {top[:8]}\n")
            code = main(["explain", top[:8], *common])
        if keep is not None:
            print(f"\nDemo kept in {root}. Try: tandem session <id> {d.repo} --db {d.db}",
                  file=sys.stderr)  # fmt: skip
        return code


def _top_interaction(db: Path) -> str | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT interaction_id FROM interactions WHERE primary_label = 'exact_revert' "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None
