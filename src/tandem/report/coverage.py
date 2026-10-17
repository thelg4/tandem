"""Coverage report from the latest completed run (spec §14 UX-01, §5 "Coverage display").

Before attribution exists, this reports what the logs show for the repository:
sources, sessions, observed edits per agent with how strongly each can be
reconstructed, files edited by both agents, usage, and diagnostics. Coverage comes
first; nothing here ranks the agents.

Only rows from each source's current generation are counted.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from tandem.report.durability import durability, durability_section
from tandem.report.json_report import redact_path
from tandem.report.views import interaction_line, interaction_rows, summarize

CAVEATS = (
    "A line is attributed to an agent only when its edit is reconstructed exactly from the "
    "log and the line appears in a commit. Unknown lines were not explained by any "
    "supported log: hand edits, shell commands, formatters, or other tools.",
    "Lines still present at the target are a raw count, not a survival rate. Persistence "
    "does not show that code is correct.",
    "Files edited by both agents show overlap in touched files, not interactions: "
    "an edit by one agent may not change lines the other wrote.",
    "Token counts are as reported by each agent; Codex cached input is a subset of input. "
    "Tokens per surviving line uses only sessions with complete usage that worked only in "
    "this repository.",
    "Durability metrics are independent measurements with their own denominators, not a "
    "ranking. Survival describes persistence, not correctness, and rewrites may be "
    "improvements.",
)


@dataclass(frozen=True, slots=True)
class Filters:
    agent: str | None = None
    session: str | None = None
    file: str | None = None

    def to_json(self) -> dict[str, str]:
        return {
            k: v
            for k, v in (("agent", self.agent), ("session", self.session), ("file", self.file))
            if v
        }


@dataclass(slots=True)
class Coverage:
    run: dict[str, Any]
    repository: dict[str, Any]
    sources: dict[str, dict[str, int]] = field(default_factory=dict)
    sessions: dict[str, dict[str, int]] = field(default_factory=dict)
    edits: dict[str, dict[str, Any]] = field(default_factory=dict)
    shared_files: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=dict)
    attribution: dict[str, Any] | None = None
    interactions: list[dict[str, Any]] = field(default_factory=list)
    interaction_summary: dict[str, Any] = field(default_factory=dict)
    durability: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "attribution": self.attribution,
            "interaction_summary": self.interaction_summary,
            "durability": self.durability,
            "sources": self.sources,
            "sessions": self.sessions,
            "edits": self.edits,
            "files_edited_by_both_agents": self.shared_files,
            "usage": self.usage,
            "diagnostics": self.diagnostics,
        }


def latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT r.*, repo.display_name, repo.repo_id FROM latest_run l "
        "JOIN analysis_runs r ON r.run_id = l.run_id "
        "JOIN repositories repo ON repo.repo_id = l.repo_id "
        "ORDER BY r.run_id DESC LIMIT 1"
    ).fetchone()
    return row


AGENT_FILTER = "(?1 IS NULL OR ss.agent_family = ?1)"
EDIT_FILTER = (
    AGENT_FILTER
    + " AND (?2 IS NULL OR ss.session_id = ?2 OR ss.provider_session_id = ?2)"
    + " AND (?3 IS NULL OR d.path = ?3)"
)


CURRENT_EVENTS = (
    "FROM events e JOIN sources s ON s.source_id = e.source_id "
    "AND s.generation = e.source_generation "
    "JOIN sessions ss ON ss.session_id = e.session_id "
)


def load(conn: sqlite3.Connection, filters: Filters | None = None) -> Coverage | None:
    filters = filters or Filters()
    run = latest_run(conn)
    if run is None:
        return None
    manifest = json.loads(run["manifest"] or "{}")
    repo = manifest.get("repository", {})
    cov = Coverage(
        run={
            "run_id": run["run_id"],
            "state": run["state"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "target_commit": run["target_commit_id"],
            "policy_hash": run["policy_hash"],
        },
        repository={
            "name": run["display_name"],
            "path": redact_path(repo.get("top_level", "")),
            "shallow": bool(repo.get("shallow")),
        },
    )

    for r in conn.execute("SELECT provider, status, count(*) n FROM sources GROUP BY 1, 2"):
        cov.sources.setdefault(r["provider"], {})[r["status"]] = r["n"]

    agent_args = (filters.agent,)
    sql = (
        "SELECT ss.agent_family, count(*) n, sum(ss.parent_session_id IS NOT NULL) sub "
        "FROM sessions ss WHERE ss.completeness = 'observed' AND " + AGENT_FILTER + " GROUP BY 1"
    )
    for r in conn.execute(sql, agent_args):
        cov.sessions[r["agent_family"]] = {"sessions": r["n"], "subagent_sessions": r["sub"] or 0}

    args = (filters.agent, filters.session, filters.file)
    edit_sql = (
        "SELECT ss.agent_family agent, e.path_resolution res, d.reconstruction recon, "
        "d.evidence_source src, d.call_link link, d.change_type change, d.path, "
        "d.lines_added added, d.lines_removed removed, d.user_modified um "
        + CURRENT_EVENTS
        + "JOIN edits d ON d.event_id = e.event_id WHERE "
        + EDIT_FILTER
    )
    per_agent: dict[str, dict[str, Any]] = {}
    touched: dict[str, dict[str, int]] = defaultdict(dict)
    for r in conn.execute(edit_sql, args):
        a = per_agent.setdefault(
            r["agent"],
            {
                "observed": 0,
                "in_repository": 0,
                "path_status": {},
                "reconstruction": {},
                "evidence_source": {},
                "call_link": {},
                "change_type": {},
                "lines_added": 0,
                "lines_removed": 0,
                "user_modified": 0,
                "files": 0,
            },
        )
        a["observed"] += 1
        a["path_status"][r["res"]] = a["path_status"].get(r["res"], 0) + 1
        if r["res"] != "resolved":
            continue
        a["in_repository"] += 1
        for key, col in (
            ("reconstruction", "recon"),
            ("evidence_source", "src"),
            ("call_link", "link"),
            ("change_type", "change"),
        ):
            a[key][r[col]] = a[key].get(r[col], 0) + 1
        a["lines_added"] += r["added"] or 0
        a["lines_removed"] += r["removed"] or 0
        a["user_modified"] += r["um"] or 0
        touched[r["path"]][r["agent"]] = touched[r["path"]].get(r["agent"], 0) + 1
    for agent, a in per_agent.items():
        a["files"] = sum(1 for counts in touched.values() if agent in counts)
    cov.edits = per_agent
    cov.shared_files = sorted(
        ({"path": p, "edits": dict(sorted(c.items()))} for p, c in touched.items() if len(c) > 1),
        key=lambda f: (-sum(f["edits"].values()), f["path"]),
    )

    usage_sql = (
        "SELECT ss.agent_family agent, u.category, sum(u.incremental) total "
        + CURRENT_EVENTS
        + "JOIN usage_records u ON u.event_id = e.event_id WHERE "
        + AGENT_FILTER
        + " GROUP BY 1, 2"
    )
    for r in conn.execute(usage_sql, agent_args):
        cov.usage.setdefault(r["agent"], {})[r["category"]] = r["total"] or 0

    for r in conn.execute(
        "SELECT code, count(*) n FROM diagnostics WHERE run_id = ? GROUP BY 1 ORDER BY 2 DESC",
        (run["run_id"],),
    ):
        cov.diagnostics[r["code"]] = r["n"]
    cov.attribution = _attribution(conn, int(run["run_id"]), filters)
    session_id = _session_id(conn, filters.session)
    cov.interactions = interaction_rows(
        conn, int(run["run_id"]), agent=filters.agent, session=session_id, path=filters.file
    )
    cov.interaction_summary = summarize(cov.interactions)
    cov.durability = durability(conn, int(run["run_id"]), "agent", agent=filters.agent)
    return cov


def _attribution(conn: sqlite3.Connection, run_id: int, filters: Filters) -> dict[str, Any] | None:
    meta = conn.execute("SELECT * FROM run_attribution WHERE run_id = ?", (run_id,)).fetchone()
    if meta is None:
        return None
    args = (run_id, filters.agent, _session_id(conn, filters.session), filters.file)

    inserted = int(meta["tracked_inserted_lines"]) + int(meta["untracked_inserted_lines"])
    classes: dict[str, int] = {}
    for r in conn.execute(
        "SELECT l.evidence_class cls, sum(l.line_count) n FROM lineage l WHERE l.run_id = ?1 "
        "AND l.origin_kind = 'agent' AND l.state IN ('present', 'removed', 'unavailable') AND "
        "l.introduced_commit_id IS NOT NULL GROUP BY 1",
        (run_id,),
    ):
        classes[r["cls"]] = int(r["n"])
    attributed = sum(classes.values())

    agents: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT l.owner_agent_family agent, l.state, l.evidence_class cls, sum(l.line_count) n "
        "FROM lineage l WHERE l.run_id = ?1 AND l.origin_kind = 'agent' "
        "AND (?2 IS NULL OR l.owner_agent_family = ?2) AND (?3 IS NULL OR l.owner_session_id = ?3) "
        "AND (?4 IS NULL OR l.path = ?4) GROUP BY 1, 2, 3",
        args,
    ):
        a = agents.setdefault(r["agent"], _agent_block())
        n = int(r["n"])
        if r["state"] in ("present", "removed", "unavailable"):
            a["lines_committed"][r["cls"]] = a["lines_committed"].get(r["cls"], 0) + n
        if r["state"] == "present":
            a["lines_present_at_target"] += n
        elif r["state"] == "dropped_before_commit":
            a["lines_dropped_before_commit"] += n
        elif r["state"] == "provisional":
            a["lines_uncommitted"] += n

    for r in conn.execute(
        "SELECT l.owner_agent_family agent, l.owner_session_id owner, rs.agent_family by_agent, "
        "re.session_id by_session, sum(l.line_count) n FROM lineage l "
        "LEFT JOIN events re ON re.event_id = l.removed_edit_id "
        "LEFT JOIN sessions rs ON rs.session_id = re.session_id "
        "WHERE l.run_id = ?1 AND l.origin_kind = 'agent' AND l.state = 'removed' "
        "AND (?2 IS NULL OR l.owner_agent_family = ?2) AND (?3 IS NULL OR l.owner_session_id = ?3) "
        "AND (?4 IS NULL OR l.path = ?4) GROUP BY 1, 2, 3, 4",
        args,
    ):
        a = agents.setdefault(r["agent"], _agent_block())
        if r["by_agent"] is None:
            key = "unattributed_change"
        elif r["by_agent"] != r["agent"]:
            key = "other_agent"
        elif r["by_session"] == r["owner"]:
            key = "same_session"
        else:
            key = "same_agent_other_session"
        a["lines_removed_later"][key] = a["lines_removed_later"].get(key, 0) + int(r["n"])

    for r in conn.execute(
        "SELECT ss.agent_family agent, o.outcome, count(*) n FROM edit_outcomes o "
        "JOIN events e ON e.event_id = o.edit_id JOIN sessions ss ON ss.session_id = e.session_id "
        "JOIN edits d ON d.edit_id = o.edit_id WHERE o.run_id = ?1 AND "
        "(?2 IS NULL OR ss.agent_family = ?2) AND (?3 IS NULL OR ss.session_id = ?3) "
        "AND (?4 IS NULL OR d.path = ?4) GROUP BY 1, 2",
        args,
    ):
        a = agents.setdefault(r["agent"], _agent_block())
        a["edits_by_outcome"][r["outcome"]] = int(r["n"])

    return {
        "commits_analyzed": meta["commits_analyzed"],
        "baseline_commit": meta["baseline_commit_id"],
        "history_truncated": bool(meta["history_truncated"]),
        "inserted_lines_in_range": inserted,
        "attributed_lines_by_class": classes,
        "unattributed_lines": max(0, inserted - attributed),
        "unavailable_paths": json.loads(meta["unavailable_paths"] or "{}"),
        "rename_policy": meta["rename_policy"],
        "diff_policy": meta["diff_policy"],
        "rules_version": meta["rules_version"],
        "agents": agents,
    }


def _agent_block() -> dict[str, Any]:
    return {
        "edits_by_outcome": {},
        "lines_committed": {},
        "lines_present_at_target": 0,
        "lines_removed_later": {},
        "lines_dropped_before_commit": 0,
        "lines_uncommitted": 0,
    }


def _session_id(conn: sqlite3.Connection, session: str | None) -> str | None:
    if session is None:
        return None
    row = conn.execute(
        "SELECT session_id FROM sessions WHERE session_id = ? OR provider_session_id = ?",
        (session, session),
    ).fetchone()
    return str(row[0]) if row else session


def _pct(n: int, d: int) -> str:
    return f"{n} of {d} ({n / d * 100:.0f}%)" if d else f"{n} of 0 (n/a)"


# -- text --------------------------------------------------------------------------------


def _count_values(mapping: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in mapping.values():
        out[value] = out.get(value, 0) + 1
    return out


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"


def render_text(cov: Coverage, *, head: str | None = None, filters: Filters | None = None) -> str:
    run, repo = cov.run, cov.repository
    lines = [f"Tandem report: {repo['name']} ({repo['path']})"]
    target = run["target_commit"] or "?"
    fresh = ""
    if head is not None and head != target:
        fresh = f"  [stale: HEAD is now {head[:12]}; run `tandem scan`]"
    lines.append(
        f"Revision {target[:12]}  run #{run['run_id']} {run['state']}, "
        f"finished {run['finished_at']}{fresh}"
    )
    if filters and filters.to_json():
        lines.append("Filters: " + ", ".join(f"{k}={v}" for k, v in filters.to_json().items()))
    if repo["shallow"]:
        lines.append("History is shallow: older history is unavailable.")
    lines.append("")

    lines.append("Coverage")
    if not cov.sources:
        lines.append("  No agent logs found. Check `tandem doctor` for log locations.")
    for agent, statuses in sorted(cov.sources.items()):
        sess = cov.sessions.get(agent, {"sessions": 0, "subagent_sessions": 0})
        lines.append(
            f"  {agent}: {sum(statuses.values())} log file(s) ({_fmt_counts(statuses)}); "
            f"{sess['sessions']} session(s) in this repo, "
            f"{sess['subagent_sessions']} subagent"
        )
    lines.append("")

    att = cov.attribution
    if att is not None:
        lines.append("Attribution coverage")
        start = (
            f"from baseline {att['baseline_commit'][:12]}"
            if att["history_truncated"]
            else ("from the first commit")
        )
        lines.append(f"  {att['commits_analyzed']} first-parent commit(s) analyzed {start}")
        inserted = att["inserted_lines_in_range"]
        classes = att["attributed_lines_by_class"]
        lines.append(f"  verified lines: {_pct(classes.get('verified', 0), inserted)} inserted")
        others = {k: v for k, v in classes.items() if k != "verified"}
        if others:
            lines.append(f"  also attributed: {_fmt_counts(others)}")
        lines.append(f"  not explained by agent logs: {att['unattributed_lines']}")
        if att["unavailable_paths"]:
            lines.append(
                f"  files not tracked: {_fmt_counts(_count_values(att['unavailable_paths']))}"
            )
        lines.append("")

    lines.append("Observed edits")
    if not cov.edits:
        lines.append("  No edits from either agent touched this repository.")
    for agent, a in sorted(cov.edits.items()):
        outside = a["observed"] - a["in_repository"]
        lines.append(
            f"  {agent}: {a['in_repository']} edit(s) to {a['files']} file(s) in this repo"
            + (f" ({outside} elsewhere or unresolved)" if outside else "")
        )
        if a["in_repository"]:
            lines.append(
                f"    +{a['lines_added']} / -{a['lines_removed']} lines as logged; "
                f"changes: {_fmt_counts(a['change_type'])}"
            )
            lines.append(f"    reconstructable from log: {_fmt_counts(a['reconstruction'])}")
            lines.append(f"    evidence: {_fmt_counts(a['evidence_source'])}")
            if a["call_link"].get("source_order"):
                lines.append(
                    f"    linked to tool call by position only: {a['call_link']['source_order']}"
                )
            if a["user_modified"]:
                lines.append(f"    changed by the user during approval: {a['user_modified']}")
    lines.append("")

    if att is not None and att["agents"]:
        lines.append("Attributed lines")
        for agent, a in sorted(att["agents"].items()):
            committed = sum(a["lines_committed"].values())
            lines.append(
                f"  {agent}: {committed} line(s) committed "
                f"({_fmt_counts(a['lines_committed'])}); "
                f"{a['lines_present_at_target']} still present at target"
            )
            if a["lines_removed_later"]:
                lines.append(f"    later removed, by: {_fmt_counts(a['lines_removed_later'])}")
            if a["lines_dropped_before_commit"]:
                lines.append(f"    dropped before any commit: {a['lines_dropped_before_commit']}")
            if a["lines_uncommitted"]:
                lines.append(f"    not in any analyzed commit: {a['lines_uncommitted']}")
            lines.append(f"    edits: {_fmt_counts(a['edits_by_outcome'])}")
        lines.append("")

    if att is not None:
        lines += _interaction_section(cov)
    if cov.durability:
        lines += durability_section(cov.durability)

    if len(cov.edits) > 1:
        lines.append(f"Files edited by both agents: {len(cov.shared_files)}")
        for f in cov.shared_files[:10]:
            lines.append(f"  {f['path']}  ({_fmt_counts(f['edits'])})")
        lines.append("")

    if cov.usage:
        lines.append("Tokens as reported")
        for agent, cats in sorted(cov.usage.items()):
            lines.append(f"  {agent}: {_fmt_counts(cats)}")
        lines.append("")

    if cov.diagnostics:
        lines.append(f"Diagnostics this run: {_fmt_counts(cov.diagnostics)}")
        lines.append("")
    lines.append("Notes")
    lines += [f"  - {c}" for c in CAVEATS]
    return "\n".join(lines)


def _interaction_section(cov: Coverage) -> list[str]:
    summary = cov.interaction_summary
    lines = []
    if not summary.get("cross_agent") and len(cov.edits) < 2:
        lines.append("Cross-agent interactions: none (only one agent's edits are in this repo)")
    else:
        lines.append(
            f"Cross-agent interactions (verified): {summary.get('cross_agent', 0)} "
            f"({summary.get('cross_agent_lines_affected', 0)} line(s) affected, "
            f"{summary.get('recent', 0)} recent)"
        )
        for direction, labels in sorted(summary.get("by_direction", {}).items()):
            lines.append(f"  {direction}'s lines: {_fmt_counts(labels)}")
    if summary.get("lower_evidence"):
        lines.append(
            f"  supported evidence only, not in the counts above: {summary['lower_evidence']}"
        )
    if summary.get("overlap_candidates"):
        lines.append(f"  overlap candidates, order uncertain: {summary['overlap_candidates']}")
    if summary.get("same_agent_cross_session"):
        lines.append(
            "  same agent, different session (counted separately): "
            f"{summary['same_agent_cross_session']}"
        )
    shown = [r for r in cov.interactions if r["original"]["agent"] != r["later"]["agent"]][:10]
    if shown:
        lines.append("  Review first (review order, not severity):")
        lines += ["    " + interaction_line(r) for r in shown]
        lines.append("    Run `tandem explain <id>` for the evidence.")
    lines.append("")
    return lines
