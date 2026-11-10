"""Per-line agent blame for one file, from the latest scan's logs and live Git history.

The attribution engine runs over the file's first-parent history with only that file's
agent edits, exactly as a scan would for it, and its final lines are aligned with the
file on disk. A line on disk that the engine's final state does not contain is reported
as ``changed``: something no ingested log explains yet (a hand edit, a shell command, or
an agent edit newer than the last scan). Nothing is guessed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from tandem.attribution.engine import Engine, Line, OriginKind
from tandem.attribution.git_history import blob_lines
from tandem.attribution.run import load_edits
from tandem.attribution.transitions import to_lines
from tandem.config import Policy
from tandem.git_reader import GitReader
from tandem.globs import matches_any


@dataclass(frozen=True, slots=True)
class BlameSpan:
    start: int
    """1-based first line on disk."""
    count: int
    kind: str
    """agent | unknown | baseline | changed"""
    evidence: str | None = None
    """verified | supported | ambiguous | unknown for agent lines."""
    agent: str | None = None
    session_id: str | None = None
    edit_id: str | None = None
    at: str | None = None
    """When the edit happened (UTC ISO), for agent lines."""
    commit: str | None = None
    """First commit containing these lines; None while uncommitted."""


@dataclass(slots=True)
class FileBlame:
    path: str
    lines: int
    spans: list[BlameSpan] = field(default_factory=list)
    scanned_at: str | None = None
    """When the scan whose logs this uses finished; None when there was none."""
    edits: int = 0
    """Agent edits to this file the scan knows about."""
    unavailable_edits: int = 0
    """Edits whose log records could not be read back (deleted or rewritten logs)."""
    note: str | None = None

    def to_json(self) -> dict[str, object]:
        out = asdict(self)
        out["spans"] = [asdict(s) for s in self.spans]
        return out


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _key(line: Line | None) -> tuple[object, ...]:
    if line is None:
        return ("changed",)
    o = line.origin
    return (o.kind, o.edit_id, o.evidence, line.commit)


def blame_file(
    conn: sqlite3.Connection,
    reader: GitReader,
    repo_id: str,
    path: str,
    disk_text: str,
    policy: Policy,
) -> FileBlame:
    disk = to_lines(disk_text)
    out = FileBlame(path=path, lines=len(disk))
    row = conn.execute(
        "SELECT r.finished_at FROM latest_run l JOIN analysis_runs r ON r.run_id = l.run_id "
        "WHERE l.repo_id = ?",
        (repo_id,),
    ).fetchone()
    out.scanned_at = row["finished_at"] if row else None

    def whole(kind: str, note: str) -> FileBlame:
        out.note = note
        if disk:
            out.spans = [BlameSpan(1, len(disk), kind)]
        return out

    if matches_any(path, policy.exclude_globs):
        return whole("unknown", "path_excluded_by_policy")
    ids = {
        r["edit_id"]
        for r in conn.execute(
            "SELECT edit_id FROM edits WHERE repo_id = ? AND path = ?", (repo_id, path)
        )
    }
    if not ids:
        return whole("unknown", "no_agent_edits")
    edits, unavailable = load_edits(conn, repo_id, only=ids)
    out.edits = len(ids)
    out.unavailable_edits = len(unavailable)
    if not edits:
        return whole("unknown", "edit_logs_unavailable")

    head = reader.resolve_commit("HEAD")
    commits = reader.history(head, max_count=policy.max_history_commits)
    deltas = reader.path_deltas(commits, path)
    load = blob_lines(reader, policy.max_blob_bytes)

    def baseline_lines(commit: str, p: str) -> list[str] | None:
        oid = reader.blob_at(commit, p)
        return None if oid is None else load(oid)

    baseline_time = None
    if commits and commits[0].parents:
        baseline_time = reader.history(commits[0].parents[0], max_count=1)[0].commit_time
    engine = Engine(
        commits,
        lambda c: deltas.get(c.oid, []),
        load,
        baseline_lines,
        baseline_time=baseline_time,
    )
    engine.run(edits)
    final = engine.final_lines(path)
    if final is None:
        return whole("unknown", "file_not_line_tracked")

    # Align the engine's final state with the file as it is now.
    per_line: list[Line | None] = [None] * len(disk)
    matcher = SequenceMatcher(None, [ln.text for ln in final], disk, autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            per_line[block.b + k] = final[block.a + k]

    times = {e.edit_id: e.time for e in edits}
    start = 0
    for i in range(1, len(disk) + 1):
        if i < len(disk) and _key(per_line[i]) == _key(per_line[start]):
            continue
        line = per_line[start]
        if line is None:
            span = BlameSpan(start + 1, i - start, "changed")
        else:
            o = line.origin
            agent = o.kind is OriginKind.AGENT
            span = BlameSpan(
                start + 1,
                i - start,
                o.kind.value,
                evidence=o.evidence.value if agent else None,
                agent=o.agent if agent else None,
                session_id=o.session_id if agent else None,
                edit_id=o.edit_id if agent else None,
                at=_iso(times.get(o.edit_id or "")) if agent else None,
                commit=line.commit,
            )
        out.spans.append(span)
        start = i
    return out
