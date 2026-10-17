"""Claude Code transcript adapter (audited formats: 2.1.285, 2.1.302; see docs/log-audit.md).

Edit evidence comes from ``toolUseResult`` on the user record that answers a tool call:
the original file text plus a numbered ``structuredPatch``. Tool inputs alone never
produce a succeeded edit (ING-05).

Usage sits on assistant records and is repeated on every record of one API response,
so it is counted once per ``requestId``.

The adapter parses one complete source generation. Calls whose results arrive in a
later append are paired when the generation is re-parsed (incremental pairing state
is a storage concern, B05).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from tandem.adapters.base import AdapterContext
from tandem.adapters.common import At, EventFactory, SessionInfo
from tandem.diffs import DiffParseError, parse_structured_patch
from tandem.model.edits import CallLink, ChangeType, EditSource, ObservedEdit
from tandem.model.events import ActionStatus, AgentFamily, Event, EventType, Usage, UsageBasis
from tandem.sources.jsonl import Record
from tandem.versions import ADAPTER_VERSIONS

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
SHELL_TOOLS = frozenset({"Bash"})
BOOKKEEPING = frozenset(
    {
        "attachment",
        "queue-operation",
        "last-prompt",
        "mode",
        "cost-state",
        "atis-latch",
        "summary",
        "system",
        "file-history-snapshot",
    }
)
USAGE_FIELDS = {
    "input_tokens": "input",
    "cache_creation_input_tokens": "cache_write",
    "cache_read_input_tokens": "cache_read",
    "output_tokens": "output",
}


class ClaudeCodeAdapter:
    agent = AgentFamily.CLAUDE_CODE
    version = ADAPTER_VERSIONS["claude_code"]
    supported_versions: tuple[str, ...] = ("2.1.285", "2.1.302")

    def parse(self, records: Iterable[Record], ctx: AdapterContext) -> Iterator[Event]:
        factory = EventFactory(ctx, self.version)
        calls: dict[str, tuple[str, str | None]] = {}  # tool_use id -> (name, model)
        seen_requests: set[str] = set()
        answered: set[str] = set()
        unknown_types: set[str] = set()
        events: list[Event] = []

        for index, record in enumerate(records):
            rec = record.value
            kind = rec.get("type")
            if kind in BOOKKEEPING:
                continue
            if kind not in ("user", "assistant"):
                if isinstance(kind, str) and kind not in unknown_types:
                    unknown_types.add(kind)
                    ctx.warn("unknown_record_type", f"ignored record type {kind!r}", record.offset)
                continue
            session = _session(rec)
            if session is None:
                ctx.warn("missing_session_id", "record has no sessionId", record.offset)
                continue
            raw_message = rec.get("message")
            message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
            base = At(record, index, session, rec.get("timestamp"), _str(rec.get("cwd")))
            sub = 0

            if kind == "assistant":
                model = _str(message.get("model"))
                request = _str(rec.get("requestId"))
                usage = message.get("usage")
                if isinstance(usage, dict) and request and request not in seen_requests:
                    seen_requests.add(request)
                    events.append(
                        factory.make(
                            base,
                            sub=sub,
                            event_type=EventType.USAGE,
                            status=ActionStatus.SUCCEEDED,
                            model=model,
                            provider_event_id=request,
                            usage=_usage(usage),
                        )
                    )
                    sub += 1
                for block in _blocks(message):
                    btype = block.get("type")
                    if btype == "text":
                        events.append(
                            factory.make(
                                base,
                                sub=sub,
                                event_type=EventType.RESPONSE,
                                status=ActionStatus.SUCCEEDED,
                                model=model,
                            )
                        )
                    elif btype == "tool_use":
                        name, tool_id = _str(block.get("name")) or "?", _str(block.get("id"))
                        if tool_id:
                            calls[tool_id] = (name, model)
                        etype = (
                            EventType.SHELL_COMMAND if name in SHELL_TOOLS else EventType.TOOL_CALL
                        )
                        events.append(
                            factory.make(
                                base,
                                sub=sub,
                                event_type=etype,
                                status=ActionStatus.ATTEMPTED,
                                model=model,
                                tool_name=name,
                                tool_call_id=tool_id,
                            )
                        )
                    else:
                        continue
                    sub += 1
                continue

            # user record: a prompt, or results for earlier tool calls
            content = message.get("content")
            if isinstance(content, str):
                events.append(
                    factory.make(
                        base, sub=sub, event_type=EventType.PROMPT, status=ActionStatus.SUCCEEDED
                    )
                )
                continue
            for block in _blocks(message):
                btype = block.get("type")
                if btype == "text":
                    events.append(
                        factory.make(
                            base,
                            sub=sub,
                            event_type=EventType.PROMPT,
                            status=ActionStatus.SUCCEEDED,
                        )
                    )
                    sub += 1
                    continue
                if btype != "tool_result":
                    continue
                tool_id = _str(block.get("tool_use_id"))
                name, model = calls.get(tool_id or "", ("?", None))
                if tool_id:
                    answered.add(tool_id)
                status = (
                    ActionStatus.FAILED if block.get("is_error") is True else ActionStatus.SUCCEEDED
                )
                events.append(
                    factory.make(
                        base,
                        sub=sub,
                        event_type=EventType.TOOL_RESULT,
                        status=status,
                        model=model,
                        tool_name=name,
                        tool_call_id=tool_id,
                    )
                )
                sub += 1
                if name in EDIT_TOOLS and status is ActionStatus.SUCCEEDED:
                    edit, reasons = _edit(name, rec.get("toolUseResult"), tool_id)
                    if edit is None:
                        ctx.warn(
                            "edit_detail_missing",
                            f"{name} result has no usable detail: {', '.join(reasons)}",
                            record.offset,
                        )
                        continue
                    events.append(
                        factory.make(
                            base,
                            sub=sub,
                            event_type=EventType.TEXT_EDIT,
                            status=ActionStatus.SUCCEEDED,
                            model=model,
                            tool_name=name,
                            tool_call_id=tool_id,
                            edit=edit,
                            reasons=reasons,
                        )
                    )
                    sub += 1

        for tool_id in calls.keys() - answered:
            ctx.warn("call_without_result", f"tool call {tool_id} has no result; left attempted")
        return iter(events)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _session(rec: dict[str, Any]) -> SessionInfo | None:
    sid = _str(rec.get("sessionId"))
    if sid is None:
        return None
    if rec.get("isSidechain") is True:
        agent_id = _str(rec.get("agentId")) or "sidechain"
        return SessionInfo(
            AgentFamily.CLAUDE_CODE,
            sid,
            f"claude_code:{sid}/{agent_id}",
            parent_session_id=f"claude_code:{sid}",
        )
    return SessionInfo(AgentFamily.CLAUDE_CODE, sid, f"claude_code:{sid}")


def _usage(raw: dict[str, Any]) -> Usage:
    categories = {
        USAGE_FIELDS[k]: int(v) for k, v in raw.items() if k in USAGE_FIELDS and isinstance(v, int)
    }
    return Usage(
        basis=UsageBasis.PER_CALL,
        categories=categories,
        complete=len(categories) == len(USAGE_FIELDS),
    )


def _edit(
    tool: str, tur: object, tool_id: str | None
) -> tuple[ObservedEdit | None, tuple[str, ...]]:
    if not isinstance(tur, dict):
        return None, ("tool_use_result_not_structured",)
    path = _str(tur.get("filePath"))
    if path is None:
        return None, ("file_path_missing",)
    reasons: list[str] = []
    try:
        hunks = parse_structured_patch(tur.get("structuredPatch") or [])
    except DiffParseError:
        hunks = ()
        reasons.append("structured_patch_unparseable")
    after: str | None
    original = tur.get("originalFile")
    before = original if isinstance(original, str) else None
    if tool == "Write" and tur.get("type") == "create":
        change, after = ChangeType.ADD, _str(tur.get("content")) or ""
    elif tool == "Write":
        content = tur.get("content")
        change, after = ChangeType.UPDATE, content if isinstance(content, str) else None
    else:
        change, after = ChangeType.UPDATE, None
    if change is ChangeType.UPDATE and before is None:
        reasons.append("original_file_missing")
    user_modified = tur.get("userModified") is True
    if user_modified:
        reasons.append("user_modified")
    edit = ObservedEdit(
        path=path,
        change=change,
        source=EditSource.CLAUDE_TOOL_USE_RESULT,
        hunks=hunks,
        before_text=before,
        after_text=after,
        call_id=tool_id,
        call_link=CallLink.CALL_ID if tool_id else CallLink.NONE,
        user_modified=user_modified,
    )
    return edit, tuple(reasons)
