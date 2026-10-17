"""Helpers shared by provider adapters: event construction, IDs, timestamps."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from tandem.adapters.base import AdapterContext
from tandem.model.edits import ObservedEdit
from tandem.model.events import (
    Action,
    ActionStatus,
    Actor,
    AgentFamily,
    Event,
    EventType,
    Evidence,
    Identity,
    Order,
    PathResolution,
    Scope,
    SourceLocator,
    TimeQuality,
    Usage,
)
from tandem.sources.jsonl import Record

SUB_SLOTS = 1000
"""Sequence = record index * SUB_SLOTS + sub-index, so events derived later (for example
Codex patch-text edits resolved at end of file) still sort at their source position."""


def normalize_timestamp(raw: object) -> tuple[str | None, TimeQuality]:
    if not isinstance(raw, str) or not raw:
        return None, TimeQuality.MISSING
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, TimeQuality.MISSING
    if parsed.tzinfo is None:
        return parsed.isoformat(timespec="milliseconds") + "Z", TimeQuality.COARSE
    utc = parsed.astimezone(UTC).replace(tzinfo=None)
    return utc.isoformat(timespec="milliseconds") + "Z", TimeQuality.EXACT


def event_id(source_id: str, generation: int, offset: int, sub: int) -> str:
    return hashlib.sha256(f"{source_id}\0{generation}\0{offset}\0{sub}".encode()).hexdigest()[:32]


@dataclass(slots=True)
class SessionInfo:
    agent: AgentFamily
    provider_session_id: str
    session_id: str
    parent_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class At:
    """Where an event comes from: one source record and the context active at it."""

    record: Record
    index: int
    session: SessionInfo
    timestamp: object
    cwd: str | None


class EventFactory:
    """Builds envelopes for one source file with stable IDs and ordering."""

    def __init__(self, ctx: AdapterContext, adapter_version: str) -> None:
        self.ctx = ctx
        self.adapter_version = adapter_version

    def make(
        self,
        at: At,
        *,
        sub: int,
        event_type: EventType,
        status: ActionStatus,
        model: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        provider_event_id: str | None = None,
        usage: Usage | None = None,
        edit: ObservedEdit | None = None,
        reasons: tuple[str, ...] = (),
    ) -> Event:
        if sub >= SUB_SLOTS:
            raise ValueError("too many events derived from one record")
        record, session, cwd, timestamp = at.record, at.session, at.cwd, at.timestamp
        utc, quality = normalize_timestamp(timestamp)
        return Event(
            identity=Identity(
                event_id=event_id(
                    self.ctx.source_id, self.ctx.source_generation, record.offset, sub
                ),
                source_id=self.ctx.source_id,
                source_generation=self.ctx.source_generation,
                record_offset=record.offset,
                adapter_version=self.adapter_version,
                provider_event_id=provider_event_id,
            ),
            actor=Actor(
                agent_family=session.agent,
                provider_session_id=session.provider_session_id,
                session_id=session.session_id,
                parent_session_id=session.parent_session_id,
                model_id=model,
            ),
            scope=Scope(
                recorded_cwd=cwd,
                effective_cwd=cwd,
                relative_path=None,
                path_resolution=PathResolution.UNRESOLVED,  # resolved against the repo later
            ),
            order=Order(
                sequence=at.index * SUB_SLOTS + sub,
                raw_timestamp=timestamp if isinstance(timestamp, str) else None,
                utc_timestamp=utc,
                time_quality=quality,
            ),
            action=Action(event_type, status, tool_name, tool_call_id),
            evidence=Evidence(
                locator=SourceLocator(self.ctx.source_path, record.offset, record.length),
                reason_codes=reasons,
                analyzer_version=self.adapter_version,
            ),
            usage=usage,
            edit=edit,
        )
