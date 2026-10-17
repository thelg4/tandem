from __future__ import annotations

from adapter_helpers import of_type, parse_fixture
from tandem.adapters import ClaudeCodeAdapter
from tandem.diffs import apply_hunks
from tandem.model.edits import CallLink, ChangeType, EditSource
from tandem.model.events import ActionStatus, AgentFamily, EventType, UsageBasis

FIXTURE = "claude_code/2.1/session.jsonl"


def parse():  # type: ignore[no-untyped-def]
    return parse_fixture(ClaudeCodeAdapter(), FIXTURE)


def test_only_successful_edits_with_detail_become_text_edits() -> None:
    events, _ = parse()
    edits = of_type(events, EventType.TEXT_EDIT)
    assert [e.action.tool_call_id for e in edits] == ["toolu_001", "toolu_002", "toolu_005"]
    assert all(e.action.status is ActionStatus.SUCCEEDED for e in edits)


def test_write_create_is_an_add_with_content() -> None:
    edit = of_type(parse()[0], EventType.TEXT_EDIT)[0].edit
    assert edit is not None
    assert edit.change is ChangeType.ADD
    assert edit.before_text is None
    assert edit.after_text == "def run():\n    return 1\n"
    assert edit.source is EditSource.CLAUDE_TOOL_USE_RESULT
    assert edit.call_link is CallLink.CALL_ID


def test_edit_is_reconstructable_from_the_log_alone() -> None:
    edit = of_type(parse()[0], EventType.TEXT_EDIT)[1].edit
    assert edit is not None and edit.before_text is not None
    assert apply_hunks(edit.before_text, edit.hunks) == "def run():\n    return 2\n"
    assert (edit.lines_added, edit.lines_removed) == (1, 1)


def test_user_modified_edit_is_flagged() -> None:
    event = of_type(parse()[0], EventType.TEXT_EDIT)[2]
    assert event.edit is not None and event.edit.user_modified
    assert "user_modified" in event.evidence.reason_codes


def test_failed_edit_is_a_failed_result_not_an_edit() -> None:
    results = {e.action.tool_call_id: e for e in of_type(parse()[0], EventType.TOOL_RESULT)}
    assert results["toolu_003"].action.status is ActionStatus.FAILED
    assert results["toolu_004"].action.tool_name == "Bash"


def test_call_without_result_stays_attempted() -> None:
    events, ctx = parse()
    calls = {e.action.tool_call_id: e for e in of_type(events, EventType.TOOL_CALL)}
    assert calls["toolu_006"].action.status is ActionStatus.ATTEMPTED
    assert "toolu_006" not in {
        e.action.tool_call_id for e in of_type(events, EventType.TOOL_RESULT)
    }
    assert any(code == "call_without_result" for code, _, _ in ctx.diagnostics)


def test_shell_calls_are_shell_commands() -> None:
    shells = of_type(parse()[0], EventType.SHELL_COMMAND)
    assert [e.action.tool_call_id for e in shells] == ["toolu_004"]


def test_usage_counted_once_per_request() -> None:
    usage = of_type(parse()[0], EventType.USAGE)
    # req_001 spans two records with identical usage; six requests in total.
    assert [e.identity.provider_event_id for e in usage] == [f"req_00{i}" for i in range(1, 7)]
    first = usage[0].usage
    assert first is not None and first.basis is UsageBasis.PER_CALL and first.complete
    assert first.categories == {"input": 1200, "cache_write": 300, "cache_read": 0, "output": 80}


def test_envelope_fields() -> None:
    events, _ = parse()
    event = events[0]
    assert event.actor.agent_family is AgentFamily.CLAUDE_CODE
    assert event.actor.session_id == "claude_code:cc-session-0001"
    assert event.scope.recorded_cwd == "/work/demo"
    assert event.order.utc_timestamp == "2026-10-01T12:00:01.000Z"
    assert all(e.actor.model_id == "claude-opus-5-5" for e in of_type(events, EventType.TEXT_EDIT))


def test_ordering_and_ids_are_stable() -> None:
    first, _ = parse()
    second, _ = parse()
    assert [e.identity.event_id for e in first] == [e.identity.event_id for e in second]
    sequences = [e.order.sequence for e in first]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
