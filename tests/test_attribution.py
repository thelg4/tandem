"""Attribution scenarios from the spec's verification table (§16), on real Git repositories."""

from __future__ import annotations

import difflib
import os
import subprocess
from pathlib import Path

from tandem.attribution.engine import AttrEdit, OriginKind, Outcome, Result, Span
from tandem.attribution.git_history import attribute
from tandem.diffs import parse_codex_patch, parse_unified_diff
from tandem.git_reader import GitReader
from tandem.globs import matches_any
from tandem.model.attribution import EvidenceClass
from tandem.model.edits import ChangeType, EditSource, ObservedEdit

T0 = 1_790_000_000


class Scenario:
    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.repo.mkdir()
        self.t = T0
        self.edits: list[AttrEdit] = []
        self.commits: list[str] = []
        self._git("init", "-q", "-b", "main")

    def _git(self, *args: str) -> str:
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_AUTHOR_NAME": "dev",
            "GIT_AUTHOR_EMAIL": "dev@example.invalid",
            "GIT_COMMITTER_NAME": "dev",
            "GIT_COMMITTER_EMAIL": "dev@example.invalid",
            "GIT_AUTHOR_DATE": f"@{self.t} +0000",
            "GIT_COMMITTER_DATE": f"@{self.t} +0000",
        }
        out = subprocess.run(
            ["git", *args], cwd=self.repo, env=env, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def read(self, path: str) -> str | None:
        p = self.repo / path
        return p.read_text() if p.exists() else None

    def human(self, path: str, text: str | None) -> None:
        p = self.repo / path
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def commit(self) -> str:
        self.t += 100
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", f"c{len(self.commits)}")
        oid = self._git("rev-parse", "HEAD")
        self.commits.append(oid)
        return oid

    def move(self, old: str, new: str) -> None:
        self._git("mv", old, new)

    def claude(
        self,
        session: str,
        path: str,
        after: str,
        *,
        user_modified: bool = False,
        before: str | None = None,
        apply: bool = True,
    ) -> str:
        """A Claude Edit/Write: the log records the original file (or ``before``)."""
        original = self.read(path) if before is None else before
        if original is None:
            edit = ObservedEdit(
                path, ChangeType.ADD, EditSource.CLAUDE_TOOL_USE_RESULT, after_text=after
            )
        else:
            hunks = parse_unified_diff(
                "".join(
                    difflib.unified_diff(original.splitlines(True), after.splitlines(True), n=1)
                )
            )
            edit = ObservedEdit(
                path,
                ChangeType.UPDATE,
                EditSource.CLAUDE_TOOL_USE_RESULT,
                hunks=hunks,
                before_text=original,
                user_modified=user_modified,
            )
        return self._log("claude_code", session, path, edit, after if apply else None)

    def codex(self, session: str, path: str, after: str | None) -> str:
        """A Codex FileChange: numbered diff, no before-state in the log."""
        original = self.read(path)
        if original is None:
            edit = ObservedEdit(
                path, ChangeType.ADD, EditSource.CODEX_FILE_CHANGE, after_text=after
            )
        elif after is None:
            edit = ObservedEdit(path, ChangeType.DELETE, EditSource.CODEX_FILE_CHANGE)
        else:
            hunks = parse_unified_diff(
                "".join(
                    difflib.unified_diff(original.splitlines(True), after.splitlines(True), n=1)
                )
            )
            edit = ObservedEdit(path, ChangeType.UPDATE, EditSource.CODEX_FILE_CHANGE, hunks=hunks)
        return self._log("codex", session, path, edit, after, delete=after is None)

    def codex_patch(self, session: str, path: str, patch: str, after: str) -> str:
        (op,) = parse_codex_patch(patch)
        edit = ObservedEdit(path, op.change, EditSource.CODEX_PATCH_TEXT, hunks=op.hunks)
        return self._log("codex", session, path, edit, after)

    def _log(
        self,
        agent: str,
        session: str,
        path: str,
        edit: ObservedEdit,
        after: str | None,
        *,
        delete: bool = False,
    ) -> str:
        self.t += 10
        edit_id = f"e{len(self.edits)}"
        self.edits.append(
            AttrEdit(
                edit_id,
                f"{agent}:{session}",
                agent,
                float(self.t),
                ("src", len(self.edits)),
                path,
                edit,
            )
        )
        if delete:
            self.human(path, None)
        elif after is not None:
            self.human(path, after)
        return edit_id

    def run(
        self, *, max_count: int = 100, excluded: tuple[str, ...] = (), max_blob: int = 1 << 20
    ) -> Result:
        reader = GitReader(self.repo)
        commits = reader.history(reader.resolve_commit("HEAD"), max_count=max_count)
        return attribute(
            reader,
            commits,
            self.edits,
            max_blob_bytes=max_blob,
            excluded=lambda p: matches_any(p, excluded),
        )


def present(result: Result, path: str) -> list[Span]:
    return [s for s in result.spans if s.path == path and s.state == "present"]


def lines_by(result: Result, edit_id: str, state: str = "present") -> int:
    return sum(s.count for s in result.spans if s.origin.edit_id == edit_id and s.state == state)


TWO_FUNCS = "def f1():\n    return 1\n\n\ndef f2():\n    return 2\n"


def test_disjoint_change_leaves_other_agents_lines_intact(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", TWO_FUNCS)
    s.commit()
    b = s.codex("b", "m.py", TWO_FUNCS.replace("return 2", "return 20"))
    s.commit()
    r = s.run()
    assert r.edits[a].outcome is Outcome.COMMITTED and r.edits[a].evidence is EvidenceClass.VERIFIED
    assert lines_by(r, a) == 5
    removed = [x for x in r.spans if x.state == "removed" and x.origin.edit_id == a]
    assert [(x.count, x.removed_by) for x in removed] == [(1, b)]
    assert lines_by(r, b) == 1 and r.edits[b].evidence is EvidenceClass.VERIFIED


def test_rewrite_of_other_agents_lines_is_recorded_as_removal_by_that_edit(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", TWO_FUNCS)
    c1 = s.commit()
    b = s.codex("b", "m.py", "def f1():\n    return 10\n\n\ndef f2():\n    return 20\n")
    c2 = s.commit()
    r = s.run()
    removed = [x for x in r.spans if x.state == "removed" and x.origin.edit_id == a]
    assert sum(x.count for x in removed) == 2
    assert all(
        x.removed_by == b and x.removed_commit == c2 and x.introduced_commit == c1 for x in removed
    )


def test_exact_revert_restores_prior_text_as_new_instances(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", TWO_FUNCS)
    s.commit()
    a = s.claude("a", "m.py", TWO_FUNCS.replace("return 2", "return 99"))
    s.commit()
    b = s.codex("b", "m.py", TWO_FUNCS)
    s.commit()
    r = s.run()
    assert lines_by(r, a, "removed") == 1
    assert [x.removed_by for x in r.spans if x.origin.edit_id == a] == [b]
    assert lines_by(r, b) == 1  # restored line is a new instance owned by the reverting edit


def test_overwrite_before_commit_gets_no_committed_credit(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", TWO_FUNCS)
    s.commit()
    a = s.claude("a", "m.py", TWO_FUNCS.replace("return 1", "return 11"))
    b = s.codex("b", "m.py", TWO_FUNCS.replace("return 1", "return 12"))
    s.commit()
    r = s.run()
    assert r.edits[a].outcome is Outcome.OVERWRITTEN_BEFORE_COMMIT
    dropped = [x for x in r.spans if x.origin.edit_id == a]
    assert [(x.state, x.removed_by) for x in dropped] == [("dropped_before_commit", b)]
    assert r.edits[b].outcome is Outcome.COMMITTED


def test_mixed_commit_keeps_per_span_attribution(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "x.py", "a1\na2\n")
    b = s.codex("b", "y.py", "b1\n")
    s.human("x.py", "a1\nhuman\na2\n")
    s.commit()
    r = s.run()
    assert lines_by(r, a) == 2 and lines_by(r, b) == 1
    unknown = [x for x in present(r, "x.py") if x.origin.kind is OriginKind.UNKNOWN]
    assert sum(x.count for x in unknown) == 1
    assert r.inserted_lines_in_range == 4


def test_unlogged_change_between_edits_is_not_credited(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", "one\ntwo\n")
    s.human("m.py", "one\ntwo\nshell-added\n")  # e.g. a shell command, no edit log
    a2 = s.claude("a", "m.py", "one\ntwo\nshell-added\nthree\n")
    s.commit()
    r = s.run()
    assert lines_by(r, a) == 2 and lines_by(r, a2) == 1
    gap = [x for x in present(r, "m.py") if x.origin.kind is OriginKind.UNKNOWN]
    assert sum(x.count for x in gap) == 1 and "working_state_gap" in r.edits[a2].reasons


def test_edit_based_on_state_replaced_by_other_session_is_ambiguous(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "base\n")
    s.commit()
    s.claude("a", "m.py", "base\nfrom-a\n")
    s.codex("b", "m.py", "base\nfrom-a\nfrom-b\n")
    # Session a edits again from its own earlier view, unaware of b's change.
    a2 = s.claude("a", "m.py", "base\nfrom-a\nfrom-a-2\n", before="base\nfrom-a\n")
    s.commit()
    r = s.run()
    assert r.edits[a2].evidence is EvidenceClass.AMBIGUOUS
    assert "based_on_state_replaced_by_another_session" in r.edits[a2].reasons


def test_rename_keeps_lineage(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "old.py", "x = 1\ny = 2\n")
    s.commit()
    s.move("old.py", "new.py")
    s.commit()
    r = s.run()
    assert [(x.path, x.origin.edit_id, x.count) for x in r.spans if x.state == "present"] == [
        ("new.py", a, 2)
    ]


def test_uncommitted_edit_is_provisional(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    a = s.claude("a", "m.py", "x\ny\n")
    r = s.run()
    assert r.edits[a].outcome is Outcome.UNCOMMITTED
    assert lines_by(r, a, "provisional") == 1 and lines_by(r, a) == 0


def test_truncated_history_marks_baseline_and_earlier_edits(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    early = s.claude("a", "m.py", "x\n")
    s.commit()
    late = s.claude("a", "m.py", "x\ny\n")
    s.commit()
    r = s.run(max_count=1)
    assert r.baseline_commit == s.commits[0]
    assert r.edits[early].outcome is Outcome.BEFORE_HISTORY
    assert r.edits[late].outcome is Outcome.COMMITTED
    kinds = {x.origin.kind for x in present(r, "m.py")}
    assert kinds == {OriginKind.BASELINE, OriginKind.AGENT}
    assert r.inserted_lines_in_range == 1


def test_non_unique_patch_and_user_modified_are_supported(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "a = 1\nb = 2\na = 1\n")
    s.commit()
    p = s.codex_patch(
        "b",
        "m.py",
        "*** Begin Patch\n*** Update File: m.py\n-a = 1\n+a = 9\n*** End Patch",
        "a = 9\nb = 2\na = 1\n",
    )
    u = s.claude("a", "m.py", "a = 9\nb = 3\na = 1\n", user_modified=True)
    s.commit()
    r = s.run()
    assert r.edits[p].evidence is EvidenceClass.SUPPORTED
    assert r.edits[u].evidence is EvidenceClass.SUPPORTED and "user_modified" in r.edits[u].reasons


def test_excluded_paths_and_no_op_writes(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    lock = s.codex("b", "package-lock.json", "{}\n")
    noop = s.claude("a", "m.py", "x\n")
    s.commit()
    r = s.run(excluded=("**/package-lock.json",))
    assert r.edits[lock].outcome is Outcome.EXCLUDED
    assert r.edits[noop].outcome is Outcome.NO_EFFECT


def test_agent_change_discarded_before_commit(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    a = s.claude("a", "m.py", "x\nagent\n")
    s.human("m.py", "x\nz\n")  # the developer replaces it by hand, then commits
    s.commit()
    r = s.run()
    assert r.edits[a].outcome is Outcome.OVERWRITTEN_BEFORE_COMMIT
    assert [x.removed_by for x in r.spans if x.origin.edit_id == a] == [None]


def test_agent_delete_is_removal_by_that_edit(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", "x\ny\n")
    s.commit()
    d = s.codex("b", "m.py", None)
    s.commit()
    r = s.run()
    assert lines_by(r, a, "removed") == 2
    assert {x.removed_by for x in r.spans if x.origin.edit_id == a} == {d}
    assert r.edits[d].outcome is Outcome.COMMITTED


def test_removal_only_edit_of_uncommitted_lines_is_committed(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.claude("a", "m.py", "x\ny\n")
    trim = s.claude("a", "m.py", "x\n")
    s.commit()
    r = s.run()
    assert r.edits[trim].outcome is Outcome.COMMITTED


# -- Regressions from the independent review ---------------------------------------------


def _retime(
    s: Scenario, edit_id: str, *, time: float | None = None, order: tuple[str, int] | None = None
) -> None:
    s.edits = [
        AttrEdit(
            e.edit_id,
            e.session_id,
            e.agent,
            e.time if time is None else time,
            e.order if order is None else order,
            e.path,
            e.edit,
        )
        if e.edit_id == edit_id
        else e
        for e in s.edits
    ]


def test_add_over_unlogged_content_is_not_verified(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    s.human("m.py", "x\nshell\n")  # unlogged shell change
    add = s._log(
        "codex",
        "b",
        "m.py",
        ObservedEdit(
            "m.py", ChangeType.ADD, EditSource.CODEX_FILE_CHANGE, after_text="x\nshell\nnew\n"
        ),
        "x\nshell\nnew\n",
    )
    s.commit()
    r = s.run()
    verified = [
        x
        for x in r.spans
        if x.origin.edit_id == add and x.origin.evidence is EvidenceClass.VERIFIED
    ]
    assert verified == []


def test_rename_carries_tracking_forward(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "old.py", "a1\na2\n")
    s.commit()
    s.move("old.py", "new.py")
    s.commit()
    s.human("new.py", "h\n")
    s.commit()
    r = s.run()
    assert lines_by(r, a) == 0 and lines_by(r, a, "removed") == 2
    assert r.inserted_lines_in_range == 3


def test_reverted_pure_deletion_is_not_committed(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\ny\n")
    s.commit()
    d = s.claude("a", "m.py", "x\n")
    s.human("m.py", "x\ny\nz\n")  # the deleted line comes back before the commit
    s.commit()
    r = s.run()
    assert r.edits[d].outcome is not Outcome.COMMITTED
    assert not [x for x in r.spans if x.removed_by == d]
    assert r.inserted_lines_in_range == 3  # matches git: 2 + 1


def test_oversized_file_counts_in_denominator_and_is_not_present(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    a = s.claude("a", "m.py", "a\n")
    s.commit()
    s.human("m.py", "a\n" + "".join(f"line{i}\n" for i in range(50)))
    s.commit()
    r = s.run(max_blob=100)
    assert "m.py" in r.unavailable_paths
    assert lines_by(r, a) == 0
    assert r.unavailable_since["m.py"] == s.commits[-1]


def test_equal_timestamps_prefer_order_matching_logged_state(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "base\n")
    s.commit()
    a = s.claude("zzz", "m.py", "base\nA\n")
    b = s.claude("aaa", "m.py", "base\nA\nB\n")
    t = s.edits[-1].time
    _retime(s, a, time=t, order=("srcZ", 0))
    _retime(s, b, time=t, order=("srcA", 0))
    s.commit()
    r = s.run()
    assert r.edits[a].outcome is Outcome.COMMITTED and r.edits[b].outcome is Outcome.COMMITTED
    assert lines_by(r, a) == 1 and lines_by(r, b) == 1


def test_edit_in_same_second_after_commit_goes_to_next_commit(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\n")
    s.commit()
    s.human("m.py", "x\nhuman\n")
    s.commit()
    ct = s.t
    a = s.claude("a", "m.py", "x\nhuman\nagent\n")
    _retime(s, a, time=ct + 0.5)
    s.commit()
    r = s.run()
    assert r.edits[a].outcome is Outcome.COMMITTED and lines_by(r, a) == 1


def test_pending_removal_at_target_is_not_double_counted(tmp_path: Path) -> None:
    s = Scenario(tmp_path)
    s.human("m.py", "x\ny\n")
    s.commit()
    s.codex("b", "m.py", "x\n")  # uncommitted removal
    r = s.run()
    assert sum(x.count for x in r.spans if x.path == "m.py") == 2
