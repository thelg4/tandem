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

from tandem.report.json_report import redact_path

CAVEATS = (
    "Edits are observed in agent logs and placed by path; they are not yet matched to "
    "Git history, so no line is attributed and no survival is measured.",
    "Files edited by both agents show overlap in touched files, not interactions: "
    "an edit by one agent may not change lines the other wrote.",
    "Token counts are as reported by each agent; Codex cached input is a subset of input.",
)


@dataclass(frozen=True, slots=True)
class Filters:
    agent: str | None = None
    session: str | None = None
    file: str | None = None

    def to_json(self) -> dict[str, str]:
        return {k: v for k, v in (("agent", self.agent), ("session", self.session),
                                  ("file", self.file)) if v}  # fmt: skip


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

    def to_json(self) -> dict[str, Any]:
        return {
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
        a = per_agent.setdefault(r["agent"], {
            "observed": 0, "in_repository": 0, "path_status": {}, "reconstruction": {},
            "evidence_source": {}, "call_link": {}, "change_type": {}, "lines_added": 0,
            "lines_removed": 0, "user_modified": 0, "files": 0,
        })  # fmt: skip
        a["observed"] += 1
        a["path_status"][r["res"]] = a["path_status"].get(r["res"], 0) + 1
        if r["res"] != "resolved":
            continue
        a["in_repository"] += 1
        for key, col in (("reconstruction", "recon"), ("evidence_source", "src"),
                         ("call_link", "link"), ("change_type", "change")):  # fmt: skip
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
    return cov


# -- text --------------------------------------------------------------------------------


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"


def render_text(cov: Coverage, *, head: str | None = None, filters: Filters | None = None) -> str:
    run, repo = cov.run, cov.repository
    lines = [f"Tandem report: {repo['name']} ({repo['path']})"]
    target = run["target_commit"] or "?"
    fresh = ""
    if head is not None and head != target:
        fresh = f"  [stale: HEAD is now {head[:12]}; run `tandem scan`]"
    lines.append(f"Revision {target[:12]}  run #{run['run_id']} {run['state']}, "
                 f"finished {run['finished_at']}{fresh}")  # fmt: skip
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
        lines.append(f"  {agent}: {sum(statuses.values())} log file(s) ({_fmt_counts(statuses)}); "
                     f"{sess['sessions']} session(s) in this repo, "
                     f"{sess['subagent_sessions']} subagent")  # fmt: skip
    lines.append("")

    lines.append("Observed edits")
    if not cov.edits:
        lines.append("  No edits from either agent touched this repository.")
    for agent, a in sorted(cov.edits.items()):
        outside = a["observed"] - a["in_repository"]
        lines.append(f"  {agent}: {a['in_repository']} edit(s) to {a['files']} file(s) in this repo"
                     + (f" ({outside} elsewhere or unresolved)" if outside else ""))  # fmt: skip
        if a["in_repository"]:
            lines.append(f"    +{a['lines_added']} / -{a['lines_removed']} lines as logged; "
                         f"changes: {_fmt_counts(a['change_type'])}")  # fmt: skip
            lines.append(f"    reconstructable from log: {_fmt_counts(a['reconstruction'])}")
            lines.append(f"    evidence: {_fmt_counts(a['evidence_source'])}")
            if a["call_link"].get("source_order"):
                lines.append(
                    f"    linked to tool call by position only: {a['call_link']['source_order']}"
                )
            if a["user_modified"]:
                lines.append(f"    changed by the user during approval: {a['user_modified']}")
    lines.append("")

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
