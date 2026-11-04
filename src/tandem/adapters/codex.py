"""Codex rollout adapter (audited formats: 0.94 through 0.153; see docs/log-audit.md).

Edit evidence, strongest first:

1. ``event_msg/item_completed`` with a ``FileChange`` item (0.144+): per-file numbered
   unified diffs, emitted only on success. Linked to its call by ID when the item ID
   matches a call, otherwise by source order to the preceding call (exec code cells).
2. ``apply_patch`` input text (all versions): context-anchored hunks without line
   numbers. Used only for a successful apply_patch call that has no FileChange.

Usage comes from ``event_msg/token_count``: per-call ``last_token_usage`` counted once per
change in the cumulative ``total_token_usage``; a drop in the cumulative total starts a
new counter segment. ``token_usage_record`` overlaps token_count and is not counted.

Subagent threads begin with history copied from the parent; records before
``subagent_history_start_ordinal`` are skipped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from tandem.adapters.base import AdapterContext
from tandem.adapters.common import SUB_SLOTS, At, EventFactory, SessionInfo
from tandem.diffs import DiffParseError, parse_codex_patch, parse_unified_diff
from tandem.model.edits import CallLink, ChangeType, EditSource, ObservedEdit
from tandem.model.events import ActionStatus, AgentFamily, Event, EventType, Usage, UsageBasis
from tandem.sources.jsonl import Record
from tandem.versions import ADAPTER_VERSIONS

CALL_TYPES = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
SHELL_TOOLS = frozenset({"shell", "shell_command", "exec_command", "local_shell", "container.exec"})
IGNORED_TOP = frozenset({"world_state", "compacted", "token_usage_record"})
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_EXIT_CODE = re.compile(r"^Exit code: (-?\d+)", re.M)


@dataclass(slots=True)
class _Call:
    name: str
    at: At
    payload: dict[str, Any]
    status: ActionStatus = ActionStatus.ATTEMPTED
    output_at: At | None = None
    has_file_change: bool = False


@dataclass(slots=True)
class _State:
    session: SessionInfo | None = None
    cwd: str | None = None
    model: str | None = None
    start_ordinal: int | None = None
    calls: dict[str, _Call] = field(default_factory=dict)
    last_call_id: str | None = None
    last_total: dict[str, int] | None = None
    usage_segment: int = 0
    skipped_inherited: int = 0


class CodexAdapter:
    agent = AgentFamily.CODEX
    version = ADAPTER_VERSIONS["codex"]
    supported_versions: tuple[str, ...] = (
        "0.94.0-alpha.10",
        "0.98.0",
        "0.144.0-alpha.4",
        "0.153.4",
    )

    def parse(self, records: Iterable[Record], ctx: AdapterContext) -> Iterator[Event]:
        factory = EventFactory(ctx, self.version)
        st = _State()
        events: list[Event] = []

        for index, record in enumerate(records):
            rec = record.value
            kind = rec.get("type")
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            if kind == "session_meta":
                st.session, st.cwd, st.start_ordinal = _session(payload)
                if st.session is None:
                    ctx.warn("missing_session_id", "session_meta has no id", record.offset)
                continue
            if st.session is None:
                ctx.warn("record_before_session_meta", "record ignored", record.offset)
                continue
            ordinal = rec.get("ordinal")
            if (
                st.start_ordinal is not None
                and isinstance(ordinal, int)
                and ordinal < st.start_ordinal
            ):
                st.skipped_inherited += 1
                continue
            if kind in IGNORED_TOP:
                continue

            at = At(record, index, st.session, rec.get("timestamp"), st.cwd)
            if kind == "turn_context":
                st.cwd = _str(payload.get("cwd")) or st.cwd
                st.model = _str(payload.get("model")) or st.model
            elif kind == "response_item":
                events.extend(self._response_item(factory, st, at, payload))
            elif kind == "event_msg":
                events.extend(self._event_msg(factory, st, at, payload, ctx))

        if st.skipped_inherited:
            ctx.warn(
                "inherited_history_skipped",
                f"skipped {st.skipped_inherited} records copied from the parent thread",
            )
        events.extend(self._patch_text_fallback(factory, st, ctx))
        for call_id, call in st.calls.items():
            if call.output_at is None:
                ctx.warn(
                    "call_without_result", f"tool call {call_id} has no output; left attempted"
                )
        events.sort(key=lambda e: e.order.sequence)
        return iter(events)

    # -- response items -------------------------------------------------------------------

    def _response_item(
        self, factory: EventFactory, st: _State, at: At, payload: dict[str, Any]
    ) -> list[Event]:
        ptype = payload.get("type")
        if ptype == "message":
            etype = EventType.PROMPT if payload.get("role") == "user" else EventType.RESPONSE
            return [
                factory.make(
                    at, sub=0, event_type=etype, status=ActionStatus.SUCCEEDED, model=st.model
                )
            ]
        if ptype in CALL_TYPES:
            call_id = _str(payload.get("call_id"))
            name = _str(payload.get("name")) or str(ptype)
            if call_id:
                st.calls[call_id] = _Call(name, at, payload)
                st.last_call_id = call_id
            etype = EventType.SHELL_COMMAND if name in SHELL_TOOLS else EventType.TOOL_CALL
            return [
                factory.make(
                    at,
                    sub=0,
                    event_type=etype,
                    status=ActionStatus.ATTEMPTED,
                    model=st.model,
                    tool_name=name,
                    tool_call_id=call_id,
                )
            ]
        if ptype in OUTPUT_TYPES:
            call_id = _str(payload.get("call_id"))
            call = st.calls.get(call_id or "")
            status = _output_status(payload.get("output"))
            if call is not None:
                call.status, call.output_at = status, at
            return [
                factory.make(
                    at,
                    sub=0,
                    event_type=EventType.TOOL_RESULT,
                    status=status,
                    model=st.model,
                    tool_name=call.name if call else None,
                    tool_call_id=call_id,
                )
            ]
        return []

    # -- event messages -------------------------------------------------------------------

    def _event_msg(
        self,
        factory: EventFactory,
        st: _State,
        at: At,
        payload: dict[str, Any],
        ctx: AdapterContext,
    ) -> list[Event]:
        ptype = payload.get("type")
        if ptype == "token_count":
            usage = self._usage(st, payload)
            if usage is None:
                return []
            reasons: tuple[str, ...] = ("cached_input_is_subset_of_input",)
            if st.usage_segment:
                reasons += (f"usage_segment_{st.usage_segment}",)
            return [
                factory.make(
                    at,
                    sub=0,
                    event_type=EventType.USAGE,
                    status=ActionStatus.SUCCEEDED,
                    model=st.model,
                    usage=usage,
                    reasons=reasons,
                )
            ]
        if ptype != "item_completed":
            return []
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "FileChange":
            return []
        changes = item.get("changes")
        if not isinstance(changes, dict):
            ctx.warn(
                "file_change_without_changes", "FileChange has no changes map", at.record.offset
            )
            return []

        item_id = _str(item.get("id"))
        if item_id and item_id in st.calls:
            call_id, link = item_id, CallLink.CALL_ID
            st.calls[item_id].has_file_change = True
        elif st.last_call_id:
            call_id, link = st.last_call_id, CallLink.SOURCE_ORDER
        else:
            call_id, link = None, CallLink.NONE
        tool = st.calls[call_id].name if call_id and call_id in st.calls else None

        events = []
        for sub, path in enumerate(sorted(changes)):
            edit, edit_reasons = _file_change_edit(path, changes[path], call_id, link)
            if edit is None:
                ctx.warn(
                    "file_change_unparseable",
                    f"{', '.join(edit_reasons)} for one path",
                    at.record.offset,
                )
                continue
            events.append(
                factory.make(
                    at,
                    sub=sub,
                    event_type=EventType.TEXT_EDIT,
                    status=ActionStatus.SUCCEEDED,
                    model=st.model,
                    tool_name=tool,
                    tool_call_id=call_id,
                    provider_event_id=item_id,
                    edit=edit,
                    reasons=edit_reasons,
                )
            )
        return events

    def _usage(self, st: _State, payload: dict[str, Any]) -> Usage | None:
        info = payload.get("info")
        if not isinstance(info, dict):
            return None
        total, last = info.get("total_token_usage"), info.get("last_token_usage")
        if not isinstance(total, dict) or not isinstance(last, dict):
            return None
        total_n = {k: v for k, v in total.items() if isinstance(v, int)}
        if st.last_total is not None:
            if total_n == st.last_total:
                return None  # repeated observation of the same counter
            if total_n.get("total_tokens", 0) < st.last_total.get("total_tokens", 0):
                st.usage_segment += 1  # counter reset
        st.last_total = total_n
        categories = {k: int(last[k]) for k in USAGE_FIELDS if isinstance(last.get(k), int)}
        return Usage(
            basis=UsageBasis.PER_CALL,
            categories=categories,
            complete=len(categories) == len(USAGE_FIELDS),
        )

    # -- apply_patch text fallback ----------------------------------------------------------

    def _patch_text_fallback(
        self, factory: EventFactory, st: _State, ctx: AdapterContext
    ) -> list[Event]:
        events: list[Event] = []
        for call_id, call in st.calls.items():
            if call.name != "apply_patch" or call.has_file_change:
                continue
            if call.status is not ActionStatus.SUCCEEDED or call.output_at is None:
                continue
            text = call.payload.get("input")
            if not isinstance(text, str):
                text = _patch_from_arguments(call.payload.get("arguments"))
            if text is None:
                ctx.warn("patch_text_missing", f"apply_patch {call_id} has no patch text")
                continue
            try:
                ops = parse_codex_patch(text)
            except DiffParseError as exc:
                ctx.warn(
                    "patch_text_unparseable", f"apply_patch {call_id}: {exc}", call.at.record.offset
                )
                continue
            # Place derived edits right after the call's output, after anything it emitted.
            for n, op in enumerate(ops):
                edit = ObservedEdit(
                    path=op.path,
                    change=op.change,
                    source=EditSource.CODEX_PATCH_TEXT,
                    hunks=op.hunks,
                    after_text=op.added_content,
                    move_to=op.move_to,
                    call_id=call_id,
                    call_link=CallLink.CALL_ID,
                )
                events.append(
                    factory.make(
                        call.output_at,
                        sub=SUB_SLOTS // 2 + n,
                        event_type=EventType.TEXT_EDIT,
                        status=ActionStatus.SUCCEEDED,
                        model=st.model,
                        tool_name="apply_patch",
                        tool_call_id=call_id,
                        edit=edit,
                        reasons=("unnumbered_hunks",),
                    )
                )
        return events


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _session(payload: dict[str, Any]) -> tuple[SessionInfo | None, str | None, int | None]:
    thread = _str(payload.get("id")) or _str(payload.get("session_id"))
    cwd = _str(payload.get("cwd"))
    start = payload.get("subagent_history_start_ordinal")
    if thread is None:
        return None, cwd, None
    parent = _str(payload.get("parent_thread_id"))
    info = SessionInfo(
        AgentFamily.CODEX,
        thread,
        f"codex:{thread}",
        parent_session_id=f"codex:{parent}" if parent else None,
    )
    return info, cwd, start if isinstance(start, int) else None


def _output_status(output: object) -> ActionStatus:
    if isinstance(output, str):
        if "verification failed" in output[:200]:
            return ActionStatus.FAILED
        try:
            parsed = json.loads(output)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("metadata"), dict):
            code = parsed["metadata"].get("exit_code")
            if isinstance(code, int):
                return ActionStatus.SUCCEEDED if code == 0 else ActionStatus.FAILED
        match = _EXIT_CODE.search(output[:200])
        if match:
            return ActionStatus.SUCCEEDED if int(match.group(1)) == 0 else ActionStatus.FAILED
        if output.lstrip().startswith("Success"):
            return ActionStatus.SUCCEEDED
    return ActionStatus.UNKNOWN


def _patch_from_arguments(arguments: object) -> str | None:
    if not isinstance(arguments, str):
        return None
    try:
        parsed = json.loads(arguments)
    except ValueError:
        return arguments if "*** Begin Patch" in arguments else None
    if isinstance(parsed, dict):
        for key in ("input", "patch"):
            if isinstance(parsed.get(key), str):
                return str(parsed[key])
        cmd = parsed.get("command")
        if isinstance(cmd, list):
            return next((c for c in cmd if isinstance(c, str) and "*** Begin Patch" in c), None)
    return None


def _file_change_edit(
    path: str, change: object, call_id: str | None, link: CallLink
) -> tuple[ObservedEdit | None, tuple[str, ...]]:
    if not isinstance(change, dict):
        return None, ("change_not_an_object",)
    kind = change.get("type")

    def make(change_type: ChangeType, **fields: Any) -> ObservedEdit:
        return ObservedEdit(
            path=path,
            change=change_type,
            source=EditSource.CODEX_FILE_CHANGE,
            call_id=call_id,
            call_link=link,
            **fields,
        )

    reasons: tuple[str, ...] = ("linked_by_source_order",) if link is CallLink.SOURCE_ORDER else ()
    if kind == "add":
        content = change.get("content")
        if not isinstance(content, str):
            return None, ("add_without_content",)
        return make(ChangeType.ADD, after_text=content), reasons
    if kind == "delete":
        return make(ChangeType.DELETE), reasons
    if kind == "update":
        diff = change.get("unified_diff")
        if not isinstance(diff, str):
            return None, ("update_without_diff",)
        try:
            hunks = parse_unified_diff(diff)
        except DiffParseError:
            return None, ("unified_diff_unparseable",)
        if not hunks:
            reasons += ("empty_diff",)
        move = _str(change.get("move_path"))
        return make(ChangeType.UPDATE, hunks=hunks, move_to=move), reasons
    return None, (f"unknown_change_type_{kind}",)
