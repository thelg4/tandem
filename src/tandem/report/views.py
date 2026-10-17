"""Interaction list, interaction evidence (`tandem explain`), and session view (`tandem session`).

All views read the latest completed run. Evidence text comes from the original agent
logs, re-read from approved roots; the database itself holds no file text. Text from
logs is untrusted, so terminal control characters are escaped before printing (NFR-03).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tandem.attribution.run import load_edits
from tandem.model.edits import ChangeType, ObservedEdit
from tandem.report.json_report import redact_path

MAX_DIFF_LINES = 60

INTERACTION_NOTE = (
    "Labels describe what changed, not intent or correctness: a rewrite may be an "
    "improvement, and an exact revert may be the right call."
)


def safe(text: str) -> str:
    """Escape terminal control characters in untrusted text."""
    return "".join(ch if ch.isprintable() or ch == "\t" else f"\\x{ord(ch):02x}" for ch in text)


def _latest(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT r.run_id, r.repo_id FROM latest_run l JOIN analysis_runs r ON r.run_id = l.run_id "
        "ORDER BY r.run_id DESC LIMIT 1"
    ).fetchone()
    return row


# -- interaction list (report section) ---------------------------------------------------


def interaction_rows(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    agent: str | None = None,
    session: str | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT i.*, eo.session_id AS original_session, el.session_id AS later_session, "
        "eo.utc_timestamp AS original_time, el.utc_timestamp AS later_time "
        "FROM interactions i "
        "JOIN events eo ON eo.event_id = i.original_edit_id "
        "JOIN events el ON el.event_id = i.later_edit_id "
        "WHERE i.run_id = ?1 "
        "AND (?2 IS NULL OR i.original_agent = ?2 OR i.later_agent = ?2) "
        "AND (?3 IS NULL OR eo.session_id = ?3 OR el.session_id = ?3) "
        "AND (?4 IS NULL OR i.path = ?4) "
        "ORDER BY i.review_rank",
        (run_id, agent, session, path),
    ).fetchall()
    return [_row_json(r) for r in rows]


def _row_json(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "interaction_id": r["interaction_id"],
        "label": r["primary_label"],
        "tags": json.loads(r["tags"]),
        "evidence_class": r["evidence_class"],
        "path": r["path"],
        "start_line": r["start_line"],
        "lines_affected": r["lines_affected"],
        "original": {
            "agent": r["original_agent"],
            "session": r["original_session"],
            "edit_id": r["original_edit_id"],
            "time": r["original_time"],
            "commit": r["original_commit_id"],
        },
        "later": {
            "agent": r["later_agent"],
            "session": r["later_session"],
            "edit_id": r["later_edit_id"],
            "time": r["later_time"],
            "commit": r["removal_commit_id"],
        },
        "elapsed_seconds": r["elapsed_seconds"],
        "commit_distance": r["commit_distance"],
        "recent": bool(r["recent"]),
        "change": {
            "later_removed": r["block_removed"],
            "later_inserted": r["block_inserted"],
            "original_replaced": r["original_replaced"],
            "original_inserted": r["original_inserted"],
        },
        "rule_version": r["rule_version"],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline counts. Each interaction has one primary label, so counts never overlap."""
    cross = [r for r in rows if r["original"]["agent"] != r["later"]["agent"]]
    confirmed = [r for r in cross if r["label"] != "overlap_candidate"]
    directions: dict[str, dict[str, int]] = {}
    for r in confirmed:
        key = f"{r['later']['agent']} changed {r['original']['agent']}"
        directions.setdefault(key, {})
        directions[key][r["label"]] = directions[key].get(r["label"], 0) + 1
    return {
        "cross_agent": len(confirmed),
        "cross_agent_lines_affected": sum(r["lines_affected"] for r in confirmed),
        "by_direction": directions,
        "recent": sum(1 for r in confirmed if r["recent"]),
        "overlap_candidates": sum(1 for r in cross if r["label"] == "overlap_candidate"),
        "same_agent_cross_session": len(rows) - len(cross),
    }


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "time unknown"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min later"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h later"
    return f"{seconds / 86400:.1f} days later"


def _distance(d: int | None) -> str:
    return "before any commit" if d is None else f"{d} commit(s) later"


def interaction_line(r: dict[str, Any]) -> str:
    return (
        f"{r['interaction_id']}  {safe(r['path'])}:{r['start_line']}  "
        f"{r['later']['agent']} -> {r['original']['agent']}'s lines  {r['label']}, "
        f"{r['lines_affected']} line(s), {_distance(r['commit_distance'])}, "
        f"{r['evidence_class']}" + ("  [recent]" if r["recent"] else "")
    )


# -- explain -----------------------------------------------------------------------------


def find_interaction(conn: sqlite3.Connection, ident: str) -> dict[str, Any] | str:
    """The interaction in the latest run whose ID starts with ``ident``, or an error message."""
    run = _latest(conn)
    if run is None:
        return "no completed scan; run `tandem scan` first"
    rows = [
        r
        for r in interaction_rows(conn, int(run["run_id"]))
        if r["interaction_id"].startswith(ident)
    ]
    if not rows:
        return f"no interaction matching {ident!r} in the latest scan"
    if len(rows) > 1:
        return f"{ident!r} matches {len(rows)} interactions; use more characters"
    row = rows[0]
    row["repo_id"] = run["repo_id"]
    return row


def _edit_meta(conn: sqlite3.Connection, edit_id: str) -> dict[str, Any]:
    r = conn.execute(
        "SELECT e.tool_name, e.tool_call_id, e.utc_timestamp, e.record_offset, s.path AS source, "
        "s.provider, d.evidence_source, d.change_type, d.call_link "
        "FROM events e JOIN sources s ON s.source_id = e.source_id "
        "JOIN edits d ON d.edit_id = e.event_id WHERE e.event_id = ?",
        (edit_id,),
    ).fetchone()
    # sqlite3.Row iterates values, not keys, so .keys() is required here.
    return {} if r is None else {k: r[k] for k in r.keys()}  # noqa: SIM118


def _diff_lines(edit: ObservedEdit | None) -> list[str]:
    if edit is None:
        return ["(log record unavailable)"]
    if edit.change is ChangeType.DELETE:
        return ["(file deleted)"]
    if edit.hunks:
        out = []
        for h in edit.hunks:
            if h.numbered:
                out.append(f"@@ -{h.old_start},{h.old_lines} +{h.new_start},{h.new_lines} @@")
            else:
                out.append("@@ " + (h.anchor or "") + " (no line numbers in log)")
            out.extend(h.lines)
        return out
    if edit.after_text is not None:
        return ["(new file)"] + ["+" + ln for ln in edit.after_text.splitlines()]
    return ["(no diff recorded)"]


def explain(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    ids = {row["original"]["edit_id"], row["later"]["edit_id"]}
    edits, unavailable = load_edits(conn, row["repo_id"], ids)
    content = {e.edit_id: e.edit for e in edits}
    detail = dict(row)
    for side in ("original", "later"):
        edit_id = row[side]["edit_id"]
        meta = _edit_meta(conn, edit_id)
        edit = content.get(edit_id)
        detail[side] = {
            **row[side],
            "tool": meta.get("tool_name"),
            "tool_call_id": meta.get("tool_call_id"),
            "evidence_source": meta.get("evidence_source"),
            "call_link": meta.get("call_link"),
            "log": {
                "path": redact_path(meta.get("source", "")),
                "byte_offset": meta.get("record_offset"),
            },
            "diff": _diff_lines(edit if isinstance(edit, ObservedEdit) else None),
            "log_status": unavailable.get(edit_id, "read"),
        }
    detail.pop("repo_id", None)
    return detail


def render_explain(d: dict[str, Any]) -> str:
    ch = d["change"]
    lines = [
        f"Interaction {d['interaction_id']}  (rules {d['rule_version']})",
        f"  {d['label']} · {d['evidence_class']} · {safe(d['path'])} line {d['start_line']}",
        f"  {d['later']['agent']} changed {d['lines_affected']} line(s) that "
        f"{d['original']['agent']} wrote; {_distance(d['commit_distance'])}, "
        f"{_ago(d['elapsed_seconds'])}",
    ]
    if d["tags"]:
        lines.append(f"  tags: {', '.join(d['tags'])}")
    lines.append("")
    for title, side in (("Earlier edit", "original"), ("Later edit", "later")):
        s = d[side]
        commit = (s["commit"] or "")[:12] or "not committed"
        lines.append(f"{title}: {s['agent']} session {safe(s['session'] or '?')}")
        lines.append(f"  {s['tool'] or '?'} at {s['time'] or 'unknown time'}; commit {commit}")
        lines.append(f"  evidence: {s['evidence_source']}, linked to tool call by {s['call_link']}")
        lines.append(f"  log: {safe(s['log']['path'])} @ byte {s['log']['byte_offset']}")
    lines += [
        "",
        "Change",
        f"  earlier edit replaced {ch['original_replaced']} line(s) with {ch['original_inserted']}",
        f"  later edit replaced {ch['later_removed']} line(s) with {ch['later_inserted']} "
        f"at line {d['start_line']}",
    ]
    if d["label"] == "exact_revert":
        lines.append("  the later edit put back exactly the text the earlier edit replaced")
    elif d["label"] == "partial_revert":
        lines.append("  the later edit put back part of the text the earlier edit replaced")
    elif d["label"] == "overlap_candidate":
        lines.append("  order or lineage is uncertain; excluded from verified totals")
    for title, side in (
        ("Earlier edit diff (from its log)", "original"),
        ("Later edit diff (from its log)", "later"),
    ):
        diff = d[side]["diff"]
        lines += ["", title]
        lines += ["  " + safe(x) for x in diff[:MAX_DIFF_LINES]]
        if len(diff) > MAX_DIFF_LINES:
            lines.append(f"  … {len(diff) - MAX_DIFF_LINES} more line(s)")
    lines += ["", f"Note: {INTERACTION_NOTE}"]
    return "\n".join(lines)


# -- session -----------------------------------------------------------------------------


def find_session(conn: sqlite3.Connection, ident: str) -> sqlite3.Row | str:
    rows = conn.execute(
        "SELECT * FROM sessions WHERE completeness = 'observed' AND (session_id = ?1 OR "
        "provider_session_id = ?1 OR provider_session_id LIKE ?2 OR session_id LIKE ?3)",
        (ident, ident.replace("%", "") + "%", "%:" + ident.replace("%", "") + "%"),
    ).fetchall()
    exact = [r for r in rows if ident in (r["session_id"], r["provider_session_id"])]
    if len(exact) == 1:
        return exact[0]  # type: ignore[no-any-return]
    if not rows:
        return f"no session matching {ident!r}"
    if len(rows) > 1:
        names = ", ".join(r["session_id"] for r in rows[:5])
        return f"{ident!r} matches {len(rows)} sessions ({names}); use a longer ID"
    return rows[0]  # type: ignore[no-any-return]


def session_view(conn: sqlite3.Connection, session: sqlite3.Row) -> dict[str, Any]:
    run = _latest(conn)
    run_id = int(run["run_id"]) if run else -1
    sid = session["session_id"]
    outcomes = {
        r["outcome"]: r["n"]
        for r in conn.execute(
            "SELECT o.outcome, count(*) n FROM edit_outcomes o "
            "JOIN events e ON e.event_id = o.edit_id "
            "WHERE o.run_id = ? AND e.session_id = ? GROUP BY 1",
            (run_id, sid),
        )
    }
    files = [
        r["path"]
        for r in conn.execute(
            "SELECT d.path, count(*) n FROM edits d JOIN events e ON e.event_id = d.event_id "
            "WHERE e.session_id = ? AND d.repo_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
            (sid,),
        )
    ]
    lines: dict[str, int] = {}
    for r in conn.execute(
        "SELECT state, evidence_class, sum(line_count) n FROM lineage WHERE run_id = ? AND "
        "owner_session_id = ? AND origin_kind = 'agent' GROUP BY 1, 2",
        (run_id, sid),
    ):
        key = r["state"] if r["state"] != "present" else f"present ({r['evidence_class']})"
        lines[key] = lines.get(key, 0) + int(r["n"])
    rows = interaction_rows(conn, run_id, session=sid)
    usage = {
        r["category"]: r["n"]
        for r in conn.execute(
            "SELECT u.category, sum(u.incremental) n FROM usage_records u JOIN events e "
            "ON e.event_id = u.event_id WHERE e.session_id = ? GROUP BY 1",
            (sid,),
        )
    }
    segments = conn.execute(
        "SELECT count(*) FROM session_segments WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    return {
        "session_id": sid,
        "agent": session["agent_family"],
        "provider_session_id": session["provider_session_id"],
        "parent_session_id": session["parent_session_id"],
        "observed": [session["first_observed_at"], session["last_observed_at"]],
        "repository_segments": segments,
        "edits_by_outcome": outcomes,
        "files_edited": files,
        "lines": lines,
        "interactions_given": [r for r in rows if r["later"]["session"] == sid],
        "interactions_received": [r for r in rows if r["original"]["session"] == sid],
        "usage": usage,
    }


def render_session(v: dict[str, Any]) -> str:
    def counts(d: dict[str, int]) -> str:
        return ", ".join(f"{k} {n}" for k, n in sorted(d.items(), key=lambda kv: -kv[1])) or "none"

    lines = [
        f"Session {safe(v['session_id'])}  ({v['agent']})",
        f"  observed {v['observed'][0]} to {v['observed'][1]} (includes idle time)",
    ]
    if v["parent_session_id"]:
        lines.append(f"  subagent of {safe(v['parent_session_id'])}")
    lines += [
        f"  repository segments: {v['repository_segments']}",
        "",
        f"Edits in this repo: {counts(v['edits_by_outcome'])}",
    ]
    if v["files_edited"]:
        lines.append("  files: " + ", ".join(safe(f) for f in v["files_edited"]))
    lines += ["", f"Lines from this session: {counts(v['lines'])}", ""]
    for title, key in (
        ("Changed other sessions' lines", "interactions_given"),
        ("Its lines changed by other sessions", "interactions_received"),
    ):
        rows = v[key]
        lines.append(f"{title}: {len(rows)}")
        lines += ["  " + interaction_line(r) for r in rows[:10]]
    if v["usage"]:
        lines += ["", f"Tokens as reported: {counts(v['usage'])}"]
    return "\n".join(lines)
