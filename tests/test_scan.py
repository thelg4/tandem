"""End-to-end scan tests: fixture logs pointed at a real temporary Git repository."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from adapter_helpers import FIXTURES
from tandem.config import Policy
from tandem.errors import ScanBusyError
from tandem.ingest import Scanner, ScanOptions
from tandem.storage import db


def _copy_logs(dest: Path, agent_dir: str, repo: Path) -> None:
    """Copy fixture logs, rewriting the synthetic /work/demo working directory to ``repo``."""
    for src in (FIXTURES / agent_dir).rglob("*.jsonl"):
        out = dest / src.relative_to(FIXTURES / agent_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(src.read_text().replace("/work/demo", str(repo)))


@pytest.fixture
def env(repo_with_history: tuple[Path, list[str]], tmp_path: Path) -> dict[str, Path]:
    repo, _ = repo_with_history
    claude, codex = tmp_path / "logs" / "claude", tmp_path / "logs" / "codex"
    _copy_logs(claude, "claude_code", repo)
    _copy_logs(codex, "codex", repo)
    return {"repo": repo, "claude": claude, "codex": codex, "db": tmp_path / "t.sqlite3"}


def scan(env: dict[str, Path], **kw: object):  # type: ignore[no-untyped-def]
    options = ScanOptions(claude_root=env["claude"], codex_root=env["codex"], **kw)  # type: ignore[arg-type]
    return Scanner(env["repo"], Policy(), env["db"]).run(options)


def q(env: dict[str, Path], sql: str, *args: object) -> list[sqlite3.Row]:
    conn = db.connect(env["db"], readonly=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_first_scan_imports_sessions_events_and_edits(env: dict[str, Path]) -> None:
    summary = scan(env)
    assert summary.state == "complete"
    assert summary.sources["new"] == 4
    assert dict(summary.sessions) == {"claude_code": 1, "codex": 3}
    edits = q(
        env,
        "SELECT path, change_type, evidence_source, reconstruction FROM edits "
        "WHERE repo_id IS NOT NULL ORDER BY path, evidence_source",
    )
    paths = {(r["path"], r["evidence_source"]) for r in edits}
    assert ("app.py", "claude_code.tool_use_result") in paths
    assert ("app.py", "codex.patch_text") in paths
    assert ("docs/notes.md", "codex.file_change") in paths
    assert ("lib/util.py", "codex.file_change") in paths
    # Claude updates rebuild exactly from the log; patch text cannot be placed yet.
    recon = {(r["evidence_source"], r["reconstruction"]) for r in edits}
    assert ("claude_code.tool_use_result", "reconstructed") in recon
    assert ("codex.patch_text", "partial") in recon


def test_no_prompt_or_file_text_is_stored(env: dict[str, Path]) -> None:
    scan(env)
    raw = env["db"].read_bytes()
    for text in (b"synthetic prompt", b"return 1", b"# Notes"):
        assert text not in raw


def test_rescan_unchanged_is_idempotent(env: dict[str, Path]) -> None:
    first = scan(env)
    count = q(env, "SELECT count(*) FROM events")[0][0]
    second = scan(env)
    assert second.sources["unchanged"] == 4 and second.events_inserted == 0
    assert q(env, "SELECT count(*) FROM events")[0][0] == count
    assert first.run_id != second.run_id
    latest = q(env, "SELECT run_id FROM latest_run")[0][0]
    assert latest == second.run_id


def test_append_adds_only_new_records_and_pairs_results(env: dict[str, Path]) -> None:
    scan(env)
    before = q(env, "SELECT count(*) FROM edits")[0][0]
    log = next(env["claude"].rglob("*.jsonl"))
    lines = log.read_text().splitlines()
    # Answer the call that had no result (toolu_006) in a later append.
    tail = json.loads(lines[-2])
    result = dict(
        tail,
        type="user",
        uuid="u900",
        parentUuid=tail["uuid"],
        message={
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_006", "content": "ok"}],
        },
        toolUseResult={
            "type": "create",
            "filePath": f"{env['repo']}/never.py",
            "content": "x = 1\n",
            "originalFile": None,
            "structuredPatch": [],
            "userModified": False,
        },
    )
    result.pop("requestId", None)
    with log.open("a") as fh:
        fh.write(json.dumps(result) + "\n")
    summary = scan(env)
    assert summary.sources["appended"] == 1
    assert q(env, "SELECT count(*) FROM edits")[0][0] == before + 1
    assert q(env, "SELECT count(*) FROM edits WHERE path='never.py'")[0][0] == 1


def test_rewritten_source_starts_new_generation_and_keeps_old_rows(env: dict[str, Path]) -> None:
    scan(env)
    log = next(env["codex"].rglob("0.98-patch-text/*.jsonl"))
    old_rows = q(env, "SELECT count(*) FROM events")[0][0]
    log.write_text(log.read_text().replace('"total_tokens": 1050', '"total_tokens": 1051', 1))
    summary = scan(env)
    assert summary.sources["rewritten"] == 1
    gens = q(env, "SELECT generation FROM sources WHERE path=?", str(log))
    assert gens[0][0] == 1
    assert q(env, "SELECT count(*) FROM events")[0][0] > old_rows


def test_missing_source_marked_unavailable_and_run_partial(env: dict[str, Path]) -> None:
    scan(env)
    log = next(env["codex"].rglob("0.144-file-change/*.jsonl"))
    log.unlink()
    summary = scan(env)
    assert summary.state == "partial"
    status = q(env, "SELECT status FROM sources WHERE path=?", str(log))[0][0]
    assert status == "unavailable"
    assert q(env, "SELECT count(*) FROM edits WHERE path='docs/notes.md'")[0][0] == 1  # kept


def test_sessions_from_other_repos_are_not_stored(env: dict[str, Path], tmp_path: Path) -> None:
    other = tmp_path / "logs" / "codex" / "elsewhere.jsonl"
    src = next(env["codex"].rglob("0.144-file-change/*.jsonl")).read_text()
    other.write_text(src.replace(str(env["repo"]), "/somewhere/else").replace("0144", "9999"))
    summary = scan(env)
    assert summary.sources["new"] == 5
    assert dict(summary.sessions)["codex"] == 3
    assert not q(env, "SELECT 1 FROM sessions WHERE session_id='codex:cx-thread-9999'")


def test_subagent_parent_is_recorded_as_reference(env: dict[str, Path]) -> None:
    scan(env)
    rows = q(env, "SELECT completeness FROM sessions WHERE session_id='codex:cx-thread-parent'")
    assert rows[0][0] == "referenced_only"


def test_usage_dedup_holds_in_storage(env: dict[str, Path]) -> None:
    scan(env)
    rows = q(
        env,
        "SELECT count(DISTINCT e.provider_event_id) AS reqs, count(*) AS n "
        "FROM usage_records u "
        "JOIN events e USING (event_id) WHERE e.session_id LIKE 'claude_code:%' "
        "AND u.category='input'",
    )
    assert rows[0]["reqs"] == rows[0]["n"] == 6


def test_second_scan_waits_on_lock(env: dict[str, Path]) -> None:
    with db.writer_lock(env["db"]), pytest.raises(ScanBusyError):
        scan(env)
    assert scan(env).state == "complete"
