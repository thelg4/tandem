from __future__ import annotations

from tandem.attribution.transitions import Transition, Unplaceable, reconstruct
from tandem.diffs import parse_codex_patch, parse_unified_diff
from tandem.model.edits import ChangeType, EditSource, Hunk, ObservedEdit

WORK = ["def run():", "    return 1", "", "def stop():", "    return 1"]


def fc(diff: str) -> ObservedEdit:
    return ObservedEdit("a.py", ChangeType.UPDATE, EditSource.CODEX_FILE_CHANGE,
                        hunks=parse_unified_diff(diff))  # fmt: skip


def patch(text: str) -> ObservedEdit:
    (op,) = parse_codex_patch(text)
    return ObservedEdit("a.py", op.change, EditSource.CODEX_PATCH_TEXT, hunks=op.hunks)


def test_numbered_hunk_applies_exactly() -> None:
    t = reconstruct(fc("@@ -1,2 +1,2 @@\n def run():\n-    return 1\n+    return 2\n"), WORK)
    assert isinstance(t, Transition) and t.unique and t.reasons == ()
    assert t.after == ["def run():", "    return 2", "", "def stop():", "    return 1"]


def test_numbered_hunk_with_wrong_numbers_is_located_by_content() -> None:
    t = reconstruct(fc("@@ -10,2 +10,2 @@\n def stop():\n-    return 1\n+    return 0\n"), WORK)
    assert isinstance(t, Transition) and t.unique
    assert t.after is not None and t.after[-1] == "    return 0"
    assert "located_by_content" in t.reasons


def test_patch_text_anchor_disambiguates() -> None:
    edit = patch("*** Begin Patch\n*** Update File: a.py\n@@ def stop():\n-    return 1\n"
                 "+    return 9\n*** End Patch")  # fmt: skip
    t = reconstruct(edit, WORK)
    assert isinstance(t, Transition) and t.unique
    assert t.after == ["def run():", "    return 1", "", "def stop():", "    return 9"]


def test_patch_text_without_anchor_is_non_unique() -> None:
    edit = patch(
        "*** Begin Patch\n*** Update File: a.py\n-    return 1\n+    return 9\n*** End Patch"
    )
    t = reconstruct(edit, WORK)
    assert isinstance(t, Transition) and not t.unique
    assert "non_unique_location" in t.reasons


def test_content_not_found_is_unplaceable() -> None:
    edit = patch("*** Begin Patch\n*** Update File: a.py\n-nope\n+yes\n*** End Patch")
    assert isinstance(reconstruct(edit, WORK), Unplaceable)


def test_claude_edit_uses_logged_original() -> None:
    edit = ObservedEdit("a.py", ChangeType.UPDATE, EditSource.CLAUDE_TOOL_USE_RESULT,
                        hunks=(Hunk(1, 1, 1, 1, ("-x", "+y")),), before_text="x\n")  # fmt: skip
    t = reconstruct(edit, ["something", "else"])
    assert isinstance(t, Transition) and t.before_from_log
    assert (t.before, t.after) == (["x"], ["y"])


def test_add_and_delete() -> None:
    add = ObservedEdit("n.py", ChangeType.ADD, EditSource.CODEX_FILE_CHANGE, after_text="a\nb\n")
    t = reconstruct(add, None)
    assert isinstance(t, Transition) and (t.before, t.after) == (None, ["a", "b"])
    gone = ObservedEdit("n.py", ChangeType.DELETE, EditSource.CODEX_FILE_CHANGE)
    t = reconstruct(gone, ["a"])
    assert isinstance(t, Transition) and t.after is None
    assert isinstance(reconstruct(gone, None), Unplaceable)


def test_pure_append_without_context() -> None:
    edit = patch("*** Begin Patch\n*** Update File: a.py\n+# end\n*** End Patch")
    t = reconstruct(edit, ["a"])
    assert isinstance(t, Transition) and t.after == ["a", "# end"]
