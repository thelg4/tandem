"""Scorecards built from real attribution runs (regressions from the metrics review)."""

from __future__ import annotations

from pathlib import Path

from tandem.interactions.derive import Window, derive
from tandem.metrics.build import build, store
from tandem.metrics.compute import Scorecard, SessionUsage
from tandem.model.metrics import MetricName as M
from tandem.model.metrics import MetricStatus as S
from test_attribution import Scenario

BASE = "".join(f"l{i}\n" for i in range(20))


def cards(
    s: Scenario, usage: list[SessionUsage] | None = None, horizon: int = 1
) -> dict[str, Scorecard]:
    r = s.run()
    by = {e.edit_id: e for e in s.edits}
    found = derive(r, by, Window())
    return {card.scope: card for _k, _a, card in build(r, s.edits, found, usage or [], horizon)}


def test_received_interactions_stay_with_the_session_that_was_changed(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "a.py", "x = 1\ny = 2\n")
    s.commit()
    s.claude("b", "b.py", "p = 1\nq = 2\n")
    s.commit()
    s.codex("c", "a.py", "x = 10\ny = 2\n")
    for _ in range(3):
        s.commit()
    c = cards(s)
    assert c["claude_code:a"].metrics[M.CHURN_INTERACTIONS].numerator == 1
    assert c["claude_code:b"].metrics[M.CHURN_INTERACTIONS].numerator == 0
    assert c["claude_code:b"].metrics[M.CHURN_LINES].numerator == 0
    assert c["claude_code"].metrics[M.CHURN_INTERACTIONS].numerator == 1


def test_reverting_one_hunk_of_a_two_hunk_edit_is_partial(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", BASE)
    s.commit()
    v1 = BASE.replace("l2\n", "L2\n").replace("l15\n", "L15\n")
    s.claude("a", "m.py", v1)
    s.commit()
    s.codex("c", "m.py", v1.replace("L2\n", "l2\n"))
    s.commit()
    s.commit()
    m = cards(s)["claude_code"].metrics
    assert (m[M.EXACT_REVERT_RATE].numerator, m[M.EXACT_REVERT_RATE].denominator) == (0, 1)
    assert m[M.PARTIAL_REVERTS].numerator == 1


def test_reverting_every_hunk_is_an_exact_revert(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", BASE)
    s.commit()
    s.claude("a", "m.py", BASE.replace("l2\n", "L2\n").replace("l15\n", "L15\n"))
    s.commit()
    s.codex("c", "m.py", BASE)
    s.commit()
    s.commit()
    m = cards(s)["claude_code"].metrics
    assert (m[M.EXACT_REVERT_RATE].numerator, m[M.EXACT_REVERT_RATE].denominator) == (1, 1)
    assert m[M.PARTIAL_REVERTS].numerator == 0


def test_pure_deletion_gets_a_card_and_is_kept_out_of_the_revert_rate(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", BASE)
    s.commit()
    s.claude("a", "m.py", BASE.replace("l5\n", ""))
    s.commit()
    s.codex("c", "m.py", BASE)
    s.commit()
    s.commit()
    c = cards(s)
    assert "claude_code" in c and "claude_code:a" in c
    rate = c["claude_code"].metrics[M.EXACT_REVERT_RATE]
    assert rate.status is S.NOT_APPLICABLE
    assert rate.exclusions == {"deletion_restore_not_detected": 1}


def test_tokens_of_a_session_with_uncommitted_work_are_not_counted(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "a.py", "x = 1\n")
    for _ in range(3):
        s.commit()
    s.claude("b", "b.py", "p = 1\nq = 2\n")  # never committed
    usage = [
        SessionUsage("claude_code", "claude_code:a", 100, True),
        SessionUsage("claude_code", "claude_code:b", 900, True),
    ]
    tps = cards(s, usage)["claude_code"].metrics[M.TOKENS_PER_SURVIVING_LINE]
    assert (tps.numerator, tps.denominator) == (100, 1)
    assert tps.exclusions == {"sessions_not_matured": 1}


def test_churn_requires_a_verified_remover(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.codex("c", "a.py", "x = 1\ny = 2\n")
    s.commit()
    s.claude("a", "a.py", "x = 10\ny = 2\n", user_modified=True)
    s.commit()
    s.commit()
    m = cards(s)["codex"].metrics
    assert m[M.CHURN_RECEIVED].numerator == 0
    assert m[M.CHURN_INTERACTIONS].numerator == 0
    assert m[M.CHURN_RECEIVED].notes  # the unverified removal is disclosed


def test_agent_usage_notes_only_list_that_agents_sessions(tmp_path: Path) -> None:
    import sqlite3

    s = Scenario(tmp_path)
    s.claude("a", "a.py", "x = 1\n")
    s.commit()
    s.commit()
    r = s.run()
    by = {e.edit_id: e for e in s.edits}
    usage = [SessionUsage("codex", "codex:x", None, False, "no_usage_recorded")]
    built = build(r, s.edits, derive(r, by, Window()), usage, 1)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE scorecards (run_id, scope_kind, scope_id, agent, horizon_commits, "
        "mature_lines, pending_lines, sessions, comparable, usage_notes)"
    )
    conn.execute(
        "CREATE TABLE metrics (run_id, scope_kind, scope_id, name, status, numerator, "
        "denominator, horizon_commits, exclusions, definition_version)"
    )
    store(conn, 1, built, usage, 1)
    notes = dict(
        conn.execute("SELECT scope_id, usage_notes FROM scorecards WHERE agent='claude_code'")
    )
    assert notes == {"claude_code": "{}", "claude_code:a": "{}"}


def test_tokens_of_a_session_without_edits_are_not_counted(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "a.py", "x = 1\n")
    for _ in range(3):
        s.commit()
    usage = [
        SessionUsage("claude_code", "claude_code:a", 100, True),
        SessionUsage("claude_code", "claude_code:reader", 5_000, True),
    ]
    tps = cards(s, usage)["claude_code"].metrics[M.TOKENS_PER_SURVIVING_LINE]
    assert (tps.numerator, tps.denominator) == (100, 1)
    assert tps.exclusions == {"session_made_no_edits": 1}
