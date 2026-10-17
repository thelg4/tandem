from __future__ import annotations

import pytest

from tandem.diffs import (
    ApplyError,
    DiffParseError,
    apply_hunks,
    parse_codex_patch,
    parse_structured_patch,
    parse_unified_diff,
)
from tandem.model.edits import ChangeType

BEFORE = "a\nb\nc\nd\n"

UNIFIED = """\
--- a/x.py
+++ b/x.py
@@ -2,2 +2,3 @@
-b
+B
+B2
 c
"""


def test_parse_unified_diff() -> None:
    (hunk,) = parse_unified_diff(UNIFIED)
    assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (2, 2, 2, 3)
    assert hunk.removed == 1 and hunk.added == 2


def test_apply_unified_diff() -> None:
    assert apply_hunks(BEFORE, parse_unified_diff(UNIFIED)) == "a\nB\nB2\nc\nd\n"


def test_apply_refuses_context_mismatch() -> None:
    with pytest.raises(ApplyError):
        apply_hunks("a\nX\nc\nd\n", parse_unified_diff(UNIFIED))


def test_pure_insertion_and_deletion() -> None:
    insert = parse_unified_diff("@@ -1,0 +2,1 @@\n+new\n")
    assert apply_hunks(BEFORE, insert) == "a\nnew\nb\nc\nd\n"
    delete = parse_unified_diff("@@ -4,1 +3,0 @@\n-d\n")
    assert apply_hunks(BEFORE, delete) == "a\nb\nc\n"


def test_count_mismatch_rejected() -> None:
    with pytest.raises(DiffParseError):
        parse_unified_diff("@@ -1,2 +1,2 @@\n-a\n+A\n")


def test_structured_patch_matches_unified() -> None:
    entries = [{"oldStart": 2, "oldLines": 2, "newStart": 2, "newLines": 3,
                "lines": ["-b", "+B", "+B2", " c"]}]  # fmt: skip
    assert parse_structured_patch(entries) == parse_unified_diff(UNIFIED)


def test_crlf_normalized() -> None:
    assert apply_hunks("a\r\nb\r\nc\r\nd\r\n", parse_unified_diff(UNIFIED)) == "a\nB\nB2\nc\nd\n"


CODEX_PATCH = """\
*** Begin Patch
*** Add File: docs/new.md
+# Title
+body
*** Update File: src/app.py
*** Move to: src/main.py
@@ def run():
-    return 1
+    return 2
@@
 x = 1
+y = 2
*** Delete File: old.txt
*** End Patch
"""


def test_parse_codex_patch() -> None:
    add, update, delete = parse_codex_patch(CODEX_PATCH)
    assert add.change is ChangeType.ADD and add.added_content == "# Title\nbody\n"
    assert update.change is ChangeType.UPDATE and update.move_to == "src/main.py"
    assert [h.anchor for h in update.hunks] == ["def run():", None]
    assert all(not h.numbered for h in update.hunks)
    assert delete.change is ChangeType.DELETE and delete.path == "old.txt"


def test_codex_patch_requires_markers() -> None:
    with pytest.raises(DiffParseError):
        parse_codex_patch("*** Update File: a\n-x\n+y\n")
    with pytest.raises(DiffParseError):
        parse_codex_patch("*** Begin Patch\n*** Update File: a\n-x\n")


def test_unnumbered_hunks_cannot_be_applied_exactly() -> None:
    (update,) = parse_codex_patch("*** Begin Patch\n*** Update File: a\n-b\n+B\n*** End Patch")
    with pytest.raises(ApplyError):
        apply_hunks(BEFORE, update.hunks)
