"""Durability metrics, starting with the spec's synthetic worked example (§9)."""

from __future__ import annotations

from fractions import Fraction

from tandem.metrics.compute import ChangeGroup, LineCohort, Received, SessionUsage, scorecard
from tandem.model.attribution import EvidenceClass as E
from tandem.model.metrics import MetricName as M
from tandem.model.metrics import MetricStatus as S

V = E.VERIFIED


def worked_example() -> dict[str, object]:
    """At C0 a Claude session contributes 100 verified lines through 10 change groups.
    By C5, 70 remain intact. Codex affected 20 instances in 3 interactions, including one
    exact revert of a change group. An unknown actor changed the other 10. Codex adds new
    lines at C4. The report is evaluated at C5."""
    lines = []
    groups = []
    for g in range(10):
        edit = f"cc{g}"
        if g == 7:
            lines.append(LineCohort("claude_code", "S", edit, V, 10, 0, 3, "codex"))
            groups.append(ChangeGroup("claude_code", "S", edit, V, 0, exact_revert_by_other=3))
            continue
        if g == 8:
            lines.append(LineCohort("claude_code", "S", edit, V, 10, 0, 2, "codex"))
        elif g == 9:
            lines.append(LineCohort("claude_code", "S", edit, V, 10, 0, 4, None))
        else:
            lines.append(LineCohort("claude_code", "S", edit, V, 10, 0))
        groups.append(ChangeGroup("claude_code", "S", edit, V, 0))
    lines.append(LineCohort("codex", "X", "cx", V, 12, 4))
    groups.append(ChangeGroup("codex", "X", "cx", V, 4))
    return {
        "horizon": 5,
        "last_index": 5,
        "lines": lines,
        "groups": groups,
        "usage": [
            SessionUsage("claude_code", "S", 14_000, True),
            SessionUsage("codex", "X", 9_000, True),
        ],
        "received": [
            Received("claude_code", 10),
            Received("claude_code", 5),
            Received("claude_code", 5),
        ],
    }


def test_worked_example_claude() -> None:
    card = scorecard("claude_code", **worked_example())  # type: ignore[arg-type]
    m = card.metrics
    assert m[M.SURVIVAL].ratio == Fraction(70, 100)
    assert m[M.CHURN_RECEIVED].ratio == Fraction(20, 100)
    assert m[M.CHURN_INTERACTIONS].numerator == 3
    assert m[M.CHURN_LINES].numerator == 20
    assert m[M.EXACT_REVERT_RATE].ratio == Fraction(1, 10)
    assert m[M.TOKENS_PER_SURVIVING_LINE].ratio == 200
    assert m[M.TOKENS_PER_SURVIVING_LINE].display() == "14,000 / 70 = 200 per line"
    assert card.mature_lines == 100 and card.pending_lines == 0


def test_worked_example_codex_contribution_is_pending() -> None:
    card = scorecard("codex", **worked_example())  # type: ignore[arg-type]
    for name in (M.SURVIVAL, M.CHURN_RECEIVED, M.EXACT_REVERT_RATE, M.TOKENS_PER_SURVIVING_LINE):
        assert card.metrics[name].status is S.PENDING, name
        assert card.metrics[name].display() == "pending"
    assert card.pending_lines == 12


def test_no_artificial_hundred_percent_for_young_or_deletion_only_work() -> None:
    young = scorecard(
        "a",
        horizon=5,
        last_index=2,
        lines=[LineCohort("a", "s", "e", V, 5, 1)],
        groups=[],
        usage=[],
        received=[],
    )
    assert young.metrics[M.SURVIVAL].status is S.PENDING
    nothing = scorecard(
        "a",
        horizon=5,
        last_index=9,
        lines=[],
        groups=[ChangeGroup("a", "s", "d", V, 0)],
        usage=[],
        received=[],
    )
    assert nothing.metrics[M.SURVIVAL].status is S.NOT_APPLICABLE
    # A deletion-only edit still counts as a change group for revert measurement.
    assert nothing.metrics[M.EXACT_REVERT_RATE].ratio == 0


def test_tokens_with_no_surviving_lines_is_not_zero_or_infinite() -> None:
    card = scorecard(
        "a",
        horizon=1,
        last_index=5,
        lines=[LineCohort("a", "s", "e", V, 4, 0, 1, "b")],
        groups=[],
        usage=[SessionUsage("a", "s", 5_000, True)],
        received=[],
    )
    tps = card.metrics[M.TOKENS_PER_SURVIVING_LINE]
    assert tps.status is S.NOT_APPLICABLE and tps.numerator == 5_000 and tps.ratio is None
    assert "no surviving lines" in tps.notes[0]


def test_incomplete_usage_and_untracked_files_make_metrics_partial() -> None:
    card = scorecard(
        "a",
        horizon=1,
        last_index=5,
        lines=[
            LineCohort("a", "s", "e", V, 4, 0),
            LineCohort("a", "s", "f", V, 2, 0, unavailable_from=1),
        ],
        groups=[],
        usage=[SessionUsage("a", "s", 100, True), SessionUsage("a", "t", None, False)],
        received=[],
    )
    survival = card.metrics[M.SURVIVAL]
    assert survival.status is S.PARTIAL and survival.exclusions["became_untracked"] == 2
    tps = card.metrics[M.TOKENS_PER_SURVIVING_LINE]
    assert tps.status is S.PARTIAL and tps.exclusions == {"usage_incomplete": 1}


def test_supported_lines_are_excluded_and_counted() -> None:
    card = scorecard(
        "a",
        horizon=1,
        last_index=5,
        lines=[LineCohort("a", "s", "e", V, 3, 0), LineCohort("a", "s", "f", E.SUPPORTED, 7, 0)],
        groups=[],
        usage=[],
        received=[],
    )
    assert card.metrics[M.SURVIVAL].denominator == 3
    assert card.metrics[M.SURVIVAL].exclusions["not_verified"] == 7


def test_comparison_readiness_threshold() -> None:
    lines = [LineCohort("a", f"s{i}", f"e{i}", V, 10, 0) for i in range(3)]
    ready = scorecard("a", horizon=1, last_index=5, lines=lines, groups=[], usage=[], received=[])
    assert ready.comparable
    small = scorecard(
        "a", horizon=1, last_index=5, lines=lines[:2], groups=[], usage=[], received=[]
    )
    assert not small.comparable


def test_session_scope() -> None:
    data = worked_example()
    card = scorecard("claude_code", session="S", **data)  # type: ignore[arg-type]
    assert card.scope == "S" and card.metrics[M.SURVIVAL].ratio == Fraction(70, 100)
