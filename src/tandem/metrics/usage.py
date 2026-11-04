"""Per-session usage for durability metrics.

Processed tokens are normalized so cache tokens are never counted twice:

- Claude Code reports input, cache-write, cache-read, and output tokens as disjoint
  categories, so processed = input + cache_write + cache_read + output.
- Codex reports cached input as a subset of input, and reasoning output as a subset of
  output, so processed = input_tokens + output_tokens. ``cache_write_input_tokens`` is
  treated as part of input and not added.

Usage is complete for a session only when every usage record carries every category
its agent reports, and the session did not also work outside this repository (a
session spanning repositories has no precise per-repository cost). Subagent sessions
report their own usage, and Codex subagents' copied history is skipped at ingest, so
child usage is not counted twice.
"""

from __future__ import annotations

import sqlite3

from tandem.metrics.compute import SessionUsage

PROCESSED = {
    "claude_code": ("input", "cache_write", "cache_read", "output"),
    "codex": ("input_tokens", "output_tokens"),
}
EXPECTED = {
    "claude_code": frozenset({"input", "cache_write", "cache_read", "output"}),
    "codex": frozenset(
        {
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        }
    ),
}


def session_usage(conn: sqlite3.Connection, repo_id: str) -> list[SessionUsage]:
    out: list[SessionUsage] = []
    sessions = conn.execute(
        "SELECT DISTINCT ss.session_id, ss.agent_family FROM sessions ss "
        "JOIN session_segments sg ON sg.session_id = ss.session_id "
        "WHERE sg.repo_id = ? AND ss.completeness = 'observed'",
        (repo_id,),
    ).fetchall()
    for row in sessions:
        sid, agent = row["session_id"], row["agent_family"]
        records: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            "SELECT u.event_id, u.category, u.incremental FROM usage_records u "
            "JOIN events e ON e.event_id = u.event_id "
            "JOIN sources s ON s.source_id = e.source_id AND s.generation = e.source_generation "
            "WHERE e.session_id = ?",
            (sid,),
        ):
            if r["incremental"] is not None:
                records.setdefault(r["event_id"], {})[r["category"]] = int(r["incremental"])
        elsewhere = conn.execute(
            "SELECT count(*) FROM events e JOIN sources s ON s.source_id = e.source_id "
            "AND s.generation = e.source_generation WHERE e.session_id = ? "
            "AND e.repo_id IS NULL AND e.effective_cwd IS NOT NULL "
            "AND e.event_type != 'usage'",
            (sid,),
        ).fetchone()[0]
        expected = EXPECTED.get(agent, frozenset())
        reason = None
        if not records:
            reason = "no_usage_recorded"
        elif any(not expected <= set(cats) for cats in records.values()):
            reason = "usage_categories_missing"
        elif elsewhere:
            reason = "session_also_worked_outside_repository"
        tokens = sum(cats.get(k, 0) for cats in records.values() for k in PROCESSED.get(agent, ()))
        out.append(SessionUsage(agent, sid, tokens if records else None, reason is None, reason))
    return out
