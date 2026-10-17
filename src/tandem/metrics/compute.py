"""Maturity-aware durability metrics (spec §8, §9).

Pure functions over plain records, so the definitions can be checked against the
spec's worked example without Git. Every value keeps its numerator, denominator,
horizon, and exclusions; there is no composite score.

Cohorts and maturity: a line instance's cohort commit ``c0`` is the first-parent
index of the commit where it first landed. Its endpoint is ``c0 + N``. It is mature
only if the endpoint is inside the analyzed history; younger lines are pending, not
successes. Only verified lines and edits count by default.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from tandem.model.attribution import EvidenceClass
from tandem.model.metrics import (
    MIN_MATURE_LINES_FOR_COMPARISON,
    MIN_SESSIONS_FOR_COMPARISON,
    MetricName,
    MetricStatus,
    MetricValue,
)


@dataclass(frozen=True, slots=True)
class LineCohort:
    """``count`` line instances from one edit sharing one fate."""

    agent: str
    session: str
    edit_id: str
    evidence: EvidenceClass
    count: int
    introduced: int
    """Commit index where the lines first landed (c0)."""
    removed: int | None = None
    """Commit index where they were removed; None if still present."""
    removed_by_agent: str | None = None
    """Agent whose edit removed them; None for an unattributed change."""
    unavailable_from: int | None = None
    """Commit index where the file stopped being line-tracked."""


@dataclass(frozen=True, slots=True)
class ChangeGroup:
    """One successful edit to one file, anchored to the commit where it landed."""

    agent: str
    session: str
    edit_id: str
    evidence: EvidenceClass
    anchor: int
    exact_revert_by_other: int | None = None
    """Commit index where another agent fully restored the prior state."""
    partial_revert_by_other: int | None = None


@dataclass(frozen=True, slots=True)
class SessionUsage:
    agent: str
    session: str
    tokens: int | None
    """Processed tokens (see usage.py); None when unknown."""
    complete: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Received:
    """A verified cross-agent interaction received by ``agent``."""

    agent: str
    lines: int


@dataclass(slots=True)
class Scorecard:
    scope: str
    metrics: dict[MetricName, MetricValue] = field(default_factory=dict)
    mature_lines: int = 0
    pending_lines: int = 0
    sessions: int = 0
    comparable: bool = False
    """Enough mature lines and sessions for comparative highlighting (presentation only)."""


def _mature(c0: int, horizon: int, last: int) -> bool:
    return c0 + horizon <= last


def _rate(
    name: MetricName,
    numerator: int,
    mature: int,
    pending: int,
    horizon: int,
    exclusions: dict[str, int],
) -> MetricValue:
    exclusions = {k: v for k, v in exclusions.items() if v}
    if mature == 0:
        status = MetricStatus.PENDING if pending else MetricStatus.NOT_APPLICABLE
        return MetricValue(name, status, None, None, horizon, exclusions)
    partial = any(k in exclusions for k in ("became_untracked", "usage_incomplete"))
    return MetricValue.from_counts(
        name, numerator, mature, horizon, partial=partial, exclusions=exclusions
    )


def scorecard(
    agent: str,
    *,
    horizon: int,
    last_index: int,
    lines: Iterable[LineCohort],
    groups: Iterable[ChangeGroup],
    usage: Iterable[SessionUsage],
    received: Iterable[Received],
    session: str | None = None,
) -> Scorecard:
    """Durability scorecard for one agent (or one session of it, when ``session`` is set)."""

    def mine(x: LineCohort | ChangeGroup | SessionUsage) -> bool:
        return x.agent == agent and (session is None or x.session == session)

    own = [c for c in lines if mine(c)]
    verified = [c for c in own if c.evidence is EvidenceClass.VERIFIED]
    lower = sum(c.count for c in own if c.evidence is not EvidenceClass.VERIFIED)
    card = Scorecard(scope=session or agent)

    mature, pending, survived, churned, untracked = 0, 0, 0, 0, 0
    surviving_by_session: dict[str, int] = {}
    latest_c0: dict[str, int] = {}
    for c in verified:
        latest_c0[c.session] = max(latest_c0.get(c.session, -1), c.introduced)
        endpoint = c.introduced + horizon
        if not _mature(c.introduced, horizon, last_index):
            pending += c.count
            continue
        if c.unavailable_from is not None and c.unavailable_from <= endpoint:
            untracked += c.count
            continue
        mature += c.count
        if c.removed is None or c.removed > endpoint:
            survived += c.count
            surviving_by_session[c.session] = surviving_by_session.get(c.session, 0) + c.count
        elif c.removed_by_agent is not None and c.removed_by_agent != agent:
            churned += c.count

    exclusions = {"pending": pending, "became_untracked": untracked, "not_verified": lower}
    card.mature_lines, card.pending_lines = mature, pending
    card.metrics[MetricName.SURVIVAL] = _rate(
        MetricName.SURVIVAL, survived, mature, pending, horizon, exclusions
    )
    card.metrics[MetricName.CHURN_RECEIVED] = _rate(
        MetricName.CHURN_RECEIVED, churned, mature, pending, horizon, exclusions
    )

    got = [r for r in received if r.agent == agent]
    card.metrics[MetricName.CHURN_INTERACTIONS] = MetricValue.count(
        MetricName.CHURN_INTERACTIONS, len(got), horizon
    )
    card.metrics[MetricName.CHURN_LINES] = MetricValue.count(
        MetricName.CHURN_LINES, sum(r.lines for r in got), horizon
    )

    own_groups = [g for g in groups if mine(g) and g.evidence is EvidenceClass.VERIFIED]
    mature_groups = [g for g in own_groups if _mature(g.anchor, horizon, last_index)]
    reverted = sum(
        1
        for g in mature_groups
        if g.exact_revert_by_other is not None and g.exact_revert_by_other <= g.anchor + horizon
    )
    partial = sum(
        1
        for g in mature_groups
        if g.partial_revert_by_other is not None
        and g.partial_revert_by_other <= g.anchor + horizon
        and not (
            g.exact_revert_by_other is not None and g.exact_revert_by_other <= g.anchor + horizon
        )
    )
    card.metrics[MetricName.EXACT_REVERT_RATE] = _rate(
        MetricName.EXACT_REVERT_RATE,
        reverted,
        len(mature_groups),
        len(own_groups) - len(mature_groups),
        horizon,
        {"pending_groups": len(own_groups) - len(mature_groups)},
    )
    card.metrics[MetricName.PARTIAL_REVERTS] = MetricValue.count(
        MetricName.PARTIAL_REVERTS, partial, horizon
    )

    # Tokens per surviving line: only sessions with complete usage whose verified lines
    # have all matured, so tokens and the lines they produced are measured together.
    tokens, lines_out, excluded_usage, excluded_young = 0, 0, 0, 0
    sessions_seen = set()
    for u in usage:
        if not mine(u):
            continue
        sessions_seen.add(u.session)
        if u.session in latest_c0 and not _mature(latest_c0[u.session], horizon, last_index):
            excluded_young += 1
            continue
        if not u.complete or u.tokens is None:
            excluded_usage += 1
            continue
        tokens += u.tokens
        lines_out += surviving_by_session.get(u.session, 0)
    eligible = len(sessions_seen) - excluded_usage - excluded_young
    token_ex = {"usage_incomplete": excluded_usage, "sessions_not_matured": excluded_young}
    token_ex = {k: v for k, v in token_ex.items() if v}
    if eligible == 0:
        status = MetricStatus.PENDING if excluded_young else MetricStatus.UNAVAILABLE
        tps = MetricValue(
            MetricName.TOKENS_PER_SURVIVING_LINE, status, None, None, horizon, token_ex
        )
    else:
        tps = MetricValue.from_counts(
            MetricName.TOKENS_PER_SURVIVING_LINE,
            tokens,
            lines_out,
            horizon,
            partial=bool(excluded_usage),
            exclusions=token_ex,
        )
        if lines_out == 0:
            tps = MetricValue(
                tps.name,
                MetricStatus.NOT_APPLICABLE,
                tokens,
                0,
                horizon,
                token_ex,
                notes=("no surviving lines; tokens shown without a ratio",),
            )
    card.metrics[MetricName.TOKENS_PER_SURVIVING_LINE] = tps

    card.sessions = len({c.session for c in verified})
    card.comparable = (
        mature >= MIN_MATURE_LINES_FOR_COMPARISON and card.sessions >= MIN_SESSIONS_FOR_COMPARISON
    )
    return card
