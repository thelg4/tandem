"""Run attribution for one scan and store the results (spec §5, §6, §12).

Edit content is not kept in the database, so each run re-reads the original log
records for edits placed in this repository, from approved source roots only, and
matches them back to stored edits by their stable event IDs. A log that has since
disappeared leaves its edits unplaced with a reason; earlier evidence is not erased.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tandem.adapters import AdapterContext, ClaudeCodeAdapter, CodexAdapter
from tandem.adapters.base import Adapter
from tandem.attribution.engine import DIFF_POLICY, AttrEdit, Outcome, Result
from tandem.attribution.git_history import attribute
from tandem.config import Policy
from tandem.git_reader import RENAME_POLICY, CommitMeta, GitReader
from tandem.globs import matches_any
from tandem.interactions import Interaction, Window, derive
from tandem.model.attribution import EvidenceClass
from tandem.model.edits import ObservedEdit
from tandem.model.interactions import InteractionLabel
from tandem.sources.jsonl import read_incremental
from tandem.versions import ATTRIBUTION_RULES_VERSION, INTERACTION_RULES_VERSION


@dataclass(frozen=True, slots=True)
class AttributionSummary:
    commits_analyzed: int
    edits: int
    placed: int
    verified_lines: int
    inserted_lines: int
    cross_agent_interactions: int = 0


def _epoch(utc: str | None) -> float | None:
    if not utc:
        return None
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _adapter(provider: str) -> Adapter:
    return ClaudeCodeAdapter() if provider == "claude_code" else CodexAdapter()


def load_edits(
    conn: sqlite3.Connection, repo_id: str, only: set[str] | None = None
) -> tuple[list[AttrEdit], dict[str, str]]:
    """Stored in-repo edits joined back to their content in the original logs.

    ``only`` limits loading to those edit IDs (and the logs that hold them).
    """
    rows = conn.execute(
        "SELECT d.edit_id, d.path, e.session_id, ss.agent_family, e.utc_timestamp, "
        "e.source_id, e.sequence, s.path AS source_path, s.provider, s.generation, "
        "s.processed_offset, s.status "
        "FROM edits d JOIN events e ON e.event_id = d.event_id "
        "JOIN sources s ON s.source_id = e.source_id AND s.generation = e.source_generation "
        "JOIN sessions ss ON ss.session_id = e.session_id "
        "WHERE d.repo_id = ? AND e.path_resolution = 'resolved' AND e.status = 'succeeded'",
        (repo_id,),
    ).fetchall()
    by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if only is None or row["edit_id"] in only:
            by_source[row["source_id"]].append(row)

    edits: list[AttrEdit] = []
    unavailable: dict[str, str] = {}
    for source_id, group in by_source.items():
        first = group[0]
        path = Path(first["source_path"])
        if first["status"] != "active" or not path.is_file():
            for row in group:
                unavailable[row["edit_id"]] = "source_unavailable"
            continue
        result = read_incremental(path, None, snapshot_limit=int(first["processed_offset"]))
        ctx = AdapterContext(source_id, int(first["generation"]), str(path))
        content: dict[str, ObservedEdit] = {
            ev.identity.event_id: ev.edit
            for ev in _adapter(first["provider"]).parse(result.records, ctx)
            if ev.edit is not None
        }
        for row in group:
            edit = content.get(row["edit_id"])
            if edit is None:
                unavailable[row["edit_id"]] = "source_record_changed"
                continue
            edits.append(
                AttrEdit(
                    edit_id=row["edit_id"],
                    session_id=row["session_id"],
                    agent=row["agent_family"],
                    time=_epoch(row["utc_timestamp"]),
                    order=(source_id, int(row["sequence"])),
                    path=row["path"],
                    edit=edit,
                )
            )
    return edits, unavailable


def run_attribution(
    conn: sqlite3.Connection,
    reader: GitReader,
    run_id: int,
    repo_id: str,
    target: str,
    policy: Policy,
    *,
    since: str | None = None,
) -> AttributionSummary:
    commits = reader.history(target, max_count=policy.max_history_commits)
    if since is not None:
        cut = next((i for i, c in enumerate(commits) if c.oid == since), None)
        if cut is not None:
            commits = commits[cut + 1 :]

    def excluded(path: str) -> bool:
        return matches_any(path, policy.exclude_globs)

    edits, unavailable = load_edits(conn, repo_id)
    result = attribute(
        reader, commits, edits, max_blob_bytes=policy.max_blob_bytes, excluded=excluded
    )
    index = {c.oid: i for i, c in enumerate(commits)}
    tracked = result.tracked_paths
    since_index = {p: index[oid] for p, oid in result.unavailable_since.items()}
    per_commit = reader.added_lines_by_commit(
        target, max_count=policy.max_history_commits, include=lambda p: not excluded(p)
    )
    # Lines the engine did not line-track count as unknown: files no agent touched, and
    # tracked files from the commit where they became binary or too large onward.
    untracked = 0
    for oid, paths in per_commit.items():
        at = index.get(oid)
        if at is None:
            continue
        for path, n in paths.items():
            if path not in tracked or (path in since_index and at >= since_index[path]):
                untracked += n
    in_range = set(index)
    window = Window(policy.window_hours, policy.window_commits, policy.window_mode)
    found = derive(result, {e.edit_id: e for e in edits}, window)

    with conn:
        _store(conn, run_id, repo_id, result, unavailable, untracked, commits)
        _store_interactions(conn, run_id, found, window)
    verified = sum(
        s.count
        for s in result.spans
        if s.origin.evidence is EvidenceClass.VERIFIED
        and s.origin.edit_id is not None
        and s.introduced_commit in in_range
    )
    placed = sum(
        1
        for r in result.edits.values()
        if r.outcome not in (Outcome.UNPLACED, Outcome.EXCLUDED, Outcome.BEFORE_HISTORY)
    )
    return AttributionSummary(
        commits_analyzed=len(commits),
        edits=len(result.edits) + len(unavailable),
        placed=placed,
        verified_lines=verified,
        inserted_lines=result.inserted_lines_in_range + untracked,
        cross_agent_interactions=sum(
            1
            for i in found
            if i.cross_agent
            and i.label is not InteractionLabel.OVERLAP_CANDIDATE
            and i.evidence is EvidenceClass.VERIFIED
        ),
    )


def _store_interactions(
    conn: sqlite3.Connection, run_id: int, found: list[Interaction], window: Window
) -> None:
    for rank, i in enumerate(found):
        conn.execute(
            "INSERT INTO interactions (run_id, interaction_id, original_edit_id, later_edit_id, "
            "path, original_agent, later_agent, primary_label, tags, evidence_class, "
            "lines_affected, start_line, block_removed, block_inserted, original_replaced, "
            "original_inserted, elapsed_seconds, commit_distance, original_commit_id, "
            "removal_commit_id, recent, review_rank, rule_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                i.interaction_id,
                i.original_edit,
                i.later_edit,
                i.path,
                i.original_agent,
                i.later_agent,
                i.label.value,
                json.dumps(list(i.tags)),
                i.evidence.value,
                i.lines_affected,
                i.start_line,
                i.block_removed,
                i.block_inserted,
                i.original_replaced,
                i.original_inserted,
                i.elapsed_seconds,
                i.commit_distance,
                i.original_commit,
                i.removal_commit,
                int(i.recent),
                rank,
                INTERACTION_RULES_VERSION,
            ),
        )
    conn.execute(
        "UPDATE run_attribution SET window_hours=?, window_commits=?, window_mode=? WHERE run_id=?",
        (window.hours, window.commits, window.mode.value, run_id),
    )


def _store(
    conn: sqlite3.Connection,
    run_id: int,
    repo_id: str,
    result: Result,
    unavailable: dict[str, str],
    untracked: int,
    commits: list[CommitMeta],
) -> None:
    for c in commits:
        conn.execute(
            "INSERT OR IGNORE INTO commits (commit_id, repo_id, tree_id, parent_ids, "
            "committer_time) VALUES (?,?,?,?,?)",
            (c.oid, repo_id, c.tree, json.dumps(list(c.parents)), str(c.commit_time)),
        )
    for span in result.spans:
        o = span.origin
        evidence = "provisional" if span.state == "provisional" else o.evidence.value
        conn.execute(
            "INSERT INTO lineage (run_id, repo_id, path, introduced_commit_id, "
            "introduced_edit_id, removed_commit_id, removed_edit_id, line_start, line_count, "
            "owner_session_id, owner_agent_family, evidence_class, rule_version, state, "
            "origin_kind, reason_codes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                repo_id,
                span.path,
                span.introduced_commit,
                o.edit_id,
                span.removed_commit,
                span.removed_by,
                span.start,
                span.count,
                o.session_id,
                o.agent,
                evidence,
                ATTRIBUTION_RULES_VERSION,
                span.state,
                o.kind.value,
                json.dumps(list(o.reasons)),
            ),
        )
    for r in result.edits.values():
        conn.execute(
            "INSERT INTO edit_outcomes (run_id, edit_id, outcome, evidence_class, "
            "anchor_commit_id, lines_inserted, lines_committed, lines_removed, reason_codes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                r.edit_id,
                r.outcome.value,
                r.evidence.value if r.evidence else None,
                r.anchor_commit,
                r.lines_inserted,
                r.lines_committed,
                r.lines_removed,
                json.dumps(r.reasons),
            ),
        )
    for edit_id, reason in unavailable.items():
        conn.execute(
            "INSERT INTO edit_outcomes (run_id, edit_id, outcome, lines_inserted, "
            "lines_committed, lines_removed, reason_codes) VALUES (?,?, 'unplaced', 0, 0, 0, ?)",
            (run_id, edit_id, json.dumps([reason])),
        )
    conn.execute(
        "INSERT INTO run_attribution (run_id, baseline_commit_id, commits_analyzed, "
        "history_truncated, tracked_files, tracked_inserted_lines, untracked_inserted_lines, "
        "unavailable_paths, rename_policy, diff_policy, rules_version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            result.baseline_commit,
            result.commits_analyzed,
            int(result.baseline_commit is not None),
            len(result.tracked_paths),
            result.inserted_lines_in_range,
            untracked,
            json.dumps(result.unavailable_paths),
            RENAME_POLICY,
            DIFF_POLICY,
            ATTRIBUTION_RULES_VERSION,
        ),
    )
