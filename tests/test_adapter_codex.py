from __future__ import annotations

from adapter_helpers import of_type, parse_fixture
from tandem.adapters import CodexAdapter
from tandem.adapters.base import AdapterContext
from tandem.diffs import apply_hunks
from tandem.model.edits import CallLink, ChangeType, EditSource
from tandem.model.events import ActionStatus, Event, EventType


def parse(rel: str) -> tuple[list[Event], AdapterContext]:
    return parse_fixture(CodexAdapter(), rel)


# -- 0.98: patch text only ---------------------------------------------------------------


def test_patch_text_edit_from_successful_apply_patch_only() -> None:
    events, _ = parse("codex/0.98-patch-text/rollout.jsonl")
    edits = of_type(events, EventType.TEXT_EDIT)
    assert len(edits) == 1
    edit = edits[0].edit
    assert edit is not None
    assert edit.source is EditSource.CODEX_PATCH_TEXT
    assert edit.path == "app.py" and edit.change is ChangeType.UPDATE
    assert edit.call_id == "call_p1" and edit.call_link is CallLink.CALL_ID
    assert not edit.hunks[0].numbered and edit.hunks[0].anchor == "def run():"
    assert "unnumbered_hunks" in edits[0].evidence.reason_codes


def test_patch_text_edit_sorts_after_its_output() -> None:
    events, _ = parse("codex/0.98-patch-text/rollout.jsonl")
    kinds = [(e.action.event_type, e.action.tool_call_id) for e in events]
    assert kinds.index((EventType.TEXT_EDIT, "call_p1")) == (
        kinds.index((EventType.TOOL_RESULT, "call_p1")) + 1
    )


def test_output_statuses() -> None:
    events, _ = parse("codex/0.98-patch-text/rollout.jsonl")
    status = {
        e.action.tool_call_id: e.action.status for e in of_type(events, EventType.TOOL_RESULT)
    }
    assert status == {
        "call_p1": ActionStatus.SUCCEEDED,
        "call_p2": ActionStatus.FAILED,  # verification failed
        "call_s1": ActionStatus.FAILED,  # exit code 1
    }
    shells = of_type(events, EventType.SHELL_COMMAND)
    assert [e.action.tool_name for e in shells] == ["shell_command"]


def test_repeated_token_counts_are_not_double_counted() -> None:
    events, _ = parse("codex/0.98-patch-text/rollout.jsonl")
    usage = [e.usage for e in of_type(events, EventType.USAGE)]
    assert [u.categories["input_tokens"] for u in usage if u] == [1000, 600]


# -- 0.144: FileChange --------------------------------------------------------------------


def test_file_change_linked_by_call_id_suppresses_patch_text() -> None:
    events, _ = parse("codex/0.144-file-change/rollout.jsonl")
    edits = [e.edit for e in of_type(events, EventType.TEXT_EDIT) if e.edit]
    assert [e.source for e in edits] == [EditSource.CODEX_FILE_CHANGE] * 2
    update = edits[0]
    assert update.call_id == "call_a1" and update.call_link is CallLink.CALL_ID
    assert update.path == "/work/demo/app.py"
    assert apply_hunks("def run():\n    return 2\n", update.hunks) == "def run():\n    return 3\n"


def test_exec_file_change_linked_by_source_order() -> None:
    events, _ = parse("codex/0.144-file-change/rollout.jsonl")
    event = of_type(events, EventType.TEXT_EDIT)[1]
    edit = event.edit
    assert edit is not None
    assert edit.change is ChangeType.ADD and edit.after_text == "# Notes\n"
    assert edit.call_id == "call_e1" and edit.call_link is CallLink.SOURCE_ORDER
    assert event.identity.provider_event_id == "item-77"
    assert "linked_by_source_order" in event.evidence.reason_codes


def test_counter_reset_starts_new_usage_segment() -> None:
    events, _ = parse("codex/0.144-file-change/rollout.jsonl")
    usage = of_type(events, EventType.USAGE)
    assert len(usage) == 2
    assert "usage_segment_1" in usage[1].evidence.reason_codes


# -- 0.153: subagent ----------------------------------------------------------------------


def test_subagent_skips_copied_parent_history() -> None:
    events, ctx = parse("codex/0.153-subagent/rollout.jsonl")
    edits = [e.edit for e in of_type(events, EventType.TEXT_EDIT) if e.edit]
    assert sorted((e.path, e.change) for e in edits) == [
        ("/work/demo/lib/util.py", ChangeType.UPDATE),
        ("/work/demo/old.txt", ChangeType.DELETE),
    ]
    usage = [e.usage for e in of_type(events, EventType.USAGE) if e.usage]
    assert [u.categories["input_tokens"] for u in usage] == [300]
    assert any(code == "inherited_history_skipped" for code, _, _ in ctx.diagnostics)


def test_subagent_session_has_parent() -> None:
    events, _ = parse("codex/0.153-subagent/rollout.jsonl")
    actor = events[0].actor
    assert actor.session_id == "codex:cx-thread-0153"
    assert actor.parent_session_id == "codex:cx-thread-parent"


def test_ids_stable_and_sequences_ordered() -> None:
    for rel in ("codex/0.98-patch-text/rollout.jsonl", "codex/0.144-file-change/rollout.jsonl"):
        first, _ = parse(rel)
        second, _ = parse(rel)
        assert [e.identity.event_id for e in first] == [e.identity.event_id for e in second]
        seqs = [e.order.sequence for e in first]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
