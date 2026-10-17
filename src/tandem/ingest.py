"""Scan: bounded, incremental import of agent logs into the repository database.

One scan:

1. Captures the target commit and each source file's size before reading anything
   (stable scan boundary). Bytes written after that belong to the next scan.
2. Skips unchanged sources. A grown source is re-parsed from the start of its current
   generation so calls and results pair across the append; stable event IDs make the
   re-insert idempotent. A rewritten source starts a new generation; old rows are kept,
   not erased.
3. Resolves events against the repository and stores only sessions that touched it.
4. Imports each source in its own transaction, then marks the run complete. A failed
   run never replaces the latest complete run.

Full prompts, responses, and terminal output are not stored. Edits store digests,
hunk ranges, and change metadata; the source locator links back to the original record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tandem.adapters import AdapterContext, ClaudeCodeAdapter, CodexAdapter
from tandem.adapters.base import Adapter
from tandem.attribution.run import AttributionSummary, run_attribution
from tandem.config import Policy
from tandem.diffs import ApplyError, apply_hunks
from tandem.errors import TandemError
from tandem.git_reader import GitReader, RepoInfo
from tandem.model.edits import ChangeType, ObservedEdit
from tandem.model.events import AgentFamily, Event, EventType, PathResolution
from tandem.resolve import RepoResolver, segments, worktree_id
from tandem.sources.discovery import SourceFile, iter_source_files, resolve_roots
from tandem.sources.jsonl import (
    Checkpoint,
    SourceState,
    classify,
    prefix_digest,
    read_incremental,
)
from tandem.storage import db
from tandem.versions import manifest_versions

MAX_DIAGNOSTICS_PER_SOURCE = 50


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"


def _digest(text: str | None) -> str | None:
    return (
        None
        if text is None
        else hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()
    )


def source_id_for(provider: str, path: Path) -> str:
    return hashlib.sha256(f"{provider}\0{path}".encode("utf-8", "surrogateescape")).hexdigest()[:24]


def policy_hash(policy: Policy) -> str:
    data = {k: v for k, v in asdict(policy).items() if k != "sources"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass(slots=True)
class ScanSummary:
    run_id: int
    state: str
    target_commit: str
    sources: Counter[str] = field(default_factory=Counter)
    sources_with_repo_activity: int = 0
    sessions: Counter[str] = field(default_factory=Counter)
    events_inserted: int = 0
    edits: Counter[str] = field(default_factory=Counter)
    quarantined_records: int = 0
    diagnostics: int = 0
    attribution: AttributionSummary | None = None


@dataclass(frozen=True, slots=True)
class ScanOptions:
    ref: str = "HEAD"
    claude_root: Path | None = None
    codex_root: Path | None = None
    include_worktrees: bool = False
    rebuild: bool = False
    since: str | None = None


def _adapter(agent: AgentFamily) -> Adapter:
    return ClaudeCodeAdapter() if agent is AgentFamily.CLAUDE_CODE else CodexAdapter()


def _reconstruction(edit: ObservedEdit) -> str:
    if edit.change is ChangeType.DELETE:
        return "reconstructed"
    if edit.change is ChangeType.ADD:
        return "reconstructed" if edit.after_text is not None else "partial"
    if edit.before_text is not None and edit.hunks and all(h.numbered for h in edit.hunks):
        try:
            result = apply_hunks(edit.before_text, edit.hunks)
        except ApplyError:
            return "failed"
        if edit.after_text is not None and result != edit.after_text.replace("\r\n", "\n"):
            return "failed"
        return "reconstructed"
    if edit.before_text is not None and edit.after_text is not None:
        return "reconstructed"
    return "partial" if edit.hunks else "unsupported"


class Scanner:
    def __init__(self, repo_path: Path, policy: Policy, db_path: Path) -> None:
        self.git = GitReader(repo_path, timeout=policy.git_timeout_seconds)
        self.policy = policy
        self.db_path = db_path

    def run(self, options: ScanOptions) -> ScanSummary:
        info = self.git.discover()
        if info.head is None:
            raise TandemError("repository has no commits to analyze")
        target = self.git.resolve_commit(options.ref)
        roots = resolve_roots(options.claude_root, options.codex_root)
        # Stable scan boundary: list files and capture sizes before reading any of them.
        snapshot: list[SourceFile] = [f for root in roots for f in iter_source_files(root)]

        with db.writer_lock(self.db_path):
            conn = db.connect(self.db_path)
            try:
                db.migrate(conn, self.db_path)
                return self._run(
                    conn,
                    info,
                    target,
                    snapshot,
                    roots_found=[r for r in roots if r.available],
                    options=options,
                )
            finally:
                conn.close()

    # -- run ------------------------------------------------------------------------------

    def _run(
        self,
        conn: sqlite3.Connection,
        info: RepoInfo,
        target: str,
        snapshot: list[SourceFile],
        roots_found: list[Any],
        options: ScanOptions,
    ) -> ScanSummary:
        repo_id = self._repository(conn, info)
        siblings = [p for p in self.git.worktrees() if p.resolve() != info.top_level]
        self._worktrees(conn, repo_id, info.top_level, siblings)
        resolver = RepoResolver(
            repo_id, info.top_level, siblings=siblings, include_siblings=options.include_worktrees
        )

        with conn:
            cur = conn.execute(
                "INSERT INTO analysis_runs (repo_id, state, started_at, target_commit_id, "
                "policy_hash, manifest) VALUES (?, 'running', ?, ?, ?, '{}')",
                (repo_id, _now(), target, policy_hash(self.policy)),
            )
        run_id = int(cur.lastrowid or 0)
        summary = ScanSummary(run_id=run_id, state="running", target_commit=target)
        manifest_sources: list[dict[str, Any]] = []
        try:
            for source in snapshot:
                entry = self._import_source(conn, run_id, source, resolver, options, summary)
                manifest_sources.append(entry)
            seen = {source_id_for(s.agent.value, s.path) for s in snapshot}
            self._mark_missing(conn, run_id, seen, summary)
            since = self.git.resolve_commit(options.since) if options.since else None
            summary.attribution = run_attribution(
                conn, self.git, run_id, repo_id, target, self.policy, since=since
            )
        except BaseException:
            with conn:
                conn.execute(
                    "UPDATE analysis_runs SET state='failed', finished_at=? WHERE run_id=?",
                    (_now(), run_id),
                )
            raise

        partial = (
            summary.quarantined_records > 0
            or summary.sources["unavailable"] > 0
            or not roots_found
            or info.is_shallow
        )
        summary.state = "partial" if partial else "complete"
        manifest = {
            "repository": {"top_level": str(info.top_level), "shallow": info.is_shallow},
            "target_commit": target,
            "ref": options.ref,
            "include_worktrees": options.include_worktrees,
            "since": options.since,
            "sources": manifest_sources,
            "policy": {k: v for k, v in asdict(self.policy).items() if k != "sources"},
            "versions": manifest_versions(),
        }
        with conn:
            conn.execute(
                "UPDATE analysis_runs SET state=?, finished_at=?, manifest=? WHERE run_id=?",
                (summary.state, _now(), json.dumps(manifest, sort_keys=True, default=str), run_id),
            )
            conn.execute(
                "INSERT INTO latest_run (repo_id, run_id) VALUES (?, ?) "
                "ON CONFLICT(repo_id) DO UPDATE SET run_id=excluded.run_id",
                (repo_id, run_id),
            )
        return summary

    def _repository(self, conn: sqlite3.Connection, info: RepoInfo) -> str:
        row = conn.execute(
            "SELECT repo_id FROM repositories WHERE common_git_dir=?", (str(info.common_dir),)
        ).fetchone()
        if row:
            return str(row[0])
        repo_id = str(uuid.uuid4())
        with conn:
            conn.execute(
                "INSERT INTO repositories (repo_id, common_git_dir, display_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (repo_id, str(info.common_dir), info.top_level.name, _now()),
            )
        return repo_id

    def _worktrees(
        self, conn: sqlite3.Connection, repo_id: str, primary: Path, siblings: list[Path]
    ) -> None:
        with conn:
            for path in [primary, *siblings]:
                conn.execute(
                    "INSERT INTO worktrees (worktree_id, repo_id, path, is_primary) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(worktree_id) DO UPDATE SET is_primary=excluded.is_primary",
                    (worktree_id(path), repo_id, str(path), int(path == primary)),
                )

    # -- sources --------------------------------------------------------------------------

    def _import_source(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        source: SourceFile,
        resolver: RepoResolver,
        options: ScanOptions,
        summary: ScanSummary,
    ) -> dict[str, Any]:
        provider = source.agent.value
        sid = source_id_for(provider, source.path)
        row = conn.execute(
            "SELECT generation, processed_offset, prefix_digest, status FROM sources "
            "WHERE source_id=?",
            (sid,),
        ).fetchone()
        generation = int(row["generation"]) if row else 0
        previous = (
            Checkpoint(int(row["processed_offset"]), str(row["prefix_digest"]))
            if row and row["prefix_digest"] is not None
            else None
        )
        limit = source.size
        state = classify(source.path, previous, limit)
        if state is SourceState.REWRITTEN:
            generation += 1
        entry = {
            "source_id": sid,
            "provider": provider,
            "generation": generation,
            "snapshot_limit": limit,
            "state": state.value,
        }
        summary.sources[state.value] += 1
        if state is SourceState.UNCHANGED and not options.rebuild:
            entry["prefix_digest"] = previous.prefix_digest if previous else None
            return entry

        # Re-parse the whole current generation so calls pair with results across appends.
        result = read_incremental(
            source.path, None, snapshot_limit=limit, max_record_bytes=self.policy.max_record_bytes
        )
        if result.state is SourceState.MISSING:
            summary.sources["unavailable"] += 1
            return entry
        ctx = AdapterContext(sid, generation, str(source.path))
        adapter = _adapter(source.agent)
        events = [resolver.apply(e)[0] for e in adapter.parse(result.records, ctx)]
        touched = {e.actor.session_id for e in events if e.scope.repo_id is not None}
        kept = [e for e in events if e.actor.session_id in touched]
        digest = prefix_digest(source.path, result.processed_offset)
        entry["prefix_digest"] = digest

        with conn:
            conn.execute(
                "INSERT INTO sources (source_id, provider, path, generation, prefix_digest, "
                "processed_offset, snapshot_limit, parser_version, status, last_scanned_at) "
                "VALUES (?,?,?,?,?,?,?,?, 'active', ?) ON CONFLICT(source_id) DO UPDATE SET "
                "generation=excluded.generation, prefix_digest=excluded.prefix_digest, "
                "processed_offset=excluded.processed_offset, "
                "snapshot_limit=excluded.snapshot_limit, "
                "parser_version=excluded.parser_version, status='active', "
                "last_scanned_at=excluded.last_scanned_at",
                (
                    sid,
                    provider,
                    str(source.path),
                    generation,
                    digest,
                    result.processed_offset,
                    limit,
                    adapter.version,
                    _now(),
                ),
            )
            if kept:
                summary.sources_with_repo_activity += 1
                self._store_sessions(conn, sid, kept, resolver)
                for event in kept:
                    if self._store_event(conn, event):
                        summary.events_inserted += 1
            for q in result.quarantined[:MAX_DIAGNOSTICS_PER_SOURCE]:
                self._diagnostic(
                    conn,
                    run_id,
                    sid,
                    "warning",
                    "record_quarantined",
                    q.reason,
                    q.offset,
                    q.excerpt,
                )
            for code, message, offset in ctx.diagnostics[:MAX_DIAGNOSTICS_PER_SOURCE]:
                self._diagnostic(conn, run_id, sid, "info", code, message, offset, None)
        summary.quarantined_records += len(result.quarantined)
        summary.diagnostics += len(result.quarantined) + len(ctx.diagnostics)
        summary.sessions[provider] += len(touched)
        for event in kept:
            if event.edit is not None:
                summary.edits[f"{provider}:{event.scope.path_resolution.value}"] += 1
        return entry

    def _mark_missing(
        self, conn: sqlite3.Connection, run_id: int, seen: set[str], summary: ScanSummary
    ) -> None:
        rows = conn.execute("SELECT source_id, path FROM sources WHERE status='active'").fetchall()
        with conn:
            for row in rows:
                if row["source_id"] in seen or Path(row["path"]).exists():
                    continue
                conn.execute(
                    "UPDATE sources SET status='unavailable' WHERE source_id=?", (row["source_id"],)
                )
                self._diagnostic(
                    conn,
                    run_id,
                    row["source_id"],
                    "warning",
                    "source_missing",
                    "log file no longer exists; earlier evidence kept",
                    None,
                    None,
                )
                summary.sources["unavailable"] += 1

    # -- rows -------------------------------------------------------------------------------

    def _store_sessions(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        events: list[Event],
        resolver: RepoResolver,
    ) -> None:
        bounds: dict[str, tuple[Event, str | None, str | None]] = {}
        for e in events:
            ts = e.order.utc_timestamp
            first, lo, hi = bounds.get(e.actor.session_id, (e, ts, ts))
            if ts is not None:
                lo = ts if lo is None or ts < lo else lo
                hi = ts if hi is None or ts > hi else hi
            bounds[e.actor.session_id] = (first, lo, hi)
        for session_id, (first, lo, hi) in bounds.items():
            actor = first.actor
            if actor.parent_session_id:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, agent_family, provider_session_id, "
                    "completeness) VALUES (?, ?, ?, 'referenced_only')",
                    (
                        actor.parent_session_id,
                        actor.agent_family.value,
                        actor.parent_session_id.split(":", 1)[-1],
                    ),
                )
            conn.execute(
                "INSERT INTO sessions (session_id, agent_family, provider_session_id, "
                "parent_session_id, first_observed_at, last_observed_at, completeness) "
                "VALUES (?,?,?,?,?,?, 'observed') ON CONFLICT(session_id) DO UPDATE SET "
                "first_observed_at=min(coalesce(first_observed_at, excluded.first_observed_at), "
                "excluded.first_observed_at), "
                "last_observed_at=max(coalesce(last_observed_at, excluded.last_observed_at), "
                "excluded.last_observed_at), completeness='observed'",
                (
                    session_id,
                    actor.agent_family.value,
                    actor.provider_session_id,
                    actor.parent_session_id,
                    lo,
                    hi,
                ),
            )
        session_ids = list(bounds)
        marks = ",".join("?" * len(session_ids))
        conn.execute(
            f"DELETE FROM session_segments WHERE source_id=? AND session_id IN ({marks})",  # noqa: S608
            [source_id, *session_ids],
        )
        for seg in segments(events, resolver):
            conn.execute(
                "INSERT INTO session_segments (session_id, source_id, repo_id, worktree_id, "
                "first_sequence, last_sequence) VALUES (?,?,?,?,?,?)",
                (
                    seg.session_id,
                    source_id,
                    resolver.repo_id,
                    seg.worktree_id,
                    seg.first_sequence,
                    seg.last_sequence,
                ),
            )

    def _store_event(self, conn: sqlite3.Connection, e: Event) -> bool:
        cur = conn.execute(
            "INSERT INTO events (event_id, source_id, source_generation, record_offset, "
            "record_length, provider_event_id, session_id, sequence, event_type, status, "
            "tool_name, "
            "tool_call_id, model_id, repo_id, worktree_id, recorded_cwd, effective_cwd, "
            "relative_path, path_resolution, raw_timestamp, utc_timestamp, time_quality, "
            "adapter_version, envelope_version, reason_codes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(event_id) DO NOTHING",
            (
                e.identity.event_id,
                e.identity.source_id,
                e.identity.source_generation,
                e.identity.record_offset,
                e.evidence.locator.byte_length,
                e.identity.provider_event_id,
                e.actor.session_id,
                e.order.sequence,
                e.action.event_type.value,
                e.action.status.value,
                e.action.tool_name,
                e.action.tool_call_id,
                e.actor.model_id,
                e.scope.repo_id,
                e.scope.worktree_id,
                e.scope.recorded_cwd,
                e.scope.effective_cwd,
                e.scope.relative_path,
                e.scope.path_resolution.value,
                e.order.raw_timestamp,
                e.order.utc_timestamp,
                e.order.time_quality.value,
                e.identity.adapter_version,
                e.identity.schema_version,
                json.dumps(list(e.evidence.reason_codes)),
            ),
        )
        if cur.rowcount == 0:
            return False
        if e.usage is not None:
            segment = next(
                (
                    int(c.rsplit("_", 1)[1])
                    for c in e.evidence.reason_codes
                    if c.startswith("usage_segment_")
                ),
                0,
            )
            for category, value in e.usage.categories.items():
                conn.execute(
                    "INSERT INTO usage_records (event_id, basis, category, raw_value, incremental, "
                    "segment) VALUES (?,?,?,?,?,?)",
                    (
                        e.identity.event_id,
                        e.usage.basis.value,
                        category,
                        value,
                        value if e.usage.basis.value == "per_call" else None,
                        segment,
                    ),
                )
        if e.action.event_type is EventType.TEXT_EDIT and e.edit is not None:
            self._store_edit(conn, e, e.edit)
        return True

    def _store_edit(self, conn: sqlite3.Connection, e: Event, edit: ObservedEdit) -> None:
        resolved = e.scope.path_resolution is PathResolution.RESOLVED
        conn.execute(
            "INSERT INTO edits (edit_id, event_id, repo_id, path, before_digest, after_digest, "
            "reconstruction, change_type, evidence_source, call_link, move_to, lines_added, "
            "lines_removed, user_modified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                e.identity.event_id,
                e.identity.event_id,
                e.scope.repo_id,
                e.scope.relative_path if resolved else edit.path,
                _digest(edit.before_text),
                _digest(edit.after_text),
                _reconstruction(edit),
                edit.change.value,
                edit.source.value,
                edit.call_link.value,
                edit.move_to,
                edit.lines_added,
                edit.lines_removed,
                int(edit.user_modified),
            ),
        )
        reasons = json.dumps(list(e.evidence.reason_codes))
        if edit.hunks:
            for h in edit.hunks:
                conn.execute(
                    "INSERT INTO edit_spans (edit_id, old_start, old_count, new_start, new_count, "
                    "evidence_class, reason_codes) VALUES (?,?,?,?,?, 'provisional', ?)",
                    (
                        e.identity.event_id,
                        h.old_start,
                        h.old_lines,
                        h.new_start,
                        h.new_lines,
                        reasons,
                    ),
                )
        elif edit.change is ChangeType.ADD and edit.after_text is not None:
            conn.execute(
                "INSERT INTO edit_spans (edit_id, old_start, old_count, new_start, new_count, "
                "evidence_class, reason_codes) VALUES (?, NULL, 0, 1, ?, 'provisional', ?)",
                (e.identity.event_id, len(edit.after_text.splitlines()), reasons),
            )

    def _diagnostic(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        source_id: str,
        severity: str,
        code: str,
        message: str,
        offset: int | None,
        excerpt: str | None,
    ) -> None:
        conn.execute(
            "INSERT INTO diagnostics (run_id, source_id, severity, code, message, record_offset, "
            "excerpt) VALUES (?,?,?,?,?,?,?)",
            (run_id, source_id, severity, code, message[:500], offset, excerpt),
        )
