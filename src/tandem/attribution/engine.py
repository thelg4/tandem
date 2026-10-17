"""Line-lineage attribution over first-parent history (spec §5, §6).

Each tracked file is a list of line instances. An instance keeps its origin (an agent
edit, an unexplained change, or the history baseline) and the commit where it first
landed. Two kinds of step move the lists forward:

- **Agent edit.** The edit is reconstructed against the file's working state. Lines it
  inserts become new instances owned by the edit; lines it replaces or deletes are
  marked removed by it. If the log's own before-state differs from the working state,
  the difference is applied first as an unexplained change, so the gap is never
  credited to an agent.
- **Commit.** The committed blob is diffed against the working state. Matching lines
  keep their instances and are anchored to the commit if new. Lines only in the commit
  are new instances of unknown origin; lines only in the working state are removed (if
  committed before) or dropped before commit (if not).

Edits are assigned to the first commit at or after their timestamp. Commit dates are
treated as metadata: an edit only counts if its lines actually appear in the commit.

Line identity is exact text after line-ending normalization (ALG-06). Unchanged lines
keep their instance across rewrites (ATT-03); a modified line is a removal plus an
introduction (ALG-05). Alignment uses Python's difflib, a deterministic algorithm that
is recorded as the diff policy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

from tandem.attribution.transitions import Transition, Unplaceable, reconstruct
from tandem.git_reader import CommitMeta, FileDelta
from tandem.model.attribution import EvidenceClass
from tandem.versions import ATTRIBUTION_RULES_VERSION

DIFF_POLICY = "difflib.SequenceMatcher(autojunk=False), exact text after newline normalization"
RULES_VERSION = ATTRIBUTION_RULES_VERSION


class OriginKind(StrEnum):
    AGENT = "agent"
    UNKNOWN = "unknown"
    """A change no supported log explains (human, shell, unsupported tool, other agent)."""
    BASELINE = "baseline"
    """Present at the start of the analyzed history; introduction age unknown."""


@dataclass(frozen=True, slots=True)
class Origin:
    kind: OriginKind
    evidence: EvidenceClass
    edit_id: str | None = None
    session_id: str | None = None
    agent: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(slots=True, eq=False)
class Line:
    text: str
    origin: Origin
    commit: str | None = None
    """First commit containing this instance; None while uncommitted."""
    block: int | None = None
    """The edit block that inserted this instance (agent lines only)."""


@dataclass(frozen=True, slots=True)
class Block:
    """One contiguous change made by one agent edit: the lines it replaced and inserted."""

    edit_id: str
    path: str
    position: int
    """0-based line index in the working state where the change starts."""
    old: tuple[str, ...]
    new: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Touch:
    """An agent edit removed a line another edit had inserted, and the removal stuck."""

    path: str
    origin: Origin
    """Origin of the removed line (always an agent edit)."""
    introduced_commit: str | None
    born_block: int | None
    remover: str
    removal_block: int | None
    removed_commit: str | None
    """Commit where the removal landed; None when the line was never committed."""


@dataclass(frozen=True, slots=True)
class AttrEdit:
    edit_id: str
    session_id: str
    agent: str
    time: float | None
    """Epoch seconds, or None when the log had no usable timestamp."""
    order: tuple[str, int]
    """(source id, sequence): order within one log."""
    path: str
    edit: object
    """An ObservedEdit (typed loosely to keep the engine import-light)."""


class Outcome(StrEnum):
    COMMITTED = "committed"
    OVERWRITTEN_BEFORE_COMMIT = "overwritten_before_commit"
    UNCOMMITTED = "uncommitted"
    NO_EFFECT = "no_effect"
    UNPLACED = "unplaced"
    BEFORE_HISTORY = "before_history"
    EXCLUDED = "excluded"


@dataclass(slots=True)
class EditResult:
    edit_id: str
    outcome: Outcome = Outcome.UNPLACED
    evidence: EvidenceClass | None = None
    anchor_commit: str | None = None
    lines_inserted: int = 0
    lines_committed: int = 0
    lines_removed: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Span:
    """A run of consecutive line instances sharing one origin and fate."""

    path: str
    origin: Origin
    introduced_commit: str | None
    start: int
    """0-based position in the file at the point the span was recorded."""
    count: int
    removed_commit: str | None = None
    removed_by: str | None = None
    """Edit ID that removed these lines, when an agent edit did."""
    state: str = "present"
    """present | removed | dropped_before_commit | provisional | unavailable"""


@dataclass(slots=True)
class Result:
    spans: list[Span] = field(default_factory=list)
    edits: dict[str, EditResult] = field(default_factory=dict)
    commits_analyzed: int = 0
    baseline_commit: str | None = None
    tracked_paths: set[str] = field(default_factory=set)
    unavailable_paths: dict[str, str] = field(default_factory=dict)
    unavailable_since: dict[str, str] = field(default_factory=dict)
    """Path -> first commit whose blob could not be line-tracked (binary or too large)."""
    inserted_lines_in_range: int = 0
    """Lines introduced by commits in range for tracked files (denominator part)."""
    blocks: list[Block] = field(default_factory=list)
    touches: list[Touch] = field(default_factory=list)
    commit_index: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class _File:
    committed: list[Line] | None
    working: list[Line] | None = None
    dirty: bool = False
    pending: list[tuple[Line, str, int | None]] = field(default_factory=list)
    """Committed lines an agent edit removed in the working state: (line, edit_id, block)."""
    states: list[tuple[str, str | None]] = field(default_factory=list)
    """Digest of each working state with the session that produced it."""
    applied: list[str] = field(default_factory=list)
    """Edits applied to the working state since the last commit that touched the file."""


def _digest(lines: list[str] | None) -> str:
    return hashlib.sha256("\n".join(lines or []).encode("utf-8", "surrogateescape")).hexdigest()


def _texts(lines: list[Line] | None) -> list[str] | None:
    return None if lines is None else [ln.text for ln in lines]


Loader = Callable[[str], "list[str] | None"]
"""Blob ID -> lines, or None when the blob is binary or too large."""


class Engine:
    def __init__(
        self,
        commits: list[CommitMeta],
        deltas: Callable[[CommitMeta], list[FileDelta]],
        load: Loader,
        baseline_lines: Callable[[str, str], list[str] | None],
        *,
        baseline_time: int | None = None,
        excluded: Callable[[str], bool] = lambda _p: False,
    ) -> None:
        self.commits = commits
        self.deltas = deltas
        self.load = load
        self.baseline_lines = baseline_lines
        self.excluded = excluded
        self.baseline_time = baseline_time
        self.files: dict[str, _File] = {}
        self.result = Result()
        self.baseline = commits[0].parents[0] if commits and commits[0].parents else None
        self.result.baseline_commit = self.baseline
        self.result.commits_analyzed = len(commits)
        self.result.commit_index = {c.oid: i for i, c in enumerate(commits)}
        self._commit_origins: dict[str, Origin] = {}
        self._dropped_by: dict[str, int] = {}
        """Edit -> uncommitted lines it removed (those removals land with the next commit)."""
        self._landed: set[str] = set()
        """Edits whose removal of a committed line reached a commit."""

    # -- public -----------------------------------------------------------------------------

    def run(self, edits: list[AttrEdit]) -> Result:
        for e in edits:
            self.result.edits[e.edit_id] = EditResult(e.edit_id)
        deltas = {c.oid: self.deltas(c) for c in self.commits}
        tracked = self._tracked_paths(edits, deltas)
        self.result.tracked_paths = tracked

        timed: list[tuple[float, AttrEdit]] = []
        for e in edits:
            if e.time is None:
                self._mark(e, Outcome.UNPLACED, "no_timestamp")
            else:
                timed.append((e.time, e))
        timed.sort(key=lambda item: (item[0], item[1].order))

        i = 0
        if self.baseline is not None and self.baseline_time is not None:
            # Edits made before the baseline commit belong to unanalyzed history.
            while i < len(timed) and timed[i][0] < self.baseline_time + 1:
                self._mark(timed[i][1], Outcome.BEFORE_HISTORY, "before_history_start")
                i += 1

        for path in sorted(tracked):
            self._init_file(path)

        for commit in self.commits:
            before: list[AttrEdit] = []
            while i < len(timed) and timed[i][0] < commit.commit_time:
                before.append(timed[i][1])
                i += 1
            self._apply_batch(before)
            # Commit times have one-second resolution. An edit in the commit's own second
            # lands before it only if the commit contains what the edit produced.
            same: list[AttrEdit] = []
            while i < len(timed) and timed[i][0] < commit.commit_time + 1:
                same.append(timed[i][1])
                i += 1
            later: list[AttrEdit] = []
            for e in self._order_ties(same):
                if self._lands_in(e, deltas[commit.oid]):
                    self._apply_edit(e)
                else:
                    later.append(e)
            self._commit(commit, deltas[commit.oid], tracked)
            for e in later:
                self.result.edits[e.edit_id].reasons.append("same_second_as_previous_commit")
                self._apply_edit(e)
        self._apply_batch([e for _, e in timed[i:]])
        self._finish()
        return self.result

    def _apply_batch(self, edits: list[AttrEdit]) -> None:
        for e in self._order_ties(edits):
            self._apply_edit(e)

    def _order_ties(self, edits: list[AttrEdit]) -> list[AttrEdit]:
        """Order edits that share a timestamp so each one's logged state fits, when possible.

        Edits arrive sorted by (time, source, sequence). Across sources that tie-break is
        arbitrary, so within a group of equal times, prefer an edit whose reconstruction
        fits the current working state exactly.
        """
        out: list[AttrEdit] = []
        k = 0
        while k < len(edits):
            j = k
            while j < len(edits) and edits[j].time == edits[k].time:
                j += 1
            group = edits[k:j]
            if len(group) == 1 or len({e.session_id for e in group}) == 1:
                out.extend(group)
            else:
                out.extend(self._fit_order(group))
            k = j
        return out

    def _fit_order(self, group: list[AttrEdit]) -> list[AttrEdit]:
        # Simulate on copies of working texts: pick the first edit that fits, repeat.
        texts = {e.path: _texts(self._current(e.path)) for e in group if e.path in self.files}
        remaining, ordered = list(group), []
        while remaining:
            pick = next((e for e in remaining if self._fits(e, texts.get(e.path))), remaining[0])
            if pick is not remaining[0]:
                self.result.edits[pick.edit_id].reasons.append("reordered_within_same_timestamp")
            remaining.remove(pick)
            ordered.append(pick)
            tr = reconstruct(pick.edit, texts.get(pick.path))  # type: ignore[arg-type]
            if isinstance(tr, Transition):
                texts[pick.path] = tr.after
        return ordered

    @staticmethod
    def _fits(e: AttrEdit, current: list[str] | None) -> bool:
        tr = reconstruct(e.edit, current)  # type: ignore[arg-type]
        if isinstance(tr, Unplaceable) or not tr.unique or "located_by_content" in tr.reasons:
            return False
        return not (tr.before_from_log and tr.before != current)

    def _current(self, path: str) -> list[Line] | None:
        state = self.files[path]
        return state.working if state.dirty else state.committed

    def _lands_in(self, e: AttrEdit, deltas: list[FileDelta]) -> bool:
        """True when ``deltas`` contain the result of ``e`` for its file."""
        delta = next((d for d in deltas if e.path in (d.path, d.old_path)), None)
        if delta is None or e.path not in self.files:
            return True  # the commit does not touch the file; order does not matter
        tr = reconstruct(e.edit, _texts(self._current(e.path)))  # type: ignore[arg-type]
        if isinstance(tr, Unplaceable):
            return True
        if tr.after is None:
            return delta.new_blob is None
        if delta.new_blob is None:
            return False
        committed = self.load(delta.new_blob)
        if committed is None:
            return True
        before = list(tr.before or [])
        added = [t for t in tr.after if t not in before]
        pool = list(committed)
        for t in added:
            if t not in pool:
                return False
            pool.remove(t)
        return True

    # -- setup ------------------------------------------------------------------------------

    def _tracked_paths(self, edits: list[AttrEdit], deltas: dict[str, list[FileDelta]]) -> set[str]:
        tracked = {e.path for e in edits if not self.excluded(e.path)}
        for e in edits:
            if self.excluded(e.path):
                self._mark(e, Outcome.EXCLUDED, "path_excluded_by_policy")
            move = getattr(e.edit, "move_to", None)
            if isinstance(move, str):
                tracked.add(move)
        # Follow exact renames both ways, so lineage starts at the range start and keeps
        # being tracked after the file moves.
        renames = [
            (d.old_path, d.path)
            for c in self.commits
            for d in deltas[c.oid]
            if d.status == "R" and d.old_path
        ]
        changed = True
        while changed:
            changed = False
            for old, new in renames:
                if (old in tracked) != (new in tracked):
                    tracked.update((old, new))
                    changed = True
        return tracked

    def _init_file(self, path: str) -> None:
        if self.baseline is None:
            self.files[path] = _File(committed=None)
            return
        lines = self.baseline_lines(self.baseline, path)
        origin = Origin(
            OriginKind.BASELINE, EvidenceClass.UNKNOWN, reasons=("present_at_history_start",)
        )
        # Baseline lines count as committed at the baseline, so they are never re-counted.
        self.files[path] = _File(
            committed=None
            if lines is None
            else [Line(t, origin, commit=self.baseline) for t in lines]
        )

    def _mark(self, e: AttrEdit, outcome: Outcome, reason: str) -> None:
        r = self.result.edits.setdefault(e.edit_id, EditResult(e.edit_id))
        r.outcome = outcome
        r.reasons.append(reason)

    # -- agent edits ------------------------------------------------------------------------

    def _apply_edit(self, e: AttrEdit) -> None:
        result = self.result.edits[e.edit_id]
        if result.outcome in (Outcome.EXCLUDED, Outcome.BEFORE_HISTORY):
            return
        state = self.files.get(e.path)
        if state is None or e.path in self.result.unavailable_paths:
            self._mark(
                e, Outcome.UNPLACED, self.result.unavailable_paths.get(e.path, "path_untracked")
            )
            return
        current = state.working if state.dirty else state.committed
        tr = reconstruct(e.edit, _texts(current))  # type: ignore[arg-type]
        if isinstance(tr, Unplaceable):
            self._mark(e, Outcome.UNPLACED, tr.reason)
            return

        evidence, reasons = self._evidence(e, tr, state, current)
        lines = current
        if tr.before is not None and _texts(current) != tr.before:
            # The log's before-state differs from what Tandem tracked: an unlogged change
            # (or a concurrent edit) happened in between. Apply it without agent credit.
            concurrent = evidence is EvidenceClass.AMBIGUOUS
            gap = Origin(
                OriginKind.UNKNOWN,
                EvidenceClass.AMBIGUOUS if concurrent else EvidenceClass.UNKNOWN,
                reasons=("concurrent_edit" if concurrent else "unlogged_change_before_edit",),
            )
            lines = self._transform(e.path, state, current or [], tr.before, gap, None)
            reasons = (*reasons, "working_state_gap")

        origin = Origin(OriginKind.AGENT, evidence, e.edit_id, e.session_id, e.agent, reasons)
        if tr.after is None:
            removed = list(lines or [])
            block = len(self.result.blocks)
            self.result.blocks.append(
                Block(e.edit_id, e.path, 0, tuple(ln.text for ln in removed), ())
            )
            for line in removed:
                self._remove(e.path, state, line, e.edit_id, block)
            result.lines_removed += len(removed)
            new: list[Line] | None = None
        else:
            before_count = len(lines or [])
            new = self._transform(e.path, state, lines or [], tr.after, origin, e.edit_id)
            inserted = sum(1 for ln in new if ln.origin is origin)
            result.lines_inserted += inserted
            result.lines_removed += before_count - (len(new) - inserted)
        state.working, state.dirty = new, True
        state.states.append((_digest(_texts(new)), e.session_id))
        state.applied.append(e.edit_id)
        result.evidence = evidence
        result.reasons.extend(r for r in reasons if r not in result.reasons)
        if result.lines_inserted == 0 and result.lines_removed == 0:
            result.outcome = Outcome.NO_EFFECT
        else:
            result.outcome = Outcome.UNCOMMITTED

    def _evidence(
        self, e: AttrEdit, tr: Transition, state: _File, current: list[Line] | None
    ) -> tuple[EvidenceClass, tuple[str, ...]]:
        reasons = list(tr.reasons)
        evidence = EvidenceClass.VERIFIED
        if not tr.unique:
            evidence = EvidenceClass.SUPPORTED
        if "add_over_existing_content" in tr.reasons:
            # A whole-file write with no logged before-state: lines already on disk may
            # have come from an unlogged change, so the edit cannot be verified.
            evidence = EvidenceClass.SUPPORTED
        if getattr(e.edit, "user_modified", False):
            evidence = EvidenceClass.SUPPORTED
            reasons.append("user_modified")
        if tr.before is not None and _texts(current) != tr.before:
            # Was the log's before-state an earlier working state, since replaced by
            # another session's edit? Then the two edits ran concurrently.
            target = _digest(tr.before)
            for i in range(len(state.states) - 1, -1, -1):
                digest, session = state.states[i]
                if digest == target:
                    later = {s for _, s in state.states[i + 1 :]}
                    if later - {e.session_id}:
                        evidence = EvidenceClass.AMBIGUOUS
                        reasons.append("based_on_state_replaced_by_another_session")
                    break
        return evidence, tuple(reasons)

    def _transform(
        self,
        path: str,
        state: _File,
        old: list[Line],
        new_texts: list[str],
        origin: Origin,
        remover: str | None,
    ) -> list[Line]:
        out: list[Line] = []
        matcher = SequenceMatcher(None, [ln.text for ln in old], new_texts, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                out.extend(old[i1:i2])
                continue
            block = None
            if remover is not None:
                block = len(self.result.blocks)
                self.result.blocks.append(
                    Block(
                        remover,
                        path,
                        len(out),
                        tuple(ln.text for ln in old[i1:i2]),
                        tuple(new_texts[j1:j2]),
                    )
                )
            for line in old[i1:i2]:
                self._remove(path, state, line, remover, block)
            out.extend(Line(t, origin, block=block) for t in new_texts[j1:j2])
        return out

    def _remove(
        self, path: str, state: _File, line: Line, remover: str | None, block: int | None = None
    ) -> None:
        if line.commit is not None:
            state.pending.append((line, remover or "", block))
        else:
            if remover:
                self._dropped_by[remover] = self._dropped_by.get(remover, 0) + 1
                self._touch(path, line, remover, block, None)
            self._span(path, [line], 0, state="dropped_before_commit", removed_by=remover)

    def _touch(
        self, path: str, line: Line, remover: str, block: int | None, commit: str | None
    ) -> None:
        if line.origin.kind is OriginKind.AGENT and line.origin.edit_id != remover:
            self.result.touches.append(
                Touch(path, line.origin, line.commit, line.block, remover, block, commit)
            )

    # -- commits ----------------------------------------------------------------------------

    def _commit_origin(self, commit: CommitMeta) -> Origin:
        origin = self._commit_origins.get(commit.oid)
        if origin is None:
            origin = Origin(
                OriginKind.UNKNOWN, EvidenceClass.UNKNOWN, reasons=("not_explained_by_agent_logs",)
            )
            self._commit_origins[commit.oid] = origin
        return origin

    def _commit(self, commit: CommitMeta, deltas: list[FileDelta], tracked: set[str]) -> None:
        for d in deltas:
            if d.status == "R" and d.old_path in self.files:
                self.files[d.path] = self.files.pop(d.old_path)
        for d in deltas:
            if d.path not in tracked or d.path not in self.files:
                continue
            if not d.is_regular_file:
                self._make_unavailable(d.path, commit, "not_a_regular_file")
                continue
            new_lines = None if d.new_blob is None else self.load(d.new_blob)
            if d.new_blob is not None and new_lines is None:
                self._make_unavailable(d.path, commit, "binary_or_too_large")
                continue
            self._reconcile(d.path, self.files[d.path], new_lines, commit)

    def _make_unavailable(self, path: str, commit: CommitMeta, reason: str) -> None:
        """Stop line-tracking a file. Its committed lines keep their introduction record but
        are no longer reported present, and later commits count toward unknown lines."""
        state = self.files.pop(path)
        self.result.unavailable_paths[path] = reason
        self.result.unavailable_since[path] = commit.oid
        for start, run in _runs(state.committed or []):
            self._span(path, run, start, state="unavailable")

    def _reconcile(
        self, path: str, state: _File, new_texts: list[str] | None, commit: CommitMeta
    ) -> None:
        base = (state.working if state.dirty else state.committed) or []
        origin = self._commit_origin(commit)
        # Committed lines an edit removed in the working state may still be in the commit
        # (the removal was undone before committing). Reuse those instances instead of
        # minting new ones, and do not finalize their removal.
        spare: dict[str, list[tuple[Line, str]]] = {}
        for line, remover, _block in state.pending:
            spare.setdefault(line.text, []).append((line, remover))
        restored: set[int] = set()
        restored_by: set[str] = set()

        def fresh(text: str) -> Line:
            queue = spare.get(text)
            if queue:
                line, remover = queue.pop(0)
                restored.add(id(line))
                if remover:
                    restored_by.add(remover)
                return line
            return Line(text, origin)

        out: list[Line] = []
        if new_texts is not None:
            matcher = SequenceMatcher(None, [ln.text for ln in base], new_texts, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    out.extend(base[i1:i2])
                    continue
                for k, line in enumerate(base[i1:i2]):
                    self._finalize_removal(path, line, commit, None, i1 + k)
                out.extend(fresh(t) for t in new_texts[j1:j2])
        else:
            for k, line in enumerate(base):
                self._finalize_removal(path, line, commit, None, k)

        for line, remover, block in state.pending:
            if id(line) not in restored:
                self._finalize_removal(path, line, commit, remover or None, None, block)
        for line in out:
            if line.commit is None:
                line.commit = commit.oid
                self.result.inserted_lines_in_range += 1
                self._credit_commit(line, commit)
        for edit_id in state.applied:
            r = self.result.edits[edit_id]
            if r.outcome is not Outcome.UNCOMMITTED or r.lines_inserted:
                continue
            if edit_id in self._landed or self._dropped_by.get(edit_id):
                # A removal-only edit has landed once its removals reach a commit.
                r.outcome = Outcome.COMMITTED
                r.anchor_commit = r.anchor_commit or commit.oid
            elif edit_id in restored_by:
                r.outcome = Outcome.OVERWRITTEN_BEFORE_COMMIT
                r.reasons.append("removal_undone_before_commit")
        state.committed = out if new_texts is not None else None
        state.working, state.dirty, state.pending, state.applied = None, False, [], []

    def _credit_commit(self, line: Line, commit: CommitMeta) -> None:
        edit_id = line.origin.edit_id
        if edit_id is None:
            return
        r = self.result.edits[edit_id]
        r.lines_committed += 1
        r.outcome = Outcome.COMMITTED
        r.anchor_commit = r.anchor_commit or commit.oid

    def _finalize_removal(
        self,
        path: str,
        line: Line,
        commit: CommitMeta,
        remover: str | None,
        pos: int | None,
        block: int | None = None,
    ) -> None:
        if line.commit is None:
            self._span(path, [line], pos or 0, state="dropped_before_commit", removed_by=remover)
            return
        if remover:
            self._touch(path, line, remover, block, commit.oid)
            self._landed.add(remover)
            r = self.result.edits.get(remover)
            if r is not None:
                r.anchor_commit = r.anchor_commit or commit.oid
                if r.lines_inserted == 0:
                    # A pure deletion is committed once its removal lands. An edit that
                    # inserted lines counts as committed only if some of them land.
                    r.outcome = Outcome.COMMITTED
        self._span(
            path, [line], pos or 0, state="removed", removed_commit=commit.oid, removed_by=remover
        )

    # -- finish -----------------------------------------------------------------------------

    def _finish(self) -> None:
        for path, state in sorted(self.files.items()):
            if state.committed is not None:
                for start, run in _runs(state.committed):
                    self._span(path, run, start, state="present")
            if state.dirty:
                for start, run in _runs(state.working or []):
                    fresh = [ln for ln in run if ln.commit is None]
                    if fresh:
                        self._span(path, fresh, start, state="provisional")
        provisional = {s.origin.edit_id for s in self.result.spans if s.state == "provisional"}
        for r in self.result.edits.values():
            if (
                r.outcome is Outcome.UNCOMMITTED
                and r.lines_inserted
                and not r.lines_committed
                and r.edit_id not in provisional
            ):
                r.outcome = Outcome.OVERWRITTEN_BEFORE_COMMIT

    def _span(
        self,
        path: str,
        lines: list[Line],
        start: int,
        *,
        state: str,
        removed_commit: str | None = None,
        removed_by: str | None = None,
    ) -> None:
        if not lines:
            return
        first = lines[0]
        span = Span(
            path, first.origin, first.commit, start, len(lines), removed_commit, removed_by, state
        )
        spans = self.result.spans
        prev = spans[-1] if spans else None
        if (
            prev is not None
            and prev.path == path
            and prev.state == state
            and prev.origin is span.origin
            and prev.introduced_commit == span.introduced_commit
            and prev.removed_commit == span.removed_commit
            and prev.removed_by == span.removed_by
            and state in ("removed", "dropped_before_commit")
        ):
            spans[-1] = Span(
                path,
                prev.origin,
                prev.introduced_commit,
                prev.start,
                prev.count + span.count,
                removed_commit,
                removed_by,
                state,
            )
            return
        spans.append(span)


def _runs(lines: list[Line]) -> Iterator[tuple[int, list[Line]]]:
    """Group consecutive lines with the same origin and introduction commit."""
    start = 0
    for i in range(1, len(lines) + 1):
        if i == len(lines) or (
            lines[i].origin is not lines[start].origin or lines[i].commit != lines[start].commit
        ):
            yield start, lines[start:i]
            start = i
