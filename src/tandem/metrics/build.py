"""Turn one attribution run into durability scorecards and store them (spec §8)."""

from __future__ import annotations

import json
import sqlite3
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
                unavailable_from=index.get(unavailable) if unavailable else None,
            )
        )
    return out


def change_groups(
    result: Result, edits: dict[str, AttrEdit], found: Iterable[Interaction]
) -> list[ChangeGroup]:
    index = result.commit_index
    exact: dict[str, int] = {}
    partial: dict[str, int] = {}
    for i in found:
        if not i.cross_agent or i.removal_commit not in index:
            continue
        at = index[i.removal_commit]
        if i.label is InteractionLabel.EXACT_REVERT:
            exact[i.original_edit] = min(at, exact.get(i.original_edit, at))
        elif i.label is InteractionLabel.PARTIAL_REVERT:
            partial[i.original_edit] = min(at, partial.get(i.original_edit, at))
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
        out.append(
            ChangeGroup(
                agent=e.agent,
                session=e.session_id,
                edit_id=r.edit_id,
                evidence=r.evidence,
                anchor=index[r.anchor_commit],
                exact_revert_by_other=exact.get(r.edit_id),
                partial_revert_by_other=partial.get(r.edit_id),
            )
        )
    return out


def received(found: Iterable[Interaction]) -> list[Received]:
    return [
        Received(i.original_agent, i.lines_affected)
        for i in found
        if i.cross_agent
        and i.label is not InteractionLabel.OVERLAP_CANDIDATE
        and i.evidence is EvidenceClass.VERIFIED
    ]


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
    got = received(found)
    last = len(result.commit_index) - 1
    agents = sorted({c.agent for c in lines} | {u.agent for u in usage})
    cards: list[tuple[str, str, Scorecard]] = []
    for agent in agents:
        common = {
            "horizon": horizon,
            "last_index": last,
            "lines": lines,
            "groups": groups,
            "usage": usage,
            "received": got,
        }
        cards.append(("agent", agent, scorecard(agent, **common)))  # type: ignore[arg-type]
        for session in sorted({c.session for c in lines if c.agent == agent}):
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
    notes = {u.session: u.reason for u in usage if u.reason}
    for kind, agent, card in cards:
        scoped = {s: r for s, r in notes.items() if kind == "agent" or s == card.scope}
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
