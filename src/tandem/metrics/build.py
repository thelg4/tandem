"""Turn one attribution run into durability scorecards and store them (spec §8)."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable

from tandem.attribution.engine import AttrEdit, Outcome, Result
from tandem.interactions import Interaction
from tandem.metrics.compute import (
    ChangeGroup,
    LineCohort,
    Received,
    Scorecard,
    SessionUsage,
    scorecard,
)
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import InteractionLabel
from tandem.versions import METRIC_DEFINITIONS_VERSION


def cohorts(result: Result, edits: dict[str, AttrEdit]) -> list[LineCohort]:
    index = result.commit_index
    out = []
    for span in result.spans:
        o = span.origin
        if (
            o.edit_id is None
            or o.agent is None
            or o.session_id is None
            or span.state not in ("present", "removed", "unavailable")
            or span.introduced_commit not in index
        ):
            continue
        remover = edits.get(span.removed_by) if span.removed_by else None
        removed_result = result.edits.get(span.removed_by) if span.removed_by else None
        unavailable = (
            result.unavailable_since.get(span.path) if span.state == "unavailable" else None
        )
        out.append(
            LineCohort(
                agent=o.agent,
                session=o.session_id,
                edit_id=o.edit_id,
                evidence=o.evidence,
                count=span.count,
                introduced=index[span.introduced_commit],
                removed=index.get(span.removed_commit) if span.removed_commit else None,
                removed_by_agent=remover.agent if remover else None,
                removed_by_evidence=removed_result.evidence if removed_result else None,
                unavailable_from=index.get(unavailable) if unavailable else None,
            )
        )
    return out


def change_groups(
    result: Result, edits: dict[str, AttrEdit], found: Iterable[Interaction]
) -> list[ChangeGroup]:
    """Change groups with revert outcomes.

    A group is exactly reverted only when another agent's verified exact reverts cover
    every block of the edit that inserted lines, and the edit had no deletion-only
    block (restoring deleted text is not detected). Any other verified exact or partial
    revert by another agent marks the group as partially reverted. Pure deletions are
    kept for counting but flagged as not observable for revert detection.
    """
    index = result.commit_index
    inserting: dict[str, set[int]] = defaultdict(set)
    deleting: set[str] = set()
    for k, b in enumerate(result.blocks):
        if b.new:
            inserting[b.edit_id].add(k)
        elif b.old:
            deleting.add(b.edit_id)
    exact_blocks: dict[str, dict[int, int]] = defaultdict(dict)
    touched: dict[str, int] = {}
    for i in found:
        if (
            not i.cross_agent
            or i.evidence is not EvidenceClass.VERIFIED
            or i.removal_commit not in index
        ):
            continue
        at = index[i.removal_commit]
        if i.label is InteractionLabel.EXACT_REVERT and i.original_block is not None:
            owner = result.blocks[i.original_block].edit_id
            prev = exact_blocks[owner].get(i.original_block, at)
            exact_blocks[owner][i.original_block] = min(prev, at)
            touched[owner] = min(at, touched.get(owner, at))
        elif i.label in (InteractionLabel.EXACT_REVERT, InteractionLabel.PARTIAL_REVERT):
            touched[i.original_edit] = min(at, touched.get(i.original_edit, at))
    out = []
    for r in result.edits.values():
        e = edits.get(r.edit_id)
        if (
            e is None
            or r.outcome is not Outcome.COMMITTED
            or r.evidence is None
            or r.anchor_commit not in index
        ):
            continue
        blocks = inserting.get(r.edit_id, set())
        hits = exact_blocks.get(r.edit_id, {})
        full = bool(blocks) and r.edit_id not in deleting and blocks <= hits.keys()
        out.append(
            ChangeGroup(
                agent=e.agent,
                session=e.session_id,
                edit_id=r.edit_id,
                evidence=r.evidence,
                anchor=index[r.anchor_commit],
                exact_revert_by_other=max(hits[b] for b in blocks) if full else None,
                partial_revert_by_other=None if full else touched.get(r.edit_id),
                revert_observable=bool(blocks),
            )
        )
    return out


def received(found: Iterable[Interaction], edits: dict[str, AttrEdit]) -> list[Received]:
    return [
        Received(
            i.original_agent,
            i.lines_affected,
            edits[i.original_edit].session_id if i.original_edit in edits else None,
        )
        for i in found
        if i.cross_agent
        and i.label is not InteractionLabel.OVERLAP_CANDIDATE
        and i.evidence is EvidenceClass.VERIFIED
    ]


def token_ineligible(
    result: Result,
    edits: list[AttrEdit],
    lines: list[LineCohort],
    usage: Iterable[SessionUsage] = (),
) -> dict[str, str]:
    """Sessions whose tokens cannot be paired with measured lines, with the reason.

    A session's tokens count only when all its work is settled and measured: no edit
    still uncommitted, every line verified and line-tracked. A session that made no
    edits in this repository (a read-only helper, say) has no lines to pair its tokens
    with, so it is excluded too.
    """
    editing = {e.session_id for e in edits}
    out: dict[str, str] = {u.session: "session_made_no_edits" for u in usage}
    for sid in editing:
        out.pop(sid, None)
    for e in edits:
        r = result.edits.get(e.edit_id)
        if r is not None and r.outcome is Outcome.UNCOMMITTED:
            out[e.session_id] = "sessions_not_matured"
    for c in lines:
        if c.session in out:
            continue
        if c.evidence is not EvidenceClass.VERIFIED:
            out[c.session] = "lines_not_verified"
        elif c.unavailable_from is not None:
            out[c.session] = "lines_became_untracked"
    return out


def build(
    result: Result,
    edits: list[AttrEdit],
    found: list[Interaction],
    usage: list[SessionUsage],
    horizon: int,
) -> list[tuple[str, str, Scorecard]]:
    """Scorecards per agent and per session: (scope_kind, agent, card)."""
    by_id = {e.edit_id: e for e in edits}
    lines = cohorts(result, by_id)
    groups = change_groups(result, by_id, found)
    got = received(found, by_id)
    last = len(result.commit_index) - 1
    ineligible = token_ineligible(result, edits, lines, usage)
    agents = sorted({c.agent for c in lines} | {g.agent for g in groups} | {u.agent for u in usage})
    cards: list[tuple[str, str, Scorecard]] = []
    for agent in agents:
        common = {
            "horizon": horizon,
            "last_index": last,
            "lines": lines,
            "groups": groups,
            "usage": usage,
            "received": got,
            "token_ineligible": ineligible,
        }
        cards.append(("agent", agent, scorecard(agent, **common)))  # type: ignore[arg-type]
        sessions = {c.session for c in lines if c.agent == agent}
        sessions |= {g.session for g in groups if g.agent == agent}
        for session in sorted(sessions):
            card = scorecard(agent, session=session, **common)  # type: ignore[arg-type]
            cards.append(("session", agent, card))
    return cards


def store(
    conn: sqlite3.Connection,
    run_id: int,
    cards: list[tuple[str, str, Scorecard]],
    usage: list[SessionUsage],
    horizon: int,
) -> None:
    for kind, agent, card in cards:
        scoped = {
            u.session: u.reason
            for u in usage
            if u.reason and u.agent == agent and (kind == "agent" or u.session == card.scope)
        }
        conn.execute(
            "INSERT INTO scorecards (run_id, scope_kind, scope_id, agent, horizon_commits, "
            "mature_lines, pending_lines, sessions, comparable, usage_notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                kind,
                card.scope,
                agent,
                horizon,
                card.mature_lines,
                card.pending_lines,
                card.sessions,
                int(card.comparable),
                json.dumps(scoped),
            ),
        )
        for name, value in card.metrics.items():
            conn.execute(
                "INSERT INTO metrics (run_id, scope_kind, scope_id, name, status, numerator, "
                "denominator, horizon_commits, exclusions, definition_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    kind,
                    card.scope,
                    name.value,
                    value.status.value,
                    value.numerator,
                    value.denominator,
                    value.horizon_commits,
                    json.dumps({"exclusions": value.exclusions, "notes": list(value.notes)}),
                    METRIC_DEFINITIONS_VERSION,
                ),
            )
