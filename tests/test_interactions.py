"""Interaction labels for the spec's cross-agent scenarios (§7, §16)."""

from __future__ import annotations

from pathlib import Path

from tandem.interactions import Interaction, Window, derive
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import InteractionLabel as L
from tandem.model.interactions import WindowMode
from test_attribution import TWO_FUNCS, Scenario


def interactions(s: Scenario, window: Window | None = None) -> list[Interaction]:
    return derive(s.run(), {e.edit_id: e for e in s.edits}, window or Window())


def test_disjoint_change_is_not_an_interaction(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "m.py", TWO_FUNCS)
    s.commit()
    s.codex("b", "m.py", TWO_FUNCS + "\n\ndef f3():\n    return 3\n")
    s.commit()
    assert interactions(s) == []


def test_rewrite_of_other_agents_lines(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", TWO_FUNCS)
    s.commit()
    b = s.codex(
        "b", "m.py", TWO_FUNCS.replace("return 1", "return 10").replace("return 2", "return 20")
    )
    s.commit()
    found = interactions(s)
    assert {(i.original_edit, i.later_edit, i.label) for i in found} == {(a, b, L.REWRITE)}
    assert sum(i.lines_affected for i in found) == 2
    assert all(i.evidence is EvidenceClass.VERIFIED and i.commit_distance == 1 for i in found)
    assert (found[0].original_agent, found[0].later_agent) == ("claude_code", "codex")


def test_exact_revert_is_one_interaction_not_also_a_rewrite(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", TWO_FUNCS)
    s.commit()
    a = s.claude("a", "m.py", TWO_FUNCS.replace("return 2", "return 99"))
    s.commit()
    b = s.codex("b", "m.py", TWO_FUNCS)
    s.commit()
    (only,) = interactions(s)
    assert (only.original_edit, only.later_edit, only.label) == (a, b, L.EXACT_REVERT)


def test_deleting_an_added_block_restores_the_prior_state(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    s.claude("a", "m.py", "x\nadded1\nadded2\n")
    s.commit()
    s.codex("b", "m.py", "x\n")
    s.commit()
    (only,) = interactions(s)
    assert only.label is L.EXACT_REVERT and only.lines_affected == 2


def test_removal_and_partial_revert(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "keep\nold1\nold2\nkeep2\nx\nkeep3\n")
    s.commit()
    s.claude("a", "m.py", "keep\nnew1\nnew2\nkeep2\ny\nkeep3\n")
    s.commit()
    # Codex restores part of the first change and deletes the second without replacement.
    s.codex("b", "m.py", "keep\nold1\nkeep2\nkeep3\n")
    s.commit()
    labels = sorted(i.label.value for i in interactions(s))
    assert labels == ["partial_revert", "removal"]


def test_overwrite_before_commit_is_tagged(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", TWO_FUNCS)
    s.commit()
    a = s.claude("a", "m.py", TWO_FUNCS.replace("return 1", "return 11"))
    b = s.codex("b", "m.py", TWO_FUNCS.replace("return 1", "return 12"))
    s.commit()
    (only,) = interactions(s)
    assert (only.original_edit, only.later_edit, only.label) == (a, b, L.REWRITE)
    assert "before_commit" in only.tags and only.commit_distance is None


def test_same_session_is_not_an_interaction_but_other_session_is_tagged(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "m.py", "x\ny\n")
    s.commit()
    s.claude("a", "m.py", "x\nz\n")  # same session revises its own line
    s.commit()
    assert interactions(s) == []
    s.claude("other", "m.py", "x\nw\n")
    s.commit()
    (only,) = interactions(s)
    assert "same_agent_cross_session" in only.tags and not only.cross_agent


def test_ambiguous_edit_gives_overlap_candidate(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "base\n")
    s.commit()
    s.codex("a", "m.py", "base\nA\n")
    s.codex("b", "m.py", "base\nA\nB\n")
    # Session c works from the state before b's change and replaces a's line.
    s.claude("c", "m.py", "base\nC\n", before="base\nA\n")
    s.commit()
    found = [i for i in interactions(s) if i.later_agent == "claude_code"]
    assert found and all(i.label is L.OVERLAP_CANDIDATE for i in found)
    assert all(i.evidence is EvidenceClass.AMBIGUOUS for i in found)


def test_window_rules() -> None:
    w = Window()
    assert w.contains(3600, None) and w.contains(None, 3) and not w.contains(None, None)
    assert not w.contains(48 * 3600, 9)
    both = Window(mode=WindowMode.BOTH)
    assert both.contains(3600, 1) and not both.contains(3600, None)
    assert Window(mode=WindowMode.COMMITS).contains(10, None) is False


def test_review_order_puts_exact_reverts_first(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", TWO_FUNCS)
    s.human("n.py", "p\nq\nr\n")
    s.commit()
    s.claude("a", "m.py", TWO_FUNCS.replace("return 2", "return 99"))
    s.claude("a", "n.py", "p\nQ1\nQ2\nr\n")
    s.commit()
    s.codex("b", "n.py", "p\nZ1\nZ2\nr\n")
    s.codex("b", "m.py", TWO_FUNCS)
    s.commit()
    found = interactions(s)
    assert [i.label for i in found] == [L.EXACT_REVERT, L.REWRITE]


def test_ids_are_stable_across_runs(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "m.py", "x\n")
    s.commit()
    s.codex("b", "m.py", "y\n")
    s.commit()
    assert [i.interaction_id for i in interactions(s)] == [
        i.interaction_id for i in interactions(s)
    ]


# -- Regressions from the second independent review --------------------------------------


def test_removal_undone_by_unlogged_change_then_later_edit_is_not_an_interaction(
    tmp_path: Path,
) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\nL\nz\n")
    s.commit()
    a = s.claude("a", "m.py", "x\nL2\nz\n")
    s.commit()
    s.codex("b", "m.py", "x\nz\n")  # removal, not committed
    s.human("m.py", "x\nL2\nz\n")  # discarded on disk, unlogged
    s.claude("c", "m.py", "X\nL2\nz\n")  # logs the restored state as its before-state
    s.commit()
    assert interactions(s) == []
    r = s.run()
    assert sum(x.count for x in r.spans if x.state == "present" and x.origin.edit_id == a) == 1


def test_uncommitted_removal_undone_before_commit_is_not_an_interaction(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    a = s.claude("a", "m.py", "x\nL\n")
    s.codex("b", "m.py", "x\n")
    s.human("m.py", "x\nL\n")  # undone by hand before committing
    s.commit()
    assert interactions(s) == []
    r = s.run()
    assert sum(x.count for x in r.spans if x.state == "present" and x.origin.edit_id == a) == 1


def test_same_text_elsewhere_does_not_hide_a_removal(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\ny\n")
    s.commit()
    a = s.claude("a", "m.py", "x\nDUP\ny\n")
    s.commit()
    s.codex("b", "m.py", "x\ny\n")
    s.human("m.py", "x\ny\nDUP\n")  # same text appended elsewhere, unlogged
    s.commit()
    (only,) = interactions(s)
    assert only.label is L.EXACT_REVERT
    r = s.run()
    assert not [x for x in r.spans if x.state == "present" and x.origin.edit_id == a]


def test_partial_revert_needs_a_contiguous_meaningful_restoration(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "a\n\nb\n")
    s.commit()
    s.claude("a", "m.py", "A1\nA2\nA3\n")
    s.commit()
    s.codex("b", "m.py", "\n")  # only a blank line, which happened to be in the old text
    s.commit()
    assert all(i.label is not L.PARTIAL_REVERT for i in interactions(s))


def test_one_later_change_over_one_sessions_edits_is_one_interaction(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\nz\n")
    s.commit()
    s.claude("a", "m.py", "x\nA1\nA2\nz\n")
    s.claude("a", "m.py", "x\nA1\nA2b\nz\n")  # same session tweaks its own line
    s.commit()
    s.codex("b", "m.py", "x\nz\n")
    s.commit()
    (only,) = interactions(s)
    assert only.lines_affected == 2 and only.label is L.REMOVAL


def test_headline_counts_verified_only() -> None:
    from tandem.report.views import summarize

    def row(evidence: str, label: str = "rewrite") -> dict[str, object]:
        return {
            "label": label,
            "evidence_class": evidence,
            "lines_affected": 1,
            "recent": False,
            "original": {"agent": "claude_code"},
            "later": {"agent": "codex"},
        }

    summary = summarize([row("verified"), row("supported"), row("ambiguous", "overlap_candidate")])
    assert summary["cross_agent"] == 1
    assert summary["lower_evidence"] == 1 and summary["overlap_candidates"] == 1
