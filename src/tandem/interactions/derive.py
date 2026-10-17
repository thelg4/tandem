"""Derive cross-agent interactions from attribution touches (spec §7).

A touch says that a later agent edit removed a line an earlier edit inserted, and that
the removal stuck. Touches are grouped by (earlier edit, later edit, later edit's
block), and each group gets exactly one primary label (INT-02):

- ``exact_revert``: the later block removed exactly what the earlier block inserted
  and put back exactly what it replaced.
- ``partial_revert``: the later block put back only text the earlier block replaced,
  but not an exact restoration.
- ``removal``: the later block deleted the lines without a replacement.
- ``rewrite``: the later block replaced the lines with something else.
- ``overlap_candidate``: either edit's evidence is ambiguous, so order or lineage is
  uncertain. Excluded from verified totals.

Labels are neutral (INT-01). A rewrite is not a fix or a regression; commit messages
and prompts cannot establish correctness.

Edits from one session touching its own lines are not interactions. The same agent in
another session is kept, tagged ``same_agent_cross_session``, and counted separately.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from tandem.attribution.engine import AttrEdit, Block, Result, Touch
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import InteractionLabel, InteractionTag, WindowMode

_RANK = {EvidenceClass.VERIFIED: 0, EvidenceClass.SUPPORTED: 1, EvidenceClass.AMBIGUOUS: 2}


@dataclass(frozen=True, slots=True)
class Window:
    hours: float = 24.0
    commits: int = 5
    mode: WindowMode = WindowMode.EITHER

    def contains(self, elapsed: float | None, distance: int | None) -> bool:
        """Unknown time or distance never satisfies a predicate (spec §7)."""
        in_hours = elapsed is not None and elapsed <= self.hours * 3600
        in_commits = distance is not None and distance <= self.commits
        if self.mode is WindowMode.HOURS:
            return in_hours
        if self.mode is WindowMode.COMMITS:
            return in_commits
        if self.mode is WindowMode.BOTH:
            return in_hours and in_commits
        return in_hours or in_commits


@dataclass(frozen=True, slots=True)
class Interaction:
    interaction_id: str
    path: str
    original_edit: str
    later_edit: str
    original_agent: str
    later_agent: str
    label: InteractionLabel
    tags: tuple[str, ...]
    evidence: EvidenceClass
    lines_affected: int
    """Unique original line instances the later edit removed."""
    start_line: int
    """1-based line in the working state where the later edit's change starts."""
    block_removed: int
    block_inserted: int
    original_replaced: int
    original_inserted: int
    elapsed_seconds: float | None
    later_time: float | None
    commit_distance: int | None
    original_commit: str | None
    removal_commit: str | None
    recent: bool

    @property
    def cross_agent(self) -> bool:
        return self.original_agent != self.later_agent


def interaction_id(original: str, later: str, path: str, position: int) -> str:
    key = f"{original}\0{later}\0{path}\0{position}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(key).hexdigest()[:12]


def _label(later: Block, earlier: Block | None, removed_all_of_earlier: bool) -> InteractionLabel:
    if (
        earlier is not None
        and removed_all_of_earlier
        and later.old == earlier.new
        and later.new == earlier.old
    ):
        return InteractionLabel.EXACT_REVERT
    if earlier is not None and later.new and all(t in earlier.old for t in later.new):
        return InteractionLabel.PARTIAL_REVERT
    return InteractionLabel.REMOVAL if not later.new else InteractionLabel.REWRITE


def derive(result: Result, edits: dict[str, AttrEdit], window: Window) -> list[Interaction]:
    groups: dict[tuple[str, str, int | None], list[Touch]] = defaultdict(list)
    for touch in result.touches:
        if touch.origin.edit_id is None:
            continue
        groups[(touch.origin.edit_id, touch.remover, touch.removal_block)].append(touch)

    out: list[Interaction] = []
    for (orig_id, later_id, block_id), touches in groups.items():
        a, e = edits.get(orig_id), edits.get(later_id)
        if a is None or e is None or (a.session_id == e.session_id):
            continue  # one session revising its own work is not an interaction
        later_block = result.blocks[block_id] if block_id is not None else None
        if later_block is None:
            continue
        born = {t.born_block for t in touches if t.born_block is not None}
        earlier = (
            result.blocks[next(iter(born))]
            if len(born) == 1 and all(t.born_block is not None for t in touches)
            else None
        )
        removed_all = earlier is not None and len(touches) == len(earlier.new)
        label = _label(later_block, earlier, removed_all)

        later_evidence = result.edits[later_id].evidence or EvidenceClass.UNKNOWN
        origin_evidence = touches[0].origin.evidence
        worst = max((origin_evidence, later_evidence), key=lambda c: _RANK.get(c, 3))
        tags: list[str] = []
        if worst is EvidenceClass.AMBIGUOUS or _RANK.get(worst, 3) > 2:
            tags.append(label.value)  # keep the underlying shape visible
            label = InteractionLabel.OVERLAP_CANDIDATE
            worst = EvidenceClass.AMBIGUOUS
        if a.agent == e.agent:
            tags.append(InteractionTag.SAME_AGENT_CROSS_SESSION.value)
        before_commit = all(t.introduced_commit is None for t in touches)
        if before_commit:
            tags.append("before_commit")

        index = result.commit_index
        introduced = [index[t.introduced_commit] for t in touches if t.introduced_commit in index]
        removed = [index[t.removed_commit] for t in touches if t.removed_commit in index]
        distance = max(removed) - min(introduced) if introduced and removed else None
        elapsed = e.time - a.time if e.time is not None and a.time is not None else None
        recent = window.contains(elapsed, distance)
        if recent:
            tags.append(InteractionTag.RECENT.value)

        out.append(
            Interaction(
                interaction_id=interaction_id(
                    orig_id, later_id, later_block.path, later_block.position
                ),
                path=later_block.path,
                original_edit=orig_id,
                later_edit=later_id,
                original_agent=a.agent,
                later_agent=e.agent,
                label=label,
                tags=tuple(tags),
                evidence=worst,
                lines_affected=len(touches),
                start_line=later_block.position + 1,
                block_removed=len(later_block.old),
                block_inserted=len(later_block.new),
                original_replaced=len(earlier.old) if earlier else 0,
                original_inserted=len(earlier.new) if earlier else 0,
                elapsed_seconds=elapsed,
                later_time=e.time,
                commit_distance=distance,
                original_commit=touches[0].introduced_commit,
                removal_commit=next((t.removed_commit for t in touches if t.removed_commit), None),
                recent=recent,
            )
        )
    return sorted(out, key=review_order)


_LABEL_ORDER = {
    InteractionLabel.EXACT_REVERT: 0,
    InteractionLabel.PARTIAL_REVERT: 1,
    InteractionLabel.REWRITE: 2,
    InteractionLabel.REMOVAL: 3,
    InteractionLabel.OVERLAP_CANDIDATE: 4,
}


def review_order(i: Interaction) -> tuple[int, int, int, float, str]:
    """Inspection order (UX-03): cross-agent first, exact restorations, affected lines,
    then most recent. This is a review order, not a severity ranking."""
    return (
        0 if i.cross_agent else 1,
        _LABEL_ORDER[i.label],
        -i.lines_affected,
        -(i.later_time if i.later_time is not None else float("-inf")),
        i.interaction_id,
    )
